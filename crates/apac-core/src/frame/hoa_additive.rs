//! Additive mixed recovery with a single Float32 boundary after both contributions.
use super::{
    ChannelPacketReport, HoaSpatialData, HoaState,
    hoa::{HoaConfiguration, RecoverySlotSpectrum},
};
use crate::config::ParseError;
use crate::prelude::*;

pub const NUMERIC_PROFILE: &str = "apac-hoa-additive-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-additive-state-v1";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum AmbientCombination {
    #[default]
    Replace,
    Add,
}

/// Diagnostic Float64 ambient contribution; never rounded and fed back into recovery.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct AmbientContribution {
    pub transport_slot: u8,
    pub recovery_index: u8,
    pub scaled: Vec<f64>,
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct HoaAdditiveData {
    pub combination: AmbientCombination,
    pub numeric_profile: String,
    pub coordinate_space: String,
    pub selection: Vec<u8>,
    pub salient_transport_channels: Vec<u8>,
    pub ambient_transport_channels: Vec<u8>,
    pub effective_transform_index: u8,
    pub spectral_stage: String,
    pub ambient_contributions: Vec<AmbientContribution>,
}

#[derive(Default)]
pub(super) struct Sum {
    value: f64,
    correction: f64,
}
impl Sum {
    pub(super) fn add(&mut self, product: f64) -> bool {
        let next = self.value + product;
        let residual = if self.value.abs() >= product.abs() {
            (self.value - next) + product
        } else {
            (product - next) + self.value
        };
        self.correction += residual;
        self.value = next;
        product.is_finite() && next.is_finite() && self.correction.is_finite()
    }
    pub(super) fn result(&self) -> f64 {
        self.value + self.correction
    }
}

pub(super) fn restore(
    packet: &ChannelPacketReport,
    spatial: &mut HoaSpatialData,
    state: &mut HoaState,
    configuration: &HoaConfiguration,
) -> Result<(Vec<RecoverySlotSpectrum>, HoaAdditiveData), ParseError> {
    let count = usize::from(configuration.recovery_slots);
    let side = spatial.salient.as_mut().expect("mixed descriptors");
    super::hoa_salient::restore_descriptors(
        packet,
        side,
        state.salient.as_mut().expect("mixed history"),
    )?;
    let index = spatial
        .ambient
        .as_ref()
        .map_or(3, |data| data.effective_index);
    let selected = configuration.ambient_indices();
    let sources = super::hoa_transport::spectra(packet)?;
    let sources = &sources[..usize::from(configuration.core_channels)];
    let error = |slot: usize, line: usize| {
        ParseError::new(
            side.descriptors.last().expect("descriptors").end_bit_offset,
            "hoa-additive-numeric",
            format!("nonfinite additive recovery at slot {slot}, line {line}"),
        )
    };
    if index > 3
        || sources.len() != usize::from(configuration.core_channels)
        || sources
            .iter()
            .any(|s| s.len() != 1024 || s.iter().any(|v| !v.is_finite()))
    {
        return Err(error(0, 0));
    }
    let mut output: Vec<_> = (0..count)
        .map(|slot| RecoverySlotSpectrum {
            slot_index: slot as u8,
            scaled: vec![0.; 1024],
        })
        .collect();
    let mut ambient: Vec<_> = selected
        .iter()
        .enumerate()
        .map(|(slot, &k)| AmbientContribution {
            transport_slot: configuration.transport_slot(slot as u8),
            recovery_index: k,
            scaled: vec![0.; 1024],
        })
        .collect();
    let short = packet.hoa.as_ref().expect("HOA packet").common_window == Some(2);
    let offsets = side.descriptor_offsets();
    for line in 0..1024 {
        let bands = side.bands_for_line(line, short);
        for (slot, out) in output.iter_mut().enumerate() {
            let mut sum = Sum::default();
            if !configuration.controls.flag_a {
                sum.add(super::hoa_controls::mean(slot));
            }
            for sc in 0..usize::from(configuration.salient_components) {
                let descriptor = side.descriptors[offsets[sc] + bands[sc]]
                    .restored
                    .get(slot)
                    .copied()
                    .unwrap_or(0.);
                let product = descriptor
                    * f64::from(sources[usize::from(configuration.ambient_components) + sc][line]);
                if !sum.add(product) {
                    return Err(error(slot, line));
                }
            }
            if let Some(row) = selected.iter().position(|&k| usize::from(k) == slot) {
                let mut contribution = Sum::default();
                for (j, source) in sources
                    .iter()
                    .take(usize::from(configuration.ambient_components))
                    .enumerate()
                {
                    let coefficient = if index == 3 || row >= 4 {
                        if j != row {
                            continue;
                        }
                        1.
                    } else {
                        if j >= 4 {
                            continue;
                        }
                        super::hoa_ambient::matrix_coefficient(index, row, j)
                    };
                    let product = f64::from(source[line]) * coefficient;
                    if !sum.add(product) || !contribution.add(product) {
                        return Err(error(slot, line));
                    }
                }
                let value = contribution.result();
                if !value.is_finite() {
                    return Err(error(slot, line));
                }
                ambient[row].scaled[line] = if value == 0. { 0. } else { value };
            }
            let value = sum.result() as f32;
            if !value.is_finite() {
                return Err(error(slot, line));
            }
            out.scaled[line] = if value == 0. { 0. } else { value };
        }
    }
    Ok((
        output,
        HoaAdditiveData {
            combination: AmbientCombination::Add,
            numeric_profile: if configuration.controls_extended() {
                super::hoa_controls::numeric_profile(configuration.controls)
            } else {
                NUMERIC_PROFILE
            }
            .into(),
            coordinate_space: if configuration.dynamic_method.is_some() {
                "internal_slots"
            } else {
                "acn"
            }
            .into(),
            selection: selected.to_vec(),
            salient_transport_channels: (configuration.ambient_components
                ..configuration.core_channels)
                .map(|slot| configuration.transport_slot(slot))
                .collect(),
            ambient_transport_channels: (0..configuration.ambient_components)
                .map(|slot| configuration.transport_slot(slot))
                .collect(),
            effective_transform_index: index,
            spectral_stage: if configuration.dynamic_method.is_some() {
                "hoa_recovery_slots_before_dynamic_selection"
            } else {
                "hoa_coefficients_before_synthesis"
            }
            .into(),
            ambient_contributions: ambient,
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compensation_preserves_cross_contribution_residuals_and_rejects_nonfinite() {
        for terms in [
            [18014398509481984., 1., -18014398509481984.],
            [-18014398509481984., 1., 18014398509481984.],
        ] {
            let mut sum = Sum::default();
            for value in terms {
                assert!(sum.add(value));
            }
            assert_eq!(sum.result(), 1.);
        }
        let mut sum = Sum::default();
        assert!(sum.add(f64::MAX));
        assert!(!sum.add(f64::MAX));
        assert!(!Sum::default().add(f64::NAN));
        assert!(!Sum::default().add(f64::INFINITY));
    }
    #[test]
    fn full_descriptors_and_transform_indices_reject_each_bit_truncation() {
        use crate::config::bits::BitReader;
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../../../data/hoa-additive-state-v1.json"))
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
            let raw = bytes(&row["bytes"]);
            let end = row["bits"].as_u64().unwrap() as usize;
            for cut in 0..=end {
                let mut parser = super::super::Parser {
                    mode: crate::frame::ParseMode::Report,
                    bits: BitReader::new(&raw),
                    report: template.clone(),
                };
                parser.bits.set_end(cut).unwrap();
                let result = super::super::hoa::spatial(
                    &mut parser,
                    &mut HoaState::default(),
                    &context.configuration,
                    0,
                );
                if cut < end {
                    let error = result.unwrap_err();
                    assert_eq!(error.kind, "truncated");
                    assert!(error.bit_offset <= cut);
                    if cut < 2 {
                        assert_eq!(error.bit_offset, 0);
                    }
                } else {
                    let result = result.unwrap();
                    assert_eq!(result.end_bit_offset, end);
                    let value = serde_json::to_value(result.salient.unwrap()).unwrap();
                    for (actual, expected) in value["descriptors"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .zip(row["truth"]["salient"]["descriptors"].as_array().unwrap())
                    {
                        for (key, wanted) in expected.as_object().unwrap() {
                            assert_eq!(&actual[key], wanted, "{key}");
                        }
                    }
                    parser.bits.set_end(raw.len() * 8).unwrap();
                    assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
                }
            }
        }
    }
}
