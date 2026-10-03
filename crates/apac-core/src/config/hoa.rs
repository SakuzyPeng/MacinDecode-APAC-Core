//! HOA ASC configuration. Output channels come from the serialized layout;
//! they are distinct from HOA coefficients and internal transport channels.
use super::{
    HoaSalientDeclaration, ParseError,
    parser::{PResult, Parser},
};
use crate::prelude::*;
use serde_json::json;

fn index_width(count: u64) -> usize {
    // ceil(log2(count)), with a zero-bit index for one entry.
    (u64::BITS - count.saturating_sub(1).leading_zeros()) as usize
}

/// Resolve wire links to the carrier permutation once, with a strict traversal
/// bound. Repeated indices can still describe a terminating permutation.
fn effective_remapping(links: &[usize]) -> Result<Vec<usize>, usize> {
    let mut order: Vec<_> = (0..links.len()).collect();
    for i in 0..links.len() {
        let mut next = i;
        let mut resolved = false;
        for _ in 0..links.len() {
            next = *links.get(next).ok_or(i)?;
            if next <= i {
                order.swap(i, next);
                resolved = true;
                break;
            }
        }
        if !resolved {
            return Err(i);
        }
    }
    Ok(order)
}

impl Parser<'_> {
    fn hoa_square(&self, order: u64) -> PResult<u64> {
        let count = order
            .checked_add(1)
            .and_then(|n| n.checked_mul(n))
            .ok_or_else(|| ParseError::new(self.pos(), "overflow", "HOA order square overflow"))?;
        // The verified writer rejects orders >= 11; its reader limits coefficients to 121.
        if order > 10 || count > 121 {
            return self.invalid("hoa-order", "HOA order exceeds the verified 0..10 range");
        }
        Ok(count)
    }

    pub(super) fn hoa_component(
        &mut self,
        component: &str,
        start: u64,
        total: u64,
    ) -> PResult<u64> {
        let p = format!("{component}.hoa");
        let full_order = self.flag_at(&format!("{p}.full_order"))?;
        self.hoa_mut().full_order = Some(full_order);
        let full_order = full_order.value;
        self.hoa_mut().flag_a = Some(self.flag_at(&format!("{p}.flag_a"))?);
        let conditional = self.flag_at(&format!("{p}.flag_b"))?;
        self.hoa_mut().flag_b = Some(conditional);
        let conditional = conditional.value;
        if conditional {
            self.hoa_mut().flag_c = Some(self.flag_at(&format!("{p}.flag_c"))?);
        }
        self.hoa_mut().flag_d = Some(self.flag_at(&format!("{p}.flag_d"))?);
        self.hoa_mut().flag_e = Some(self.flag_at(&format!("{p}.flag_e"))?);
        self.hoa_mut().flag_f = Some(self.flag_at(&format!("{p}.flag_f"))?);
        let dynamic = self.flag_at(&format!("{p}.dynamic_selection_config_present"))?;
        self.hoa_mut().dynamic_selection_config_present = Some(dynamic);
        if dynamic.value {
            self.hoa_mut().dynamic_selection_parameter =
                Some(self.take_at(&format!("{p}.dynamic_selection.parameter"), 2)?);
            let bands = self.esc_at(
                &format!("{p}.dynamic_selection.subbands_minus_one"),
                [4, 6, 8],
            )?;
            self.hoa_mut().dynamic_selection_subbands_minus_one = Some(bands);
            let bands = bands.value + 1;
            if bands > 8 {
                return self.invalid(
                    "hoa-subband-count",
                    "dynamic selection has more than eight subbands",
                );
            }
            self.report
                .derived
                .insert(format!("{p}.dynamic_selection.subbands"), json!(bands));
        }
        self.hoa_mut().parameter_0 = Some(self.take_at(&format!("{p}.parameter_0"), 2)?);
        self.hoa_mut().parameter_1 = Some(self.take_at(&format!("{p}.parameter_1"), 2)?);
        let value = self.take_at(&format!("{p}.parameter_2_minus_six"), 2)?;
        self.hoa_mut().parameter_2_minus_six = Some(value);
        let value = value.value + 6;
        self.report
            .derived
            .insert(format!("{p}.parameter_2"), json!(value));
        let (order, coefficients) = if full_order {
            let order = self.esc_at(&format!("{p}.order"), [4, 6, 8])?;
            self.hoa_mut().order = Some(order);
            (order.value, self.hoa_square(order.value)?)
        } else {
            let count = self.esc_at(&format!("{p}.coefficient_count_minus_one"), [7, 12, 17])?;
            self.hoa_mut().coefficient_count_minus_one = Some(count);
            let count = count.value + 1;
            if count > 121 {
                return self.invalid("hoa-coefficient-count", "HOA coefficient count exceeds 121");
            }
            // ceil(sqrt(count)) - 1, without rounding or a floating-point ABI.
            ((count - 1).isqrt(), count)
        };
        self.report
            .derived
            .insert(format!("{p}.order"), json!(order));
        self.report
            .derived
            .insert(format!("{p}.coefficient_count"), json!(coefficients));
        self.hoa_mut().coefficient_count = Some(coefficients);
        let salient = self.esc_at(&format!("{p}.max_salient_components"), [4, 6, 8])?;
        self.hoa_mut().max_salient_components = Some(salient);
        let salient = salient.value;
        if salient > coefficients {
            return self.invalid(
                "hoa-component-count",
                "salient component count exceeds HOA coefficients",
            );
        }
        let ambient = self.take_at(
            &format!("{p}.ambient_components_encoded"),
            index_width(coefficients),
        )?;
        self.hoa_mut().ambient_components_encoded = Some(ambient);
        let ambient = ambient.value + u64::from(salient == 0);
        if ambient > coefficients {
            return self.invalid(
                "hoa-component-count",
                "ambient component count exceeds HOA coefficients",
            );
        }
        let core = salient.checked_add(ambient).ok_or_else(|| {
            ParseError::new(self.pos(), "overflow", "HOA core channel count overflow")
        })?;
        self.report
            .derived
            .insert(format!("{p}.ambient_components"), json!(ambient));
        self.report
            .derived
            .insert(format!("{p}.core_channels"), json!(core));
        let salient = self.count(
            salient,
            4 + if full_order {
                index_width(order + 1)
            } else {
                0
            },
        )?;
        for i in 0..salient {
            let q = format!("{p}.salient[{i}]");
            let bands = self.esc_at(&format!("{q}.subbands_minus_one"), [4, 6, 8])?;
            self.hoa_mut().salient.push(HoaSalientDeclaration {
                subbands_minus_one: Some(bands),
                order: None,
            });
            let bands = bands.value + 1;
            if bands > 16 {
                return self.invalid(
                    "hoa-subband-count",
                    "salient component has more than sixteen subbands",
                );
            }
            self.report
                .derived
                .insert(format!("{q}.subbands"), json!(bands));
            let count = if full_order {
                let suborder = self.take_at(&format!("{q}.order"), index_width(order + 1))?;
                let declared = self.hoa_mut().salient.last_mut().expect("salient declared");
                declared.order = Some(suborder);
                self.hoa_square(suborder.value)?
            } else {
                coefficients
            };
            if count > coefficients {
                return self.invalid(
                    "hoa-order",
                    "salient component order exceeds the overall HOA order",
                );
            }
            self.report
                .derived
                .insert(format!("{q}.coefficient_count"), json!(count));
        }
        let ambient_count = self.count(ambient, 0)?;
        let mut selected: Vec<u64> = (0..ambient).collect();
        let explicit = self.flag_at(&format!("{p}.ambient_selection_present"))?;
        self.hoa_mut().ambient_selection_present = Some(explicit);
        if explicit.value {
            let mut limit = coefficients;
            for i in (0..ambient_count).rev() {
                let value =
                    self.take(&format!("{p}.ambient_selection[{i}]"), index_width(limit))?;
                if value >= coefficients {
                    return self.invalid(
                        "hoa-selection",
                        "ambient selection index exceeds HOA coefficients",
                    );
                }
                selected[i] = value;
                if value == i as u64 {
                    break;
                }
                limit = value + 1;
            }
        }
        self.report
            .derived
            .insert(format!("{p}.ambient_selection"), json!(selected));
        self.hoa_mut().ambient_selection = Some(selected);
        if ambient > 3 || conditional {
            let present = self.flag_at(&format!("{p}.parameter_3_present"))?;
            self.hoa_mut().parameter_3_present = Some(present);
            if present.value {
                let value = self.take(&format!("{p}.parameter_3_minus_one"), 2)? + 1;
                self.report
                    .derived
                    .insert(format!("{p}.parameter_3"), json!(value));
                self.hoa_mut().parameter_3 = Some(value);
            }
        }
        let tces = self.esc_at(&format!("{p}.tce_count"), [5, 10, 16])?;
        self.hoa_mut().tce_count = Some(tces);
        let tces = self.count(tces.value, 3)?;
        let mut transported = 0u64;
        for i in 0..tces {
            let kind = self.take_at(&format!("{p}.tce[{i}].type"), 3)?;
            self.hoa_mut().tce_types.push(kind);
            let kind = kind.value;
            let channels = match kind {
                0 | 3 | 4 => 1,
                1 => 2,
                6 => 0,
                _ => return self.stop(format!("unverified HOA TCE type {kind}")),
            };
            transported = transported.checked_add(channels).ok_or_else(|| {
                ParseError::new(self.pos(), "overflow", "HOA TCE channel sum overflow")
            })?;
        }
        if transported < core {
            return self.invalid(
                "hoa-tce-channels",
                "TCEs provide fewer channels than the HOA core requires",
            );
        }
        self.report
            .derived
            .insert(format!("{p}.transport_channels"), json!(transported));
        let custom = self.flag(&format!("{p}.custom_layout_present"))?;
        let (family, channels) = if custom {
            let channels = self.take(&format!("{p}.layout_channels_minus_one"), 16)? + 1;
            self.component_range(start, channels, total)?;
            let count = self.count(channels, 32)?;
            let mut labels = Vec::with_capacity(count);
            for i in 0..count {
                labels.push(self.take(&format!("{p}.channel_labels[{i}]"), 32)?);
            }
            self.report
                .derived
                .insert(format!("{component}.channel_labels"), json!(labels));
            self.hoa_mut().channel_labels = Some(labels);
            self.report
                .derived
                .insert(format!("{p}.layout_channels"), json!(channels));
            (0, channels)
        } else {
            let family = self.take(&format!("{p}.layout_family"), 16)?;
            let channels = self.take(&format!("{p}.layout_channels"), 16)?;
            if family <= 1 {
                return self.invalid(
                    "hoa-layout",
                    "HOA tagged layouts cannot declare descriptions or a channel bitmap",
                );
            }
            (family, channels)
        };
        // Check the shared ASC bounds before a remapping branch can stop parsing.
        self.component_range(start, channels, total)?;
        let tag = if custom { 0 } else { (family << 16) | channels };
        self.report
            .derived
            .insert(format!("{component}.layout_tag"), json!(tag));
        self.component_mut().layout_tag = Some(tag);
        if matches!(family, 190 | 191) {
            self.report
                .derived
                .insert(format!("{component}.ambisonic_channel_order"), json!("ACN"));
            self.report.derived.insert(
                format!("{component}.ambisonic_normalization"),
                json!(if family == 190 { "SN3D" } else { "N3D" }),
            );
            let root = channels.isqrt();
            if root.checked_mul(root) == Some(channels) {
                self.report
                    .derived
                    .insert(format!("{component}.ambisonic_order"), json!(root - 1));
            }
        }
        if self.flag(&format!("{p}.remapping_present"))? {
            if core > channels {
                return self.invalid(
                    "hoa-remapping",
                    "HOA remapping core exceeds the output channel count",
                );
            }
            let width = index_width(channels);
            let count = self.count(core, width)?;
            let start = self.pos();
            let mut links = Vec::with_capacity(count);
            for i in 0..count {
                let value = self.take(&format!("{p}.remapping[{i}]"), width)?;
                self.hoa_mut().remapping.push(value);
                if value >= core {
                    return self.invalid(
                        "hoa-remapping",
                        "HOA remapping index exceeds core channel count",
                    );
                }
                links.push(value as usize);
            }
            let effective = effective_remapping(&links).map_err(|i| {
                ParseError::new(
                    start + i * width,
                    "hoa-remapping-cycle",
                    "HOA remapping contains a nonterminating link cycle",
                )
            })?;
            self.report
                .derived
                .insert(format!("{p}.remapping_core_to_transport"), json!(effective));
            self.hoa_mut().remapping_core_to_transport =
                Some(effective.iter().map(|&v| v as u64).collect());
            // The fixed cookie core prefix is active. The serialized output
            // tail is consumed even if its values exceed the active domain.
            let tail = self.count(channels - core, width)?;
            for i in 0..tail {
                let value = self.take(&format!("{p}.remapping_tail[{i}]"), width)?;
                self.hoa_mut().remapping_tail.push(value);
            }
        }
        Ok(channels)
    }
}

#[cfg(test)]
mod remapping_tests {
    use super::effective_remapping;
    #[test]
    fn canonical_mappings_are_transport_to_core_and_noncanonical_forms_are_bounded() {
        fn permutations(values: &mut [usize], at: usize) {
            if at == values.len() {
                let actual = effective_remapping(values).unwrap();
                for (transport, &core) in values.iter().enumerate() {
                    assert_eq!(actual[core], transport);
                }
            } else {
                for i in at..values.len() {
                    values.swap(at, i);
                    permutations(values, at + 1);
                    values.swap(at, i);
                }
            }
        }
        permutations(&mut [0, 1, 2, 3], 0);
        assert_eq!(effective_remapping(&[0, 0, 2, 3]).unwrap(), [1, 0, 2, 3]);
        assert_eq!(effective_remapping(&[0, 0, 0, 0]).unwrap(), [3, 0, 1, 2]);
        assert_eq!(effective_remapping(&[1, 1, 2, 3]), Err(0));
        assert_eq!(effective_remapping(&[0]).unwrap(), [0]);
        assert!(effective_remapping(&[2, 0]).is_err());
    }
}
