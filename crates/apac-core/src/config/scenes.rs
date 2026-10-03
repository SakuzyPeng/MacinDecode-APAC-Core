//! Bounded syntax for the channel-bed scene forms observed in the reference corpus.
//! Unassigned control names are deliberately numeric, rather than guessed DSP semantics.
use super::parser::{PResult, Parser};

impl Parser<'_> {
    fn scene_extension(&mut self, prefix: &str) -> PResult<()> {
        if !self.flag(&format!("{prefix}.extension_present"))? {
            return Ok(());
        }
        for i in 0..8 {
            let p = format!("{prefix}.extensions[{i}]");
            if self.take(&format!("{p}.type"), 3)? == 0 {
                return Ok(());
            }
            let width = self.take(&format!("{p}.length_width_minus_four"), 4)? as usize + 4;
            let count = self.take(&format!("{p}.bits_minus_one"), width)? as usize + 1;
            self.passive_bits(&format!("{p}.payload"), count)?;
        }
        self.invalid(
            "scene-extension-count",
            "scene extension chain requires a terminator within eight records",
        )
    }
    fn scene_tag(&mut self, prefix: &str) -> PResult<()> {
        let words = self.take(&format!("{prefix}.word_count_minus_one"), 2)? + 1;
        for i in 0..words {
            self.take(&format!("{prefix}.words[{i}]"), 10)?;
        }
        if self.flag(&format!("{prefix}.fallback_present"))? {
            let count = self.take(&format!("{prefix}.fallback_word_count_minus_one"), 2)? + 1;
            for i in 0..count {
                self.take(&format!("{prefix}.fallback_words[{i}]"), 10)?;
            }
        }
        self.scene_extension(prefix)
    }

    fn scene_language(&mut self, prefix: &str) -> PResult<()> {
        // Prefix coding: stop/hyphen use 3 bits, common letters 5, j/q/x 8, digits 9.
        let alphabet = "abcdefghiklmnoprstuvwyz";
        let mut text = String::new();
        loop {
            let start = self.pos();
            let mut code = 0;
            let mut character = None;
            let mut terminated = false;
            for width in 1..=9 {
                code = (code << 1) | self.bits.read(1)?;
                if width == 3 && code == 0 {
                    terminated = true;
                    break;
                }
                if width == 3 && code == 1 {
                    character = Some('-');
                    break;
                }
                if width == 5 && (8..=30).contains(&code) {
                    character = alphabet.chars().nth((code - 8) as usize);
                    break;
                }
                if width == 8 && (248..=250).contains(&code) {
                    character = Some(['j', 'q', 'x'][(code - 248) as usize]);
                    break;
                }
                if width == 9 && (502..=511).contains(&code) {
                    character = Some(char::from(b'0' + (code - 502) as u8));
                    break;
                }
            }
            if terminated {
                self.record(&format!("{prefix}.terminator"), start, serde_json::json!(0))?;
                break;
            }
            let Some(ch) = character else {
                return self.invalid("language-code", "invalid language prefix code");
            };
            self.record(
                &format!("{prefix}.characters[{}]", text.len()),
                start,
                serde_json::json!(ch.to_string()),
            )?;
            text.push(ch);
            if text.len() > 256 {
                return self.invalid("language-limit", "language exceeds 256 characters");
            }
        }
        self.flag(&format!("{prefix}.flag"))?;
        self.report
            .derived
            .insert(prefix.to_string(), serde_json::json!(text));
        Ok(())
    }

    pub fn audio_scenes(&mut self) -> PResult<()> {
        let root = "ancillary.audio_scenes";
        for i in 0..3 {
            self.flag(&format!("{root}.flags[{i}]"))?;
        }
        self.take(&format!("{root}.parameter"), 2)?;
        let count = self.take(&format!("{root}.composition_count"), 6)?;
        let count = self.count(count, 29)?;
        for i in 0..count {
            self.scene_composition(&format!("{root}.compositions[{i}]"))?;
        }
        if self.flag(&format!("{root}.tag_present"))? {
            self.scene_tag(&format!("{root}.tag"))?;
        }
        self.scene_extension(root)
    }

    fn scene_composition(&mut self, p: &str) -> PResult<()> {
        self.flag(&format!("{p}.flag"))?;
        if self.flag(&format!("{p}.language_present"))? {
            self.scene_language(&format!("{p}.language"))?;
        }
        let nonlanguage = self.take(&format!("{p}.nonlanguage_item_count"), 6)?;
        let language = self.take(&format!("{p}.language_item_count"), 6)?;
        let selection = self.take(&format!("{p}.selection_item_count"), 6)?;
        let groups = self.take(&format!("{p}.group_count"), 5)?;
        let presets = self.take(&format!("{p}.preset_count"), 4)?;
        let nonlanguage = self.count(nonlanguage, 7)?;
        for i in 0..nonlanguage {
            let prefix = format!("{p}.nonlanguage_items[{i}]");
            let count = self.take(&format!("{prefix}.source_count"), 6)?;
            let count = self.count(count, 6)?;
            for j in 0..count {
                self.take(&format!("{prefix}.source_indices[{j}]"), 6)?;
            }
            if self.flag(&format!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
        }
        let language = self.count(language, 8)?;
        for i in 0..language {
            let prefix = format!("{p}.language_items[{i}]");
            let count = self.drc_count(&format!("{prefix}.source_count"), 2, 6)?;
            for j in 0..count {
                self.take(&format!("{prefix}.source_indices[{j}]"), 6)?;
            }
            self.scene_language(&format!("{prefix}.language"))?;
            self.flag(&format!("{prefix}.flag_a"))?;
            self.flag(&format!("{prefix}.flag_b"))?;
        }
        let selection = self.count(selection, 22)?;
        for i in 0..selection {
            let prefix = format!("{p}.selection_items[{i}]");
            let count = self.drc_count(&format!("{prefix}.language_item_count"), 6, 6)?;
            if count == 0 {
                return self.invalid("scene-reference", "language selection set is empty");
            }
            for j in 0..count {
                if self.take(&format!("{prefix}.language_item_indices[{j}]"), 6)? as usize
                    >= language
                {
                    return self.invalid("scene-reference", "language item index is out of range");
                }
            }
            self.take(&format!("{prefix}.parameter"), 15)?;
            if self.flag(&format!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
        }
        let groups = self.count(groups, 14)?;
        for i in 0..groups {
            let prefix = format!("{p}.groups[{i}]");
            let n = self.take(&format!("{prefix}.item_count"), 6)?;
            let n = self.count(n, 6)?;
            for j in 0..n {
                if self.take(&format!("{prefix}.item_indices[{j}]"), 6)? >= nonlanguage as u64 {
                    return self.invalid("scene-reference", "group item index is out of range");
                }
            }
            let other = self.drc_count(&format!("{prefix}.selection_count"), 6, 6)?;
            for j in 0..other {
                if self.take(&format!("{prefix}.selection_indices[{j}]"), 6)? as usize >= selection
                {
                    return self.invalid(
                        "scene-reference",
                        "language selection index is out of range",
                    );
                }
            }
            for preset in 0..presets {
                self.scene_controls(&format!("{prefix}.controls[{preset}]"))?;
            }
            if self.flag(&format!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
            self.scene_extension(&prefix)?;
        }
        let selection_data = self.flag(&format!("{p}.preset_selection_data_present"))?;
        let presets = self.count(presets, 20)?;
        for i in 0..presets {
            let prefix = format!("{p}.presets[{i}]");
            self.scene_tag(&format!("{prefix}.tag"))?;
            let index = self.take(&format!("{prefix}.characteristics_size_index"), 2)?;
            let flags = [0, 16, 64, 256][index as usize];
            for j in 0..flags {
                self.flag(&format!("{prefix}.characteristics[{j}]"))?;
            }
            self.scene_language(&format!("{prefix}.language"))?;
            if selection_data {
                self.flag(&format!("{prefix}.selection_flag"))?;
                let count = self.drc_count(&format!("{prefix}.selection_count"), 6, 6)?;
                for j in 0..count {
                    self.take(&format!("{prefix}.selection_indices[{j}]"), 6)?;
                }
            }
        }
        if self.flag(&format!("{p}.tag_present"))? {
            self.scene_tag(&format!("{p}.tag"))?;
        }
        // Keep the existing presence-field name; its payload is category metadata.
        if self.flag(&format!("{p}.selection_updates_present"))? {
            let count = self.drc_count(&format!("{p}.category_count"), 4, 8)?;
            for i in 0..count {
                let prefix = format!("{p}.categories[{i}]");
                let members = self.drc_count(&format!("{prefix}.group_count"), 6, 6)?;
                for j in 0..members {
                    if self.take(&format!("{prefix}.group_indices[{j}]"), 6)? as usize >= groups {
                        return self
                            .invalid("scene-reference", "category group index is out of range");
                    }
                }
                if self.flag(&format!("{prefix}.members_present"))? {
                    for preset in 0..presets {
                        let selected =
                            self.take(&format!("{prefix}.members[{preset}]"), 6)? as usize;
                        if selected >= members {
                            return self.invalid(
                                "scene-reference",
                                "category member index is out of range",
                            );
                        }
                    }
                }
                if self.flag(&format!("{prefix}.tag_present"))? {
                    self.scene_tag(&format!("{prefix}.tag"))?;
                }
            }
        }
        self.scene_extension(p)
    }

    fn scene_controls(&mut self, p: &str) -> PResult<()> {
        let primary = self.flag(&format!("{p}.primary_flag"))?;
        if self.flag(&format!("{p}.range_0_present"))?
            && self.flag(&format!("{p}.range_0_explicit"))?
        {
            self.take(&format!("{p}.range_0_min"), 7)?;
            self.take(&format!("{p}.range_0_max"), 7)?;
        }
        for (i, width) in [5, 5, 3].into_iter().enumerate() {
            if self.flag(&format!("{p}.range_{}_present", i + 1))? {
                self.take(&format!("{p}.range_{}_min", i + 1), width)?;
                self.take(&format!("{p}.range_{}_max", i + 1), width)?;
            }
        }
        self.flag(&format!("{p}.secondary_flag"))?;
        let parameters = self.flag(&format!("{p}.parameters_present"))?;
        if primary || parameters {
            self.take(&format!("{p}.parameter_0"), 6)?;
            for (i, width) in [9, 6, 6, 4, 1].into_iter().enumerate() {
                if self.flag(&format!("{p}.parameter_{}_present", i + 1))? {
                    self.take(&format!("{p}.parameter_{}", i + 1), width)?;
                }
            }
        }
        Ok(())
    }
}
