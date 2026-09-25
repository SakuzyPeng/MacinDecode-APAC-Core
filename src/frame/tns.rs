//! Per-window APAC TNS syntax and a Float64 synthesis lattice, before BWE2.
use super::{CacReport, FrameContext, IcsInfo, Parser, parse_cac, spectrum::tables};
use crate::config::{ConfigField, ParseError, bits::BitReader};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::OnceLock};

pub const NUMERIC_PROFILE: &str = "apac-tns-math-v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TnsFilter {
    pub length: usize,
    pub order: usize,
    pub direction: Option<bool>,
    pub compression: Option<bool>,
    pub coefficient_width: Option<usize>,
    pub quantized: Vec<i8>,
    pub reflection: Vec<f64>,
    /// Unclipped descending SFB cursor, including order-zero filters.
    pub top_band: usize,
    pub bottom_band: usize,
    /// Effective half-open interval in the window-major 1024-line spectrum.
    pub start_line: usize,
    pub end_line: usize,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TnsWindow {
    pub window_index: usize,
    pub resolution: Option<usize>,
    pub filters: Vec<TnsFilter>,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TnsChannel {
    pub channel_index: u8,
    pub present: bool,
    /// Includes this channel's presence bit.
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub windows: Vec<TnsWindow>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TnsChannelSpectrum {
    pub channel_index: u8,
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TnsReport {
    #[serde(flatten)]
    pub cac: CacReport,
    /// Only the TNS stage is complete; component and packet ends remain unknown.
    pub tns_complete: bool,
    pub tns_numeric_profile: String,
    pub tns_tables_sha256: String,
    pub tns_stage: String,
    pub tns: Vec<TnsChannel>,
    pub channels_after_tns: Vec<TnsChannelSpectrum>,
}

#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    reflection_f64: BTreeMap<String, Vec<u64>>,
}
fn math() -> &'static Math {
    static MATH: OnceLock<Math> = OnceLock::new();
    MATH.get_or_init(|| {
        let data: Math = serde_json::from_str(include_str!("../../data/tns-math-v1.json"))
            .expect("built-in TNS mathematical constants");
        assert_eq!(data.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(data.reflection_f64["3"].len(), 8);
        assert_eq!(data.reflection_f64["4"].len(), 16);
        data
    })
}
pub(crate) fn math_sha256() -> &'static str {
    &math().tables_sha256
}
fn reflection(q: i8, resolution: usize) -> f64 {
    let (key, bias) = if resolution == 3 { ("3", 4) } else { ("4", 8) };
    f64::from_bits(math().reflection_f64[key][(q + bias) as usize])
}
fn signed(bits: &mut BitReader<'_>, width: usize) -> Result<i8, ParseError> {
    let raw = bits.read(width)? as i8;
    Ok(if raw & (1 << (width - 1)) == 0 {
        raw
    } else {
        raw - (1 << width)
    })
}

fn read_channel(
    bits: &mut BitReader<'_>,
    ics: &IcsInfo,
    rate: u64,
    channel_index: u8,
) -> Result<TnsChannel, ParseError> {
    let start_bit_offset = bits.position();
    let present = bits.read(1)? != 0;
    let short = ics.block_type == 2;
    let (size, full, limit, maximum, offsets) = if short {
        (128, 14, 14, 7, &tables().short_offsets)
    } else {
        (
            1024,
            49,
            if rate == 48000 { 40 } else { 42 },
            12,
            &tables().long_offsets,
        )
    };
    let active = ics.max_sfb.min(limit);
    let mut windows = Vec::new();
    if present {
        for window_index in 0..if short { 8 } else { 1 } {
            let start = bits.position();
            let count = bits.read(if short { 1 } else { 2 })? as usize;
            let resolution = if count > 0 {
                Some(3 + bits.read(1)? as usize)
            } else {
                None
            };
            let mut filters = Vec::new();
            let mut top = full;
            for _ in 0..count {
                let at = bits.position();
                let length = bits.read(if short { 4 } else { 6 })? as usize;
                if length == 0 {
                    return Err(ParseError::new(
                        at,
                        "tns-length",
                        "TNS filter length must be positive",
                    ));
                }
                let order_at = bits.position();
                let order = bits.read(if short { 3 } else { 5 })? as usize;
                if order > maximum {
                    return Err(ParseError::new(
                        order_at,
                        "tns-order",
                        format!("TNS order {order} exceeds {maximum}"),
                    ));
                }
                let mut record = TnsFilter {
                    length,
                    order,
                    direction: None,
                    compression: None,
                    coefficient_width: None,
                    quantized: Vec::new(),
                    reflection: Vec::new(),
                    top_band: top,
                    bottom_band: top.saturating_sub(length),
                    start_line: window_index * size
                        + offsets[top.saturating_sub(length).min(active)],
                    end_line: window_index * size + offsets[top.min(active)],
                    start_bit_offset: at,
                    end_bit_offset: 0,
                };
                if order > 0 {
                    record.direction = Some(bits.read(1)? != 0);
                    let compressed = bits.read(1)? != 0;
                    record.compression = Some(compressed);
                    let resolution = resolution.expect("nonempty window has resolution");
                    let width = resolution - usize::from(compressed);
                    record.coefficient_width = Some(width);
                    for _ in 0..order {
                        let q = signed(bits, width)?;
                        record.quantized.push(q);
                        record.reflection.push(reflection(q, resolution));
                    }
                }
                record.end_bit_offset = bits.position();
                top = record.bottom_band;
                filters.push(record);
            }
            windows.push(TnsWindow {
                window_index,
                resolution,
                filters,
                start_bit_offset: start,
                end_bit_offset: bits.position(),
            });
        }
    }
    Ok(TnsChannel {
        channel_index,
        present,
        start_bit_offset,
        end_bit_offset: bits.position(),
        windows,
    })
}

fn apply(input: &[f32], channel: &TnsChannel) -> Result<Vec<f32>, ParseError> {
    let mut output = input.to_vec();
    for window in &channel.windows {
        for filter in &window.filters {
            if filter.order == 0 {
                continue;
            }
            // Fresh Float64 backward errors for every filter. The Float32 output
            // is never fed back. Descending updates consume each old state first.
            let mut state = [0f64; 12];
            for position in filter.start_line..filter.end_line {
                let line = if filter.direction == Some(true) {
                    filter.end_line - 1 - (position - filter.start_line)
                } else {
                    position
                };
                let mut forward = f64::from(output[line]);
                for j in (0..filter.order).rev() {
                    // Every product/add/subtract rounds separately: no mul_add,
                    // fast math, or algebraic reassociation is part of this model.
                    let product = filter.reflection[j] * state[j];
                    forward -= product;
                    if j + 1 < filter.order {
                        let product = filter.reflection[j] * forward;
                        state[j + 1] = state[j] + product;
                    }
                }
                state[0] = forward;
                let value = forward as f32;
                if !value.is_finite() || state[..filter.order].iter().any(|v| !v.is_finite()) {
                    return Err(ParseError::new(
                        filter.end_bit_offset,
                        "tns-nonfinite",
                        format!(
                            "nonfinite TNS result at channel {}, window {}, line {}",
                            channel.channel_index, window.window_index, line
                        ),
                    ));
                }
                output[line] = if value == 0. { 0. } else { value };
            }
        }
    }
    Ok(output)
}

pub fn parse_tns(context: &FrameContext, packet: &[u8]) -> Result<TnsReport, ParseError> {
    let mut cac = parse_cac(context, packet)?;
    let mut tns = Vec::new();
    let mut channels_after_tns = Vec::new();
    if cac.cac_complete {
        let position = cac.spectrum.frame.stop_bit_offset;
        let payload = cac.spectrum.frame.payload_bit_offset;
        let mut report = cac.spectrum.frame.clone();
        if report
            .unknown_ranges
            .last()
            .is_some_and(|r| r.bit_offset == position)
        {
            report.unknown_ranges.pop();
        }
        report.diagnostics.pop();
        let mut bits = BitReader::new(packet);
        bits.skip(position)?;
        // Presence is immediately followed by that channel's entire TNS data.
        for channel in &cac.spectrum.channels {
            let parameters = read_channel(
                &mut bits,
                &channel.ics,
                context.sample_rate_hz.expect("supported CAC sample rate"),
                channel.channel_index,
            )?;
            let scaled = apply(
                &cac.channels_after_cac[channel.channel_index as usize].scaled,
                &parameters,
            )?;
            report.fields.push(ConfigField {
                name: format!("components[0].tce[0].tns[{}]", channel.channel_index),
                bit_offset: parameters.start_bit_offset,
                bit_length: parameters.end_bit_offset - parameters.start_bit_offset,
                value: serde_json::to_value(&parameters).expect("finite TNS parameters"),
            });
            channels_after_tns.push(TnsChannelSpectrum {
                channel_index: channel.channel_index,
                scaled,
            });
            tns.push(parameters);
        }
        cac.spectrum.frame =
            Parser { bits, report }.finish("sq_after_tns_before_bwe2", true, false)?;
        cac.spectrum.frame.payload_bit_offset = payload;
    }
    Ok(TnsReport {
        tns_complete: channels_after_tns.len() == 2,
        cac,
        tns_numeric_profile: NUMERIC_PROFILE.into(),
        tns_tables_sha256: math_sha256().into(),
        tns_stage: "scaled_after_tns_before_bwe2".into(),
        tns,
        channels_after_tns,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packed(value: u64, width: usize) -> Vec<u8> {
        (value << (width.div_ceil(8) * 8 - width)).to_be_bytes()[8 - width.div_ceil(8)..].to_vec()
    }
    #[test]
    fn all_36_signed_codes_and_every_truncation_preserve_marker() {
        let mut count = 0;
        for resolution in [3, 4] {
            for compression in [0, 1] {
                let width = resolution - compression;
                for raw in 0..1u64 << width {
                    let bytes = packed(raw << 8 | 0xa5, width + 8);
                    let mut bits = BitReader::new(&bytes);
                    let q = signed(&mut bits, width).unwrap();
                    assert_eq!(
                        q,
                        if raw >> (width - 1) == 0 {
                            raw as i8
                        } else {
                            raw as i8 - (1 << width)
                        }
                    );
                    assert!(reflection(q, resolution).abs() < 1.);
                    assert_eq!(bits.position(), width);
                    assert_eq!(bits.read(8).unwrap(), 0xa5);
                    for cut in 0..width {
                        let mut bits = BitReader::new(&bytes);
                        bits.set_end(cut).unwrap();
                        assert_eq!(signed(&mut bits, width).unwrap_err().bit_offset, 0);
                    }
                    count += 1;
                }
            }
        }
        assert_eq!(count, 36);
    }
    #[test]
    fn nonfinite_filter_output_is_an_explicit_error() {
        let ics = IcsInfo {
            block_type: 0,
            max_sfb: 3,
            window_groups: vec![1],
        };
        let word = (1 << 20) | (1 << 18) | (1 << 17) | (63 << 11) | (1 << 6) | 8;
        let bytes = packed(word, 21);
        let channel = read_channel(&mut BitReader::new(&bytes), &ics, 48000, 1).unwrap();
        let error = apply(&[f32::MAX; 1024], &channel).unwrap_err();
        assert_eq!(error.kind, "tns-nonfinite");
        assert!(error.message.contains("channel 1, window 0, line 1"));
    }
    #[test]
    fn channel_truncation_zero_length_and_excess_order_are_errors() {
        let ics = IcsInfo {
            block_type: 0,
            max_sfb: 0,
            window_groups: vec![1],
        };
        // presence, one filter, r=4, length=63, order=1, direction=0, compression=0, q=-8.
        let width = 1 + 2 + 1 + 6 + 5 + 1 + 1 + 4;
        let word = (1 << 20) | (1 << 18) | (1 << 17) | (63 << 11) | (1 << 6) | 8;
        let bytes = packed(word, width);
        for cut in 0..width {
            let mut bits = BitReader::new(&bytes);
            bits.set_end(cut).unwrap();
            assert!(read_channel(&mut bits, &ics, 48000, 0).is_err(), "{cut}");
        }
        let data = read_channel(&mut BitReader::new(&bytes), &ics, 48000, 0).unwrap();
        assert_eq!(data.end_bit_offset, width);
        assert_eq!(data.windows[0].filters[0].quantized, [-8]);
        assert_eq!(
            data.windows[0].filters[0].start_line,
            data.windows[0].filters[0].end_line
        );
        for (word, kind) in [
            (word & !(63 << 11), "tns-length"),
            ((word & !(31 << 6)) | (13 << 6), "tns-order"),
        ] {
            let bytes = packed(word, width);
            assert_eq!(
                read_channel(&mut BitReader::new(&bytes), &ics, 48000, 0)
                    .unwrap_err()
                    .kind,
                kind
            );
        }
    }
}
