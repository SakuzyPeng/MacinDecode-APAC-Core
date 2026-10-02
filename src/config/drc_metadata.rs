//! Passive APAC version-8 loudness equalization and EQ declarations.
//! Values are reported in their encoded units; no filter or gain is applied.
use super::{
    drc::Downmix,
    parser::{PResult, Parser},
};

impl Parser<'_> {
    fn drc_id_list(&mut self, p: &str, width: usize) -> PResult<()> {
        if self.flag(&format!("{p}.present"))? {
            self.take(&format!("{p}.id"), width)?;
            self.drc_additional_ids(p, width)?;
        }
        Ok(())
    }
    fn drc_additional_ids(&mut self, p: &str, width: usize) -> PResult<()> {
        if self.flag(&format!("{p}.additional_present"))? {
            let count = self.drc_count(&format!("{p}.additional_count"), width, width)?;
            for i in 0..count {
                self.take(&format!("{p}.additional[{i}]"), width)?;
            }
        }
        Ok(())
    }
    pub(super) fn drc_loudness_eq(&mut self, p: &str, channels: usize) -> PResult<()> {
        let count = self.drc_count(&format!("{p}.count"), 6, 19)?;
        for i in 0..count {
            let q = format!("{p}.instructions[{i}]");
            self.drc_presets(&format!("{q}.presets"))?;
            self.take(&format!("{q}.set_id"), 4)?;
            self.take(&format!("{q}.location"), 4)?;
            for (name, width) in [("downmix", 7), ("drc", 6), ("eq", 6)] {
                self.drc_id_list(&format!("{q}.{name}"), width)?;
            }
            self.flag(&format!("{q}.loudness_after_drc"))?;
            self.flag(&format!("{q}.loudness_after_eq"))?;
            let mut groups = 1;
            if !self.flag(&format!("{q}.all_channels"))? {
                groups = 0;
                for c in 0..channels {
                    groups =
                        groups.max(self.take(&format!("{q}.channel_groups[{c}]"), 6)? as usize);
                }
            }
            let explicit_gain_sequence = self.flag(&format!("{q}.group_sequence_present"))?;
            for group in 0..groups {
                let g = format!("{q}.groups[{group}]");
                if explicit_gain_sequence {
                    self.take(&format!("{g}.sequence_index"), 6)?;
                }
                let count = self.drc_count(&format!("{g}.gain_count"), 6, 25)?;
                for j in 0..count {
                    let b = format!("{g}.gains[{j}]");
                    self.take(&format!("{b}.sequence_index"), 6)?;
                    if self.flag(&format!("{b}.characteristic_format"))? {
                        self.take(&format!("{b}.characteristic_code"), 7)?;
                    } else {
                        self.take(&format!("{b}.left_characteristic_index"), 4)?;
                        self.take(&format!("{b}.right_characteristic_index"), 4)?;
                    }
                    self.take(&format!("{b}.frequency_range"), 6)?;
                    self.take(&format!("{b}.scaling_encoded"), 3)?;
                    self.take(&format!("{b}.offset_encoded"), 5)?;
                }
            }
        }
        Ok(())
    }
    pub(super) fn drc_eq(
        &mut self,
        p: &str,
        channels: usize,
        downmixes: &[Downmix],
    ) -> PResult<()> {
        let (blocks, subbands) = self.drc_eq_coefficients(&format!("{p}.coefficients"))?;
        let count = self.drc_count(&format!("{p}.instruction_count"), 6, 36)?;
        for i in 0..count {
            let q = format!("{p}.instructions[{i}]");
            self.drc_presets(&format!("{q}.presets"))?;
            self.take(&format!("{q}.set_id"), 6)?;
            self.take(&format!("{q}.complexity_level"), 4)?;
            let count = self.drc_downmix_target(&q, channels, downmixes, 7)?;
            self.take(&format!("{q}.drc.id"), 6)?;
            self.drc_additional_ids(&format!("{q}.drc"), 6)?;
            self.take(&format!("{q}.purpose"), 16)?;
            if self.flag(&format!("{q}.depends_on_set_present"))? {
                self.take(&format!("{q}.depends_on_set_id"), 6)?;
            } else {
                self.flag(&format!("{q}.no_independent_use"))?;
            }
            let mut groups = Vec::new();
            for c in 0..count {
                let group = self.take(&format!("{q}.channel_groups[{c}]"), 7)?;
                if !groups.contains(&group) {
                    groups.push(group);
                }
            }
            if self.flag(&format!("{q}.time_domain_present"))? {
                for (g, _) in groups.iter().enumerate() {
                    let r = format!("{q}.cascades[{g}]");
                    if self.flag(&format!("{r}.gain_present"))? {
                        self.take(&format!("{r}.gain_encoded"), 10)?;
                    }
                    let count = self.drc_count(&format!("{r}.block_count"), 4, 7)?;
                    for k in 0..count {
                        if self.take(&format!("{r}.block_indices[{k}]"), 7)? as usize >= blocks {
                            return self.invalid(
                                "eq-block-reference",
                                "EQ cascade references an undeclared block",
                            );
                        }
                    }
                }
                if self.flag(&format!("{q}.phase_alignment_present"))? {
                    for i in 0..groups.len() {
                        for j in i + 1..groups.len() {
                            self.flag(&format!("{q}.phase_alignment[{i}][{j}]"))?;
                        }
                    }
                }
            }
            if self.flag(&format!("{q}.subband_gains_present"))? {
                for i in 0..groups.len() {
                    if self.take(&format!("{q}.subband_indices[{i}]"), 6)? as usize >= subbands {
                        return self.invalid(
                            "eq-subband-reference",
                            "EQ instruction references undeclared subband gains",
                        );
                    }
                }
            }
            if self.flag(&format!("{q}.transition_duration_present"))? {
                self.take(&format!("{q}.transition_duration_encoded"), 5)?;
            }
        }
        Ok(())
    }
    fn drc_eq_coefficients(&mut self, p: &str) -> PResult<(usize, usize)> {
        if self.flag(&format!("{p}.delay_max_present"))? {
            self.take(&format!("{p}.delay_max_encoded"), 8)?;
        }
        let blocks = self.drc_count(&format!("{p}.block_count"), 6, 6)?;
        let mut references = Vec::new();
        for i in 0..blocks {
            let q = format!("{p}.blocks[{i}]");
            let count = self.drc_count(&format!("{q}.element_count"), 6, 7)?;
            for j in 0..count {
                let r = format!("{q}.elements[{j}]");
                references.push(self.take(&format!("{r}.index"), 6)? as usize);
                if self.flag(&format!("{r}.gain_present"))? {
                    self.take(&format!("{r}.gain_encoded"), 10)?;
                }
            }
        }
        let elements = self.drc_count(&format!("{p}.element_count"), 6, 9)?;
        if references.iter().any(|&i| i >= elements) {
            return self.invalid(
                "eq-element-reference",
                "EQ block references an undeclared element",
            );
        }
        for i in 0..elements {
            let q = format!("{p}.elements[{i}]");
            if self.flag(&format!("{q}.fir"))? {
                let order = self.take(&format!("{q}.order"), 7)? as usize;
                self.flag(&format!("{q}.symmetry"))?;
                for j in 0..=order / 2 {
                    self.take(&format!("{q}.coefficients[{j}]"), 11)?;
                }
            } else {
                let unit = self.take(&format!("{q}.unit_zero_pairs"), 3)? as usize * 2;
                let zr = self.take(&format!("{q}.real_zero_count"), 6)? as usize;
                let zc = self.take(&format!("{q}.complex_zero_count"), 6)? as usize;
                let pr = self.take(&format!("{q}.real_pole_count"), 4)? as usize;
                let pc = self.take(&format!("{q}.complex_pole_count"), 4)? as usize;
                for j in 0..unit {
                    self.flag(&format!("{q}.unit_zero_signs[{j}]"))?;
                }
                for (kind, count, width) in [
                    ("real_zeros", zr, 1),
                    ("complex_zeros", zc, 7),
                    ("real_poles", pr, 1),
                    ("complex_poles", pc, 7),
                ] {
                    for j in 0..count {
                        self.take(&format!("{q}.{kind}[{j}].radius_encoded"), 7)?;
                        self.take(&format!("{q}.{kind}[{j}].angle_or_sign_encoded"), width)?;
                    }
                }
            }
        }
        let subbands = self.drc_count(&format!("{p}.subband_gain_count"), 6, 1)?;
        if subbands == 0 {
            return Ok((blocks, subbands));
        }
        let spline = self.flag(&format!("{p}.subband_spline"))?;
        let format = self.take(&format!("{p}.subband_format"), 4)? as usize;
        let bands = match format {
            1..=6 => [32, 39, 64, 71, 128, 135][format - 1],
            7 => self.take(&format!("{p}.subband_count_minus_one"), 8)? as usize + 1,
            _ => return self.invalid("eq-subband-format", "reserved EQ subband format"),
        };
        for i in 0..subbands {
            let q = format!("{p}.subband_gains[{i}]");
            if spline {
                let count = self.take(&format!("{q}.node_count_minus_two"), 5)? as usize + 2;
                for j in 0..count {
                    if !self.flag(&format!("{q}.nodes[{j}].zero_slope"))? {
                        self.take(&format!("{q}.nodes[{j}].slope_encoded"), 4)?;
                    }
                }
                for j in 1..count {
                    self.take(&format!("{q}.nodes[{j}].frequency_delta_minus_one"), 4)?;
                }
                let prefix = self.take(&format!("{q}.initial_gain_prefix"), 2)? as usize;
                self.take(&format!("{q}.initial_gain_encoded"), [5, 4, 4, 3][prefix])?;
                for j in 1..count {
                    self.take(&format!("{q}.nodes[{j}].gain_delta_encoded"), 5)?;
                }
            } else {
                for j in 0..bands {
                    self.take(&format!("{q}.gains_encoded[{j}]"), 9)?;
                }
            }
        }
        Ok((blocks, subbands))
    }
}
