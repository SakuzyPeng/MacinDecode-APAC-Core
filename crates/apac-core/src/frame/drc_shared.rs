//! Shared UniDRC gain syntax. No instruction selection, interpolation, or gain application.
use super::{
    Parser,
    drc::{DrcNode, DrcParameters, DrcTimeDelta},
};
use crate::config::{CookieReport, ParseError};
use crate::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::OnceLock;

pub const PROFILE: &str = "apac-hoa-shared-drc-syntax-v1";
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DrcSequenceParameters {
    pub sequence_index: usize,
    pub gain_set_index: usize,
    pub parameters: DrcParameters,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DrcGainSequence {
    pub sequence_index: usize,
    pub gain_set_index: usize,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub coding_mode: u8,
    pub frame_end: bool,
    pub time_deltas: Vec<DrcTimeDelta>,
    pub encoded_times: Vec<i32>,
    pub nodes: Vec<DrcNode>,
}
#[derive(Deserialize)]
struct Code {
    width: usize,
    code: u16,
    value: i32,
}
#[derive(Deserialize)]
struct Tables {
    format_profile: String,
    format_sha256: String,
    clipping: Vec<Code>,
    slopes: Vec<Code>,
}
pub fn format_sha256() -> &'static str {
    &tables().format_sha256
}
fn tables() -> &'static Tables {
    static TABLES: OnceLock<Tables> = OnceLock::new();
    TABLES.get_or_init(|| {
        let tables: Tables = serde_json::from_str(include_str!(
            "../../../../data/hoa-shared-drc-format-v1.json"
        ))
        .expect("shared DRC tables");
        assert_eq!(tables.format_profile, PROFILE);
        tables
    })
}
fn default_delta(rate: u64) -> u16 {
    // The reference rounds rate/2000 to an integer, then chooses the strictly
    // greater power of two. The thirteen supported integral rates are exact here.
    let rounded = ((rate + 1000) / 2000) as u32;
    (1u32 << (32 - rounded.leading_zeros())) as u16
}
pub(super) fn coefficient_index(report: &CookieReport) -> Option<usize> {
    let count = report
        .fields
        .iter()
        .find(|f| f.name == "ancillary.loudness_drc.coefficient_count")?
        .value
        .as_u64()?;
    (0..count as usize).find(|i| {
        report.fields.iter().any(|f| {
            f.name == format!("ancillary.loudness_drc.coefficients[{i}].location")
                && f.value == json!(1)
        })
    })
}
pub(super) fn empty_parameters(report: &CookieReport) -> DrcParameters {
    let index = coefficient_index(report);
    let frames = index
        .and_then(|i| {
            report.fields.iter().find(|f| {
                f.name == format!("ancillary.loudness_drc.coefficients[{i}].frame_size_minus_one")
            })
        })
        .and_then(|f| f.value.as_u64())
        .map_or(1024, |v| v as u16 + 1);
    let rate = report
        .derived
        .get("sample_rate_hz")
        .and_then(|v| v.as_u64())
        .unwrap_or(48000);
    DrcParameters {
        coefficient_location: u8::from(index.is_some()),
        gain_sequences: 0,
        gain_sets: 0,
        bands: 0,
        coding_profile: 3,
        interpolation: "linear".into(),
        full_frame: false,
        time_alignment: false,
        frame_samples: frames,
        time_delta_min: default_delta(rate),
    }
}
pub(super) fn parameters(
    report: &CookieReport,
    channels: u64,
) -> Result<Vec<DrcSequenceParameters>, String> {
    const ROOT: &str = "ancillary.loudness_drc";
    let value = |name: &str| {
        report
            .fields
            .iter()
            .find(|f| f.name == name)
            .map(|f| &f.value)
    };
    let uint = |name: &str| {
        value(name)
            .and_then(|v| v.as_u64())
            .ok_or_else(|| format!("missing DRC field {name}"))
    };
    let flag = |name: &str| {
        value(name)
            .and_then(|v| v.as_bool())
            .ok_or_else(|| format!("missing DRC flag {name}"))
    };
    if !report.is_complete() {
        return Err("shared DRC requires a complete matching configuration".into());
    }
    if !flag(&format!("{ROOT}.header_present"))? || !flag(&format!("{ROOT}.config_present"))? {
        return Ok(vec![]);
    }
    if uint(&format!("{ROOT}.base_channel_count"))? != channels {
        return Err("shared DRC channel count disagrees with the stream".into());
    }
    let Some(index) = coefficient_index(report) else {
        return Ok(vec![]);
    };
    let coefficient = format!("{ROOT}.coefficients[{index}]");
    let count = uint(&format!("{coefficient}.gain_sequence_count"))? as usize;
    let sets = uint(&format!("{coefficient}.gain_set_count"))? as usize;
    if count > 63 || sets > 63 {
        return Err("shared DRC exceeds its 6-bit sequence/set counts".into());
    }
    let frames = if flag(&format!("{coefficient}.frame_size_present"))? {
        uint(&format!("{coefficient}.frame_size_minus_one"))? + 1
    } else {
        1024
    };
    if !(1..=32768).contains(&frames) {
        return Err("DRC frame size exceeds its 15-bit syntax".into());
    }
    let rate = report
        .derived
        .get("sample_rate_hz")
        .and_then(|v| v.as_u64())
        .ok_or("missing DRC sample rate")?;
    let mut sequences = vec![None; count];
    for set in 0..sets {
        let root = format!("{coefficient}.gain_sets[{set}]");
        let profile = uint(&format!("{root}.coding_profile"))? as u8;
        let bands = if profile == 3 {
            1
        } else {
            uint(&format!("{root}.band_count"))? as usize
        };
        let dt = if flag(&format!("{root}.time_delta_min_present"))? {
            uint(&format!("{root}.time_delta_min_minus_one"))? as u16 + 1
        } else {
            default_delta(rate)
        };
        if !(1..=2048).contains(&dt) {
            return Err("DRC time step exceeds its 11-bit syntax".into());
        }
        let parameters = DrcParameters {
            coefficient_location: 1,
            gain_sequences: count as u8,
            gain_sets: sets as u8,
            bands: bands as u8,
            coding_profile: profile,
            interpolation: if flag(&format!("{root}.interpolation_type"))? {
                "linear"
            } else {
                "spline"
            }
            .into(),
            full_frame: flag(&format!("{root}.full_frame"))?,
            time_alignment: flag(&format!("{root}.time_alignment"))?,
            frame_samples: frames as u16,
            time_delta_min: dt,
        };
        for band in 0..bands {
            let sequence = report
                .derived
                .get(&format!("{root}.bands[{band}].sequence_index"))
                .and_then(|v| v.as_u64())
                .ok_or("missing DRC gain sequence reference")? as usize;
            if sequence >= count {
                return Err("DRC sequence reference exceeds its declaration".into());
            }
            sequences[sequence] = Some(DrcSequenceParameters {
                sequence_index: sequence,
                gain_set_index: set,
                parameters: parameters.clone(),
            });
        }
    }
    sequences
        .into_iter()
        .map(|s| s.ok_or_else(|| "unassigned DRC gain sequence".into()))
        .collect()
}
fn symbol(parser: &mut Parser<'_>, name: &str, table: &[Code]) -> Result<i32, ParseError> {
    let start = parser.bits.position();
    let mut code = 0u16;
    for width in 1..=16 {
        code = (code << 1) | parser.bits.read(1)? as u16;
        if let Some(entry) = table.iter().find(|e| e.width == width && e.code == code) {
            if parser.capture {
                parser.report.fields.push(crate::config::ConfigField {
                    name: name.into(),
                    bit_offset: start,
                    bit_length: width,
                    value: json!(entry.value),
                });
            }
            return Ok(entry.value);
        }
    }
    Err(ParseError::new(
        start,
        "drc-codeword",
        "invalid shared DRC codeword",
    ))
}
pub(super) fn read_sequence(
    parser: &mut Parser<'_>,
    config: &DrcSequenceParameters,
) -> Result<DrcGainSequence, ParseError> {
    let root = format!(
        "ancillary.loudness_drc.sequences[{}]",
        config.sequence_index
    );
    let p = &config.parameters;
    let start = parser.bits.position();
    let frames = i32::from(p.frame_samples);
    let dt = i32::from(p.time_delta_min);
    let offset = if p.time_alignment {
        (dt - 1) / 2 - dt
    } else {
        -1
    };
    let mode = if p.coding_profile == 3 {
        0
    } else {
        parser.take(&format!("{root}.coding_mode"), 1)? as u8
    };
    let mut count = 1;
    let mut slopes = vec![];
    let mut deltas = vec![];
    let mut times = vec![];
    let mut frame_end = true;
    if mode == 1 {
        let at = parser.bits.position();
        while parser.bits.read(1)? == 0 {
            count += 1;
            if count > 256 {
                return Err(ParseError::new(
                    at,
                    "drc-node-count",
                    "DRC node count exceeds 256",
                ));
            }
        }
        if parser.capture {
            parser.report.fields.push(crate::config::ConfigField {
                name: format!("{root}.node_count"),
                bit_offset: at,
                bit_length: parser.bits.position() - at,
                value: json!(count),
            });
        }
        if p.interpolation == "spline" {
            for i in 0..count {
                slopes
                    .push(symbol(parser, &format!("{root}.slopes[{i}]"), &tables().slopes)? as u8);
            }
        }
        if !p.full_frame {
            frame_end = parser.flag(&format!("{root}.frame_end"))?;
        }
        let mut cursor = offset;
        for i in 0..count - usize::from(frame_end) {
            let at = parser.bits.position();
            let value = super::drc::time_delta(&mut parser.bits, (frames / dt) as u32)?;
            cursor = i32::try_from(i64::from(cursor) + i64::from(value) * i64::from(dt)).map_err(
                |_| {
                    ParseError::new(
                        at,
                        "drc-node-time",
                        "DRC node coordinate exceeds its signed range",
                    )
                },
            )?;
            if times.last().is_some_and(|&t| cursor <= t) {
                return Err(ParseError::new(
                    at,
                    "drc-node-time",
                    "shared DRC nodes must advance",
                ));
            }
            if parser.capture {
                parser.report.fields.push(crate::config::ConfigField {
                    name: format!("{root}.time_deltas[{i}]"),
                    bit_offset: at,
                    bit_length: parser.bits.position() - at,
                    value: json!(value),
                });
            }
            deltas.push(DrcTimeDelta {
                value,
                bit_offset: at,
                bit_length: parser.bits.position() - at,
            });
            times.push(cursor);
        }
    }
    if frame_end {
        times.push(frames + offset);
    }
    if times.len() != count || times.windows(2).any(|w| w[0] >= w[1]) {
        return Err(ParseError::new(
            parser.bits.position(),
            "drc-node-time",
            "nonadvancing shared DRC nodes",
        ));
    }
    let mut nodes = Vec::with_capacity(count);
    let mut gain = 0;
    for (i, &time) in times.iter().enumerate() {
        let at = parser.bits.position();
        if p.coding_profile == 3 {
            gain = 0;
        } else if i == 0 {
            let negative = parser.flag(&format!("{root}.initial_gain_negative"))?;
            if p.coding_profile == 0 {
                let magnitude = parser.take(&format!("{root}.initial_gain_magnitude"), 8)? as i32;
                gain = if negative { -magnitude } else { magnitude };
            } else if negative {
                gain = -1
                    - parser.take(
                        &format!("{root}.initial_gain_magnitude"),
                        if p.coding_profile == 1 { 10 } else { 8 },
                    )? as i32;
            }
        } else if p.coding_profile == 2 {
            gain += symbol(
                parser,
                &format!("{root}.gain_deltas[{i}]"),
                &tables().clipping,
            )?;
        } else {
            let delta = super::drc::gain_delta(&mut parser.bits)?;
            gain += delta;
            if parser.capture {
                parser.report.fields.push(crate::config::ConfigField {
                    name: format!("{root}.gain_deltas[{i}]"),
                    bit_offset: at,
                    bit_length: parser.bits.position() - at,
                    value: json!(delta),
                });
            }
        }
        nodes.push(DrcNode {
            gain_eighth_db: gain,
            time,
            gain_bit_offset: at,
            gain_bit_length: parser.bits.position() - at,
            slope_index: slopes.get(i).copied(),
        });
    }
    Ok(DrcGainSequence {
        sequence_index: config.sequence_index,
        gain_set_index: config.gain_set_index,
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        coding_mode: mode,
        frame_end,
        time_deltas: deltas,
        encoded_times: times,
        nodes,
    })
}
