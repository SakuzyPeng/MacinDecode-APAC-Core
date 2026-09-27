//! APAC's UniDRC header (internal version 8, header payload type 4).
//! Wire values remain encoded unless a conversion has independent evidence.
use super::parser::{PResult, Parser};
use serde_json::json;

const ROOT: &str = "ancillary.loudness_drc";

struct Coefficients {
    location: u64,
    left_count: usize,
    right_count: usize,
    shape_count: usize,
    bands: Vec<usize>,
}

impl Parser<'_> {
    fn drc_count(&mut self, name: &str, width: usize, minimum_bits: usize) -> PResult<usize> {
        let count = self.take(name, width)?;
        self.count(count, minimum_bits)
    }

    // These references use zero as a sentinel; positive values are one-based.
    fn drc_reference(&mut self, name: &str, width: usize, count: usize) -> PResult<u64> {
        let value = self.take(name, width)?;
        if value > count as u64 {
            return self.invalid("drc-reference", format!("{name} exceeds {count} entries"));
        }
        Ok(value)
    }

    pub(super) fn loudness_drc(&mut self, channels: u64) -> PResult<()> {
        self.drc_header(channels, false)
    }

    pub(super) fn drc_header(&mut self, channels: u64, allow_reuse: bool) -> PResult<()> {
        // SetClientInfo selects internal version 8 for APAC's feature profile.
        // This is contextual syntax, not an additional version field in the cookie.
        if !self.flag(&format!("{ROOT}.header_present"))? {
            return Ok(());
        }
        if !self.flag(&format!("{ROOT}.config_present"))? {
            if allow_reuse {
                return self.drc_loudness();
            }
            return self.stop("DRC header without a fresh configuration is not implemented");
        }
        if self.flag(&format!("{ROOT}.sample_rate_present"))? {
            let rate = self.take(&format!("{ROOT}.sample_rate_minus_1000"), 18)? + 1000;
            if self
                .report
                .derived
                .get("sample_rate_hz")
                .and_then(|v| v.as_u64())
                != Some(rate)
            {
                return self.invalid("drc-sample-rate", "DRC and global sample rates disagree");
            }
            self.report
                .derived
                .insert(format!("{ROOT}.sample_rate_hz"), json!(rate));
        }
        self.absent(&format!("{ROOT}.channel_layout_present"))?;
        let base_channels = self.take(&format!("{ROOT}.base_channel_count"), 10)?;
        if base_channels != channels {
            return self.invalid(
                "drc-channel-count",
                "DRC and global channel counts disagree",
            );
        }
        self.absent(&format!("{ROOT}.downmix_instructions_present"))?;
        let count = self.drc_count(&format!("{ROOT}.coefficient_count"), 3, 20)?;
        let mut coefficients = Vec::with_capacity(count);
        for i in 0..count {
            let entry = self.drc_coefficients(&format!("{ROOT}.coefficients[{i}]"))?;
            if coefficients
                .iter()
                .any(|c: &Coefficients| c.location == entry.location)
            {
                return self.stop("selection between multiple DRC coefficients at one location is not implemented");
            }
            coefficients.push(entry);
        }
        let count = self.drc_count(&format!("{ROOT}.instruction_count"), 8, 37)?;
        for i in 0..count {
            self.drc_instruction(
                &format!("{ROOT}.instructions[{i}]"),
                channels as usize,
                &coefficients,
            )?;
        }
        self.absent(&format!("{ROOT}.loudness_eq_present"))?;
        self.absent(&format!("{ROOT}.eq_present"))?;
        self.absent(&format!("{ROOT}.scene_extension_present"))?;
        self.drc_loudness()?;
        Ok(())
    }

    fn drc_characteristics(&mut self, prefix: &str) -> PResult<usize> {
        if !self.flag(&format!("{prefix}_present"))? {
            return Ok(0);
        }
        let count = self.drc_count(&format!("{prefix}_count"), 4, 16)?;
        for i in 0..count {
            let p = format!("{prefix}[{i}]");
            if self.flag(&format!("{p}.node_format"))? {
                let nodes = self.take(&format!("{p}.node_count_minus_one"), 2)? + 1;
                let nodes = self.count(nodes, 13)?;
                for j in 0..nodes {
                    self.take(&format!("{p}.nodes[{j}].level_encoded"), 5)?;
                    self.take(&format!("{p}.nodes[{j}].gain_encoded"), 8)?;
                }
            } else {
                for (field, width) in [
                    ("gain_encoded", 6),
                    ("ratio_encoded", 4),
                    ("exponent_encoded", 4),
                ] {
                    self.take(&format!("{p}.{field}"), width)?;
                }
                self.flag(&format!("{p}.flip_sign"))?;
            }
        }
        Ok(count)
    }

    fn drc_coefficients(&mut self, p: &str) -> PResult<Coefficients> {
        let location = self.take(&format!("{p}.location"), 4)?;
        if self.flag(&format!("{p}.frame_size_present"))? {
            let frames = self.take(&format!("{p}.frame_size_minus_one"), 15)? + 1;
            self.report
                .derived
                .insert(format!("{p}.frame_samples"), json!(frames));
        }
        // The APAC header calls the coefficients reader with coefficient version 1.
        let left_count = self.drc_characteristics(&format!("{p}.left_characteristics"))?;
        let right_count = self.drc_characteristics(&format!("{p}.right_characteristics"))?;
        let mut shape_count = 0;
        if self.flag(&format!("{p}.shape_filters_present"))? {
            shape_count = self.drc_count(&format!("{p}.shape_filter_count"), 4, 4)?;
            for i in 0..shape_count {
                for j in 0..4 {
                    let q = format!("{p}.shape_filters[{i}].filters[{j}]");
                    if self.flag(&format!("{q}.present"))? {
                        self.take(&format!("{q}.corner_encoded"), 3)?;
                        self.take(&format!("{q}.strength_encoded"), 2)?;
                    }
                }
            }
        }
        let sequences = self.take(&format!("{p}.gain_sequence_count"), 6)?;
        let sets = self.drc_count(&format!("{p}.gain_set_count"), 6, 6)?;
        let mut bands_per_set = Vec::with_capacity(sets);
        let mut next_sequence = 0;
        for i in 0..sets {
            let q = format!("{p}.gain_sets[{i}]");
            let profile = self.take(&format!("{q}.coding_profile"), 2)?;
            for field in ["interpolation_type", "full_frame", "time_alignment"] {
                self.flag(&format!("{q}.{field}"))?;
            }
            if self.flag(&format!("{q}.time_delta_min_present"))? {
                let delta = self.take(&format!("{q}.time_delta_min_minus_one"), 11)? + 1;
                self.report
                    .derived
                    .insert(format!("{q}.time_delta_min"), json!(delta));
            }
            if profile == 3 {
                return self.stop("DRC constant coding profile is not implemented");
            }
            let bands = self.drc_count(&format!("{q}.band_count"), 4, 2)?;
            if bands == 0 {
                return self.invalid("drc-band-count", "gain set has no bands");
            }
            bands_per_set.push(bands);
            let crossover = bands > 1 && self.flag(&format!("{q}.band_type"))?;
            for j in 0..bands {
                let band = format!("{q}.bands[{j}]");
                let sequence = if self.flag(&format!("{band}.sequence_index_present"))? {
                    self.take(&format!("{band}.sequence_index"), 6)?
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
                self.report
                    .derived
                    .insert(format!("{band}.sequence_index"), json!(sequence));
                if self.flag(&format!("{band}.characteristic_present"))? {
                    if self.flag(&format!("{band}.characteristic_format"))? {
                        self.take(&format!("{band}.characteristic_code"), 7)?;
                    } else {
                        self.drc_reference(&format!("{band}.left_index"), 4, left_count)?;
                        self.drc_reference(&format!("{band}.right_index"), 4, right_count)?;
                    }
                }
            }
            for j in 1..bands {
                self.take(
                    &format!("{q}.bands[{j}].boundary_encoded"),
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

    fn drc_presets(&mut self, p: &str) -> PResult<()> {
        let count = self.drc_count(&format!("{p}.count"), 4, 4)?;
        for i in 0..count {
            self.take(&format!("{p}.ids[{i}]"), 4)?;
        }
        Ok(())
    }

    fn drc_instruction(
        &mut self,
        p: &str,
        channels: usize,
        coefficients: &[Coefficients],
    ) -> PResult<()> {
        self.flag(&format!("{p}.flag_a"))?;
        self.drc_presets(&format!("{p}.presets"))?;
        self.take(&format!("{p}.set_id"), 6)?;
        self.take(&format!("{p}.complexity_level"), 4)?;
        let location = self.take(&format!("{p}.location"), 4)?;
        self.absent(&format!("{p}.downmix_id_present"))?;
        let effect = self.take(&format!("{p}.effect"), 16)?;
        if effect & 0x8c00 != 0 {
            return self.stop("DRC ducking or special-effect instruction is not implemented");
        }
        if self.flag(&format!("{p}.limiter_peak_present"))? {
            self.take(&format!("{p}.limiter_peak_encoded"), 8)?;
        }
        if self.flag(&format!("{p}.target_loudness_present"))? {
            self.take(&format!("{p}.target_loudness_upper_encoded"), 6)?;
            if self.flag(&format!("{p}.target_loudness_lower_present"))? {
                self.take(&format!("{p}.target_loudness_lower_encoded"), 6)?;
            }
        }
        if self.flag(&format!("{p}.depends_on_set_present"))? {
            return self.stop("DRC dependent instruction is not implemented");
        }
        self.flag(&format!("{p}.no_independent_use"))?;
        self.absent(&format!("{p}.requires_eq"))?;
        let Some(coefficient) = coefficients.iter().find(|c| c.location == location) else {
            return self.invalid(
                "drc-reference",
                "instruction has no coefficients at its location",
            );
        };
        let mut indices = Vec::with_capacity(channels);
        let mut groups = Vec::new();
        while indices.len() < channels {
            let channel = indices.len();
            let q = format!("{p}.channel_runs[{channel}]");
            let index = self.drc_reference(
                &format!("{q}.gain_set_index_plus_one"),
                6,
                coefficient.bands.len(),
            )?;
            let repeat = if self.flag(&format!("{q}.repeat_present"))? {
                self.take(&format!("{q}.repeat_count_minus_one"), 5)? as usize + 1
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
            if index != 0 && !groups.contains(&(index as usize - 1)) {
                groups.push(index as usize - 1);
            }
        }
        self.report
            .derived
            .insert(format!("{p}.channel_gain_set_indices"), json!(indices));
        for (group, &set) in groups.iter().enumerate() {
            let q = format!("{p}.groups[{group}]");
            self.report
                .derived
                .insert(format!("{q}.gain_set_index"), json!(set));
            for band in 0..coefficient.bands[set] {
                let b = format!("{q}.bands[{band}]");
                if self.flag(&format!("{b}.parameter_a_present"))? {
                    self.take(&format!("{b}.parameter_a"), 5)?;
                }
                if self.flag(&format!("{b}.target_left_present"))? {
                    self.drc_reference(
                        &format!("{b}.target_left_index"),
                        4,
                        coefficient.left_count,
                    )?;
                }
                if self.flag(&format!("{b}.target_right_present"))? {
                    self.drc_reference(
                        &format!("{b}.target_right_index"),
                        4,
                        coefficient.right_count,
                    )?;
                }
                if self.flag(&format!("{b}.gain_scaling_present"))? {
                    self.take(&format!("{b}.attenuation_scaling_encoded"), 4)?;
                    self.take(&format!("{b}.amplification_scaling_encoded"), 4)?;
                }
                if self.flag(&format!("{b}.gain_offset_present"))? {
                    self.take(&format!("{b}.gain_offset_encoded"), 6)?;
                }
            }
            if coefficient.bands[set] == 1 && self.flag(&format!("{q}.shape_filter_present"))? {
                self.drc_reference(
                    &format!("{q}.shape_filter_index"),
                    4,
                    coefficient.shape_count,
                )?;
            }
        }
        Ok(())
    }

    fn drc_loudness(&mut self) -> PResult<()> {
        let p = format!("{ROOT}.loudness");
        self.flag(&format!("{p}.flag_a"))?;
        self.flag(&format!("{p}.flag_b"))?;
        let counts = [
            self.drc_count(&format!("{p}.count_0"), 8, 7)?,
            self.drc_count(&format!("{p}.count_1"), 8, 7)?,
        ];
        for (group, &count) in counts.iter().enumerate() {
            for i in 0..count {
                let q = format!("{p}.entries_{group}[{i}]");
                self.drc_presets(&format!("{q}.presets"))?;
                if self.flag(&format!("{q}.parameter_id_present"))? {
                    self.take(&format!("{q}.parameter_id"), 6)?;
                }
                if self.flag(&format!("{q}.mp4_info_present"))? {
                    self.drc_mp4_loudness(&format!("{q}.mp4_info"))?;
                }
                if self.flag(&format!("{q}.compositions_present"))? {
                    self.drc_composition_loudness(&format!("{q}.compositions"))?;
                }
            }
        }
        if self.flag(&format!("{p}.sources_present"))? {
            let default = self.flag(&format!("{p}.default_source"))?;
            let count = if default {
                1
            } else {
                self.drc_count(&format!("{p}.source_count"), 8, 18)?
            };
            for i in 0..count {
                let q = format!("{p}.sources[{i}]");
                if !default {
                    self.take(&format!("{q}.pairs[0].parameter_0"), 8)?;
                    self.take(&format!("{q}.pairs[0].parameter_1"), 8)?;
                    if self.flag(&format!("{q}.additional_pairs_present"))? {
                        let count = self.drc_count(&format!("{q}.additional_pair_count"), 8, 16)?;
                        for j in 1..=count {
                            self.take(&format!("{q}.pairs[{j}].parameter_0"), 8)?;
                            self.take(&format!("{q}.pairs[{j}].parameter_1"), 8)?;
                        }
                    }
                }
                if self.flag(&format!("{q}.value_a_present"))? {
                    self.take(&format!("{q}.value_a_encoded"), 8)?;
                    if self.flag(&format!("{q}.value_b_present"))? {
                        self.take(&format!("{q}.value_b_encoded"), 8)?;
                    }
                }
            }
        }
        self.absent(&format!("{p}.extensions_present"))?;
        Ok(())
    }

    fn drc_composition_loudness(&mut self, p: &str) -> PResult<()> {
        // The ID fields include wildcards; their bit widths do not establish an
        // ordinary array index. Preserve their encoded values without guessing.
        self.take(&format!("{p}.drc_set_id"), 6)?;
        self.take(&format!("{p}.eq_set_id"), 6)?;
        self.take(&format!("{p}.downmix_id"), 7)?;
        let compositions = self.drc_count(&format!("{p}.composition_count"), 6, 0)?;
        let measurements = self.drc_count(&format!("{p}.measurement_count"), 4, 24)?;
        for i in 0..measurements {
            let q = format!("{p}.measurements[{i}]");
            self.take(&format!("{q}.method_definition"), 4)?;
            self.take(&format!("{q}.measurement_system"), 4)?;
            self.take(&format!("{q}.reliability"), 2)?;
            self.count(compositions as u64, 8)?;
            for j in 0..compositions {
                self.take(&format!("{q}.values_encoded[{j}]"), 8)?;
            }
            let mixes = self.take(&format!("{q}.mix_count_minus_one"), 6)? + 1;
            let mixes = self.count(mixes, 8 + compositions * 6)?;
            for j in 0..mixes {
                self.take(&format!("{q}.mixes[{j}].value_encoded"), 8)?;
                for k in 0..compositions {
                    self.take(&format!("{q}.mixes[{j}].parameters[{k}]"), 6)?;
                }
            }
        }
        Ok(())
    }

    fn drc_mp4_loudness(&mut self, p: &str) -> PResult<()> {
        self.take(&format!("{p}.drc_set_id"), 6)?;
        self.take(&format!("{p}.eq_set_id"), 6)?;
        self.take(&format!("{p}.downmix_id"), 7)?;
        if self.flag(&format!("{p}.sample_peak_present"))? {
            self.take(&format!("{p}.sample_peak_encoded"), 12)?;
        }
        if self.flag(&format!("{p}.true_peak_present"))? {
            self.take(&format!("{p}.true_peak_encoded"), 12)?;
            self.take(&format!("{p}.true_peak_system"), 4)?;
            self.take(&format!("{p}.true_peak_reliability"), 2)?;
        }
        let count = self.drc_count(&format!("{p}.measurement_count"), 4, 12)?;
        for i in 0..count {
            let q = format!("{p}.measurements[{i}]");
            let method = self.take(&format!("{q}.method_definition"), 4)?;
            self.take(
                &format!("{q}.value_encoded"),
                match method {
                    7 => 5,
                    8 => 2,
                    _ => 8,
                },
            )?;
            self.take(&format!("{q}.measurement_system"), 4)?;
            self.take(&format!("{q}.reliability"), 2)?;
        }
        Ok(())
    }
}
