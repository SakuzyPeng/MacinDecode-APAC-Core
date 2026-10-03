//! Bounded syntax for the channel-bed scene forms observed in the reference corpus.
//! Unassigned control names are deliberately numeric, rather than guessed DSP semantics.
use super::{
    AudioScenes, SceneCategory, SceneControls, SceneGroup, SceneLanguageItem, SceneSelection,
    SceneSources,
    parser::{PResult, Parser},
};
use crate::prelude::*;
use crate::record::FieldValue;

impl Parser<'_> {
    fn scene_extension(&mut self, prefix: &str) -> PResult<()> {
        if !self.flag(format_args!("{prefix}.extension_present"))? {
            return Ok(());
        }
        for i in 0..8 {
            let p = format!("{prefix}.extensions[{i}]");
            if self.take(format_args!("{p}.type"), 3)? == 0 {
                return Ok(());
            }
            let width = self.take(format_args!("{p}.length_width_minus_four"), 4)? as usize + 4;
            let count = self.take(format_args!("{p}.bits_minus_one"), width)? as usize + 1;
            self.passive_bits(format_args!("{p}.payload"), count)?;
        }
        self.invalid(
            "scene-extension-count",
            "scene extension chain requires a terminator within eight records",
        )
    }
    fn scene_tag(&mut self, prefix: &str) -> PResult<()> {
        let words = self.take(format_args!("{prefix}.word_count_minus_one"), 2)? + 1;
        for i in 0..words {
            self.take(format_args!("{prefix}.words[{i}]"), 10)?;
        }
        if self.flag(format_args!("{prefix}.fallback_present"))? {
            let count = self.take(format_args!("{prefix}.fallback_word_count_minus_one"), 2)? + 1;
            for i in 0..count {
                self.take(format_args!("{prefix}.fallback_words[{i}]"), 10)?;
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
                self.record(
                    format_args!("{prefix}.terminator"),
                    start,
                    FieldValue::from(0u64),
                )?;
                break;
            }
            let Some(ch) = character else {
                return self.invalid("language-code", "invalid language prefix code");
            };
            self.record(
                format_args!("{prefix}.characters[{}]", text.len()),
                start,
                FieldValue::from(ch.to_string()),
            )?;
            text.push(ch);
            if text.len() > 256 {
                return self.invalid("language-limit", "language exceeds 256 characters");
            }
        }
        self.flag(format_args!("{prefix}.flag"))?;
        self.derive(prefix.to_string(), FieldValue::from(text));
        Ok(())
    }

    pub fn audio_scenes(&mut self) -> PResult<()> {
        let root = "ancillary.audio_scenes";
        for i in 0..3 {
            self.flag(format_args!("{root}.flags[{i}]"))?;
        }
        self.take(format_args!("{root}.parameter"), 2)?;
        let count = self.take_at(format_args!("{root}.composition_count"), 6)?;
        self.config.ancillary.audio_scenes.composition_count = Some(count);
        let count = self.count(count.value, 29)?;
        for i in 0..count {
            self.scene_composition(&format!("{root}.compositions[{i}]"), i == 0)?;
        }
        if self.flag(format_args!("{root}.tag_present"))? {
            self.scene_tag(&format!("{root}.tag"))?;
        }
        self.scene_extension(root)
    }

    /// Neutral routing reads the first composition, recorded in `scenes`.
    fn scenes(&mut self, first: bool) -> Option<&mut AudioScenes> {
        first.then_some(&mut self.config.ancillary.audio_scenes)
    }
    fn scene_composition(&mut self, p: &str, first: bool) -> PResult<()> {
        let flag = self.flag_at(format_args!("{p}.flag"))?;
        if let Some(s) = self.scenes(first) {
            s.flag = Some(flag);
        }
        if self.flag(format_args!("{p}.language_present"))? {
            self.scene_language(&format!("{p}.language"))?;
        }
        let nonlanguage = self.take(format_args!("{p}.nonlanguage_item_count"), 6)?;
        if let Some(s) = self.scenes(first) {
            s.nonlanguage_item_count = Some(nonlanguage);
        }
        let language = self.take(format_args!("{p}.language_item_count"), 6)?;
        if let Some(s) = self.scenes(first) {
            s.language_item_count = Some(language);
        }
        let selection = self.take(format_args!("{p}.selection_item_count"), 6)?;
        if let Some(s) = self.scenes(first) {
            s.selection_item_count = Some(selection);
        }
        let groups = self.take(format_args!("{p}.group_count"), 5)?;
        if let Some(s) = self.scenes(first) {
            s.group_count = Some(groups);
        }
        let presets = self.take(format_args!("{p}.preset_count"), 4)?;
        if let Some(s) = self.scenes(first) {
            s.preset_count = Some(presets);
        }
        let nonlanguage = self.count(nonlanguage, 7)?;
        for i in 0..nonlanguage {
            let prefix = format!("{p}.nonlanguage_items[{i}]");
            let count = self.take(format_args!("{prefix}.source_count"), 6)?;
            if let Some(s) = self.scenes(first) {
                s.nonlanguage_items.push(SceneSources {
                    source_count: Some(count),
                    source_indices: Vec::new(),
                });
            }
            let count = self.count(count, 6)?;
            for j in 0..count {
                let index = self.take(format_args!("{prefix}.source_indices[{j}]"), 6)?;
                if let Some(s) = self.scenes(first) {
                    let item = s.nonlanguage_items.last_mut().expect("item declared");
                    item.source_indices.push(index);
                }
            }
            if self.flag(format_args!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
        }
        let language = self.count(language, 8)?;
        for i in 0..language {
            let prefix = format!("{p}.language_items[{i}]");
            let count = self.drc_count(format_args!("{prefix}.source_count"), 2, 6)?;
            if let Some(s) = self.scenes(first) {
                s.language_items.push(SceneLanguageItem {
                    sources: SceneSources {
                        source_count: Some(count as u64),
                        source_indices: Vec::new(),
                    },
                    flag_a: None,
                });
            }
            for j in 0..count {
                let index = self.take(format_args!("{prefix}.source_indices[{j}]"), 6)?;
                if let Some(s) = self.scenes(first) {
                    let item = s.language_items.last_mut().expect("item declared");
                    item.sources.source_indices.push(index);
                }
            }
            self.scene_language(&format!("{prefix}.language"))?;
            let flag_a = self.flag(format_args!("{prefix}.flag_a"))?;
            if let Some(s) = self.scenes(first) {
                s.language_items.last_mut().expect("item declared").flag_a = Some(flag_a);
            }
            self.flag(format_args!("{prefix}.flag_b"))?;
        }
        let selection = self.count(selection, 22)?;
        for i in 0..selection {
            let prefix = format!("{p}.selection_items[{i}]");
            let count = self.drc_count(format_args!("{prefix}.language_item_count"), 6, 6)?;
            if let Some(s) = self.scenes(first) {
                s.selection_items.push(SceneSelection {
                    language_item_count: Some(count as u64),
                    language_item_indices: Vec::new(),
                });
            }
            if count == 0 {
                return self.invalid("scene-reference", "language selection set is empty");
            }
            for j in 0..count {
                let index = self.take(format_args!("{prefix}.language_item_indices[{j}]"), 6)?;
                if let Some(s) = self.scenes(first) {
                    let item = s.selection_items.last_mut().expect("selection declared");
                    item.language_item_indices.push(index);
                }
                if index as usize >= language {
                    return self.invalid("scene-reference", "language item index is out of range");
                }
            }
            self.take(format_args!("{prefix}.parameter"), 15)?;
            if self.flag(format_args!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
        }
        let groups = self.count(groups, 14)?;
        for i in 0..groups {
            let prefix = format!("{p}.groups[{i}]");
            let n = self.take(format_args!("{prefix}.item_count"), 6)?;
            if let Some(s) = self.scenes(first) {
                s.groups.push(SceneGroup {
                    item_count: Some(n),
                    ..SceneGroup::default()
                });
            }
            let n = self.count(n, 6)?;
            for j in 0..n {
                let index = self.take(format_args!("{prefix}.item_indices[{j}]"), 6)?;
                if let Some(group) = self.scene_group(first) {
                    group.item_indices.push(index);
                }
                if index >= nonlanguage as u64 {
                    return self.invalid("scene-reference", "group item index is out of range");
                }
            }
            let other = self.drc_count(format_args!("{prefix}.selection_count"), 6, 6)?;
            for j in 0..other {
                let index = self.take(format_args!("{prefix}.selection_indices[{j}]"), 6)?;
                if let Some(group) = self.scene_group(first) {
                    group.selection_indices.push(index);
                }
                if index as usize >= selection {
                    return self.invalid(
                        "scene-reference",
                        "language selection index is out of range",
                    );
                }
            }
            for preset in 0..presets {
                self.scene_controls(&format!("{prefix}.controls[{preset}]"), first)?;
            }
            if self.flag(format_args!("{prefix}.tag_present"))? {
                self.scene_tag(&format!("{prefix}.tag"))?;
            }
            self.scene_extension(&prefix)?;
        }
        let selection_data = self.flag(format_args!("{p}.preset_selection_data_present"))?;
        let presets = self.count(presets, 20)?;
        for i in 0..presets {
            let prefix = format!("{p}.presets[{i}]");
            self.scene_tag(&format!("{prefix}.tag"))?;
            let index = self.take(format_args!("{prefix}.characteristics_size_index"), 2)?;
            let flags = [0, 16, 64, 256][index as usize];
            for j in 0..flags {
                self.flag(format_args!("{prefix}.characteristics[{j}]"))?;
            }
            self.scene_language(&format!("{prefix}.language"))?;
            if selection_data {
                self.flag(format_args!("{prefix}.selection_flag"))?;
                let count = self.drc_count(format_args!("{prefix}.selection_count"), 6, 6)?;
                for j in 0..count {
                    self.take(format_args!("{prefix}.selection_indices[{j}]"), 6)?;
                }
            }
        }
        if self.flag(format_args!("{p}.tag_present"))? {
            self.scene_tag(&format!("{p}.tag"))?;
        }
        // Keep the existing presence-field name; its payload is category metadata.
        if self.flag(format_args!("{p}.selection_updates_present"))? {
            let count = self.drc_count(format_args!("{p}.category_count"), 4, 8)?;
            if let Some(s) = self.scenes(first) {
                s.category_count = Some(count as u64);
            }
            for i in 0..count {
                let prefix = format!("{p}.categories[{i}]");
                let members = self.drc_count(format_args!("{prefix}.group_count"), 6, 6)?;
                if let Some(s) = self.scenes(first) {
                    s.categories.push(SceneCategory {
                        group_count: Some(members as u64),
                        ..SceneCategory::default()
                    });
                }
                for j in 0..members {
                    let index = self.take(format_args!("{prefix}.group_indices[{j}]"), 6)?;
                    if let Some(category) = self.scene_category(first) {
                        category.group_indices.push(index);
                    }
                    if index as usize >= groups {
                        return self
                            .invalid("scene-reference", "category group index is out of range");
                    }
                }
                let members_present = self.flag(format_args!("{prefix}.members_present"))?;
                if let Some(category) = self.scene_category(first) {
                    category.members_present = Some(members_present);
                }
                if members_present {
                    for preset in 0..presets {
                        let selected = self.take(format_args!("{prefix}.members[{preset}]"), 6)?;
                        if let Some(category) = self.scene_category(first) {
                            category.members.push(selected);
                        }
                        let selected = selected as usize;
                        if selected >= members {
                            return self.invalid(
                                "scene-reference",
                                "category member index is out of range",
                            );
                        }
                    }
                }
                if self.flag(format_args!("{prefix}.tag_present"))? {
                    self.scene_tag(&format!("{prefix}.tag"))?;
                }
            }
        }
        self.scene_extension(p)
    }

    fn scene_group(&mut self, first: bool) -> Option<&mut SceneGroup> {
        self.scenes(first)?.groups.last_mut()
    }
    fn scene_category(&mut self, first: bool) -> Option<&mut SceneCategory> {
        self.scenes(first)?.categories.last_mut()
    }
    fn scene_controls_mut(&mut self, first: bool) -> Option<&mut SceneControls> {
        self.scene_group(first)?.controls.last_mut()
    }
    fn scene_controls(&mut self, p: &str, first: bool) -> PResult<()> {
        let primary = self.flag(format_args!("{p}.primary_flag"))?;
        if let Some(group) = self.scene_group(first) {
            group.controls.push(SceneControls::default());
        }
        if self.flag(format_args!("{p}.range_0_present"))?
            && self.flag(format_args!("{p}.range_0_explicit"))?
        {
            self.take(format_args!("{p}.range_0_min"), 7)?;
            self.take(format_args!("{p}.range_0_max"), 7)?;
        }
        for (i, width) in [5, 5, 3].into_iter().enumerate() {
            if self.flag(format_args!("{p}.range_{}_present", i + 1))? {
                self.take(format_args!("{p}.range_{}_min", i + 1), width)?;
                self.take(format_args!("{p}.range_{}_max", i + 1), width)?;
            }
        }
        self.flag(format_args!("{p}.secondary_flag"))?;
        let parameters = self.flag(format_args!("{p}.parameters_present"))?;
        if let Some(controls) = self.scene_controls_mut(first) {
            controls.parameters_present = Some(parameters);
        }
        if primary || parameters {
            let parameter_0 = self.take(format_args!("{p}.parameter_0"), 6)?;
            if let Some(controls) = self.scene_controls_mut(first) {
                controls.parameter_0 = Some(parameter_0);
            }
            for (i, width) in [9, 6, 6, 4, 1].into_iter().enumerate() {
                let present = self.flag(format_args!("{p}.parameter_{}_present", i + 1))?;
                if i == 0
                    && let Some(controls) = self.scene_controls_mut(first)
                {
                    controls.parameter_1_present = Some(present);
                }
                if present {
                    let value = self.take_at(format_args!("{p}.parameter_{}", i + 1), width)?;
                    if i == 0
                        && let Some(controls) = self.scene_controls_mut(first)
                    {
                        controls.parameter_1 = Some(value);
                    }
                }
            }
        }
        Ok(())
    }
}
