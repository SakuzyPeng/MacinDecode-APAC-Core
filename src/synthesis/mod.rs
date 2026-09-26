//! Independent SQ mathematics: fixed IEEE constants, Float64 synthesis and overlap.
//! Ordinary products and sums round separately; only final PCM is cast to Float32.
mod bundle;
use crate::{
    error::{Error, Result},
    frame::{FrameContext, PacketReport, parse_packet},
};
pub use bundle::{SqDecodeOptions, decode_sq, decode_sq_with_options};
pub const NUMERIC_PROFILE: &str = crate::numeric::PROFILE;
pub const BACKEND: &str = "rust_sq_cac_tns_bwe2_f64_fft_v8";
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
}
impl ChannelState {
    fn new() -> Self {
        Self {
            overlap: vec![0.; 1024],
        }
    }
    fn render(&mut self, spectrum: &[f32], block: u8) -> Result<Vec<f32>> {
        if spectrum.len() != 1024 || spectrum.iter().any(|v| !v.is_finite()) {
            return Err(Error::new(
                "SQ synthesis",
                "requires 1024 finite coefficients",
            ));
        }
        if block > 3 {
            return Err(Error::new("SQ synthesis", "unsupported window type"));
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
        Ok(output)
    }
}

/// Qualified no-DRC stereo SQ packets, neutral scene metadata and bounded ASP preroll.
/// One packet produces exactly 1024 interleaved stereo frames. Errors do not advance state.
pub struct SqDecoder {
    context: FrameContext,
    channels: [ChannelState; 2],
}
impl SqDecoder {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self> {
        let context = FrameContext::from_cookie(cookie)?;
        if let Some(reason) = context.packet_rejection() {
            return Err(Error::new(
                "SQ decoder",
                format!("unsupported configuration: {reason}"),
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
        self.decode_frame_report(packet).map(|(samples, _)| samples)
    }
    pub(crate) fn decode_frame_report(
        &mut self,
        packet: &[u8],
    ) -> Result<(Vec<f32>, FrameStateCounts)> {
        let decoded = parse_packet(&self.context, packet).map_err(|e| {
            let mut error = Error::new("SQ spectrum", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
        if !decoded.packet_complete {
            let frame = decoded.frame();
            let mut error = Error::new(
                "SQ decoder",
                format!("unsupported frame: {}", frame.stop_reason),
            );
            error.bit_offset = Some(
                frame
                    .diagnostics
                    .first()
                    .map_or(frame.stop_bit_offset, |d| d.bit_offset),
            );
            return Err(error);
        }
        let mut next = self.channels.clone();
        let output = render_packet(&mut next, &decoded)?;
        self.channels = next;
        Ok(output)
    }
}

#[derive(Default)]
pub(crate) struct FrameStateCounts {
    pub cpe_absent: bool,
    pub embedded_preroll_frames: u64,
    pub embedded_cpe_absent: u64,
}

fn render_packet(
    channels: &mut [ChannelState; 2],
    decoded: &PacketReport,
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let mut counts = FrameStateCounts::default();
    if let Some(preroll) = &decoded.embedded_preroll {
        // The internal frame replaces the overlap used by the current frame.
        // Its PCM is discarded; the entire outer packet commits atomically.
        let (_, inner) = render_packet(channels, &preroll.report)?;
        counts.embedded_preroll_frames = 1 + inner.embedded_preroll_frames;
        counts.embedded_cpe_absent = u64::from(inner.cpe_absent) + inner.embedded_cpe_absent;
    }
    counts.cpe_absent = decoded.cpe_absent();
    let mut sides = Vec::with_capacity(2);
    for (index, state) in channels.iter_mut().enumerate() {
        let samples = if counts.cpe_absent {
            // Exact zero current spectrum: retain the previous tail for this
            // output interval, then clear it. No fabricated encoded channels.
            let samples: Vec<f32> = state
                .overlap
                .iter()
                .map(|&v| {
                    let value = v as f32;
                    if value == 0. { 0. } else { value }
                })
                .collect();
            if samples.iter().any(|v| !v.is_finite()) {
                return Err(Error::new("SQ synthesis", "nonfinite PCM"));
            }
            state.overlap.fill(0.);
            samples
        } else {
            state.render(
                &decoded.bwe2.channels_after_bwe2[index].scaled,
                decoded.bwe2.tns.cac.spectrum.channels[index].ics.block_type,
            )?
        };
        sides.push(samples);
    }
    let mut output = Vec::with_capacity(2048);
    for (&left, &right) in sides[0].iter().zip(&sides[1]) {
        output.extend([left, right]);
    }
    Ok((output, counts))
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
        for first in 0..4 {
            for second in 0..4 {
                state.render(&zero, first).unwrap();
                state.render(&zero, second).unwrap();
            }
        }
        assert!(state.render(&zero, 4).is_err());
        assert!(state.render(&[f32::NAN; 1024], 0).is_err());
    }
}
