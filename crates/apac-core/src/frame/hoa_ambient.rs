//! Static ambient selection and exact-rational FOA transforms.
use super::{ChannelPacketReport, hoa::RecoverySlotSpectrum};
use crate::config::ParseError;
use crate::prelude::*;
use serde::Serialize;

pub const COUNTS_NUMERIC_PROFILE: &str = "apac-hoa-ambient-counts-math-v1";
pub const COUNTS_STATE_PROFILE: &str = "apac-hoa-ambient-counts-state-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-static-ambient-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-static-ambient-state-v1";

#[derive(Debug, Clone, Copy, Default, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum AmbientTransform {
    #[default]
    Disabled,
    Fixed {
        index: u8,
    },
    PerFrame,
}

#[derive(Debug, Clone, Serialize)]
pub struct AmbientSpectrum {
    pub transport_slot: u8,
    pub acn_index: u8,
    pub scaled: Vec<f32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct StaticAmbientData {
    pub explicit_selection: bool,
    pub selection: Vec<u8>,
    pub transform_config: AmbientTransform,
    pub effective_index: u8,
    pub index_source: String,
    pub index_start_bit_offset: Option<usize>,
    pub index_end_bit_offset: Option<usize>,
    pub channels_after_transform: Vec<AmbientSpectrum>,
}

/// Generated from `data/hoa-static-ambient-tables-v1.json` by the build script.
pub fn format_sha256() -> &'static str {
    crate::tables::HOA_AMBIENT_FORMAT_SHA256
}
pub fn math_sha256() -> &'static str {
    crate::tables::HOA_AMBIENT_MATH_SHA256
}

pub(super) fn matrix_coefficient(index: u8, row: usize, column: usize) -> f64 {
    if index == 3 {
        return f64::from(u8::from(row == column));
    }
    f64::from_bits(crate::tables::HOA_AMBIENT_MATRICES[usize::from(index)][row * 4 + column])
}

fn transform(input: [f32; 4], index: u8, output: usize) -> f32 {
    if index == 3 {
        return input[output];
    }
    let matrix = &crate::tables::HOA_AMBIENT_MATRICES[usize::from(index)];
    let (mut sum, mut correction) = (0.0_f64, 0.0_f64);
    for (j, value) in input.into_iter().enumerate() {
        let product = f64::from(value) * f64::from_bits(matrix[output * 4 + j]);
        let next = sum + product;
        let residual = if sum.abs() >= product.abs() {
            (sum - next) + product
        } else {
            (product - next) + sum
        };
        correction += residual;
        sum = next;
    }
    (sum + correction) as f32
}

pub(super) fn restore(
    packet: &ChannelPacketReport,
    data: &mut StaticAmbientData,
    output: &mut [RecoverySlotSpectrum],
    position: usize,
) -> Result<(), ParseError> {
    let count = data.selection.len();
    let sources = super::hoa_transport::spectra(packet)?;
    let sources = &sources[..count];
    if (count < 4 && data.effective_index != 3)
        || data.effective_index > 3
        || sources
            .iter()
            .any(|c| c.len() != 1024 || c.iter().any(|v| !v.is_finite()))
    {
        return Err(ParseError::new(
            position,
            "hoa-ambient-numeric",
            "invalid ambient transform input",
        ));
    }
    for (slot, &acn) in data.selection.iter().enumerate() {
        let mut scaled = sources[slot].to_vec();
        for (line, value) in scaled.iter_mut().enumerate() {
            if slot < 4 && data.effective_index != 3 {
                *value = transform(
                    std::array::from_fn(|i| sources[i][line]),
                    data.effective_index,
                    slot,
                );
            }
            if !value.is_finite() {
                return Err(ParseError::new(
                    position,
                    "hoa-ambient-numeric",
                    format!(
                        "nonfinite ambient output at slot {slot}, coefficient {acn}, line {line}"
                    ),
                ));
            }
            if *value == 0. {
                *value = 0.;
            }
        }
        output[usize::from(acn)].scaled.clone_from(&scaled);
        data.channels_after_transform.push(AmbientSpectrum {
            transport_slot: super::hoa_transport::physical_slot(packet, slot as u8),
            acn_index: acn,
            scaled,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn count_data() -> serde_json::Value {
        serde_json::from_str(include_str!(
            "../../../../data/hoa-ambient-counts-state-v1.json"
        ))
        .unwrap()
    }
    fn count_bytes(value: &serde_json::Value) -> Vec<u8> {
        value
            .as_str()
            .unwrap()
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn every_ambient_count_preserves_the_last_source_and_zeros_unused_outputs() {
        for f in count_data()["counts"].as_array().unwrap() {
            let context =
                crate::frame::HoaFrameContext::from_cookie(&count_bytes(&f["cookie"])).unwrap();
            assert!(context.is_supported(), "{:?}", context.rejection());
            assert_eq!(
                context.ambient_components(),
                f["ambient"].as_u64().unwrap() as usize
            );
            assert_eq!(
                context.salient_components(),
                f["salient"].as_u64().unwrap() as usize
            );
            let report =
                crate::frame::parse_hoa_packet(&context, &count_bytes(&f["packet"])).unwrap();
            assert!(report.packet.packet_complete && report.hoa().hoa_complete);
            let active = context.ambient_components() - 1;
            let source = &report.packet.elements[active].channels_after_bwe2[0].scaled;
            assert!(source.iter().any(|&v| v != 0.));
            for (index, channel) in report.hoa().channels_after_hoa.iter().enumerate() {
                if index == active {
                    assert_eq!(&channel.scaled, source);
                } else {
                    assert!(channel.scaled.iter().all(|&v| v == 0.));
                }
            }
        }
    }

    #[test]
    fn ambient_count_selection_and_overlaps_roll_back_with_embedded_frames() {
        for f in count_data()["fixtures"].as_array().unwrap() {
            let cookie = count_bytes(&f["cookie"]);
            let first = count_bytes(&f["first"]);
            let good = count_bytes(&f["good"]);
            let bad = count_bytes(&f["bad"]);
            let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
            let mut state = crate::frame::HoaState::default();
            let mut drc = context.initial_drc_state();
            crate::frame::parse_hoa_packet_with_state(&context, &first, &mut drc, &mut state)
                .unwrap();
            let before = state.clone();
            let failed =
                crate::frame::parse_hoa_packet_with_state(&context, &bad, &mut drc, &mut state);
            assert!(failed.is_err() || !failed.unwrap().packet.packet_complete);
            assert_eq!(state, before);
            let mut decoder = crate::synthesis::SqDecoder::from_cookie(&cookie).unwrap();
            let mut clean = crate::synthesis::SqDecoder::from_cookie(&cookie).unwrap();
            let initial = decoder.decode_frame(&first).unwrap();
            clean.decode_frame(&first).unwrap();
            assert!(decoder.decode_frame(&bad).is_err());
            assert_eq!(
                decoder.decode_frame(&good).unwrap(),
                clean.decode_frame(&good).unwrap()
            );
            decoder.reset();
            assert_eq!(decoder.decode_frame(&first).unwrap(), initial);
        }
    }
    #[test]
    fn compensated_transform_retains_small_residual_and_detects_overflow() {
        // SQ q=4096, sf=252 gives exactly 2^54; q=1, sf=100 gives 1.
        let large = 18014398509481984.0_f32;
        assert_eq!(transform([large, 1., -large, 0.], 0, 0), 0.5);
        for index in 0..3 {
            for slot in 0..4 {
                let mut input = [0.; 4];
                input[slot] = 2.;
                for out in 0..4 {
                    assert_eq!(transform(input, index, out).abs(), 1.);
                }
            }
        }
        assert!(transform([f32::MAX; 4], 0, 0).is_infinite());
        assert_eq!(transform([large, 1., -large, 0.], 3, 1), 1.);
    }
    #[test]
    fn selectors_masks_and_spatial_boundaries_reject_every_bit_truncation() {
        use crate::config::bits::BitReader;
        let data: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../data/hoa-static-ambient-state-v1.json"
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
        let fixture = &data["fixtures"][0];
        let context =
            super::super::HoaFrameContext::from_cookie(&bytes(&fixture["cookie"])).unwrap();
        let template = super::super::parse_hoa_packet(&context, &bytes(&fixture["first"]))
            .unwrap()
            .packet
            .frame;
        for row in data["spatial_cases"].as_array().unwrap() {
            let context =
                super::super::HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
            assert!(context.is_supported());
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
                    &context.configuration,
                    0,
                );
                if cut < end {
                    let error = result.unwrap_err();
                    assert_eq!(error.kind, "truncated");
                    assert!(error.bit_offset <= cut);
                    if context.ambient_transform() == AmbientTransform::PerFrame && cut < 2 {
                        assert_eq!(error.bit_offset, 0);
                    }
                } else {
                    let result = result.unwrap();
                    assert_eq!(result.end_bit_offset, end);
                    let value = serde_json::to_value(result.ambient.unwrap()).unwrap();
                    for (key, expected) in row["truth"]["ambient"].as_object().unwrap() {
                        assert_eq!(&value[key], expected, "{key}");
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
}
