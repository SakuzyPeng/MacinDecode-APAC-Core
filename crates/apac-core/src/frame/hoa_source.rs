//! Source-layout recovery after spatial reconstruction and before dynamic selection.
use super::{hoa::RecoverySlotSpectrum, hoa_additive::Sum};
use crate::prelude::*;
use crate::{
    config::{Component, ParseError},
    model::{ChannelDescription, ChannelLayout},
};
use serde::{Deserialize, Serialize};

pub const PROFILE: &str = "apac-hoa-source-layout-format-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-source-layout-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-source-layout-state-v1";

use crate::tables::HoaSourceLayout as LayoutEntry;
/// Generated from `data/hoa-source-layout-format-v1.json` by the build script.
pub fn format_sha256() -> &'static str {
    crate::tables::HOA_SOURCE_FORMAT_SHA256
}
pub(super) fn descriptions(layout: &ChannelLayout, channels: u32) -> Vec<ChannelDescription> {
    if layout.tag == 0 {
        return layout.descriptions.clone();
    }
    let labels: Vec<u32> = match layout.tag >> 16 {
        190 => (0..channels).map(|i| (2 << 16) | i).collect(),
        191 => (0..channels).map(|i| (3 << 16) | i).collect(),
        147 => (0..channels).map(|i| (1 << 16) | i).collect(),
        _ => crate::tables::HOA_SOURCE_LAYOUTS
            .iter()
            .find(|e| e.tag == layout.tag)
            .map_or_else(
                || (0..channels).map(|i| (1 << 16) | i).collect(),
                |e| e.channel_labels.to_vec(),
            ),
    };
    labels
        .into_iter()
        .map(|label| ChannelDescription {
            label,
            flags: 0,
            coordinates: [0.; 3],
        })
        .collect()
}

#[derive(Debug, Clone)]
pub(super) struct SourceLayout {
    pub layout: ChannelLayout,
    pub channels: u8,
    pub extended: bool,
    pub parameter: u8,
}
impl SourceLayout {
    pub fn selected(component: &Component, recovery: u8, dynamic: bool, parameter: u8) -> Self {
        let channels = component
            .channels
            .filter(|&n| (1..=121).contains(&n))
            .unwrap_or(u64::from(recovery)) as u8;
        let tag = component
            .layout_tag
            .and_then(|v| u32::try_from(v).ok())
            .unwrap_or((190 << 16) | u32::from(channels));
        let mut layout = ChannelLayout::tagged(
            tag,
            u32::from(channels),
            (tag >> 16 == 190).then(|| "HOA ACN/SN3D".into()),
        );
        if tag == 0
            && let Some(labels) = &component.hoa.channel_labels
        {
            layout.descriptions = labels
                .iter()
                .filter_map(|&v| u32::try_from(v).ok())
                .map(|label| ChannelDescription {
                    label,
                    flags: 0,
                    coordinates: [0.; 3],
                })
                .collect();
        }
        Self {
            layout,
            channels,
            extended: tag >> 16 != 190 || (!dynamic && channels != recovery),
            parameter,
        }
    }
    fn entry(&self) -> Option<&'static LayoutEntry> {
        crate::tables::HOA_SOURCE_LAYOUTS
            .iter()
            .find(|entry| entry.tag == self.layout.tag)
    }
    pub fn rejection(&self, coefficients: usize) -> Option<String> {
        if !crate::tables::HOA_SOURCE_ACCEPTED_TAGS.iter().any(|&tag| {
            let exact = tag == 0 || tag == (1 << 16) || tag & 0xffff != 0 || tag == 0xffff0000;
            if exact {
                self.layout.tag == tag
            } else {
                self.layout.tag & 0xffff0000 == tag
            }
        }) {
            return Some(format!(
                "HOA source layout tag {} is rejected by the reference profile layout table",
                self.layout.tag
            ));
        }
        match self.layout.tag >> 16 {
            0 if self.layout.tag == 0
                && self.layout.descriptions.len() == usize::from(self.channels) =>
            {
                if matches!(self.parameter, 1 | 2) {
                    return None;
                }
            }
            147 | 190 => {
                if matches!(self.parameter, 1 | 2) {
                    return None;
                }
            }
            _ => {
                if let Some(entry) = self.entry() {
                    if matches!(self.parameter, 1 | 2) {
                        return None;
                    }
                    if self.parameter == 0 && entry.matrix_available {
                        if coefficients <= entry.matrix_columns {
                            return None;
                        }
                        return Some(
                            "HOA source matrix input exceeds its bounded coefficient table".into(),
                        );
                    }
                } else if matches!(self.parameter, 1 | 2) {
                    return None;
                }
            }
        }
        Some(format!(
            "HOA source layout does not accept parameter_0={}",
            self.parameter
        ))
    }
    pub fn channel_labels(&self) -> Vec<String> {
        if self.layout.tag >> 16 == 190 {
            return (0..self.channels).map(|i| format!("ACN{i}")).collect();
        }
        let labels: Vec<_> = self
            .entry()
            .map(|e| e.channel_labels.to_vec())
            .unwrap_or_else(|| self.layout.descriptions.iter().map(|d| d.label).collect());
        (0..usize::from(self.channels))
            .map(|i| {
                labels
                    .get(i)
                    .map_or_else(|| format!("Source{i}"), |label| format!("Label{label}"))
            })
            .collect()
    }
    pub fn normalization(&self) -> Option<&'static str> {
        match self.layout.tag >> 16 {
            190 => Some("SN3D"),
            191 => Some("N3D"),
            0 if !self.layout.descriptions.is_empty() => {
                let family = self.layout.descriptions[0].label >> 16;
                if self
                    .layout
                    .descriptions
                    .iter()
                    .any(|d| d.label >> 16 != family)
                {
                    return None;
                }
                match family {
                    2 => Some("SN3D"),
                    3 => Some("N3D"),
                    _ => None,
                }
            }
            _ => None,
        }
    }
    pub fn operation(&self) -> &'static str {
        if self.parameter == 0 {
            "matrix"
        } else if self.parameter == 1 && self.entry().is_some() {
            "position_with_lfe_omitted"
        } else {
            "position"
        }
    }
    /// The matrix reader uses the actual coefficient count as its row stride.
    /// Its complete bounded table is shared; a wider stride would read beyond it.
    pub fn convert(
        &self,
        input: &[RecoverySlotSpectrum],
        at: usize,
    ) -> Result<Vec<RecoverySlotSpectrum>, ParseError> {
        let m = input.len();
        let n = usize::from(self.channels);
        let error = || {
            ParseError::new(
                at,
                "hoa-source-layout",
                "invalid or nonfinite source-layout recovery",
            )
        };
        if input.iter().enumerate().any(|(i, s)| {
            usize::from(s.slot_index) != i
                || s.scaled.len() != 1024
                || s.scaled.iter().any(|v| !v.is_finite())
        }) || self.rejection(m).is_some()
        {
            return Err(error());
        }
        let mut output: Vec<_> = (0..m.max(n))
            .map(|i| RecoverySlotSpectrum {
                slot_index: i as u8,
                scaled: vec![0.; 1024],
            })
            .collect();
        if self.parameter == 0 {
            let entry = self.entry().ok_or_else(error)?;
            let matrix = entry.matrix;
            let mut row = 0;
            for (channel, destination) in output.iter_mut().take(n).enumerate() {
                if entry.lfe_indices.contains(&channel) {
                    continue;
                }
                for line in 0..1024 {
                    let mut sum = Sum::default();
                    for (column, source) in input.iter().enumerate() {
                        if !sum.add(
                            f64::from(f32::from_bits(matrix[row * m + column]))
                                * f64::from(source.scaled[line]),
                        ) {
                            return Err(error());
                        }
                    }
                    let value = sum.result() as f32;
                    if !value.is_finite() {
                        return Err(error());
                    }
                    destination.scaled[line] = if value == 0. { 0. } else { value };
                }
                row += 1;
            }
        } else {
            for (i, source) in input.iter().enumerate() {
                let omitted = self.parameter == 1
                    && self
                        .entry()
                        .is_some_and(|entry| i >= n || entry.lfe_indices.contains(&i));
                if !omitted {
                    output[i].scaled.clone_from(&source.scaled);
                }
            }
        }
        Ok(output)
    }
    pub fn report(&self, channels: Vec<HoaSourceChannelSpectrum>) -> HoaSourceLayoutData {
        let coefficient_indices = self.normalization().map(|_| {
            if self.layout.tag == 0 {
                self.layout
                    .descriptions
                    .iter()
                    .map(|d| d.label & 0xffff)
                    .collect()
            } else {
                (0..u32::from(self.channels)).collect()
            }
        });
        HoaSourceLayoutData {
            format_profile: PROFILE.into(),
            format_sha256: format_sha256().into(),
            numeric_profile: NUMERIC_PROFILE.into(),
            layout: self.layout.clone(),
            normalization: self.normalization().map(str::to_owned),
            coefficient_indices,
            parameter_0: self.parameter,
            operation: self.operation().into(),
            matrix_id: (self.parameter == 0)
                .then(|| self.entry().expect("qualified matrix").matrix_id.into()),
            channels,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaSourceChannelSpectrum {
    pub channel_index: u8,
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaSourceLayoutData {
    pub format_profile: String,
    pub format_sha256: String,
    pub numeric_profile: String,
    pub layout: ChannelLayout,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normalization: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coefficient_indices: Option<Vec<u32>>,
    pub parameter_0: u8,
    pub operation: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub matrix_id: Option<String>,
    pub channels: Vec<HoaSourceChannelSpectrum>,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn source(tag: u32, parameter: u8) -> SourceLayout {
        SourceLayout {
            layout: ChannelLayout::tagged(tag, tag & 0xffff, None),
            channels: (tag & 0xffff) as u8,
            extended: true,
            parameter,
        }
    }
    fn input(count: usize) -> Vec<RecoverySlotSpectrum> {
        (0..count)
            .map(|i| RecoverySlotSpectrum {
                slot_index: i as u8,
                scaled: vec![0.; 1024],
            })
            .collect()
    }
    #[test]
    fn tagged_lfe_position_uses_the_public_layout_order() {
        let mut values = input(9);
        values[0].scaled[0] = 0.25;
        let layout = source((127 << 16) | 8, 0);
        let output = layout.convert(&values, 23).unwrap();
        assert_eq!(output[7].scaled[0], 0.);
        assert_ne!(output[3].scaled[0], 0.);
        let mut values = input(9);
        values[3].scaled[0] = 1.;
        values[7].scaled[0] = 2.;
        let output = source((127 << 16) | 8, 1).convert(&values, 23).unwrap();
        assert_eq!(output[3].scaled[0], 1.);
        assert_eq!(output[7].scaled[0], 0.);
    }
    #[test]
    fn unselected_internal_values_and_matrix_overflow_are_checked() {
        let mut values = input(4);
        values[3].scaled[1023] = f32::NAN;
        assert_eq!(
            source((190 << 16) | 2, 2)
                .convert(&values, 37)
                .unwrap_err()
                .bit_offset,
            37
        );
        values[3].scaled[1023] = 0.;
        values[1].scaled[0] = f32::MAX;
        assert!(source((101 << 16) | 2, 0).convert(&values, 37).is_err());
        assert!(source((101 << 16) | 2, 0).convert(&input(5), 37).is_err());
    }
    #[test]
    fn profile_rejection_is_distinct_from_n3d_custom_labels() {
        assert!(
            source((191 << 16) | 4, 1)
                .rejection(4)
                .unwrap()
                .contains("profile layout table")
        );
        assert!(source((107 << 16) | 4, 1).rejection(4).is_some());
        assert!(source((123 << 16) | 6, 0).rejection(9).is_some());
        assert!(source((123 << 16) | 6, 1).rejection(9).is_none());
    }
}
