//! Static ambient selection and exact-rational FOA transforms.
use super::{ChannelPacketReport, hoa::RecoverySlotSpectrum};
use crate::config::ParseError;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

pub const NUMERIC_PROFILE: &str = "apac-hoa-static-ambient-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-static-ambient-state-v1";

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum AmbientTransform {
    #[default]
    Disabled,
    Fixed {
        index: u8,
    },
    PerFrame,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AmbientSpectrum {
    pub transport_slot: u8,
    pub acn_index: u8,
    pub scaled: Vec<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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

#[derive(Deserialize)]
struct Tables {
    numeric_profile: String,
    format_profile: String,
    format_sha256: String,
    tables_sha256: String,
    decoder_matrices_f64: [[u64; 16]; 3],
}
fn tables() -> &'static Tables {
    static DATA: OnceLock<Tables> = OnceLock::new();
    DATA.get_or_init(|| {
        let value: Tables =
            serde_json::from_str(include_str!("../../data/hoa-static-ambient-tables-v1.json"))
                .expect("built-in static ambient tables");
        assert_eq!(value.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(value.format_profile, "apac-hoa-ambient-transform-format-v1");
        assert!(
            value
                .decoder_matrices_f64
                .iter()
                .flatten()
                .all(|&word| f64::from_bits(word).abs() == 0.5)
        );
        value
    })
}
pub(crate) fn format_sha256() -> &'static str {
    &tables().format_sha256
}
pub(crate) fn math_sha256() -> &'static str {
    &tables().tables_sha256
}

pub(super) fn matrix_coefficient(index: u8, row: usize, column: usize) -> f64 {
    if index == 3 {
        return f64::from(u8::from(row == column));
    }
    f64::from_bits(tables().decoder_matrices_f64[usize::from(index)][row * 4 + column])
}

fn transform(input: [f32; 4], index: u8, output: usize) -> f32 {
    if index == 3 {
        return input[output];
    }
    let matrix = &tables().decoder_matrices_f64[usize::from(index)];
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
    let sources: Vec<_> = packet
        .elements
        .iter()
        .take(count)
        .map(|element| {
            if element.present {
                element.channels_after_bwe2[0].scaled.clone()
            } else {
                vec![0.; 1024]
            }
        })
        .collect();
    if count < 4
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
        let mut scaled = sources[slot].clone();
        for (line, value) in scaled.iter_mut().enumerate() {
            if slot < 4 {
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
            transport_slot: slot as u8,
            acn_index: acn,
            scaled,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
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
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../data/hoa-static-ambient-state-v1.json"))
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
