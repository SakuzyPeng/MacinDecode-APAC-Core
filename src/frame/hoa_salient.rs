//! Restricted order-3, five-component/four-subband spatial descriptors.
//! Format tables and independent mathematical constants have separate identities.
use super::{
    ChannelPacketReport, Parser,
    hoa::HoaCoefficientSpectrum,
    spectrum::{Codebook, Trie},
};
use crate::config::{ConfigField, ParseError};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::OnceLock;

pub const NUMERIC_PROFILE: &str = "apac-hoa-salient-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-salient-state-v1";
const ENDS: [usize; 4] = [32, 80, 216, 1024];

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub(crate) struct SalientState {
    history: [[[f64; 16]; 4]; 5],
    previous_frame_sha256: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SalientDescriptor {
    pub component_index: usize,
    pub subband_index: usize,
    pub mode: u8,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub quantized: Vec<u8>,
    pub signs_positive: Vec<bool>,
    pub cluster: Option<u8>,
    pub azimuth_degrees: Option<u16>,
    pub elevation_offset_degrees: Option<u8>,
    pub restored: [f64; 16],
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SalientSpatialData {
    pub history_frame_sha256: Option<String>,
    /// Boundaries in the native frequency-major ordering, before short-window inverse transpose.
    pub subband_ends: [usize; 4],
    /// Exclusive frequency-line ends in one window, not band widths.
    pub lines_per_window: [usize; 4],
    pub descriptors: Vec<SalientDescriptor>,
}

#[derive(Deserialize)]
struct Mode {
    mode: usize,
    groups: Vec<Vec<usize>>,
    codebooks: Vec<Vec<(usize, u32)>>,
    signs: bool,
    matrices_f32: Vec<Vec<u32>>,
}
#[derive(Deserialize)]
struct Format {
    format_profile: String,
    tables_sha256: String,
    modes: Vec<Mode>,
}
#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    azimuth_f64: Vec<[u64; 2]>,
    elevation_f64: Vec<[u64; 2]>,
    roots_f64: [u64; 7],
}
struct Constants {
    format: Format,
    tries: Vec<Vec<Trie>>,
    math_sha: String,
    azimuth: Vec<[f64; 2]>,
    elevation: Vec<[f64; 2]>,
    roots: [f64; 7],
}
fn constants() -> &'static Constants {
    static DATA: OnceLock<Constants> = OnceLock::new();
    DATA.get_or_init(|| {
        let format: Format =
            serde_json::from_str(include_str!("../../data/hoa-salient-format-v1.json"))
                .expect("built-in HOA format tables");
        let math: Math = serde_json::from_str(include_str!("../../data/hoa-salient-math-v1.json"))
            .expect("built-in HOA mathematical constants");
        assert_eq!(format.format_profile, "apac-hoa-salient-format-v1");
        assert_eq!(math.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(format.modes.len(), 6);
        assert_eq!(math.azimuth_f64.len(), 512);
        assert_eq!(math.elevation_f64.len(), 256);
        let tries = format
            .modes
            .iter()
            .enumerate()
            .map(|(index, m)| {
                assert_eq!(m.mode, index);
                m.codebooks
                    .iter()
                    .map(|book| {
                        assert_eq!(book.len(), 64);
                        Trie::new(&Codebook {
                            codes: book.iter().map(|v| v.1).collect(),
                            bits: book.iter().map(|v| v.0).collect(),
                        })
                    })
                    .collect()
            })
            .collect();
        Constants {
            format,
            tries,
            math_sha: math.tables_sha256,
            azimuth: math
                .azimuth_f64
                .into_iter()
                .map(|v| v.map(f64::from_bits))
                .collect(),
            elevation: math
                .elevation_f64
                .into_iter()
                .map(|v| v.map(f64::from_bits))
                .collect(),
            roots: math.roots_f64.map(f64::from_bits),
        }
    })
}
pub(crate) fn format_sha256() -> &'static str {
    &constants().format.tables_sha256
}
pub(crate) fn math_sha256() -> &'static str {
    &constants().math_sha
}

fn huffman(
    parser: &mut Parser<'_>,
    name: &str,
    mode: usize,
    book: usize,
) -> Result<u8, ParseError> {
    let start = parser.bits.position();
    let value = constants().tries[mode][book].read(&mut parser.bits)? as u8;
    if parser.capture {
        parser.report.fields.push(ConfigField {
            name: name.into(),
            bit_offset: start,
            bit_length: parser.bits.position() - start,
            value: json!(value),
        });
    }
    Ok(value)
}

pub(super) fn read(
    parser: &mut Parser<'_>,
    global_mode: Option<u8>,
    block: u8,
    state: &SalientState,
) -> Result<SalientSpatialData, ParseError> {
    let mut descriptors = Vec::with_capacity(20);
    for component in 0..5 {
        for band in 0..4 {
            let name = format!("hoa.salient[{component}].subbands[{band}]");
            let start = parser.bits.position();
            let mode = match global_mode {
                Some(mode) => mode,
                None => parser.take(&format!("{name}.mode"), 3)? as u8,
            };
            if mode > 5 {
                return Err(ParseError::new(
                    start,
                    "hoa-coding-mode",
                    "spatial descriptor mode must be 0..5",
                ));
            }
            let mut d = SalientDescriptor {
                component_index: component,
                subband_index: band,
                mode,
                start_bit_offset: start,
                end_bit_offset: start,
                quantized: vec![0; 16],
                signs_positive: vec![],
                cluster: None,
                azimuth_degrees: None,
                elevation_offset_degrees: None,
                restored: [0.; 16],
            };
            match mode {
                0 => {
                    for i in 0..16 {
                        d.quantized[i] = parser.take(&format!("{name}.quantized[{i}]"), 6)? as u8;
                    }
                }
                5 => {
                    d.azimuth_degrees =
                        Some(parser.take(&format!("{name}.azimuth_degrees"), 9)? as u16);
                    d.elevation_offset_degrees =
                        Some(parser.take(&format!("{name}.elevation_offset_degrees"), 8)? as u8);
                    // The qualified configuration carries explicit first-order coefficients.
                    d.quantized.truncate(4);
                    for i in 0..4 {
                        d.quantized[i] = huffman(parser, &format!("{name}.quantized[{i}]"), 1, 0)?;
                    }
                }
                _ => {
                    let m = &constants().format.modes[usize::from(mode)];
                    let selected = if mode == 4 {
                        let c = parser.take(&format!("{name}.cluster"), 2)? as u8;
                        d.cluster = Some(c);
                        Some(usize::from(c))
                    } else {
                        None
                    };
                    if m.signs {
                        d.signs_positive = vec![false; 16];
                    }
                    for (book, group) in m.groups.iter().enumerate() {
                        if selected.is_some_and(|c| c != book) {
                            continue;
                        }
                        for &i in group {
                            d.quantized[i] = huffman(
                                parser,
                                &format!("{name}.quantized[{i}]"),
                                usize::from(mode),
                                book,
                            )?;
                            if m.signs {
                                d.signs_positive[i] =
                                    parser.flag(&format!("{name}.sign_positive[{i}]"))?;
                            }
                        }
                    }
                }
            }
            d.end_bit_offset = parser.bits.position();
            descriptors.push(d);
        }
    }
    Ok(SalientSpatialData {
        history_frame_sha256: state.previous_frame_sha256.clone(),
        subband_ends: ENDS,
        lines_per_window: ENDS.map(|n| if block == 2 { n / 8 } else { n }),
        descriptors,
    })
}

/// Normalized real order-3 spherical harmonics, with no Condon-Shortley sign.
/// Native direction descriptors use an N3D unit vector (divide by order+1);
/// the first four entries are then replaced by explicit scalar coefficients.
fn direction(azimuth: u16, elevation: u8) -> [f64; 16] {
    let c = constants();
    let [ca, sa] = c.azimuth[usize::from(azimuth)];
    let [ce, z] = c.elevation[usize::from(elevation)];
    let x = ce * ca;
    // The wire azimuth is clockwise: negative-m terms use -sin(|m|*azimuth).
    let y = -(ce * sa);
    let xx = x * x;
    let yy = y * y;
    let zz = z * z;
    let [r3, r5, r15, r35_8, r105, r21_8, r7] = c.roots;
    let mut out = [
        1.,
        r3 * y,
        r3 * z,
        r3 * x,
        r15 * x * y,
        r15 * y * z,
        (r5 * 0.5) * (3. * zz - 1.),
        r15 * x * z,
        (r15 * 0.5) * (xx - yy),
        r35_8 * y * (3. * xx - yy),
        r105 * x * y * z,
        r21_8 * y * (5. * zz - 1.),
        (r7 * 0.5) * z * (5. * zz - 3.),
        r21_8 * x * (5. * zz - 1.),
        (r105 * 0.5) * z * (xx - yy),
        r35_8 * x * (xx - 3. * yy),
    ];
    for v in &mut out {
        *v *= 0.25;
    }
    out
}

pub(super) fn restore(
    packet: &ChannelPacketReport,
    data: &mut SalientSpatialData,
    state: &mut SalientState,
) -> Result<Vec<HoaCoefficientSpectrum>, ParseError> {
    for d in &mut data.descriptors {
        let mut v = if d.mode == 5 {
            direction(
                d.azimuth_degrees.expect("direction"),
                d.elevation_offset_degrees.expect("direction"),
            )
        } else {
            [0.; 16]
        };
        for (i, &q) in d.quantized.iter().enumerate() {
            let magnitude = f64::from(q) / 32.;
            v[i] = if d.mode == 3 {
                let delta = if d.signs_positive[i] {
                    magnitude
                } else {
                    -magnitude
                };
                state.history[d.component_index][d.subband_index][i] + delta
            } else {
                magnitude - 1.
            };
        }
        if d.mode == 4 {
            let matrix =
                &constants().format.modes[4].matrices_f32[usize::from(d.cluster.expect("cluster"))];
            let input = v;
            for (k, result) in v.iter_mut().enumerate() {
                *result = 0.;
                for j in 0..16 {
                    let product = input[j] * f64::from(f32::from_bits(matrix[j * 16 + k]));
                    *result += product;
                }
            }
        }
        for (k, x) in v.iter_mut().enumerate() {
            if !x.is_finite() {
                return Err(ParseError::new(
                    d.end_bit_offset,
                    "hoa-numeric",
                    format!(
                        "nonfinite descriptor at component {}, subband {}, coefficient {k}",
                        d.component_index, d.subband_index
                    ),
                ));
            }
            if *x == 0. {
                *x = 0.;
            }
        }
        d.restored = v;
        state.history[d.component_index][d.subband_index] = v;
    }
    let mut output: Vec<_> = (0..16)
        .map(|k| HoaCoefficientSpectrum {
            acn_index: k,
            scaled: vec![0.; 1024],
        })
        .collect();
    let block = packet
        .hoa
        .as_ref()
        .expect("HOA report")
        .common_window
        .expect("common window");
    for line in 0..1024 {
        let frequency = if block == 2 { line % 128 } else { line };
        let band = data
            .lines_per_window
            .iter()
            .position(|&end| frequency < end)
            .expect("covered line");
        for (k, out) in output.iter_mut().enumerate() {
            let mut sum = 0.;
            for sc in 0..5 {
                let e = &packet.elements[sc];
                let sample = if e.present {
                    f64::from(e.channels_after_bwe2[0].scaled[line])
                } else {
                    0.
                };
                let product = state.history[sc][band][k] * sample;
                sum += product;
            }
            let value = sum as f32;
            if !value.is_finite() {
                return Err(ParseError::new(
                    data.descriptors.last().expect("descriptors").end_bit_offset,
                    "hoa-numeric",
                    format!("nonfinite restored spectrum at coefficient {k}, line {line}"),
                ));
            }
            out.scaled[line] = if value == 0. { 0. } else { value };
        }
    }
    state.previous_frame_sha256 = Some(packet.frame.packet_sha256.clone());
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::bits::BitReader;

    fn packed(bits: &[bool]) -> Vec<u8> {
        let mut bytes = vec![0; bits.len().div_ceil(8)];
        for (i, &value) in bits.iter().enumerate() {
            bytes[i / 8] |= u8::from(value) << (7 - i % 8);
        }
        bytes
    }

    #[test]
    fn all_spatial_codewords_and_bit_truncations_preserve_the_marker() {
        let c = constants();
        let mut checked = 0;
        for (mode, m) in c.format.modes.iter().enumerate() {
            for (book, entries) in m.codebooks.iter().enumerate() {
                for (value, &(length, code)) in entries.iter().enumerate() {
                    let wire: Vec<_> = (0..length)
                        .rev()
                        .map(|s| ((code >> s) & 1) != 0)
                        .chain([true, false, true, false, true])
                        .collect();
                    let bytes = packed(&wire);
                    let mut reader = BitReader::new(&bytes);
                    assert_eq!(c.tries[mode][book].read(&mut reader).unwrap(), value);
                    assert_eq!(reader.position(), length);
                    assert_eq!(reader.read(5).unwrap(), 0b10101);
                    for end in 0..length {
                        let mut reader = BitReader::new(&bytes);
                        reader.set_end(end).unwrap();
                        let error = c.tries[mode][book].read(&mut reader).unwrap_err();
                        assert_eq!(error.bit_offset, end);
                    }
                    checked += 1;
                }
            }
        }
        assert_eq!(checked, 512);
    }

    #[test]
    fn clockwise_direction_and_format_matrix_dimensions_are_explicit() {
        let c = constants();
        assert_eq!(c.format.modes[4].matrices_f32.len(), 4);
        assert!(
            c.format.modes[4]
                .matrices_f32
                .iter()
                .all(|m| m.len() == 256)
        );
        let v = direction(45, 90);
        assert!(v[4] < 0. && v[8].abs() < 1e-15);
        assert_eq!(direction(0, 0)[4], 0.);
        assert_eq!(direction(0, 90), direction(360, 90));
    }
}
