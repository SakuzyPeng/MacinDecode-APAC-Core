//! Bounded syntax for the channel-bed scene forms observed in the reference corpus.
//! Unassigned control names are deliberately numeric, rather than guessed DSP semantics.
use super::parser::{PResult, Parser};

impl Parser<'_> {
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
        self.absent(&format!("{prefix}.extension_present"))
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
        self.absent(&format!("{root}.extension_present"))
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
        if language != 0 || selection != 0 {
            return self.stop("scene language/selection item arrays are not implemented");
        }
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
            let other = self.take(&format!("{prefix}.selection_count"), 6)?;
            // The complete path verified in this milestone has one nonlanguage item per group.
            if n != 1 || other != 0 {
                return self
                    .stop("multi-item or language-selection scene groups are not implemented");
            }
            self.scene_controls(&format!("{prefix}.controls[0]"))?;
            if self.flag(&format!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
            self.absent(&format!("{prefix}.extension_present"))?;
        }
        self.absent(&format!("{p}.preset_selection_data_present"))?;
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
        }
        if self.flag(&format!("{p}.tag_present"))? {
            self.scene_tag(&format!("{p}.tag"))?;
        }
        self.absent(&format!("{p}.selection_updates_present"))?;
        self.absent(&format!("{p}.extension_present"))
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
