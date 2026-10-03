//! Order-1/2/3 spatial descriptors with bounded component counts and local grids.
//! Format tables and independent mathematical constants have separate identities.
use super::{ChannelPacketReport, Parser, hoa::RecoverySlotSpectrum};
use crate::config::{ConfigField, ParseError};
use crate::prelude::*;
use crate::record::FieldValue;
use serde::Serialize;

#[cfg(test)]
#[path = "hoa_salient_format.rs"]
mod format;

pub const EXPANDED_PROFILE: &str = "apac-hoa-expanded-orders-v1";
pub const EXPANDED_NUMERIC_PROFILE: &str = "apac-hoa-expanded-orders-math-v1";
pub const EXPANDED_STATE_PROFILE: &str = "apac-hoa-expanded-orders-state-v1";
pub const QUANTIZATION_PROFILE: &str = "apac-hoa-salient-quantization-v1";
pub const QUANTIZATION_NUMERIC_PROFILE: &str = "apac-hoa-salient-quantization-math-v1";
pub const QUANTIZATION_STATE_PROFILE: &str = "apac-hoa-salient-quantization-state-v1";
pub const COUNTS_PROFILE: &str = "apac-hoa-salient-counts-v1";
pub const COUNTS_NUMERIC_PROFILE: &str = "apac-hoa-salient-counts-math-v1";
pub const COUNTS_STATE_PROFILE: &str = "apac-hoa-salient-counts-state-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-salient-math-v1";
pub const ORDER1_NUMERIC_PROFILE: &str = "apac-hoa-salient-order1-math-v1";
pub const ORDER1_PROFILE: &str = "apac-hoa-salient-order1-v1";
pub const ORDER2_NUMERIC_PROFILE: &str = "apac-hoa-salient-order2-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-salient-state-v1";
pub const COMPONENT_ORDERS_NUMERIC_PROFILE: &str = "apac-hoa-component-orders-math-v1";
pub const COMPONENT_ORDERS_STATE_PROFILE: &str = "apac-hoa-component-orders-state-v1";

#[cfg(test)]
#[path = "hoa_component_orders_tests.rs"]
mod component_orders_tests;

#[cfg(test)]
#[path = "hoa_order1_tests.rs"]
mod order1_tests;

#[cfg(test)]
#[path = "hoa_counts_tests.rs"]
mod counts_tests;

#[cfg(test)]
#[path = "hoa_quantization_tests.rs"]
mod quantization_tests;

#[cfg(test)]
#[path = "hoa_expanded_orders_tests.rs"]
mod expanded_orders_tests;
#[cfg(test)]
#[path = "hoa_partial_tests.rs"]
mod partial_tests;

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct SalientState {
    pub(super) history: Vec<Vec<Vec<f64>>>,
    previous_frame_sha256: Option<String>,
}
impl SalientState {
    pub(super) fn finish_active_components(
        &mut self,
        active: &[super::SalientComponentConfiguration],
        packet_sha256: &str,
    ) {
        for (sc, component) in self.history.iter_mut().enumerate() {
            for band in component
                .iter_mut()
                .skip(active.get(sc).map_or(0, |c| c.subband_count))
            {
                band.fill(0.);
            }
        }
        self.previous_frame_sha256 = Some(packet_sha256.into());
    }
    #[cfg(test)]
    pub(super) fn new(coefficients: usize) -> Self {
        Self::with_counts(coefficients, [4; 5])
    }
    #[cfg(test)]
    pub(super) fn with_counts(coefficients: usize, counts: impl AsRef<[usize]>) -> Self {
        Self::with_dimensions(vec![coefficients; counts.as_ref().len()], counts)
    }
    pub(super) fn with_dimensions(
        coefficients: impl AsRef<[usize]>,
        counts: impl AsRef<[usize]>,
    ) -> Self {
        let coefficients = coefficients.as_ref();
        let counts = counts.as_ref();
        assert!(!counts.is_empty() && counts.len() <= 121 && counts.len() == coefficients.len());
        assert!(counts.iter().all(|n| (1..=16).contains(n)));
        assert!(coefficients.iter().all(|&n| (2..=121).contains(&n)));
        Self {
            history: counts
                .iter()
                .zip(coefficients)
                .map(|(&n, &c)| vec![vec![0.; c]; n])
                .collect(),
            previous_frame_sha256: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct SalientDescriptor {
    pub component_index: usize,
    pub subband_index: usize,
    pub mode: u8,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub quantized: Vec<u16>,
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

#[derive(Clone, Debug, Serialize)]
pub struct SalientSpatialData {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unrounded_subbands: Option<bool>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub partition_method: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub partition_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub component_orders: Option<Vec<SalientComponentOrderInfo>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub order1_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub component_count: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub count_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quantization_bits: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quantization_profile: Option<String>,
    pub descriptors: Vec<SalientDescriptor>,
}

#[derive(Clone, Debug, Serialize)]
pub struct SalientSubbandInfo {
    pub component_index: usize,
    pub subband_count: usize,
    pub subband_ends: Vec<usize>,
    pub lines_per_window: Vec<usize>,
}
#[derive(Clone, Debug, Serialize)]
pub struct SalientComponentOrderInfo {
    pub component_index: usize,
    pub order: u8,
    pub coefficient_count: usize,
    pub numeric_profile: String,
    pub format_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quantization_bits: Option<u8>,
}
pub(super) fn component_information(
    orders: impl AsRef<[u8]>,
    precision: u8,
) -> Vec<SalientComponentOrderInfo> {
    orders
        .as_ref()
        .iter()
        .copied()
        .enumerate()
        .map(|(component_index, order)| {
            let coefficient_count = (usize::from(order) + 1).pow(2);
            SalientComponentOrderInfo {
                component_index,
                order,
                coefficient_count,
                numeric_profile: if order > 3 {
                    EXPANDED_NUMERIC_PROFILE
                } else if precision == 6 {
                    numeric_profile(coefficient_count)
                } else {
                    QUANTIZATION_NUMERIC_PROFILE
                }
                .into(),
                format_sha256: constants_for_bits(coefficient_count, precision)
                    .format
                    .tables_sha256
                    .into(),
                quantization_bits: (precision != 6).then_some(precision),
            }
        })
        .collect()
}
impl SalientSpatialData {
    pub(super) fn bands_for_line(&self, line: usize, short: bool) -> Vec<usize> {
        let position = if self.unrounded_subbands == Some(true) && short {
            (line % 128) * 8 + line / 128
        } else if short {
            line % 128
        } else {
            line
        };
        self.bands_for_frequency(position)
    }
    pub(super) fn bands_for_frequency(&self, frequency: usize) -> Vec<usize> {
        (0..self.component_count.unwrap_or(5))
            .map(|sc| {
                let ends = self.component_subbands.as_ref().map_or(
                    if self.unrounded_subbands == Some(true) {
                        self.subband_ends.as_slice()
                    } else {
                        self.lines_per_window.as_slice()
                    },
                    |grids| {
                        if self.unrounded_subbands == Some(true) {
                            &grids[sc].subband_ends
                        } else {
                            &grids[sc].lines_per_window
                        }
                    },
                );
                ends.iter()
                    .position(|&end| frequency < end)
                    .expect("covered component frequency")
            })
            .collect()
    }
    pub(super) fn descriptor_offsets(&self) -> Vec<usize> {
        let mut offset = 0;
        (0..self.component_count.unwrap_or(5))
            .map(|sc| {
                let start = offset;
                offset += self
                    .component_subbands
                    .as_ref()
                    .map_or(self.subband_ends.len(), |grids| grids[sc].subband_count);
                start
            })
            .collect()
    }
}

use crate::tables::SalientConstants as Constants;
fn constants(coefficients: usize) -> &'static Constants {
    constants_for_bits(coefficients, 6)
}
/// Generated from the `data/hoa-salient-*format-v1.json` dictionaries and the
/// per-order `*-shared-v1.json` tables by the build script.
fn constants_for_bits(coefficients: usize, precision: u8) -> &'static Constants {
    // Select the containing order's dictionary; callers filter groups to the
    // actual dimension. The shared dictionary data is not duplicated.
    let coefficients = ((coefficients - 1).isqrt() + 1).pow(2);
    let order = coefficients.isqrt() - 1;
    if !(1..=10).contains(&order) || !(6..=9).contains(&precision) {
        unreachable!("qualified HOA dictionary key");
    }
    let constants = &crate::tables::SALIENT_CONSTANTS[(order - 1) * 4 + usize::from(precision - 6)];
    debug_assert_eq!(
        (constants.coefficients, constants.precision),
        (coefficients, precision)
    );
    constants
}
/// Generated from `data/hoa-salient-math-v1.json` by the build script.
fn common_math() -> &'static crate::tables::SalientMath {
    &crate::tables::SALIENT_MATH
}
pub(super) fn numeric_profile(coefficients: usize) -> &'static str {
    match coefficients {
        4 => ORDER1_NUMERIC_PROFILE,
        9 => ORDER2_NUMERIC_PROFILE,
        16 => NUMERIC_PROFILE,
        n if (25..=121).contains(&n) && n.isqrt().pow(2) == n => EXPANDED_NUMERIC_PROFILE,
        _ => unreachable!("qualified descriptor dimension"),
    }
}
pub fn format_sha256(coefficients: usize) -> &'static str {
    constants(coefficients).format.tables_sha256
}
pub fn math_sha256() -> &'static str {
    common_math().math_sha
}

fn huffman_for_bits(
    parser: &mut Parser<'_>,
    name: impl core::fmt::Display,
    mode: usize,
    book: usize,
    coefficients: usize,
    precision: u8,
) -> Result<u16, ParseError> {
    let start = parser.bits.position();
    let value = constants_for_bits(coefficients, precision).tries[mode][book]
        .read(&mut parser.bits)? as u16;
    if parser.capture {
        parser.report.fields.push(ConfigField {
            name: name.to_string(),
            bit_offset: start,
            bit_length: parser.bits.position() - start,
            value: FieldValue::from(value),
        });
    }
    Ok(value)
}

pub(super) fn read(
    parser: &mut Parser<'_>,
    global_mode: Option<u8>,
    block: u8,
    state: &SalientState,
    configuration: &super::hoa::HoaConfiguration,
) -> Result<SalientSpatialData, ParseError> {
    let ambient_selection = (configuration.path == super::hoa::HoaPath::Mixed).then(|| {
        if configuration.ambient_combination == super::AmbientCombination::Add {
            &[][..]
        } else {
            configuration.ambient_indices()
        }
    });
    let precision = configuration.quantization_bits;
    let partition_method = configuration.salient_partition_method;
    let mixed = ambient_selection.is_some();
    let counts: Vec<usize> = configuration
        .salient_configurations
        .iter()
        .map(|c| c.subband_count)
        .collect();
    let dimensions = configuration.salient_dimensions();
    let mut descriptors = Vec::with_capacity(counts.iter().sum());
    for (component, &count) in counts.iter().enumerate() {
        let coefficients = dimensions[component];
        for band in 0..count {
            let name = format_args!("hoa.salient[{component}].subbands[{band}]");
            let start = parser.bits.position();
            let mode = match global_mode {
                Some(mode) => mode,
                None => parser.take(format_args!("{name}.mode"), 3)? as u8,
            };
            if mode > 5 {
                return Err(ParseError::new(
                    start,
                    "hoa-coding-mode",
                    "spatial descriptor mode must be 0..5",
                ));
            }
            if !configuration.full_order && mode >= 4 {
                return Err(ParseError::new(
                    start,
                    "hoa-coding-mode",
                    "matrix and directional descriptions require full_order=true in the bound format",
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
                        d.quantized[i] = parser.take(
                            format_args!("{name}.quantized[{i}]"),
                            usize::from(precision),
                        )? as u16;
                        *is_coded = true;
                    }
                }
                5 => {
                    d.azimuth_degrees =
                        Some(parser.take(format_args!("{name}.azimuth_degrees"), 9)? as u16);
                    d.elevation_offset_degrees = Some(
                        parser.take(format_args!("{name}.elevation_offset_degrees"), 8)? as u8,
                    );
                    // The qualified configuration carries explicit first-order coefficients.
                    let explicit = if configuration.controls.flag_e { 4 } else { 0 };
                    d.quantized.truncate(explicit);
                    for (i, is_coded) in coded.iter_mut().enumerate().take(explicit) {
                        d.quantized[i] = huffman_for_bits(
                            parser,
                            format_args!("{name}.quantized[{i}]"),
                            1,
                            0,
                            coefficients,
                            precision,
                        )?;
                        *is_coded = true;
                    }
                }
                _ => {
                    let m = &constants_for_bits(coefficients, precision).format.modes
                        [usize::from(mode)];
                    let selected = if mode == 4 {
                        let c = parser.take(format_args!("{name}.cluster"), 2)? as u8;
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
                        for &i in *group {
                            if i >= coefficients || omitted.contains(&(i as u8)) {
                                continue;
                            }
                            d.quantized[i] = huffman_for_bits(
                                parser,
                                format_args!("{name}.quantized[{i}]"),
                                usize::from(mode),
                                book,
                                coefficients,
                                precision,
                            )?;
                            coded[i] = true;
                            if m.signs {
                                d.signs_positive[i] =
                                    parser.flag(format_args!("{name}.sign_positive[{i}]"))?;
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
                d.ambient_omitted_coefficients = Some(
                    omitted
                        .iter()
                        .map(|&v| usize::from(v))
                        .filter(|&v| v < coefficients)
                        .collect(),
                );
            }
            d.end_bit_offset = parser.bits.position();
            descriptors.push(d);
        }
    }
    let extended = counts != [4; 5];
    let common = counts.iter().all(|&n| n == counts[0]);
    let ends = |n, short| {
        if configuration.controls.flag_f {
            super::hoa_salient_subbands::boundaries(n, partition_method, short).to_vec()
        } else if short {
            vec![]
        } else {
            super::hoa_controls::boundaries(n, usize::from(partition_method)).to_vec()
        }
    };
    Ok(SalientSpatialData {
        unrounded_subbands: (!configuration.controls.flag_f).then_some(true),
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
        format_sha256: if !configuration.controls.flag_f {
            Some(super::hoa_controls::format_sha256().into())
        } else {
            (extended || partition_method != 0)
                .then(|| super::hoa_salient_subbands::format_sha256(partition_method).into())
        },
        partition_method: (partition_method != 0).then_some(partition_method),
        partition_profile: (partition_method != 0)
            .then(|| super::hoa_salient_subbands::PARTITION_PROFILE.into()),
        component_orders: configuration.component_order_info(),
        order1_profile: configuration
            .salient_configurations
            .iter()
            .any(|c| c.order == 1)
            .then(|| ORDER1_PROFILE.into()),
        component_count: configuration
            .component_count_extended()
            .then_some(counts.len()),
        count_profile: configuration
            .component_count_extended()
            .then(|| COUNTS_PROFILE.into()),
        quantization_bits: (precision != 6).then_some(precision),
        quantization_profile: (precision != 6).then(|| QUANTIZATION_PROFILE.into()),
        descriptors,
    })
}

/// Generated from `data/hoa-expanded-orders-math-v1.json` by the build script.
pub fn expanded_math_sha256() -> &'static str {
    crate::tables::SALIENT_EXPANDED_MATH_SHA256
}
fn expanded_direction(azimuth: u16, elevation: u8, coefficients: usize) -> Vec<f64> {
    let order = coefficients.isqrt() - 1;
    let common = common_math();
    let [radial, z] = common.elevation[usize::from(elevation)];
    let weights = crate::tables::SALIENT_EXPANDED_NORMALIZATIONS[order];
    let mut output = vec![0.; coefficients];
    let mut diagonal = 1.;
    for m in 0..=order {
        if m != 0 {
            diagonal *= (2 * m - 1) as f64 * radial;
        }
        let [cosine, sine] = common.azimuth[(m * usize::from(azimuth)) % 360];
        let mut previous = 0.;
        let mut current = diagonal;
        for degree in m..=order {
            if degree > m {
                let next = ((2 * degree - 1) as f64 * z * current
                    - (degree + m - 1) as f64 * previous)
                    / (degree - m) as f64;
                previous = current;
                current = next;
            }
            let center = degree * (degree + 1);
            let amplitude = current * f64::from_bits(weights[center + m]);
            output[center + m] = amplitude * cosine;
            if m != 0 {
                output[center - m] = -(amplitude * sine);
            }
        }
    }
    output
}

/// Normalized real order-1/2/3 spherical harmonics, with no Condon-Shortley sign.
/// Native direction descriptors use an N3D unit vector (divide by order+1);
/// the first four entries are then replaced by explicit scalar coefficients.
fn direction(azimuth: u16, elevation: u8, coefficients: usize) -> Vec<f64> {
    if coefficients > 16 {
        return expanded_direction(azimuth, elevation, coefficients);
    }
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
        .map(|v| match coefficients {
            4 => v * 0.5,
            9 => v / 3.,
            16 => v * 0.25,
            _ => unreachable!("qualified descriptor dimension"),
        })
        .collect()
}

pub(super) fn restore_descriptors(
    packet: &ChannelPacketReport,
    data: &mut SalientSpatialData,
    state: &mut SalientState,
) -> Result<(), ParseError> {
    let precision = data.quantization_bits.unwrap_or(6);
    for d in &mut data.descriptors {
        let coefficients = d.restored.len();
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
            let magnitude = f64::from(q) / f64::from(1u16 << (precision - 1));
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
            let matrix = &constants_for_bits(coefficients, precision).format.modes[4].matrices_f32
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
        let history = &mut state.history[d.component_index][d.subband_index];
        history.fill(0.);
        history[..coefficients].copy_from_slice(&v);
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
    add_mean: bool,
) -> Result<Vec<RecoverySlotSpectrum>, ParseError> {
    restore_descriptors(packet, data, state)?;
    let sources = super::hoa_transport::spectra(packet)?;
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
    // All validated carriers have 1024 lines; retaining this frequency-major
    // traversal preserves each component's interval and the numerical order.
    for (line, _) in sources[0].iter().enumerate() {
        let bands = data.bands_for_line(line, block == 2);
        for (k, out) in output.iter_mut().enumerate() {
            // flag_d=false: selected ambient coefficients replace the salient result.
            // Their transport samples already passed the SQ/TNS/BWE2 finite checks.
            if let Some(slot) =
                ambient_selection.and_then(|m| m.iter().position(|&index| usize::from(index) == k))
            {
                let value = sources[slot][line];
                out.scaled[line] = if value == 0. { 0. } else { value };
                continue;
            }
            let mut sum = if add_mean {
                super::hoa_controls::mean(k)
            } else {
                0.
            };
            let mut compensated = super::hoa_additive::Sum::default();
            if add_mean {
                compensated.add(sum);
            }
            for (sc, &band) in bands.iter().enumerate() {
                let sample =
                    f64::from(sources[sc + ambient_selection.map_or(0, <[u8]>::len)][line]);
                // Qualified lower-order descriptors have no higher ACN entries.
                // This is structural zero extension, not recovery from a missing payload.
                let descriptor = state.history[sc][band].get(k).copied().unwrap_or(0.);
                let product = descriptor * sample;
                if coefficients > 16 {
                    compensated.add(product);
                } else {
                    sum += product;
                }
            }
            let value = if coefficients > 16 {
                compensated.result()
            } else {
                sum
            } as f32;
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
            serde_json::from_str(include_str!("../../../../data/hoa-dynamic-state-v1.json"))
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
            serde_json::from_str(include_str!("../../../../data/hoa-additive-state-v1.json"))
                .unwrap();
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
            serde_json::from_str(include_str!("../../../../data/hoa-subbands-state-v1.json"))
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
            "../../../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        let partition: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-partition-state-v1.json"))
                .unwrap();
        for f in fixtures["fixtures"]
            .as_array()
            .unwrap()
            .iter()
            .chain(partition["fixtures"].as_array().unwrap())
        {
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
        for coefficients in [4, 9, 16] {
            let c = constants(coefficients);
            let codebooks = format::stored_codebooks(coefficients.isqrt() - 1, 6);
            let mut checked = 0;
            for (mode, books) in codebooks.iter().enumerate() {
                for (book, entries) in books.iter().enumerate() {
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
            serde_json::from_str(include_str!("../../../../data/hoa-mixed-state-v1.json")).unwrap();
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
                    &configuration,
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
            "../../../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        let partition: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-partition-state-v1.json"))
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
        for row in data["restoration_cases"]
            .as_array()
            .unwrap()
            .iter()
            .chain(partition["restoration_cases"].as_array().unwrap())
        {
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
                false,
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
            "../../../../data/hoa-spatial-subbands-state-v1.json"
        ))
        .unwrap();
        let partition: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-partition-state-v1.json"))
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
        for row in data["spatial_cases"]
            .as_array()
            .unwrap()
            .iter()
            .chain(partition["spatial_cases"].as_array().unwrap())
        {
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
                    &ctx.configuration,
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
