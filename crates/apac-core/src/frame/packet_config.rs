//! Decoder eligibility is narrower than successful cookie syntax parsing.
//! The scene whitelist is the verified neutral, single-source stereo route.
use crate::config::{ConfigField, CookieReport};
use crate::prelude::*;
use serde_json::{Value, json};

#[derive(Debug, Clone)]
pub(super) struct PacketConfiguration {
    pub scene_present: bool,
    pub rejection: Option<String>,
    pub syntax_rejection: Option<String>,
}

pub(super) fn check(
    fields: &[ConfigField],
    name: &str,
    expected: Value,
    origin: &str,
    rejected: &mut Vec<String>,
) {
    match fields.iter().find(|f| f.name == name) {
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

pub(super) fn neutral_scene(fields: &[ConfigField], origin: &str, drc_off: bool) -> Vec<String> {
    neutral_scene_sources(fields, origin, drc_off, 1)
}

pub(super) fn neutral_scene_sources(
    fields: &[ConfigField],
    origin: &str,
    _drc_off: bool,
    sources: usize,
) -> Vec<String> {
    let mut rejected = Vec::new();
    let root = "ancillary.audio_scenes";
    let composition = format!("{root}.compositions[0]");
    let indexed: std::collections::BTreeMap<_, _> =
        fields.iter().map(|f| (f.name.as_str(), &f.value)).collect();
    let number = |name: &str| {
        indexed
            .get(name)
            .and_then(|v| v.as_u64())
            .and_then(|v| usize::try_from(v).ok())
    };
    let flag = |name: &str| indexed.get(name).and_then(|v| v.as_bool()).unwrap_or(false);
    check(
        fields,
        &format!("{root}.composition_count"),
        json!(1),
        origin,
        &mut rejected,
    );
    check(
        fields,
        &format!("{composition}.flag"),
        json!(true),
        origin,
        &mut rejected,
    );
    let items = number(&format!("{composition}.nonlanguage_item_count")).unwrap_or(0);
    let languages = number(&format!("{composition}.language_item_count")).unwrap_or(0);
    let selections = number(&format!("{composition}.selection_item_count")).unwrap_or(0);
    let groups = number(&format!("{composition}.group_count")).unwrap_or(0);
    let presets = number(&format!("{composition}.preset_count")).unwrap_or(0);
    if items + selections == 0 || groups == 0 || presets == 0 {
        rejected.push(format!(
            "neutral scene requires items, groups and presets at {origin}"
        ));
        return rejected;
    }
    let source_list = |prefix: &str| -> Option<Vec<usize>> {
        let count = number(&format!("{prefix}.source_count"))?;
        let mut result = Vec::with_capacity(count);
        for i in 0..count {
            let index = number(&format!("{prefix}.source_indices[{i}]"))?;
            if index >= sources {
                return None;
            }
            result.push(index);
        }
        result.sort_unstable();
        Some(result)
    };
    let item_sources: Vec<_> = (0..items)
        .map(|i| source_list(&format!("{composition}.nonlanguage_items[{i}]")))
        .collect();
    let language_sources: Vec<_> = (0..languages)
        .map(|i| source_list(&format!("{composition}.language_items[{i}]")))
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
            let p = format!("{composition}.selection_items[{i}]");
            let count = number(&format!("{p}.language_item_count"))?;
            let mut shared = None;
            let mut fallback = false;
            for j in 0..count {
                let index = number(&format!("{p}.language_item_indices[{j}]"))?;
                let entry = language_sources.get(index)?.as_ref()?;
                if shared.as_ref().is_some_and(|previous| previous != entry) {
                    return None;
                }
                shared = Some(entry.clone());
                fallback |= flag(&format!("{composition}.language_items[{index}].flag_a"));
            }
            if fallback { shared } else { None }
        })
        .collect();
    for preset in 0..presets {
        let mut routes = Vec::with_capacity(groups);
        for group in 0..groups {
            let p = format!("{composition}.groups[{group}]");
            let controls = format!("{p}.controls[{preset}]");
            // Parameters activate the selected member; descriptive primary and
            // secondary flags do not make an otherwise muted group audible.
            if !flag(&format!("{controls}.parameters_present")) {
                routes.push(Some(Vec::new()));
                continue;
            }
            // Ranges and parameters 2..5 describe presentation controls. Only
            // the explicit gain changes these raw PCM sources; 256 is unity.
            if flag(&format!("{controls}.parameter_1_present")) {
                check(
                    fields,
                    &format!("{controls}.parameter_1"),
                    json!(256),
                    origin,
                    &mut rejected,
                );
            }
            let member = number(&format!("{controls}.parameter_0")).unwrap_or(usize::MAX);
            let count = number(&format!("{p}.item_count")).unwrap_or(0);
            let route = if member < count {
                number(&format!("{p}.item_indices[{member}]"))
                    .and_then(|i| item_sources.get(i))
                    .cloned()
                    .flatten()
            } else {
                let member = member.saturating_sub(count);
                number(&format!("{p}.selection_indices[{member}]"))
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
        let categories = number(&format!("{composition}.category_count")).unwrap_or(0);
        for category in 0..categories {
            let p = format!("{composition}.categories[{category}]");
            let count = number(&format!("{p}.group_count")).unwrap_or(0);
            let mut members = Vec::new();
            for i in 0..count {
                if let Some(group) = number(&format!("{p}.group_indices[{i}]"))
                    && group < groups
                {
                    if std::mem::replace(&mut categorized[group], true) {
                        rejected.push(format!("overlapping scene categories require presentation selection at {origin}"));
                    }
                    members.push(group);
                }
            }
            let choices: Vec<_> = if flag(&format!("{p}.members_present")) {
                number(&format!("{p}.members[{preset}]"))
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
    pub fn from_cookie(parsed: &CookieReport) -> Self {
        Self::for_layout(parsed, 2, 101, 0, &[1])
    }
    pub(super) fn for_layout(
        parsed: &CookieReport,
        channels: u64,
        family: u64,
        level: u64,
        types: &[u8],
    ) -> Self {
        let fields = &parsed.fields;
        let mut rejected = Vec::new();
        for (name, value) in [
            ("global.profile_id", 31),
            ("global.level_id", level),
            ("global.parameter_b", 2),
            ("box.version_flags", 0),
            ("bitstream_version", 0x0800),
            ("global.frame_size_index", 0),
            ("global.channel_count", channels),
            ("global.component_count", 1),
            ("components[0].type", 0),
            ("components[0].lowest_channel_index", 0),
            ("components[0].tce_count", types.len() as u64),
            (
                "components[0].tce[0].type",
                u64::from(types.first().copied().unwrap_or(0)),
            ),
            ("components[0].parameter_0", 0),
            ("components[0].parameter_1", 0),
            ("components[0].layout_family", family),
        ] {
            check(fields, name, json!(value), "cookie", &mut rejected);
        }
        for (i, kind) in types.iter().enumerate().skip(1) {
            check(
                fields,
                &format!("components[0].tce[{i}].type"),
                json!(kind),
                "cookie",
                &mut rejected,
            );
        }
        for name in [
            "global.flag_a",
            "global.flag_c",
            "global.additional_asc_present",
            "components[0].lbr_flag",
            "ancillary.scene_graph_present",
            "ancillary.metadata_present",
            "ancillary.custom_data_present",
            "components[0].remapping_present",
        ] {
            check(fields, name, json!(false), "cookie", &mut rejected);
        }
        let scene = fields
            .iter()
            .find(|f| f.name == "ancillary.audio_scenes_present");
        let scene_present = scene.is_some_and(|f| f.value == json!(true));
        if scene_present {
            rejected.extend(neutral_scene(
                fields,
                "cookie",
                fields
                    .iter()
                    .any(|f| f.name == "ancillary.loudness_drc_present" && f.value == json!(true)),
            ));
        } else {
            check(
                fields,
                "ancillary.audio_scenes_present",
                json!(false),
                "cookie",
                &mut rejected,
            );
        }
        // The cookie parser already checks the bounded ContentOrigin grammar,
        // every extension type, its termination and zero padding. No byte/CRC whitelist.
        for field in fields
            .iter()
            .filter(|f| f.name.starts_with("extensions[") && f.name.ends_with(".type"))
        {
            check(fields, &field.name, json!(3), "cookie", &mut rejected);
        }
        if !matches!(
            parsed.derived.get("sample_rate_hz").and_then(Value::as_u64),
            Some(44100 | 48000)
        ) {
            if let Some(f) = fields.iter().find(|f| f.name == "global.sample_rate_index") {
                rejected.push(format!(
                    "{}={} at cookie bit {} (expected 44.1/48 kHz)",
                    f.name, f.value, f.bit_offset
                ));
            } else {
                rejected.push(
                    "sample rate missing at cookie bit unknown (expected 44.1/48 kHz)".into(),
                );
            }
        }
        if !parsed.is_complete() {
            rejected.push(format!(
                "cookie status={:?} at cookie bit {} (expected complete)",
                parsed.status,
                parsed
                    .unknown_ranges
                    .first()
                    .map_or(parsed.cookie_bytes * 8, |r| r.bit_offset)
            ));
        }
        let syntax_rejection = (!rejected.is_empty()).then(|| rejected.join("; "));
        Self {
            syntax_rejection,
            scene_present,
            rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
        }
    }
}
