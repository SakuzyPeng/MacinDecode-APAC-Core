//! Restricted order-2/3, five-component spatial descriptors with bounded local grids.
//! Format tables and independent mathematical constants have separate identities.
use super::{
    ChannelPacketReport, Parser,
    hoa::RecoverySlotSpectrum,
    spectrum::{Codebook, Trie},
};
use crate::config::{ConfigField, ParseError};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::OnceLock;

pub const NUMERIC_PROFILE: &str = "apac-hoa-salient-math-v1";
pub const ORDER2_NUMERIC_PROFILE: &str = "apac-hoa-salient-order2-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-salient-state-v1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub(crate) struct SalientState {
    history: Vec<Vec<Vec<f64>>>,
    previous_frame_sha256: Option<String>,
}
impl SalientState {
    #[cfg(test)]
    pub(super) fn new(coefficients: usize) -> Self {
        Self::with_counts(coefficients, [4; 5])
    }
    pub(super) fn with_counts(coefficients: usize, counts: [usize; 5]) -> Self {
        assert!(counts.iter().all(|n| (1..=16).contains(n)));
        assert!(matches!(coefficients, 9 | 16));
        Self {
            history: counts
                .iter()
                .map(|&n| vec![vec![0.; coefficients]; n])
                .collect(),
            previous_frame_sha256: None,
        }
    }
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
    pub restored: Vec<f64>,
    /// For mixed frames, quantized/sign arrays are compact and follow these ACN indices.
    /// Legacy pure-salient arrays retain their historical shape.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coded_coefficient_indices: Option<Vec<usize>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ambient_omitted_coefficients: Option<Vec<usize>>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SalientSpatialData {
    pub history_frame_sha256: Option<String>,
    /// Boundaries in the native frequency-major ordering, before short-window inverse transpose.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub subband_ends: Vec<usize>,
    /// Exclusive frequency-line ends in one window, not band widths.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub lines_per_window: Vec<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub component_subbands: Option<Vec<SalientSubbandInfo>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subband_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub format_sha256: Option<String>,
    pub descriptors: Vec<SalientDescriptor>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SalientSubbandInfo {
    pub component_index: usize,
    pub subband_count: usize,
    pub subband_ends: Vec<usize>,
    pub lines_per_window: Vec<usize>,
}
impl SalientSpatialData {
    pub(super) fn bands_for_frequency(&self, frequency: usize) -> [usize; 5] {
        std::array::from_fn(|sc| {
            let ends = self
                .component_subbands
                .as_ref()
                .map_or(self.lines_per_window.as_slice(), |grids| {
                    &grids[sc].lines_per_window
                });
            ends.iter()
                .position(|&end| frequency < end)
                .expect("covered component frequency")
        })
    }
    pub(super) fn descriptor_offsets(&self) -> [usize; 5] {
        let mut offset = 0;
        std::array::from_fn(|sc| {
            let start = offset;
            offset += self
                .component_subbands
                .as_ref()
                .map_or(self.subband_ends.len(), |grids| grids[sc].subband_count);
            start
        })
    }
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
}
struct CommonMath {
    math_sha: String,
    azimuth: Vec<[f64; 2]>,
    elevation: Vec<[f64; 2]>,
    roots: [f64; 7],
}
fn constants(coefficients: usize) -> &'static Constants {
    static ORDER2: OnceLock<Constants> = OnceLock::new();
    static ORDER3: OnceLock<Constants> = OnceLock::new();
    let (cell, source, profile) = match coefficients {
        9 => (
            &ORDER2,
            include_str!("../../data/hoa-salient-order2-format-v1.json"),
            "apac-hoa-salient-order2-format-v1",
        ),
        16 => (
            &ORDER3,
            include_str!("../../data/hoa-salient-format-v1.json"),
            "apac-hoa-salient-format-v1",
        ),
        _ => unreachable!("qualified coefficient count"),
    };
    cell.get_or_init(|| {
        let format: Format = serde_json::from_str(source).expect("built-in HOA format tables");
        assert_eq!(format.format_profile, profile);
        assert_eq!(format.modes.len(), 6);
        let tries = format
            .modes
            .iter()
            .enumerate()
            .map(|(index, m)| {
                assert_eq!(m.mode, index);
                assert!(m.groups.iter().flatten().all(|&i| i < coefficients));
                assert!(
                    m.matrices_f32
                        .iter()
                        .all(|m| m.len() == coefficients * coefficients)
                );
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
        Constants { format, tries }
    })
}
fn common_math() -> &'static CommonMath {
    static DATA: OnceLock<CommonMath> = OnceLock::new();
    DATA.get_or_init(|| {
        let math: Math = serde_json::from_str(include_str!("../../data/hoa-salient-math-v1.json"))
            .expect("shared HOA angle and root constants");
        assert_eq!(math.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(math.azimuth_f64.len(), 512);
        assert_eq!(math.elevation_f64.len(), 256);
        CommonMath {
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
pub(super) fn numeric_profile(coefficients: usize) -> &'static str {
    if coefficients == 9 {
        ORDER2_NUMERIC_PROFILE
    } else {
        NUMERIC_PROFILE
    }
}
pub(crate) fn format_sha256(coefficients: usize) -> &'static str {
    &constants(coefficients).format.tables_sha256
}
pub(crate) fn math_sha256() -> &'static str {
    &common_math().math_sha
}

fn huffman(
    parser: &mut Parser<'_>,
    name: &str,
    mode: usize,
    book: usize,
    coefficients: usize,
) -> Result<u8, ParseError> {
    let start = parser.bits.position();
    let value = constants(coefficients).tries[mode][book].read(&mut parser.bits)? as u8;
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
    coefficients: usize,
    ambient_selection: Option<&[u8]>,
) -> Result<SalientSpatialData, ParseError> {
    let mixed = ambient_selection.is_some();
    let counts: [usize; 5] = std::array::from_fn(|sc| state.history[sc].len());
    let mut descriptors = Vec::with_capacity(counts.iter().sum());
    for (component, &count) in counts.iter().enumerate() {
        for band in 0..count {
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
                quantized: vec![0; coefficients],
                signs_positive: vec![],
                cluster: None,
                azimuth_degrees: None,
                elevation_offset_degrees: None,
                restored: vec![0.; coefficients],
                coded_coefficient_indices: None,
                ambient_omitted_coefficients: None,
            };
            let omitted = if mode < 4 {
                ambient_selection.unwrap_or(&[])
            } else {
                &[]
            };
            let mut coded = vec![false; coefficients];
            match mode {
                0 => {
                    for (i, is_coded) in coded.iter_mut().enumerate() {
                        if omitted.contains(&(i as u8)) {
                            continue;
                        }
                        d.quantized[i] = parser.take(&format!("{name}.quantized[{i}]"), 6)? as u8;
                        *is_coded = true;
                    }
                }
                5 => {
                    d.azimuth_degrees =
                        Some(parser.take(&format!("{name}.azimuth_degrees"), 9)? as u16);
                    d.elevation_offset_degrees =
                        Some(parser.take(&format!("{name}.elevation_offset_degrees"), 8)? as u8);
                    // The qualified configuration carries explicit first-order coefficients.
                    d.quantized.truncate(4);
                    for (i, is_coded) in coded.iter_mut().enumerate().take(4) {
                        d.quantized[i] = huffman(
                            parser,
                            &format!("{name}.quantized[{i}]"),
                            1,
                            0,
                            coefficients,
                        )?;
                        *is_coded = true;
                    }
                }
                _ => {
                    let m = &constants(coefficients).format.modes[usize::from(mode)];
                    let selected = if mode == 4 {
                        let c = parser.take(&format!("{name}.cluster"), 2)? as u8;
                        d.cluster = Some(c);
                        Some(usize::from(c))
                    } else {
                        None
                    };
                    if m.signs {
                        d.signs_positive = vec![false; coefficients];
                    }
                    for (book, group) in m.groups.iter().enumerate() {
                        if selected.is_some_and(|c| c != book) {
                            continue;
                        }
                        for &i in group {
                            if omitted.contains(&(i as u8)) {
                                continue;
                            }
                            d.quantized[i] = huffman(
                                parser,
                                &format!("{name}.quantized[{i}]"),
                                usize::from(mode),
                                book,
                                coefficients,
                            )?;
                            coded[i] = true;
                            if m.signs {
                                d.signs_positive[i] =
                                    parser.flag(&format!("{name}.sign_positive[{i}]"))?;
                            }
                        }
                    }
                }
            }
            if mixed {
                let indices: Vec<_> = coded
                    .iter()
                    .enumerate()
                    .filter_map(|(i, &v)| v.then_some(i))
                    .collect();
                d.quantized = indices.iter().map(|&i| d.quantized[i]).collect();
                if !d.signs_positive.is_empty() {
                    d.signs_positive = indices.iter().map(|&i| d.signs_positive[i]).collect();
                }
                d.coded_coefficient_indices = Some(indices);
                d.ambient_omitted_coefficients =
                    Some(omitted.iter().map(|&v| usize::from(v)).collect());
            }
            d.end_bit_offset = parser.bits.position();
            descriptors.push(d);
        }
    }
    let extended = counts != [4; 5];
    let common = counts.iter().all(|&n| n == counts[0]);
    let ends = |n, short| super::hoa_salient_subbands::boundaries(n, short).to_vec();
    Ok(SalientSpatialData {
        history_frame_sha256: state.previous_frame_sha256.clone(),
        subband_ends: if common {
            ends(counts[0], false)
        } else {
            vec![]
        },
        lines_per_window: if common {
            ends(counts[0], block == 2)
        } else {
            vec![]
        },
        component_subbands: extended.then(|| {
            counts
                .iter()
                .enumerate()
                .map(|(sc, &n)| SalientSubbandInfo {
                    component_index: sc,
                    subband_count: n,
                    subband_ends: ends(n, false),
                    lines_per_window: ends(n, block == 2),
                })
                .collect()
        }),
        subband_profile: extended.then(|| super::hoa_salient_subbands::SUBBAND_PROFILE.into()),
        format_sha256: extended.then(|| super::hoa_salient_subbands::format_sha256().into()),
        descriptors,
    })
}

/// Normalized real order-2/3 spherical harmonics, with no Condon-Shortley sign.
/// Native direction descriptors use an N3D unit vector (divide by order+1);
/// the first four entries are then replaced by explicit scalar coefficients.
fn direction(azimuth: u16, elevation: u8, coefficients: usize) -> Vec<f64> {
    let c = common_math();
    let [ca, sa] = c.azimuth[usize::from(azimuth)];
    let [ce, z] = c.elevation[usize::from(elevation)];
    let x = ce * ca;
    // The wire azimuth is clockwise: negative-m terms use -sin(|m|*azimuth).
    let y = -(ce * sa);
    let xx = x * x;
    let yy = y * y;
    let zz = z * z;
    let [r3, r5, r15, r35_8, r105, r21_8, r7] = c.roots;
    let out = [
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
    out.into_iter()
        .take(coefficients)
        .map(|v| if coefficients == 9 { v / 3. } else { v * 0.25 })
        .collect()
}

pub(super) fn restore_descriptors(
    packet: &ChannelPacketReport,
    data: &mut SalientSpatialData,
    state: &mut SalientState,
    coefficients: usize,
) -> Result<(), ParseError> {
    for d in &mut data.descriptors {
        let mut v = if d.mode == 5 {
            direction(
                d.azimuth_degrees.expect("direction"),
                d.elevation_offset_degrees.expect("direction"),
                coefficients,
            )
        } else {
            vec![0.; coefficients]
        };
        for (encoded, &q) in d.quantized.iter().enumerate() {
            let i = d
                .coded_coefficient_indices
                .as_ref()
                .map_or(encoded, |indices| indices[encoded]);
            let magnitude = f64::from(q) / 32.;
            v[i] = if d.mode == 3 {
                let delta = if d.signs_positive[encoded] {
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
            let matrix = &constants(coefficients).format.modes[4].matrices_f32
                [usize::from(d.cluster.expect("cluster"))];
            let input = v.clone();
            for (k, result) in v.iter_mut().enumerate() {
                *result = 0.;
                for j in 0..coefficients {
                    let product =
                        input[j] * f64::from(f32::from_bits(matrix[j * coefficients + k]));
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
        d.restored = v.clone();
        state.history[d.component_index][d.subband_index] = v;
    }
    state.previous_frame_sha256 = Some(packet.frame.packet_sha256.clone());
    Ok(())
}

pub(super) fn restore(
    packet: &ChannelPacketReport,
    data: &mut SalientSpatialData,
    state: &mut SalientState,
    coefficients: usize,
    ambient_selection: Option<&[u8]>,
) -> Result<Vec<RecoverySlotSpectrum>, ParseError> {
    restore_descriptors(packet, data, state, coefficients)?;
    let mut output: Vec<_> = (0..coefficients)
        .map(|k| RecoverySlotSpectrum {
            slot_index: k as u8,
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
        let bands = data.bands_for_frequency(frequency);
        for (k, out) in output.iter_mut().enumerate() {
            // flag_d=false: selected ambient coefficients replace the salient result.
            // Their transport samples already passed the SQ/TNS/BWE2 finite checks.
            if let Some(slot) =
                ambient_selection.and_then(|m| m.iter().position(|&index| usize::from(index) == k))
            {
                let element = &packet.elements[slot];
                let value = if element.present {
                    element.channels_after_bwe2[0].scaled[line]
                } else {
                    0.
                };
                out.scaled[line] = if value == 0. { 0. } else { value };
                continue;
            }
            let mut sum = 0.;
            for (sc, &band) in bands.iter().enumerate() {
                let e = &packet.elements[sc + if ambient_selection.is_some() { 4 } else { 0 }];
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
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::bits::BitReader;
    #[test]
    fn restored_numeric_error_precedes_corrupt_dynamic_mapping_and_rolls_back() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../data/hoa-dynamic-state-v1.json")).unwrap();
        let bytes = |v: &serde_json::Value| -> Vec<u8> {
            v.as_str()
                .unwrap()
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        };
        for f in data["fixtures"].as_array().unwrap() {
            let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
            let mut drc = context.initial_drc_state();
            let mut state = super::super::HoaState {
                salient: Some(Box::new(SalientState::new(9))),
                ..Default::default()
            };
            state.salient.as_mut().unwrap().history[0][0][7] = f64::MAX;
            let before = state.clone();
            let error = crate::frame::parse_hoa_packet_with_state(
                &context,
                &bytes(&f["dynamic_error"]),
                &mut drc,
                &mut state,
            )
            .unwrap_err();
            assert_eq!(error.kind, "hoa-numeric");
            assert!(error.bit_offset <= f["internal_end_bit"].as_u64().unwrap() as usize);
            assert_eq!(state, before);
        }
    }

    #[test]
    fn additive_numeric_failure_precedes_mapping_and_tail_and_rolls_back() {
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../../data/hoa-additive-state-v1.json")).unwrap();
        let f = &fixtures["fixtures"][2];
        let bytes = |v: &serde_json::Value| -> Vec<u8> {
            v.as_str()
                .unwrap()
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        };
        let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        for key in ["dynamic_error", "late_tail_error"] {
            let mut drc = context.initial_drc_state();
            let mut state = super::super::HoaState {
                salient: Some(Box::new(SalientState::new(9))),
                ..Default::default()
            };
            state.salient.as_mut().unwrap().history[0][0][8] = f64::MAX;
            let before = state.clone();
            let before_drc =
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap();
            let error = crate::frame::parse_hoa_packet_with_state(
                &context,
                &bytes(&f["errors"][key]),
                &mut drc,
                &mut state,
            )
            .unwrap_err();
            assert_eq!(error.kind, "hoa-additive-numeric");
            assert_eq!(
                error.bit_offset,
                f["internal_end_bit"].as_u64().unwrap() as usize
            );
            assert_eq!(state, before);
            assert_eq!(
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap(),
                before_drc
            );
        }
    }

    #[test]
    fn effective_subband_numeric_failure_precedes_inactive_rows_and_tail() {
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../../data/hoa-subbands-state-v1.json")).unwrap();
        for f in fixtures["fixtures"].as_array().unwrap() {
            let bytes = |v: &serde_json::Value| -> Vec<u8> {
                v.as_str()
                    .unwrap()
                    .as_bytes()
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                    .collect()
            };
            let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
            for key in ["inactive_duplicate", "late_tail_error"] {
                let mut drc = context.initial_drc_state();
                let mut state = super::super::HoaState {
                    salient: Some(Box::new(SalientState::new(9))),
                    ..Default::default()
                };
                state.salient.as_mut().unwrap().history[0][0][7] = f64::MAX;
                let before = state.clone();
                let before_drc =
                    serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                        .unwrap();
                let error = crate::frame::parse_hoa_packet_with_state(
                    &context,
                    &bytes(&f["errors"][key]),
                    &mut drc,
                    &mut state,
                )
                .unwrap_err();
                assert_eq!(
                    error.kind,
                    if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                        "hoa-additive-numeric"
                    } else {
                        "hoa-numeric"
                    }
                );
                assert_eq!(
                    error.bit_offset,
                    f["internal_end_bit"].as_u64().unwrap() as usize
                );
                assert_eq!(state, before);
                assert_eq!(
                    serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                        .unwrap(),
                    before_drc
                );
            }
        }
    }

    #[test]
    fn spatial_grid_numeric_failure_precedes_mapping_and_tail() {
        let fixtures: serde_json::Value = serde_json::from_str(include_str!(
            "../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        for f in fixtures["fixtures"].as_array().unwrap() {
            let bytes = |v: &serde_json::Value| -> Vec<u8> {
                v.as_str()
                    .unwrap()
                    .as_bytes()
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                    .collect()
            };
            let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
            for key in ["dynamic_error", "tail_error"] {
                if f["errors"][key].is_null() {
                    continue;
                }
                let mut drc = context.initial_drc_state();
                let mut state = super::super::HoaState {
                    salient: Some(Box::new(SalientState::with_counts(
                        context.recovery_slot_count(),
                        context.salient_subband_counts().unwrap(),
                    ))),
                    ..Default::default()
                };
                state.salient.as_mut().unwrap().history[0][0][context.recovery_slot_count() - 2] =
                    f64::MAX;
                let before = state.clone();
                let before_drc =
                    serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                        .unwrap();
                let error = crate::frame::parse_hoa_packet_with_state(
                    &context,
                    &bytes(&f["errors"][key]),
                    &mut drc,
                    &mut state,
                )
                .unwrap_err();
                assert_eq!(
                    error.kind,
                    if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                        "hoa-additive-numeric"
                    } else {
                        "hoa-numeric"
                    }
                );
                assert_eq!(
                    error.bit_offset,
                    f["internal_end_bit"].as_u64().unwrap() as usize
                );
                assert_eq!(state, before);
                assert_eq!(
                    serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                        .unwrap(),
                    before_drc
                );
            }
        }
    }

    fn packed(bits: &[bool]) -> Vec<u8> {
        let mut bytes = vec![0; bits.len().div_ceil(8)];
        for (i, &value) in bits.iter().enumerate() {
            bytes[i / 8] |= u8::from(value) << (7 - i % 8);
        }
        bytes
    }

    #[test]
    fn all_spatial_codewords_and_bit_truncations_preserve_the_marker() {
        for coefficients in [9, 16] {
            let c = constants(coefficients);
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
    }

    #[test]
    fn clockwise_direction_and_format_matrix_dimensions_are_explicit() {
        for coefficients in [9, 16] {
            let c = constants(coefficients);
            assert_eq!(c.format.modes[4].matrices_f32.len(), 4);
            assert!(
                c.format.modes[4]
                    .matrices_f32
                    .iter()
                    .all(|m| m.len() == coefficients * coefficients)
            );
            let v = direction(45, 90, coefficients);
            assert!(v[4] < 0. && v[8].abs() < 1e-15);
            assert_eq!(direction(0, 0, coefficients)[4], 0.);
            assert_eq!(
                direction(0, 90, coefficients),
                direction(360, 90, coefficients)
            );
            assert_eq!(v.len(), coefficients);
            assert_eq!(
                direction(0, 90, coefficients)[0],
                if coefficients == 9 { 1. / 3. } else { 0.25 }
            );
        }
    }

    #[test]
    fn mixed_spatial_modes_preserve_markers_and_reject_every_bit_truncation() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../data/hoa-mixed-state-v1.json")).unwrap();
        let bytes = |v: &serde_json::Value| -> Vec<u8> {
            v.as_str()
                .unwrap()
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        };
        for row in data["spatial_cases"].as_array().unwrap() {
            let f = data["fixtures"]
                .as_array()
                .unwrap()
                .iter()
                .find(|f| f["order"] == row["order"])
                .unwrap();
            let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
            let template = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"]))
                .unwrap()
                .packet
                .frame;
            let raw = bytes(&row["bytes"]);
            let end = row["bits"].as_u64().unwrap() as usize;
            let configuration = context.configuration;
            for limit in 0..=end {
                let mut parser = Parser {
                    capture: true,
                    bits: BitReader::new(&raw),
                    report: template.clone(),
                };
                parser.bits.set_end(limit).unwrap();
                let result = super::super::hoa::spatial(
                    &mut parser,
                    &mut super::super::hoa::HoaState::default(),
                    configuration,
                    0,
                );
                if limit < end {
                    let error = result.unwrap_err();
                    assert_eq!(error.kind, "truncated");
                    assert!(error.bit_offset <= limit);
                } else {
                    let actual = result.unwrap();
                    let spatial = actual.salient.unwrap();
                    assert_eq!(actual.end_bit_offset, end);
                    for d in &spatial.descriptors {
                        let expected = &row["truth"]["salient"]["descriptors"]
                            [d.component_index * 4 + d.subband_index];
                        let actual = serde_json::to_value(d).unwrap();
                        for (key, value) in expected.as_object().unwrap() {
                            assert_eq!(&actual[key], value, "{key}");
                        }
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
    #[test]
    fn per_component_grids_cover_every_line_without_native_history_padding() {
        let data: serde_json::Value = serde_json::from_str(include_str!(
            "../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        let bytes = |v: &serde_json::Value| -> Vec<u8> {
            v.as_str()
                .unwrap()
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        };
        for row in data["restoration_cases"].as_array().unwrap() {
            let ctx = crate::frame::HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
            let mut packet = crate::frame::parse_hoa_packet(&ctx, &bytes(&row["packet"]))
                .unwrap()
                .packet;
            for (sc, e) in packet.elements.iter_mut().take(5).enumerate() {
                e.channels_after_bwe2[0].scaled.fill(32.0 * (sc + 1) as f32);
            }
            let mut side = packet
                .hoa
                .as_ref()
                .unwrap()
                .spatial
                .as_ref()
                .unwrap()
                .salient
                .clone()
                .unwrap();
            let mut state = SalientState::with_counts(
                ctx.recovery_slot_count(),
                ctx.salient_subband_counts().unwrap(),
            );
            let output = restore(
                &packet,
                &mut side,
                &mut state,
                ctx.recovery_slot_count(),
                None,
            )
            .unwrap();
            let short = row["block"] == 2;
            for line in 0..1024 {
                let frequency = if short { line % 128 } else { line };
                let mut expected = 0usize;
                for (sc, grid) in row["grids"].as_array().unwrap().iter().enumerate() {
                    let b = grid
                        .as_array()
                        .unwrap()
                        .iter()
                        .position(|v| {
                            frequency < (v.as_u64().unwrap() as usize) / if short { 8 } else { 1 }
                        })
                        .unwrap();
                    expected += (sc + b) * (sc + 1);
                }
                assert_eq!(
                    output[0].scaled[line].to_bits(),
                    (expected as f32).to_bits()
                );
                for coefficient in output.iter().skip(1) {
                    assert_eq!(coefficient.scaled[line].to_bits(), 0);
                }
            }
            assert_eq!(
                state.history.iter().map(Vec::len).collect::<Vec<_>>(),
                ctx.salient_subband_counts().unwrap()
            );
        }
    }

    #[test]
    fn variable_descriptor_lists_reject_every_bit_truncation_and_preserve_marker() {
        let data: serde_json::Value = serde_json::from_str(include_str!(
            "../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        let bytes = |v: &serde_json::Value| -> Vec<u8> {
            v.as_str()
                .unwrap()
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect()
        };
        let f = &data["fixtures"][0];
        let ctx = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let template = crate::frame::parse_hoa_packet(&ctx, &bytes(&f["first"]))
            .unwrap()
            .packet
            .frame;
        for row in data["spatial_cases"].as_array().unwrap() {
            let ctx = crate::frame::HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
            let raw = bytes(&row["bytes"]);
            let end = row["bits"].as_u64().unwrap() as usize;
            for cut in 0..=end {
                let mut parser = super::super::Parser {
                    capture: true,
                    bits: BitReader::new(&raw),
                    report: template.clone(),
                };
                parser.bits.set_end(cut).unwrap();
                let result = super::super::hoa::spatial(
                    &mut parser,
                    &mut super::super::HoaState::default(),
                    ctx.configuration,
                    0,
                );
                if cut < end {
                    let e = result.unwrap_err();
                    assert_eq!(e.kind, "truncated");
                    assert!(e.bit_offset <= cut);
                } else {
                    let result = result.unwrap();
                    assert_eq!(result.end_bit_offset, end);
                    let actual = serde_json::to_value(result.salient.unwrap()).unwrap();
                    for (a, b) in actual["descriptors"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .zip(row["truth"]["salient"]["descriptors"].as_array().unwrap())
                    {
                        for (key, value) in b.as_object().unwrap() {
                            assert_eq!(&a[key], value, "{key}");
                        }
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
}
