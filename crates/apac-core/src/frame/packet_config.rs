//! Decoder eligibility is narrower than successful cookie syntax parsing.
//! The scene whitelist is the verified neutral, single-source stereo route.
use crate::config::{AudioScenes, Config, Field, FieldExt};
use crate::prelude::*;
use core::fmt::Display;

#[derive(Debug, Clone)]
pub(super) struct PacketConfiguration {
    pub scene_present: bool,
    pub rejection: Option<String>,
    pub syntax_rejection: Option<String>,
}

/// Compare one wire field with its qualified value. Integers and booleans
/// render exactly as their recorded JSON values.
pub(super) fn check<V: Copy + PartialEq + Display>(
    name: impl Display,
    field: Field<V>,
    expected: V,
    origin: &str,
    rejected: &mut Vec<String>,
) {
    match field {
        Some(field) if field.value == expected => {}
        Some(field) => rejected.push(format!(
            "{name}={} at {origin} bit {} (expected {expected})",
            field.value, field.bit_offset
        )),
        None => rejected.push(format!(
            "{name}=missing at {origin} bit unknown (expected {expected})"
        )),
    }
}

pub(super) fn neutral_scene(scenes: &AudioScenes, origin: &str) -> Vec<String> {
    neutral_scene_sources(scenes, origin, 1)
}

pub(super) fn neutral_scene_sources(
    scenes: &AudioScenes,
    origin: &str,
    sources: usize,
) -> Vec<String> {
    let mut rejected = Vec::new();
    let root = "ancillary.audio_scenes";
    let composition = format!("{root}.compositions[0]");
    let number = |value: Option<u64>| value.and_then(|v| usize::try_from(v).ok());
    let at = |values: &[u64], index: usize| number(values.get(index).copied());
    check(
        format_args!("{root}.composition_count"),
        scenes.composition_count,
        1,
        origin,
        &mut rejected,
    );
    check(
        format_args!("{composition}.flag"),
        scenes.flag,
        true,
        origin,
        &mut rejected,
    );
    let items = number(scenes.nonlanguage_item_count).unwrap_or(0);
    let languages = number(scenes.language_item_count).unwrap_or(0);
    let selections = number(scenes.selection_item_count).unwrap_or(0);
    let groups = number(scenes.group_count).unwrap_or(0);
    let presets = number(scenes.preset_count).unwrap_or(0);
    if items + selections == 0 || groups == 0 || presets == 0 {
        rejected.push(format!(
            "neutral scene requires items, groups and presets at {origin}"
        ));
        return rejected;
    }
    let source_list = |item: Option<&crate::config::SceneSources>| -> Option<Vec<usize>> {
        let item = item?;
        let count = number(item.source_count)?;
        let mut result = Vec::with_capacity(count);
        for i in 0..count {
            let index = at(&item.source_indices, i)?;
            if index >= sources {
                return None;
            }
            result.push(index);
        }
        result.sort_unstable();
        Some(result)
    };
    let item_sources: Vec<_> = (0..items)
        .map(|i| source_list(scenes.nonlanguage_items.get(i)))
        .collect();
    let language_sources: Vec<_> = (0..languages)
        .map(|i| source_list(scenes.language_items.get(i).map(|l| &l.sources)))
        .collect();
    if item_sources
        .iter()
        .chain(&language_sources)
        .any(Option::is_none)
    {
        rejected.push(format!(
            "scene source reference is outside the coded declarations at {origin}"
        ));
    }
    let selection_sources: Vec<_> = (0..selections)
        .map(|i| {
            let selection = scenes.selection_items.get(i)?;
            let count = number(selection.language_item_count)?;
            let mut shared = None;
            let mut fallback = false;
            for j in 0..count {
                let index = at(&selection.language_item_indices, j)?;
                let entry = language_sources.get(index)?.as_ref()?;
                if shared.as_ref().is_some_and(|previous| previous != entry) {
                    return None;
                }
                shared = Some(entry.clone());
                fallback |= scenes
                    .language_items
                    .get(index)
                    .and_then(|l| l.flag_a)
                    .unwrap_or(false);
            }
            if fallback { shared } else { None }
        })
        .collect();
    for preset in 0..presets {
        let mut routes = Vec::with_capacity(groups);
        for group in 0..groups {
            let declaration = scenes.groups.get(group);
            let controls = declaration.and_then(|g| g.controls.get(preset));
            let flag = |read: fn(&crate::config::SceneControls) -> Option<bool>| {
                controls.and_then(read).unwrap_or(false)
            };
            // Parameters activate the selected member; descriptive primary and
            // secondary flags do not make an otherwise muted group audible.
            if !flag(|c| c.parameters_present) {
                routes.push(Some(Vec::new()));
                continue;
            }
            // Ranges and parameters 2..5 describe presentation controls. Only
            // the explicit gain changes these raw PCM sources; 256 is unity.
            if flag(|c| c.parameter_1_present) {
                check(
                    format_args!("{composition}.groups[{group}].controls[{preset}].parameter_1"),
                    controls.and_then(|c| c.parameter_1),
                    256,
                    origin,
                    &mut rejected,
                );
            }
            let member = number(controls.and_then(|c| c.parameter_0)).unwrap_or(usize::MAX);
            let count = number(declaration.and_then(|g| g.item_count)).unwrap_or(0);
            let route = if member < count {
                declaration
                    .and_then(|g| at(&g.item_indices, member))
                    .and_then(|i| item_sources.get(i))
                    .cloned()
                    .flatten()
            } else {
                let member = member.saturating_sub(count);
                declaration
                    .and_then(|g| at(&g.selection_indices, member))
                    .and_then(|i| selection_sources.get(i))
                    .cloned()
                    .flatten()
            };
            if route.is_none() {
                rejected.push(format!("scene group {group}, preset {preset} does not select a fixed source set at {origin}"));
            }
            routes.push(route);
        }
        let mut selected = vec![0usize; sources];
        let mut categorized = vec![false; groups];
        let categories = number(scenes.category_count).unwrap_or(0);
        for category in 0..categories {
            let declaration = scenes.categories.get(category);
            let count = number(declaration.and_then(|c| c.group_count)).unwrap_or(0);
            let mut members = Vec::new();
            for i in 0..count {
                if let Some(group) = declaration.and_then(|c| at(&c.group_indices, i))
                    && group < groups
                {
                    if core::mem::replace(&mut categorized[group], true) {
                        rejected.push(format!("overlapping scene categories require presentation selection at {origin}"));
                    }
                    members.push(group);
                }
            }
            let choices: Vec<_> = if declaration.and_then(|c| c.members_present).unwrap_or(false) {
                declaration
                    .and_then(|c| at(&c.members, preset))
                    .and_then(|i| members.get(i))
                    .copied()
                    .into_iter()
                    .collect()
            } else {
                members
            };
            let mut common = None;
            for group in choices {
                if let Some(route) = &routes[group] {
                    if common.as_ref().is_some_and(|old| old != route) {
                        rejected.push(format!(
                            "scene category {category} changes source selection at {origin}"
                        ));
                    }
                    common = Some(route.clone());
                }
            }
            if let Some(route) = common {
                for source in route {
                    selected[source] += 1;
                }
            }
        }
        for (group, route) in routes.iter().enumerate() {
            if !categorized[group]
                && let Some(route) = route
            {
                for &source in route {
                    selected[source] += 1;
                }
            }
        }
        if selected.iter().any(|&count| count != 1) {
            rejected.push(format!(
                "scene preset {preset} must select every source exactly once at {origin}"
            ));
        }
    }
    rejected
}

impl PacketConfiguration {
    pub fn from_config(config: &Config) -> Self {
        Self::for_layout(config, 2, 101, 0, &[1])
    }
    pub(super) fn for_layout(
        config: &Config,
        channels: u64,
        family: u64,
        level: u64,
        types: &[u8],
    ) -> Self {
        let global = &config.global;
        let component = config.component(0);
        let mut rejected = Vec::new();
        for (name, field, value) in [
            ("global.profile_id", global.profile_id, 31),
            ("global.level_id", global.level_id, level),
            ("global.parameter_b", global.parameter_b, 2),
            ("box.version_flags", config.version_flags, 0),
            ("bitstream_version", config.bitstream_version, 0x0800),
            ("global.frame_size_index", global.frame_size_index, 0),
            ("global.channel_count", global.channel_count, channels),
            ("global.component_count", global.component_count, 1),
            ("components[0].type", component.kind, 0),
            (
                "components[0].lowest_channel_index",
                component.lowest_channel_index,
                0,
            ),
            (
                "components[0].tce_count",
                component.tce_count,
                types.len() as u64,
            ),
            (
                "components[0].tce[0].type",
                component.tce_types.first().copied(),
                u64::from(types.first().copied().unwrap_or(0)),
            ),
            ("components[0].parameter_0", component.parameter_0, 0),
            ("components[0].parameter_1", component.parameter_1, 0),
            (
                "components[0].layout_family",
                component.layout_family,
                family,
            ),
        ] {
            check(name, field, value, "cookie", &mut rejected);
        }
        for (i, &kind) in types.iter().enumerate().skip(1) {
            check(
                format_args!("components[0].tce[{i}].type"),
                component.tce_types.get(i).copied(),
                u64::from(kind),
                "cookie",
                &mut rejected,
            );
        }
        let ancillary = &config.ancillary;
        for (name, field) in [
            ("global.flag_a", global.flag_a),
            ("global.flag_c", global.flag_c),
            (
                "global.additional_asc_present",
                global.additional_asc_present,
            ),
            ("components[0].lbr_flag", component.lbr_flag),
            (
                "ancillary.scene_graph_present",
                ancillary.scene_graph_present,
            ),
            ("ancillary.metadata_present", ancillary.metadata_present),
            (
                "ancillary.custom_data_present",
                ancillary.custom_data_present,
            ),
            (
                "components[0].remapping_present",
                component.remapping_present,
            ),
        ] {
            check(name, field, false, "cookie", &mut rejected);
        }
        let scene_present = ancillary.audio_scenes_present.is(true);
        if scene_present {
            rejected.extend(neutral_scene(&ancillary.audio_scenes, "cookie"));
        } else {
            check(
                "ancillary.audio_scenes_present",
                ancillary.audio_scenes_present,
                false,
                "cookie",
                &mut rejected,
            );
        }
        // The cookie parser already checks the bounded ContentOrigin grammar,
        // every extension type, its termination and zero padding. No byte/CRC whitelist.
        for (index, extension) in config.extensions.iter().enumerate() {
            check(
                format_args!("extensions[{index}].type"),
                extension.kind,
                3,
                "cookie",
                &mut rejected,
            );
        }
        if !matches!(global.sample_rate_hz, Some(44100 | 48000)) {
            if let Some(f) = global.sample_rate_index {
                rejected.push(format!(
                    "global.sample_rate_index={} at cookie bit {} (expected 44.1/48 kHz)",
                    f.value, f.bit_offset
                ));
            } else {
                rejected.push(
                    "sample rate missing at cookie bit unknown (expected 44.1/48 kHz)".into(),
                );
            }
        }
        rejected.extend(config.status_rejection());
        let syntax_rejection = (!rejected.is_empty()).then(|| rejected.join("; "));
        Self {
            syntax_rejection,
            scene_present,
            rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
        }
    }
}
