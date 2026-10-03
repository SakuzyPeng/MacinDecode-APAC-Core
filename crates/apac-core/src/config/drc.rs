//! APAC's UniDRC header (internal version 8, header payload type 4).
//! Wire values remain encoded unless a conversion has independent evidence.
use super::{
    DrcCoefficients, DrcGainSet,
    parser::{PResult, Parser},
};
use crate::prelude::*;
use crate::record::{DigestUnit, FieldValue};

const ROOT: &str = "ancillary.loudness_drc";

pub(super) struct Downmix {
    pub id: u64,
    pub channels: usize,
}

struct Instruction {
    id: u64,
    dependency: Option<u64>,
}

struct Coefficients {
    location: u64,
    left_count: usize,
    right_count: usize,
    shape_count: usize,
    bands: Vec<usize>,
}

impl Parser<'_> {
    pub(super) fn drc_count(
        &mut self,
        name: impl core::fmt::Display,
        width: usize,
        minimum_bits: usize,
    ) -> PResult<usize> {
        let count = self.take(name, width)?;
        self.count(count, minimum_bits)
    }

    // These references use zero as a sentinel; positive values are one-based.
    fn drc_reference(
        &mut self,
        name: impl core::fmt::Display,
        width: usize,
        count: usize,
    ) -> PResult<u64> {
        let value = self.take(&name, width)?;
        if value > count as u64 {
            return self.invalid("drc-reference", format!("{name} exceeds {count} entries"));
        }
        Ok(value)
    }

    pub(super) fn loudness_drc(&mut self, channels: u64) -> PResult<()> {
        self.with_drc_zone(|p| p.drc_header(channels, false))
    }

    pub(super) fn drc_header(&mut self, channels: u64, _allow_reuse: bool) -> PResult<()> {
        // SetClientInfo selects internal version 8 for APAC's feature profile.
        // This is contextual syntax, not an additional version field in the cookie.
        let header = self.flag_at(format_args!("{ROOT}.header_present"))?;
        self.config.ancillary.drc.header_present = Some(header);
        if !header.value {
            return Ok(());
        }
        let config = self.flag_at(format_args!("{ROOT}.config_present"))?;
        self.config.ancillary.drc.config_present = Some(config);
        if !config.value {
            return self.drc_loudness();
        }
        if self.flag(format_args!("{ROOT}.sample_rate_present"))? {
            let rate = self.take(format_args!("{ROOT}.sample_rate_minus_1000"), 18)? + 1000;
            if self.config.global.sample_rate_hz != Some(rate) {
                return self.invalid("drc-sample-rate", "DRC and global sample rates disagree");
            }
            self.derive(
                format_args!("{ROOT}.sample_rate_hz"),
                FieldValue::from(rate),
            );
        }
        let explicit_layout = self.flag_at(format_args!("{ROOT}.channel_layout_present"))?;
        self.config.ancillary.drc.channel_layout_present = Some(explicit_layout);
        let explicit_layout = explicit_layout.value;
        let base_channels = self.take_at(
            format_args!("{ROOT}.base_channel_count"),
            if explicit_layout { 7 } else { 10 },
        )?;
        self.config.ancillary.drc.base_channel_count = Some(base_channels);
        let base_channels = base_channels.value;
        if explicit_layout && self.flag(format_args!("{ROOT}.layout.signalling_present"))? {
            let layout = self.take(format_args!("{ROOT}.layout.defined_layout"), 8)?;
            if layout == 0 {
                self.count(base_channels, 7)?;
                for index in 0..base_channels {
                    self.take(format_args!("{ROOT}.layout.speaker_positions[{index}]"), 7)?;
                }
            } else if let Some(&count) = [
                0, 1, 2, 3, 4, 5, 6, 7, 2, 3, 4, 7, 8, 24, 8, 12, 10, 12, 14, 12, 14,
            ]
            .get(layout as usize)
                && count != base_channels
            {
                return self.invalid(
                    "drc-layout-count",
                    "defined DRC layout and base channel count disagree",
                );
            }
        }
        if base_channels != channels {
            return self.invalid(
                "drc-channel-count",
                "DRC and global channel counts disagree",
            );
        }
        let mut downmixes = Vec::new();
        let downmix = self.flag_at(format_args!("{ROOT}.downmix_instructions_present"))?;
        self.config.ancillary.drc.downmix_instructions_present = Some(downmix);
        if downmix.value {
            let count = self.drc_count(format_args!("{ROOT}.downmix_instruction_count"), 7, 23)?;
            for i in 0..count {
                let p = format!("{ROOT}.downmix_instructions[{i}]");
                let id = self.take(format_args!("{p}.id"), 7)?;
                let target = self.take(format_args!("{p}.target_channel_count"), 7)?;
                if target == 0 {
                    return self.invalid("drc-downmix-count", "downmix has no target channels");
                }
                downmixes.push(Downmix {
                    id,
                    channels: target as usize,
                });
                self.take(format_args!("{p}.target_layout"), 8)?;
                if self.flag(format_args!("{p}.coefficients_present"))? {
                    self.take(format_args!("{p}.offset_encoded"), 4)?;
                    let entries = self.count(target * channels, 5)?;
                    for k in 0..entries {
                        self.take(format_args!("{p}.coefficients[{k}]"), 5)?;
                    }
                }
            }
        }
        let bit_offset = self.pos();
        let count = self.drc_count(format_args!("{ROOT}.coefficient_count"), 3, 20)?;
        self.config.ancillary.drc.coefficient_count = Some(super::Located {
            value: count as u64,
            bit_offset,
        });
        let mut coefficients = Vec::with_capacity(count);
        for i in 0..count {
            let entry = self.drc_coefficients(&format!("{ROOT}.coefficients[{i}]"))?;
            coefficients.push(entry);
        }
        let count = self.drc_count(format_args!("{ROOT}.instruction_count"), 8, 36)?;
        let mut instructions = Vec::with_capacity(count);
        for i in 0..count {
            instructions.push(self.drc_instruction(
                &format!("{ROOT}.instructions[{i}]"),
                channels as usize,
                &coefficients,
                &downmixes,
            )?);
        }
        for instruction in &instructions {
            let mut next = instruction.dependency;
            let mut visited = vec![instruction.id];
            while let Some(id) = next {
                if visited.contains(&id) {
                    return self.invalid("drc-dependency", "cyclic DRC set dependency");
                }
                visited.push(id);
                let Some(target) = instructions.iter().find(|i| i.id == id) else {
                    return self.invalid("drc-dependency", "DRC dependency has no declared set");
                };
                next = target.dependency;
            }
        }
        let present = self.flag_at(format_args!("{ROOT}.loudness_eq_present"))?;
        self.config.ancillary.drc.loudness_eq_present = Some(present);
        if present.value {
            self.drc_loudness_eq(&format!("{ROOT}.loudness_eq"), channels as usize)?;
        }
        let present = self.flag_at(format_args!("{ROOT}.eq_present"))?;
        self.config.ancillary.drc.eq_present = Some(present);
        if present.value {
            self.drc_eq(&format!("{ROOT}.eq"), channels as usize, &downmixes)?;
        }
        let present = self.flag_at(format_args!("{ROOT}.scene_extension_present"))?;
        self.config.ancillary.drc.scene_extension_present = Some(present);
        if present.value {
            self.drc_extensions(&format!("{ROOT}.config_extensions"))?;
        }
        self.drc_loudness()?;
        Ok(())
    }

    fn drc_characteristics(&mut self, prefix: &str) -> PResult<usize> {
        if !self.flag(format_args!("{prefix}_present"))? {
            return Ok(0);
        }
        let count = self.drc_count(format_args!("{prefix}_count"), 4, 16)?;
        for i in 0..count {
            let p = format!("{prefix}[{i}]");
            if self.flag(format_args!("{p}.node_format"))? {
                let nodes = self.take(format_args!("{p}.node_count_minus_one"), 2)? + 1;
                let nodes = self.count(nodes, 13)?;
                for j in 0..nodes {
                    self.take(format_args!("{p}.nodes[{j}].level_encoded"), 5)?;
                    self.take(format_args!("{p}.nodes[{j}].gain_encoded"), 8)?;
                }
            } else {
                for (field, width) in [
                    ("gain_encoded", 6),
                    ("ratio_encoded", 4),
                    ("exponent_encoded", 4),
                ] {
                    self.take(format_args!("{p}.{field}"), width)?;
                }
                self.flag(format_args!("{p}.flip_sign"))?;
            }
        }
        Ok(count)
    }

    fn drc_coefficients(&mut self, p: &str) -> PResult<Coefficients> {
        let location = self.take_at(format_args!("{p}.location"), 4)?;
        self.config
            .ancillary
            .drc
            .coefficients
            .push(DrcCoefficients {
                location: Some(location),
                ..DrcCoefficients::default()
            });
        let location = location.value;
        let frame_size = self.flag_at(format_args!("{p}.frame_size_present"))?;
        self.drc_coefficients_mut().frame_size_present = Some(frame_size);
        if frame_size.value {
            let frames = self.take_at(format_args!("{p}.frame_size_minus_one"), 15)?;
            self.drc_coefficients_mut().frame_size_minus_one = Some(frames);
            let frames = frames.value + 1;
            self.derive(format_args!("{p}.frame_samples"), FieldValue::from(frames));
        }
        // The APAC header calls the coefficients reader with coefficient version 1.
        let left_count = self.drc_characteristics(&format!("{p}.left_characteristics"))?;
        let right_count = self.drc_characteristics(&format!("{p}.right_characteristics"))?;
        let mut shape_count = 0;
        if self.flag(format_args!("{p}.shape_filters_present"))? {
            shape_count = self.drc_count(format_args!("{p}.shape_filter_count"), 4, 4)?;
            for i in 0..shape_count {
                for j in 0..4 {
                    let q = format!("{p}.shape_filters[{i}].filters[{j}]");
                    if self.flag(format_args!("{q}.present"))? {
                        self.take(format_args!("{q}.corner_encoded"), 3)?;
                        self.take(format_args!("{q}.strength_encoded"), 2)?;
                    }
                }
            }
        }
        let sequences = self.take_at(format_args!("{p}.gain_sequence_count"), 6)?;
        self.drc_coefficients_mut().gain_sequence_count = Some(sequences);
        let sequences = sequences.value;
        let bit_offset = self.pos();
        let sets = self.drc_count(format_args!("{p}.gain_set_count"), 6, 6)?;
        self.drc_coefficients_mut().gain_set_count = Some(super::Located {
            value: sets as u64,
            bit_offset,
        });
        let mut bands_per_set = Vec::with_capacity(sets);
        let mut next_sequence = 0;
        for i in 0..sets {
            let q = format!("{p}.gain_sets[{i}]");
            let profile = self.take_at(format_args!("{q}.coding_profile"), 2)?;
            self.drc_coefficients_mut().gain_sets.push(DrcGainSet {
                coding_profile: Some(profile),
                ..DrcGainSet::default()
            });
            let profile = profile.value;
            self.drc_gain_set_mut().interpolation_type =
                Some(self.flag_at(format_args!("{q}.interpolation_type"))?);
            self.drc_gain_set_mut().full_frame =
                Some(self.flag_at(format_args!("{q}.full_frame"))?);
            self.drc_gain_set_mut().time_alignment =
                Some(self.flag_at(format_args!("{q}.time_alignment"))?);
            let delta_present = self.flag_at(format_args!("{q}.time_delta_min_present"))?;
            self.drc_gain_set_mut().time_delta_min_present = Some(delta_present);
            if delta_present.value {
                let delta = self.take_at(format_args!("{q}.time_delta_min_minus_one"), 11)?;
                self.drc_gain_set_mut().time_delta_min_minus_one = Some(delta);
                let delta = delta.value + 1;
                self.derive(format_args!("{q}.time_delta_min"), FieldValue::from(delta));
            }
            if profile == 3 {
                if next_sequence >= sequences {
                    return self.invalid(
                        "drc-reference",
                        "constant gain sequence exceeds declared count",
                    );
                }
                self.derive(
                    format_args!("{q}.bands[0].sequence_index"),
                    FieldValue::from(next_sequence),
                );
                self.drc_gain_set_mut()
                    .band_sequence_indices
                    .push(next_sequence);
                next_sequence += 1;
                bands_per_set.push(1);
                continue;
            }
            let bit_offset = self.pos();
            let bands = self.drc_count(format_args!("{q}.band_count"), 4, 2)?;
            self.drc_gain_set_mut().band_count = Some(super::Located {
                value: bands as u64,
                bit_offset,
            });
            if bands == 0 {
                return self.invalid("drc-band-count", "gain set has no bands");
            }
            bands_per_set.push(bands);
            let crossover = bands > 1 && self.flag(format_args!("{q}.band_type"))?;
            for j in 0..bands {
                let band = format!("{q}.bands[{j}]");
                let sequence = if self.flag(format_args!("{band}.sequence_index_present"))? {
                    self.take(format_args!("{band}.sequence_index"), 6)?
                } else {
                    next_sequence
                };
                if sequence >= sequences {
                    return self.invalid(
                        "drc-reference",
                        "gain sequence index exceeds declared count",
                    );
                }
                next_sequence = sequence + 1;
                self.derive(
                    format_args!("{band}.sequence_index"),
                    FieldValue::from(sequence),
                );
                self.drc_gain_set_mut().band_sequence_indices.push(sequence);
                if self.flag(format_args!("{band}.characteristic_present"))? {
                    if self.flag(format_args!("{band}.characteristic_format"))? {
                        self.take(format_args!("{band}.characteristic_code"), 7)?;
                    } else {
                        self.drc_reference(format_args!("{band}.left_index"), 4, left_count)?;
                        self.drc_reference(format_args!("{band}.right_index"), 4, right_count)?;
                    }
                }
            }
            for j in 1..bands {
                self.take(
                    format_args!("{q}.bands[{j}].boundary_encoded"),
                    if crossover { 4 } else { 10 },
                )?;
            }
        }
        Ok(Coefficients {
            location,
            left_count,
            right_count,
            shape_count,
            bands: bands_per_set,
        })
    }

    pub(super) fn drc_presets(&mut self, p: &str) -> PResult<()> {
        let count = self.drc_count(format_args!("{p}.count"), 4, 4)?;
        for i in 0..count {
            self.take(format_args!("{p}.ids[{i}]"), 4)?;
        }
        Ok(())
    }

    fn drc_instruction(
        &mut self,
        p: &str,
        channels: usize,
        coefficients: &[Coefficients],
        downmixes: &[Downmix],
    ) -> PResult<Instruction> {
        self.flag(format_args!("{p}.flag_a"))?;
        self.drc_presets(&format!("{p}.presets"))?;
        let id = self.take(format_args!("{p}.set_id"), 6)?;
        self.take(format_args!("{p}.complexity_level"), 4)?;
        let location = self.take(format_args!("{p}.location"), 4)?;
        let channels = self.drc_downmix_target(p, channels, downmixes, 3)?;
        let effect = self.take(format_args!("{p}.effect"), 16)?;
        self.config.ancillary.drc.instruction_effects.push(effect);
        let special = effect & 0x8000 != 0;
        let ducking = effect & 0xc00 != 0 && !special;
        if special && effect & 0xc00 != 0 {
            return self.invalid(
                "drc-effect-combination",
                "special gain-only instructions cannot declare ducking modifiers",
            );
        }
        if !special && !ducking && self.flag(format_args!("{p}.limiter_peak_present"))? {
            self.take(format_args!("{p}.limiter_peak_encoded"), 8)?;
        }
        if !special && self.flag(format_args!("{p}.target_loudness_present"))? {
            self.take(format_args!("{p}.target_loudness_upper_encoded"), 6)?;
            if self.flag(format_args!("{p}.target_loudness_lower_present"))? {
                self.take(format_args!("{p}.target_loudness_lower_encoded"), 6)?;
            }
        }
        let dependency = if special {
            None
        } else if self.flag(format_args!("{p}.depends_on_set_present"))? {
            self.config.ancillary.drc.nested_declarations = true;
            Some(self.take(format_args!("{p}.depends_on_set_id"), 6)?)
        } else {
            self.flag(format_args!("{p}.no_independent_use"))?;
            None
        };
        if !special && self.flag(format_args!("{p}.requires_eq"))? {
            self.config.ancillary.drc.nested_declarations = true;
        }
        let coefficient = coefficients.iter().find(|c| c.location == location);
        if coefficient.is_none() && !special && !ducking {
            return self.invalid(
                "drc-reference",
                "instruction has no coefficients at its location",
            );
        }
        let mut indices = Vec::with_capacity(channels);
        let mut ducking_scales = Vec::with_capacity(channels);
        let mut groups = Vec::new();
        while indices.len() < channels {
            let channel = indices.len();
            let q = format!("{p}.channel_runs[{channel}]");
            let index = self.drc_reference(
                format_args!("{q}.gain_set_index_plus_one"),
                6,
                coefficient.map_or(63, |c| c.bands.len()),
            )?;
            let scale = if ducking && self.flag(format_args!("{q}.ducking_scaling_present"))? {
                Some(self.take(format_args!("{q}.ducking_scaling_encoded"), 4)?)
            } else {
                None
            };
            let repeat = if self.flag(format_args!("{q}.repeat_present"))? {
                self.take(format_args!("{q}.repeat_count_minus_one"), 5)? as usize + 1
            } else {
                0
            };
            if repeat >= channels - channel {
                return self.invalid(
                    "drc-channel-repeat",
                    "channel run exceeds the base channel count",
                );
            }
            indices.resize(channel + repeat + 1, index as i64 - 1);
            ducking_scales.resize(channel + repeat + 1, scale);
            if index != 0 && !groups.contains(&(index as usize - 1)) {
                groups.push(index as usize - 1);
            }
        }
        if ducking {
            let other = effect & 0x400 != 0;
            if other && (groups.len() != 1 || !indices.iter().any(|&i| i < 0)) {
                return self.invalid(
                    "drc-ducking-groups",
                    "duck-other requires one gain sequence and at least one recipient channel",
                );
            }
            let mut active = Vec::new();
            for (&index, &scale) in indices.iter().zip(&ducking_scales) {
                if (other && index < 0) || (!other && (0..61).contains(&index)) {
                    let group = (if other { -1 } else { index }, scale);
                    if !active.contains(&group) {
                        active.push(group);
                    }
                }
            }
            if active.is_empty() || active.len() > 63 {
                return self.invalid(
                    "drc-ducking-groups",
                    "ducking requires one to 63 active channel groups",
                );
            }
        }
        self.derive(
            format_args!("{p}.channel_gain_set_indices"),
            FieldValue::from(indices),
        );
        if special || ducking {
            return Ok(Instruction { id, dependency });
        }
        let coefficient = coefficient.expect("checked ordinary coefficient");
        for (group, &set) in groups.iter().enumerate() {
            let q = format!("{p}.groups[{group}]");
            self.derive(format_args!("{q}.gain_set_index"), FieldValue::from(set));
            for band in 0..coefficient.bands[set] {
                let b = format!("{q}.bands[{band}]");
                if self.flag(format_args!("{b}.parameter_a_present"))? {
                    self.take(format_args!("{b}.parameter_a"), 5)?;
                }
                if self.flag(format_args!("{b}.target_left_present"))? {
                    self.drc_reference(
                        format_args!("{b}.target_left_index"),
                        4,
                        coefficient.left_count,
                    )?;
                }
                if self.flag(format_args!("{b}.target_right_present"))? {
                    self.drc_reference(
                        format_args!("{b}.target_right_index"),
                        4,
                        coefficient.right_count,
                    )?;
                }
                if self.flag(format_args!("{b}.gain_scaling_present"))? {
                    self.take(format_args!("{b}.attenuation_scaling_encoded"), 4)?;
                    self.take(format_args!("{b}.amplification_scaling_encoded"), 4)?;
                }
                if self.flag(format_args!("{b}.gain_offset_present"))? {
                    self.take(format_args!("{b}.gain_offset_encoded"), 6)?;
                }
            }
            if coefficient.bands[set] == 1 && self.flag(format_args!("{q}.shape_filter_present"))? {
                self.drc_reference(
                    format_args!("{q}.shape_filter_index"),
                    4,
                    coefficient.shape_count,
                )?;
            }
        }
        Ok(Instruction { id, dependency })
    }

    pub(super) fn drc_downmix_target(
        &mut self,
        p: &str,
        channels: usize,
        downmixes: &[Downmix],
        count_width: usize,
    ) -> PResult<usize> {
        if !self.flag(format_args!("{p}.downmix_id_present"))? {
            return Ok(channels);
        }
        self.config.ancillary.drc.nested_declarations = true;
        let id = self.take(format_args!("{p}.downmix_id"), 7)?;
        let apply = self.flag(format_args!("{p}.apply_to_downmix"))?;
        let mut additional = 0;
        if self.flag(format_args!("{p}.additional_downmix_ids_present"))? {
            additional =
                self.drc_count(format_args!("{p}.additional_downmix_count"), count_width, 7)?;
            for i in 0..additional {
                self.take(format_args!("{p}.additional_downmix_ids[{i}]"), 7)?;
            }
        }
        let count = if !apply || (id == 0 && additional == 0) {
            channels
        } else if id == 127 || additional != 0 {
            1
        } else if let Some(downmix) = downmixes.iter().find(|d| d.id == id) {
            downmix.channels
        } else {
            return self.invalid(
                "drc-downmix-reference",
                "instruction refers to an undeclared downmix",
            );
        };
        if count == 0 || count > channels {
            return self.invalid(
                "drc-instruction-count",
                "instruction channel count exceeds the base layout",
            );
        }
        Ok(count)
    }

    fn drc_extensions(&mut self, p: &str) -> PResult<()> {
        for index in 0..4096 {
            let q = format!("{p}[{index}]");
            if self.take(format_args!("{q}.type"), 4)? == 0 {
                return Ok(());
            }
            let width = self.take(format_args!("{q}.length_width_minus_four"), 4)? as usize + 4;
            let length = self.take(format_args!("{q}.bits_minus_one"), width)? as usize + 1;
            let start = self.pos();
            if length > self.bits.remaining() {
                return Err(super::ParseError::new(
                    start,
                    "truncated",
                    "DRC extension exceeds input",
                )
                .into());
            }
            let mut data = Vec::with_capacity(length.div_ceil(8));
            let mut remaining = length;
            while remaining != 0 {
                let width = remaining.min(8);
                data.push((self.bits.read(width)? << (8 - width)) as u8);
                remaining -= width;
            }
            self.record(
                format_args!("{q}.payload"),
                start,
                FieldValue::digest(DigestUnit::Bits, length, &data),
            )?;
        }
        self.invalid("drc-extension-count", "too many DRC extensions")
    }

    fn drc_loudness(&mut self) -> PResult<()> {
        let p = format!("{ROOT}.loudness");
        self.flag(format_args!("{p}.flag_a"))?;
        self.flag(format_args!("{p}.flag_b"))?;
        let counts = [
            self.drc_count(format_args!("{p}.count_0"), 8, 7)?,
            self.drc_count(format_args!("{p}.count_1"), 8, 7)?,
        ];
        for (group, &count) in counts.iter().enumerate() {
            for i in 0..count {
                let q = format!("{p}.entries_{group}[{i}]");
                self.drc_presets(&format!("{q}.presets"))?;
                if self.flag(format_args!("{q}.parameter_id_present"))? {
                    self.take(format_args!("{q}.parameter_id"), 6)?;
                }
                if self.flag(format_args!("{q}.mp4_info_present"))? {
                    self.drc_mp4_loudness(&format!("{q}.mp4_info"))?;
                }
                if self.flag(format_args!("{q}.compositions_present"))? {
                    self.drc_composition_loudness(&format!("{q}.compositions"))?;
                }
            }
        }
        if self.flag(format_args!("{p}.sources_present"))? {
            let default = self.flag(format_args!("{p}.default_source"))?;
            let count = if default {
                1
            } else {
                self.drc_count(format_args!("{p}.source_count"), 8, 18)?
            };
            for i in 0..count {
                let q = format!("{p}.sources[{i}]");
                if !default {
                    self.take(format_args!("{q}.pairs[0].parameter_0"), 8)?;
                    self.take(format_args!("{q}.pairs[0].parameter_1"), 8)?;
                    if self.flag(format_args!("{q}.additional_pairs_present"))? {
                        let count =
                            self.drc_count(format_args!("{q}.additional_pair_count"), 8, 16)?;
                        for j in 1..=count {
                            self.take(format_args!("{q}.pairs[{j}].parameter_0"), 8)?;
                            self.take(format_args!("{q}.pairs[{j}].parameter_1"), 8)?;
                        }
                    }
                }
                if self.flag(format_args!("{q}.value_a_present"))? {
                    self.take(format_args!("{q}.value_a_encoded"), 8)?;
                    if self.flag(format_args!("{q}.value_b_present"))? {
                        self.take(format_args!("{q}.value_b_encoded"), 8)?;
                    }
                }
            }
        }
        let extensions = self.flag_at(format_args!("{p}.extensions_present"))?;
        self.config.ancillary.drc.loudness_extensions_present = Some(extensions);
        if extensions.value {
            self.drc_extensions(&format!("{p}.extensions"))?;
        }
        Ok(())
    }
    fn drc_coefficients_mut(&mut self) -> &mut DrcCoefficients {
        let coefficients = &mut self.config.ancillary.drc.coefficients;
        coefficients.last_mut().expect("DRC coefficients declared")
    }
    fn drc_gain_set_mut(&mut self) -> &mut DrcGainSet {
        let sets = &mut self.drc_coefficients_mut().gain_sets;
        sets.last_mut().expect("DRC gain set declared")
    }

    fn drc_composition_loudness(&mut self, p: &str) -> PResult<()> {
        // The ID fields include wildcards; their bit widths do not establish an
        // ordinary array index. Preserve their encoded values without guessing.
        self.take(format_args!("{p}.drc_set_id"), 6)?;
        self.take(format_args!("{p}.eq_set_id"), 6)?;
        self.take(format_args!("{p}.downmix_id"), 7)?;
        let compositions = self.drc_count(format_args!("{p}.composition_count"), 6, 0)?;
        let measurements = self.drc_count(format_args!("{p}.measurement_count"), 4, 24)?;
        for i in 0..measurements {
            let q = format!("{p}.measurements[{i}]");
            self.take(format_args!("{q}.method_definition"), 4)?;
            self.take(format_args!("{q}.measurement_system"), 4)?;
            self.take(format_args!("{q}.reliability"), 2)?;
            self.count(compositions as u64, 8)?;
            for j in 0..compositions {
                self.take(format_args!("{q}.values_encoded[{j}]"), 8)?;
            }
            let mixes = self.take(format_args!("{q}.mix_count_minus_one"), 6)? + 1;
            let mixes = self.count(mixes, 8 + compositions * 6)?;
            for j in 0..mixes {
                self.take(format_args!("{q}.mixes[{j}].value_encoded"), 8)?;
                for k in 0..compositions {
                    self.take(format_args!("{q}.mixes[{j}].parameters[{k}]"), 6)?;
                }
            }
        }
        Ok(())
    }

    fn drc_mp4_loudness(&mut self, p: &str) -> PResult<()> {
        self.take(format_args!("{p}.drc_set_id"), 6)?;
        self.take(format_args!("{p}.eq_set_id"), 6)?;
        self.take(format_args!("{p}.downmix_id"), 7)?;
        if self.flag(format_args!("{p}.sample_peak_present"))? {
            self.take(format_args!("{p}.sample_peak_encoded"), 12)?;
        }
        if self.flag(format_args!("{p}.true_peak_present"))? {
            self.take(format_args!("{p}.true_peak_encoded"), 12)?;
            self.take(format_args!("{p}.true_peak_system"), 4)?;
            self.take(format_args!("{p}.true_peak_reliability"), 2)?;
        }
        let count = self.drc_count(format_args!("{p}.measurement_count"), 4, 12)?;
        for i in 0..count {
            let q = format!("{p}.measurements[{i}]");
            let method = self.take(format_args!("{q}.method_definition"), 4)?;
            self.take(
                format_args!("{q}.value_encoded"),
                match method {
                    7 => 5,
                    8 => 2,
                    _ => 8,
                },
            )?;
            self.take(format_args!("{q}.measurement_system"), 4)?;
            self.take(format_args!("{q}.reliability"), 2)?;
        }
        Ok(())
    }
}
