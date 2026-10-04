//! Eight wire rows and one-to-eight effective frequency bands. No audio arithmetic.
use super::{
    Parser,
    hoa::{HoaCoefficientSpectrum, HoaConfiguration, HoaState, RecoverySlotSpectrum},
    hoa_ambient::{AmbientTransform, StaticAmbientData},
};
use crate::config::ParseError;
use crate::prelude::*;

pub const NUMERIC_PROFILE: &str = "apac-hoa-dynamic-selection-math-v1";
pub const SUBBAND_PROFILE: &str = "apac-hoa-dynamic-subbands-v1";
pub const STATE_PROFILE: &str = "apac-hoa-dynamic-selection-state-v1";
pub const DOMAINS_PROFILE: &str = "apac-hoa-dynamic-domains-v1";
pub const DOMAINS_NUMERIC_PROFILE: &str = "apac-hoa-dynamic-domains-math-v1";
pub const DOMAINS_STATE_PROFILE: &str = "apac-hoa-dynamic-domains-state-v1";
#[cfg(test)]
#[path = "hoa_dynamic_domains_tests.rs"]
mod domains_tests;
/// SHA-256 of `data/hoa-dynamic-domains-format-v1.json`, computed at build time.
pub fn domains_format_sha256() -> &'static str {
    crate::tables::HOA_DYNAMIC_DOMAINS_FORMAT_SHA256
}

/// Generated from `data/hoa-dynamic-format-v1.json` (eight subbands) and
/// `data/hoa-dynamic-format-v2.json` (one to seven) by the build script.
pub fn format_sha256(count: usize) -> &'static str {
    if count == 8 {
        crate::tables::HOA_DYNAMIC_FORMAT_SHA256
    } else {
        crate::tables::HOA_DYNAMIC_EXTENDED_FORMAT_SHA256
    }
}
pub(super) fn boundaries(count: usize, method: usize, short: bool) -> &'static [usize] {
    if count == 8 {
        if short {
            &crate::tables::HOA_DYNAMIC_SHORT_ENDS[method]
        } else {
            &crate::tables::HOA_DYNAMIC_LONG_ENDS[method]
        }
    } else {
        let table = &crate::tables::HOA_DYNAMIC_SUBBANDS[count - 1];
        if short {
            table.short_ends[method]
        } else {
            table.long_ends[method]
        }
    }
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum DynamicSelectionEncoding {
    IndexList,
    Bitmap,
    Identity,
    Prefix,
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct DynamicBandMapping {
    pub subband_index: usize,
    pub target_acn_indices: Vec<u8>,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct InternalAmbientSpectrum {
    pub transport_slot: u8,
    pub slot_index: u8,
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct InternalAmbientData {
    pub explicit_selection: bool,
    pub selection: Vec<u8>,
    pub transform_config: AmbientTransform,
    pub effective_index: u8,
    pub index_source: String,
    pub index_start_bit_offset: Option<usize>,
    pub index_end_bit_offset: Option<usize>,
    pub channels_after_transform: Vec<InternalAmbientSpectrum>,
}
impl From<StaticAmbientData> for InternalAmbientData {
    fn from(data: StaticAmbientData) -> Self {
        Self {
            explicit_selection: data.explicit_selection,
            selection: data.selection,
            transform_config: data.transform_config,
            effective_index: data.effective_index,
            index_source: data.index_source,
            index_start_bit_offset: data.index_start_bit_offset,
            index_end_bit_offset: data.index_end_bit_offset,
            channels_after_transform: data
                .channels_after_transform
                .into_iter()
                .map(|v| InternalAmbientSpectrum {
                    transport_slot: v.transport_slot,
                    slot_index: v.acn_index,
                    scaled: v.scaled,
                })
                .collect(),
        }
    }
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct DynamicSelectionData {
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub domain_profile: Option<String>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub configured_subband_count: Option<usize>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub wire_mapping_groups: Option<usize>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub unrounded_subbands: Option<bool>,
    pub encoding: DynamicSelectionEncoding,
    pub method: u8,
    pub subband_ends: Vec<usize>,
    pub lines_per_window: Vec<usize>,
    /// The first N of the eight validated wire mappings are effective.
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub active_subband_count: Option<usize>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub subband_profile: Option<String>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub format_sha256: Option<String>,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub internal_spatial_end_bit_offset: usize,
    pub mappings: Vec<DynamicBandMapping>,
    pub recovery_numeric_profile: String,
    pub ambient_recovery_slots: Vec<u8>,
    pub ambient_transport_channels: Vec<u8>,
    pub salient_transport_channels: Vec<u8>,
    pub unused_transport_channels: Vec<u8>,
    pub before_selection: Vec<RecoverySlotSpectrum>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub internal_ambient: Option<InternalAmbientData>,
}

pub(super) fn read_and_apply(
    parser: &mut Parser<'_>,
    configuration: &HoaConfiguration,
    block: u8,
    input: Vec<RecoverySlotSpectrum>,
    ambient: Option<StaticAmbientData>,
    state: &mut HoaState,
) -> Result<(DynamicSelectionData, Vec<HoaCoefficientSpectrum>), ParseError> {
    let start = parser.bits.position();
    let method = configuration.dynamic_method.expect("dynamic context");
    let count = usize::from(configuration.dynamic_subbands.unwrap_or(0));
    let slots = usize::from(configuration.recovery_slots);
    let outputs = usize::from(configuration.channels);
    let active = slots < outputs;
    let extended = configuration.dynamic_domains_extended();
    if !(1..=8).contains(&count)
        || method > 2
        || !(1..=121).contains(&slots)
        || !(1..=121).contains(&outputs)
        || input.len() != slots
        || input.iter().enumerate().any(|(i, v)| {
            usize::from(v.slot_index) != i
                || v.scaled.len() != 1024
                || v.scaled.iter().any(|v| !v.is_finite())
        })
    {
        return Err(ParseError::new(
            start,
            "hoa-dynamic-input",
            "requires finite restored slots matching the actual domain and a supported subdivision",
        ));
    }
    let mut encoding = if slots == outputs {
        DynamicSelectionEncoding::Identity
    } else {
        DynamicSelectionEncoding::Prefix
    };
    let mut mappings = Vec::new();
    let mut saved = vec![vec![0u8; slots]; if active { 8 } else { 0 }];
    if active {
        let listed = parser.flag("hoa.dynamic_selection.index_list")?;
        encoding = if listed {
            DynamicSelectionEncoding::IndexList
        } else {
            DynamicSelectionEncoding::Bitmap
        };
        let width = usize::BITS as usize - (outputs - 1).leading_zeros() as usize;
        for (band, targets) in saved.iter_mut().enumerate() {
            let begin = parser.bits.position();
            if listed {
                let mut seen = vec![false; outputs];
                for (slot, target) in targets.iter_mut().enumerate() {
                    let position = parser.bits.position();
                    let value = parser.take(
                        format_args!("hoa.dynamic_selection.bands[{band}].target[{slot}]"),
                        width,
                    )? as usize;
                    if value >= outputs {
                        return Err(ParseError::new(
                            position,
                            "hoa-dynamic-selection",
                            format!("target ACN {value} exceeds output dimension {outputs}"),
                        ));
                    }
                    if seen[value] {
                        return Err(ParseError::new(
                            position,
                            "hoa-dynamic-selection",
                            format!("duplicate target ACN {value} in band {band}"),
                        ));
                    }
                    seen[value] = true;
                    *target = value as u8;
                }
            } else {
                let mut selected = Vec::with_capacity(slots);
                for acn in 0..outputs {
                    if parser.flag(format_args!(
                        "hoa.dynamic_selection.bands[{band}].selected[{acn}]"
                    ))? {
                        selected.push(acn as u8);
                    }
                }
                if selected.len() != slots {
                    return Err(ParseError::new(
                        begin,
                        "hoa-dynamic-selection",
                        format!(
                            "band {band} selects {} coefficients; expected {slots}",
                            selected.len()
                        ),
                    ));
                }
                *targets = selected;
            }
            mappings.push(DynamicBandMapping {
                subband_index: band,
                target_acn_indices: targets.clone(),
                start_bit_offset: begin,
                end_bit_offset: parser.bits.position(),
            });
        }
    }
    let rounded = configuration.controls.flag_f;
    let ends = if !active {
        vec![]
    } else if rounded {
        boundaries(count, usize::from(method), false).to_vec()
    } else {
        super::hoa_controls::boundaries(count, usize::from(method)).to_vec()
    };
    let per_window = if !active || (!rounded && block == 2) {
        vec![]
    } else if rounded {
        boundaries(count, usize::from(method), block == 2).to_vec()
    } else {
        ends.clone()
    };
    let mut output: Vec<_> = (0..outputs)
        .map(|acn| HoaCoefficientSpectrum {
            acn_index: acn as u8,
            scaled: if active {
                vec![0.; 1024]
            } else {
                input[acn].scaled.clone()
            },
        })
        .collect();
    if active {
        for line in 0..1024 {
            let position = if block == 2 && !rounded {
                (line % 128) * 8 + line / 128
            } else if block == 2 {
                line % 128
            } else {
                line
            };
            let band = (if rounded { &per_window } else { &ends })
                .iter()
                .position(|&end| position < end)
                .expect("complete frequency coverage");
            for (slot, source) in input.iter().enumerate() {
                output[usize::from(saved[band][slot])].scaled[line] = source.scaled[line];
            }
        }
    }
    state.last_dynamic_mapping = active.then_some(saved);
    let ambient_count = configuration.ambient_components;
    Ok((
        DynamicSelectionData {
            domain_profile: extended.then(|| DOMAINS_PROFILE.into()),
            configured_subband_count: extended.then_some(count),
            wire_mapping_groups: extended.then_some(if active { 8 } else { 0 }),
            unrounded_subbands: (active && !rounded).then_some(true),
            encoding,
            method,
            subband_ends: ends,
            lines_per_window: per_window,
            active_subband_count: if !active {
                Some(0)
            } else {
                (count < 8).then_some(count)
            },
            subband_profile: (active && count < 8).then(|| SUBBAND_PROFILE.into()),
            format_sha256: if extended {
                Some(domains_format_sha256().into())
            } else if !rounded {
                Some(super::hoa_controls::format_sha256().into())
            } else {
                (count < 8).then(|| format_sha256(count).into())
            },
            start_bit_offset: start,
            end_bit_offset: parser.bits.position(),
            internal_spatial_end_bit_offset: start,
            mappings,
            recovery_numeric_profile: configuration.recovery_numeric_profile().into(),
            ambient_recovery_slots: configuration.ambient_indices().to_vec(),
            ambient_transport_channels: (0..ambient_count)
                .map(|slot| configuration.transport_slot(slot))
                .collect(),
            salient_transport_channels: (ambient_count..configuration.core_channels)
                .map(|slot| configuration.transport_slot(slot))
                .collect(),
            unused_transport_channels: configuration.unused_transport_channels(),
            before_selection: input,
            internal_ambient: ambient.map(Into::into),
        },
        output,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::bits::BitReader;
    fn bytes(v: &serde_json::Value) -> Vec<u8> {
        v.as_str()
            .unwrap()
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
            .collect()
    }
    #[test]
    fn mapping_encodings_check_every_bit_boundary_and_short_window_coordinate() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-dynamic-state-v1.json"))
                .unwrap();
        let fixture = &data["fixtures"][0];
        let context =
            crate::frame::HoaFrameContext::from_cookie(&bytes(&fixture["cookie"])).unwrap();
        let template = crate::frame::parse_hoa_packet(&context, &bytes(&fixture["first"]))
            .unwrap()
            .packet
            .frame;
        for row in data["mapping_cases"].as_array().unwrap() {
            let context =
                crate::frame::HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
            let raw = bytes(&row["bytes"]);
            let end = row["bits"].as_u64().unwrap() as usize;
            let listed = row["truth"]["encoding"] == "index_list";
            for cut in 0..=end {
                let mut parser = Parser {
                    mode: crate::frame::ParseMode::Report,
                    bits: BitReader::new(&raw),
                    report: template.clone(),
                };
                parser.bits.set_end(cut).unwrap();
                let input = (0..9)
                    .map(|slot| RecoverySlotSpectrum {
                        slot_index: slot,
                        scaled: vec![f32::from(slot + 1); 1024],
                    })
                    .collect();
                let mut state = HoaState::default();
                let result = read_and_apply(
                    &mut parser,
                    &context.configuration,
                    2,
                    input,
                    None,
                    &mut state,
                );
                if cut < end {
                    let e = result.unwrap_err();
                    assert_eq!(e.kind, "truncated");
                    assert_eq!(
                        e.bit_offset,
                        if listed && cut > 0 {
                            1 + 4 * ((cut - 1) / 4)
                        } else {
                            cut
                        }
                    );
                    assert!(state.last_dynamic_mapping.is_none());
                } else {
                    let (selection, output) = result.unwrap();
                    assert_eq!(selection.end_bit_offset, end);
                    let actual = serde_json::to_value(&selection).unwrap();
                    for (key, expected) in row["truth"].as_object().unwrap() {
                        assert_eq!(&actual[key], expected, "{key}");
                    }
                    for line in 0..1024 {
                        let frequency = line % 128;
                        let band = row["truth"]["lines_per_window"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .position(|v| frequency < v.as_u64().unwrap() as usize)
                            .unwrap();
                        let targets = row["truth"]["mappings"][band]["target_acn_indices"]
                            .as_array()
                            .unwrap();
                        for (acn, spectrum) in output.iter().enumerate() {
                            let expected = targets
                                .iter()
                                .position(|v| v.as_u64().unwrap() as usize == acn)
                                .map_or(0., |slot| (slot + 1) as f32);
                            assert_eq!(spectrum.scaled[line].to_bits(), expected.to_bits());
                        }
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
    #[test]
    fn effective_subbands_check_every_wire_bit_and_all_boundary_lines() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-subbands-state-v1.json"))
                .unwrap();
        let fixture = &data["fixtures"][0];
        let context =
            crate::frame::HoaFrameContext::from_cookie(&bytes(&fixture["cookie"])).unwrap();
        let template = crate::frame::parse_hoa_packet(&context, &bytes(&fixture["first"]))
            .unwrap()
            .packet
            .frame;
        for row in data["mapping_cases"].as_array().unwrap() {
            let context =
                crate::frame::HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
            assert_eq!(
                context.dynamic_subband_count(),
                Some(row["truth"]["active_subband_count"].as_u64().unwrap() as usize)
            );
            let block = row["block"].as_u64().unwrap() as u8;
            let raw = bytes(&row["bytes"]);
            let end = row["bits"].as_u64().unwrap() as usize;
            let listed = row["truth"]["encoding"] == "index_list";
            for cut in 0..=end {
                let mut parser = Parser {
                    mode: crate::frame::ParseMode::Report,
                    bits: BitReader::new(&raw),
                    report: template.clone(),
                };
                parser.bits.set_end(cut).unwrap();
                let input = (0..9)
                    .map(|slot| RecoverySlotSpectrum {
                        slot_index: slot,
                        scaled: vec![f32::from(slot + 1); 1024],
                    })
                    .collect();
                let mut state = HoaState::default();
                let result = read_and_apply(
                    &mut parser,
                    &context.configuration,
                    block,
                    input,
                    None,
                    &mut state,
                );
                if cut < end {
                    let e = result.unwrap_err();
                    assert_eq!(e.kind, "truncated");
                    assert_eq!(
                        e.bit_offset,
                        if listed && cut > 0 {
                            1 + 4 * ((cut - 1) / 4)
                        } else {
                            cut
                        }
                    );
                    assert!(state.last_dynamic_mapping.is_none());
                } else {
                    let (selection, output) = result.unwrap();
                    assert_eq!(selection.end_bit_offset, end);
                    let actual = serde_json::to_value(&selection).unwrap();
                    for (key, expected) in row["truth"].as_object().unwrap() {
                        assert_eq!(&actual[key], expected, "{key}");
                    }
                    for line in 0..1024 {
                        let frequency = if block == 2 { line % 128 } else { line };
                        let band = row["truth"]["lines_per_window"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .position(|v| frequency < v.as_u64().unwrap() as usize)
                            .unwrap();
                        let targets = row["truth"]["mappings"][band]["target_acn_indices"]
                            .as_array()
                            .unwrap();
                        for (acn, spectrum) in output.iter().enumerate() {
                            let expected = targets
                                .iter()
                                .position(|v| v.as_u64().unwrap() as usize == acn)
                                .map_or(0., |slot| (slot + 1) as f32);
                            assert_eq!(spectrum.scaled[line].to_bits(), expected.to_bits());
                        }
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
    #[test]
    fn invalid_internal_spectrum_precedes_missing_mapping_bits() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-dynamic-state-v1.json"))
                .unwrap();
        let f = &data["fixtures"][0];
        let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let template = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"]))
            .unwrap()
            .packet
            .frame;
        let mut parser = Parser {
            mode: crate::frame::ParseMode::Report,
            bits: BitReader::new(&[]),
            report: template,
        };
        let mut input: Vec<_> = (0..9)
            .map(|slot| RecoverySlotSpectrum {
                slot_index: slot,
                scaled: vec![0.; 1024],
            })
            .collect();
        input[0].scaled[0] = f32::INFINITY;
        let mut state = HoaState::default();
        let error = read_and_apply(
            &mut parser,
            &context.configuration,
            0,
            input,
            None,
            &mut state,
        )
        .unwrap_err();
        assert_eq!(error.kind, "hoa-dynamic-input");
        assert!(state.last_dynamic_mapping.is_none());
    }
}
