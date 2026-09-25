//! Independent SQ mathematics: fixed IEEE constants, Float64 synthesis and overlap.
//! Ordinary products and sums round separately; only final PCM is cast to Float32.
mod bundle;
use crate::{
    config::{self, bits::BitReader},
    error::{Error, Result},
    frame::{FrameContext, parse_tns},
};
pub use bundle::decode_sq;
pub const NUMERIC_PROFILE: &str = crate::numeric::PROFILE;
pub const BACKEND: &str = "rust_sq_cac_tns_f64_fft_v5";
pub const QUALIFICATION: &str = "independent_math_reference";

#[derive(Clone, Copy, Default)]
struct Complex {
    re: f64,
    im: f64,
}
fn fft(data: &mut [Complex]) {
    let n = data.len();
    let twiddles = &crate::numeric::tables().transform(2 * n).twiddles;
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            data.swap(i, j);
        }
    }
    let mut size = 2;
    while size <= n {
        for chunk in data.chunks_exact_mut(size) {
            for k in 0..size / 2 {
                let [cos, sin] = twiddles[k * n / size];
                let a = chunk[k];
                let b = chunk[k + size / 2];
                let re = b.re * cos - b.im * sin;
                let im = b.re * sin + b.im * cos;
                chunk[k] = Complex {
                    re: a.re + re,
                    im: a.im + im,
                };
                chunk[k + size / 2] = Complex {
                    re: a.re - re,
                    im: a.im - im,
                };
            }
        }
        size *= 2;
    }
}
fn modulation(n: usize) -> &'static [[f64; 2]] {
    &crate::numeric::tables().transform(n).modulation
}
fn imdct(input: &[f32]) -> Vec<f64> {
    let n = input.len();
    let mut data = vec![Complex::default(); n / 2];
    let normalization = 1. / (n as f64 * 32768.);
    for (k, z) in data.iter_mut().enumerate() {
        let [sin, cos] = modulation(n)[k];
        let a = f64::from(input[2 * k]) * normalization;
        let b = f64::from(input[n - 1 - 2 * k]) * normalization;
        *z = Complex {
            re: a * cos + b * sin,
            im: b * cos - a * sin,
        };
    }
    fft(&mut data);
    let mut dct = vec![0f64; n];
    for (k, z) in data.iter().enumerate() {
        let [sin, cos] = modulation(n)[k];
        dct[2 * k] = z.re * cos + z.im * sin;
        dct[n - 1 - 2 * k] = z.re * sin - z.im * cos;
    }
    (0..2 * n)
        .map(|i| {
            let m = i + n / 2;
            if m < n {
                dct[m]
            } else if m < 2 * n {
                -dct[2 * n - 1 - m]
            } else {
                -dct[m - 2 * n]
            }
        })
        .collect()
}
fn window(n: usize) -> &'static [f64] {
    &crate::numeric::tables().transform(n).window
}
#[derive(Clone)]
struct ChannelState {
    overlap: Vec<f64>,
    previous: u8,
}
impl ChannelState {
    fn new() -> Self {
        Self {
            overlap: vec![0.; 1024],
            previous: 0,
        }
    }
    fn render(&mut self, spectrum: &[f32], block: u8) -> Result<Vec<f32>> {
        if spectrum.len() != 1024 || spectrum.iter().any(|v| !v.is_finite()) {
            return Err(Error::new(
                "SQ synthesis",
                "requires 1024 finite coefficients",
            ));
        }
        if !matches!((self.previous, block), (0 | 3, 0 | 1) | (1 | 2, 2 | 3)) {
            return Err(Error::new(
                "SQ synthesis",
                "unsupported window transition; start with a long frame",
            ));
        }
        let mut time = vec![0.; 2048];
        if block == 2 {
            for w in 0..8 {
                let samples = imdct(&spectrum[w * 128..(w + 1) * 128]);
                for (i, &sample) in samples.iter().enumerate() {
                    let index = 448 + w * 128 + i;
                    let product = sample * window(128)[i];
                    time[index] += product;
                }
            }
        } else {
            let samples = imdct(spectrum);
            for i in 0..2048 {
                let gain = match block {
                    1 if i >= 1024 => {
                        if i < 1472 {
                            1.
                        } else if i < 1600 {
                            window(128)[128 + i - 1472]
                        } else {
                            0.
                        }
                    }
                    3 if i < 1024 => {
                        if i < 448 {
                            0.
                        } else if i < 576 {
                            window(128)[i - 448]
                        } else {
                            1.
                        }
                    }
                    _ => window(1024)[i],
                };
                time[i] = samples[i] * gain;
            }
        }
        let output: Vec<f32> = (0..1024)
            .map(|i| {
                let value = (time[i] + self.overlap[i]) as f32;
                if value == 0. { 0. } else { value }
            })
            .collect();
        if output.iter().any(|v| !v.is_finite()) || time.iter().any(|v| !v.is_finite()) {
            return Err(Error::new("SQ synthesis", "nonfinite PCM"));
        }
        self.overlap.copy_from_slice(&time[1024..]);
        self.previous = block;
        Ok(output)
    }
}

/// Strict subset: independent/shared SQ streams with CAC/TNS, no later tools or ancillary data.
/// One packet produces exactly 1024 interleaved stereo frames. Errors do not advance state.
pub struct SqDecoder {
    context: FrameContext,
    channels: [ChannelState; 2],
}
impl SqDecoder {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self> {
        let parsed = config::parse_cookie(cookie)?;
        let context = FrameContext::from_cookie(cookie)?;
        // Preserve the restricted configuration while making every rejected wire
        // field reviewable. Missing fields have no invented cookie coordinate.
        let mut rejected = Vec::new();
        let mut check = |name: &str, expected: serde_json::Value| match parsed
            .fields
            .iter()
            .find(|f| f.name == name)
        {
            Some(field) if field.value == expected => {}
            Some(field) => rejected.push(format!(
                "{name}={} at cookie bit {} (expected {expected})",
                field.value, field.bit_offset
            )),
            None => rejected.push(format!(
                "{name}=missing at cookie bit unknown (expected {expected})"
            )),
        };
        for (name, value) in [
            ("global.profile_id", 31),
            ("global.level_id", 0),
            ("global.parameter_b", 2),
            ("box.version_flags", 0),
            ("bitstream_version", 0x0800),
            ("global.frame_size_index", 0),
            ("global.channel_count", 2),
            ("global.component_count", 1),
            ("components[0].type", 0),
            ("components[0].lowest_channel_index", 0),
            ("components[0].tce_count", 1),
            ("components[0].tce[0].type", 1),
            ("components[0].parameter_0", 0),
            ("components[0].parameter_1", 0),
            ("components[0].layout_family", 101),
        ] {
            check(name, serde_json::json!(value));
        }
        for name in [
            "global.flag_a",
            "global.flag_c",
            "global.additional_asc_present",
            "components[0].lbr_flag",
            "ancillary.scene_graph_present",
            "ancillary.audio_scenes_present",
            "ancillary.loudness_drc_present",
            "ancillary.metadata_present",
            "ancillary.custom_data_present",
            "extensions[0].present",
            "components[0].remapping_present",
        ] {
            check(name, serde_json::json!(false));
        }
        if !matches!(
            parsed
                .derived
                .get("sample_rate_hz")
                .and_then(|v| v.as_u64()),
            Some(44100 | 48000)
        ) && let Some(field) = parsed
            .fields
            .iter()
            .find(|f| f.name == "global.sample_rate_index")
        {
            rejected.push(format!(
                "{}={} at cookie bit {} (expected 44.1/48 kHz)",
                field.name, field.value, field.bit_offset
            ));
        }
        if !parsed.is_complete() {
            rejected.push(format!(
                "cookie status={:?} at cookie bit {} (expected complete)",
                parsed.status,
                parsed
                    .unknown_ranges
                    .first()
                    .map_or(cookie.len() * 8, |r| r.bit_offset)
            ));
        }
        if !context.is_supported() && rejected.is_empty() {
            rejected.push("frame context unsupported; cookie fields do not establish the restricted SQ syntax".into());
        }
        if !rejected.is_empty() {
            return Err(Error::new(
                "SQ decoder",
                format!("unsupported configuration: {}", rejected.join("; ")),
            ));
        }
        Ok(Self {
            context,
            channels: [ChannelState::new(), ChannelState::new()],
        })
    }
    pub fn reset(&mut self) {
        self.channels = [ChannelState::new(), ChannelState::new()];
    }
    pub fn decode_frame(&mut self, packet: &[u8]) -> Result<Vec<f32>> {
        let decoded = parse_tns(&self.context, packet).map_err(|e| {
            let mut error = Error::new("SQ spectrum", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
        let report = &decoded.cac.spectrum;
        if !decoded.tns_complete {
            return Err(Error::new(
                "SQ decoder",
                format!("unsupported frame: {}", report.frame.stop_reason),
            ));
        }
        if packet[0] >> 6 > 1 {
            return Err(Error::new(
                "SQ decoder",
                "ASP refresh/preroll is not implemented",
            ));
        }
        let mut bits = BitReader::new(packet);
        bits.skip(report.frame.stop_bit_offset)?;
        let mut zero = |width: usize, name: &str| -> Result<()> {
            let offset = bits.position();
            let value = bits.read(width).map_err(|e| {
                let mut error = Error::new("SQ tail", e.to_string());
                error.bit_offset = Some(e.bit_offset);
                error
            })?;
            if value != 0 {
                let mut error = Error::new("SQ decoder", format!("unsupported nonzero {name}"));
                error.bit_offset = Some(offset);
                return Err(error);
            }
            Ok(())
        };
        zero(1, "left BWE2")?;
        zero(1, "right BWE2")?;
        let position = report.frame.stop_bit_offset + 2;
        zero((8 - position % 8) % 8, "core alignment")?;
        zero(1, "ancillary trimming")?;
        zero(7, "ancillary alignment")?;
        if bits.remaining() != 0 {
            return Err(Error::new("SQ decoder", "unparsed trailing bytes"));
        }
        let mut next = self.channels.clone();
        let left = next[0].render(
            &decoded.channels_after_tns[0].scaled,
            report.channels[0].ics.block_type,
        )?;
        let right = next[1].render(
            &decoded.channels_after_tns[1].scaled,
            report.channels[1].ics.block_type,
        )?;
        let mut output = Vec::with_capacity(2048);
        for (l, r) in left.into_iter().zip(right) {
            output.extend([l, r]);
        }
        self.channels = next;
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;
    #[test]
    fn mathematical_windows_are_symmetric_and_power_complementary() {
        for n in [128, 1024] {
            let values = window(n);
            assert!(values[..n].windows(2).all(|w| w[0] < w[1]));
            for i in 0..n {
                assert_eq!(values[i].to_bits(), values[2 * n - 1 - i].to_bits());
                let a = values[i];
                let b = values[n - 1 - i];
                assert!((a * a + b * b - 1.).abs() < 3e-16);
            }
        }
    }
    #[test]
    fn transform_matches_direct_definition() {
        for n in [128, 1024] {
            let mut input = vec![0.; n];
            input[0] = 1.;
            input[3] = -2.;
            input[n - 1] = 0.5;
            let result = imdct(&input);
            for (i, &x) in result.iter().enumerate() {
                let expected: [f64; 3] = [0., 3., (n - 1) as f64];
                let sum = expected
                    .into_iter()
                    .zip([1., -2., 0.5])
                    .map(|(k, v)| {
                        v * (PI / n as f64 * (i as f64 + 0.5 + n as f64 / 2.) * (k + 0.5)).cos()
                    })
                    .sum::<f64>()
                    / (n as f64 * 32768.);
                assert!((x - sum).abs() < 1e-12, "{n} {i} {x} {sum}");
            }
        }
    }
    #[test]
    fn every_long_and_short_frequency_has_the_correct_basis() {
        for n in [128, 1024] {
            let mut input = vec![0.; n];
            for k in 0..n {
                input[k] = 1.;
                for (i, actual) in imdct(&input).into_iter().enumerate() {
                    let expected =
                        (PI / n as f64 * (i as f64 + 0.5 + n as f64 / 2.) * (k as f64 + 0.5)).cos()
                            / (n as f64 * 32768.);
                    assert!((actual - expected).abs() < 1e-18, "{n} {k} {i}");
                }
                input[k] = 0.;
            }
        }
    }
    #[test]
    fn window_transitions_and_reset_state() {
        let mut state = ChannelState::new();
        let zero = vec![0.; 1024];
        for block in [0, 1, 2, 2, 3, 0] {
            assert!(
                state
                    .render(&zero, block)
                    .unwrap()
                    .iter()
                    .all(|v| v.to_bits() == 0)
            );
        }
        assert!(state.render(&zero, 2).is_err());
        assert!(state.render(&[f32::NAN; 1024], 0).is_err());
    }
}
