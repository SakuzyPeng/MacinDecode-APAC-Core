//! Portable sine-window SQ synthesis. Float32 modulation and windows, Float64 radix-2 FFT.
mod bundle;
use crate::{
    config::{self, bits::BitReader},
    error::{Error, Result},
    frame::{FrameContext, parse_spectrum},
};
pub use bundle::decode_sq;
use std::{f64::consts::PI, sync::OnceLock};

#[derive(Clone, Copy, Default)]
struct Complex {
    re: f64,
    im: f64,
}
fn fft(data: &mut [Complex]) {
    let n = data.len();
    static SHORT: OnceLock<Vec<Complex>> = OnceLock::new();
    static LONG: OnceLock<Vec<Complex>> = OnceLock::new();
    let cell = if n == 64 { &SHORT } else { &LONG };
    let twiddles = cell.get_or_init(|| {
        (0..n / 2)
            .map(|k| {
                let (im, re) = (-2. * PI * k as f64 / n as f64).sin_cos();
                Complex { re, im }
            })
            .collect()
    });
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
                let Complex { re: cos, im: sin } = twiddles[k * n / size];
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
fn modulation(n: usize) -> &'static [(f32, f32)] {
    static SHORT: OnceLock<Vec<(f32, f32)>> = OnceLock::new();
    static LONG: OnceLock<Vec<(f32, f32)>> = OnceLock::new();
    let cell = if n == 128 { &SHORT } else { &LONG };
    cell.get_or_init(|| {
        (0..n / 2)
            .map(|k| {
                let (sin, cos) = (PI * (k as f64 + 0.125) / n as f64).sin_cos();
                // A uniform decimal quantizer reproduces the verified modulation rule;
                // no binary lookup tables or index-specific coefficient patches.
                let quantize = |x: f64| ((x * 1e10).round() / 1e10) as f32;
                (quantize(sin), quantize(cos))
            })
            .collect()
    })
}
fn imdct(input: &[f32]) -> Vec<f32> {
    let n = input.len();
    let mut data = vec![Complex::default(); n / 2];
    let normalization = 1. / (n as f32 * 32768.);
    for (k, z) in data.iter_mut().enumerate() {
        let (sin, cos) = modulation(n)[k];
        let a = input[2 * k] * normalization;
        let b = input[n - 1 - 2 * k] * normalization;
        *z = Complex {
            re: f64::from(if k < n / 4 {
                b.mul_add(sin, a * cos)
            } else {
                a.mul_add(cos, b * sin)
            }),
            im: f64::from(b.mul_add(cos, -a * sin)),
        };
    }
    fft(&mut data);
    let mut dct = vec![0f32; n];
    for (k, z) in data.iter().enumerate() {
        let (sin, cos) = modulation(n)[k];
        let (re, im) = (z.re as f32, z.im as f32);
        dct[2 * k] = im.mul_add(sin, re * cos);
        dct[n - 1 - 2 * k] = if k < n / 4 {
            re.mul_add(sin, -im * cos)
        } else {
            -im.mul_add(cos, -re * sin)
        };
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
fn window(n: usize) -> &'static [f32] {
    static LONG: OnceLock<Vec<f32>> = OnceLock::new();
    static SHORT: OnceLock<Vec<f32>> = OnceLock::new();
    let cell = if n == 1024 { &LONG } else { &SHORT };
    cell.get_or_init(|| {
        (0..2 * n)
            .map(|i| (PI * (i as f64 + 0.5) / (2 * n) as f64).sin() as f32)
            .collect()
    })
}
#[derive(Clone)]
struct ChannelState {
    overlap: Vec<f32>,
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
        let output: Vec<f32> = (0..1024).map(|i| time[i] + self.overlap[i]).collect();
        if output.iter().any(|v| !v.is_finite()) {
            return Err(Error::new("SQ synthesis", "nonfinite PCM"));
        }
        self.overlap.copy_from_slice(&time[1024..]);
        self.previous = block;
        Ok(output)
    }
}

/// Strict subset: two independent SQ channel streams, no tools or ancillary data.
/// One packet produces exactly 1024 interleaved stereo frames. Errors do not advance state.
pub struct SqDecoder {
    context: FrameContext,
    channels: [ChannelState; 2],
}
impl SqDecoder {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self> {
        let parsed = config::parse_cookie(cookie)?;
        let context = FrameContext::from_cookie(cookie)?;
        let field = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == name)
                .map(|f| &f.value)
        };
        let fixed = [
            ("global.profile_id", 31),
            ("global.level_id", 0),
            ("global.parameter_b", 2),
        ];
        if fixed
            .iter()
            .any(|(name, value)| field(name).and_then(|v| v.as_u64()) != Some(*value))
            || !context.is_supported()
            || !parsed.is_complete()
            || [
                "ancillary.scene_graph_present",
                "ancillary.audio_scenes_present",
                "ancillary.loudness_drc_present",
                "ancillary.metadata_present",
                "ancillary.custom_data_present",
                "extensions[0].present",
                "components[0].remapping_present",
            ]
            .iter()
            .any(|name| field(name).and_then(|v| v.as_bool()) != Some(false))
            || field("components[0].layout_family").and_then(|v| v.as_u64()) != Some(101)
        {
            return Err(Error::new(
                "SQ decoder",
                "requires verified stereo configuration without ancillary data, extensions or remapping",
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
        let report = parse_spectrum(&self.context, packet).map_err(|e| {
            let mut error = Error::new("SQ spectrum", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
        if !report.spectrum_complete {
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
        zero(1, "left TNS")?;
        zero(1, "right TNS")?;
        zero(1, "left BWE2")?;
        zero(1, "right BWE2")?;
        let position = report.frame.stop_bit_offset + 4;
        zero((8 - position % 8) % 8, "core alignment")?;
        zero(1, "ancillary trimming")?;
        zero(7, "ancillary alignment")?;
        if bits.remaining() != 0 {
            return Err(Error::new("SQ decoder", "unparsed trailing bytes"));
        }
        let mut next = self.channels.clone();
        let left = next[0].render(
            &report.channels[0].scaled,
            report.channels[0].ics.block_type,
        )?;
        let right = next[1].render(
            &report.channels[1].scaled,
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
                assert!((f64::from(x) - sum).abs() < 1e-12, "{n} {i} {x} {sum}");
            }
        }
    }
    #[test]
    fn window_transitions_and_reset_state() {
        let mut state = ChannelState::new();
        let zero = vec![0.; 1024];
        for block in [0, 1, 2, 2, 3, 0] {
            assert_eq!(state.render(&zero, block).unwrap(), vec![0.; 1024]);
        }
        assert!(state.render(&zero, 2).is_err());
        assert!(state.render(&[f32::NAN; 1024], 0).is_err());
    }
}
