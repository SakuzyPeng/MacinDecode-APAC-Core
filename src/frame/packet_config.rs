//! Decoder eligibility is narrower than successful cookie syntax parsing.
//! The scene whitelist is the verified neutral, single-source stereo route.
use crate::config::{ConfigField, CookieReport};
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

pub(super) fn neutral_scene(fields: &[ConfigField], origin: &str) -> Vec<String> {
    let mut rejected = Vec::new();
    let root = "ancillary.audio_scenes";
    let mut expect = |suffix: &str, value| {
        check(
            fields,
            &format!("{root}.{suffix}"),
            value,
            origin,
            &mut rejected,
        );
    };
    for suffix in [
        "flags[0]",
        "flags[1]",
        "flags[2]",
        "tag_present",
        "extension_present",
    ] {
        expect(suffix, json!(false));
    }
    expect("parameter", json!(0));
    expect("composition_count", json!(1));
    let p = "compositions[0]";
    expect(&format!("{p}.flag"), json!(true));
    for (suffix, count) in [
        ("nonlanguage_item_count", 1),
        ("language_item_count", 0),
        ("selection_item_count", 0),
        ("group_count", 1),
        ("preset_count", 1),
        ("nonlanguage_items[0].source_count", 1),
        ("nonlanguage_items[0].source_indices[0]", 0),
        ("groups[0].item_count", 1),
        ("groups[0].item_indices[0]", 0),
        ("groups[0].selection_count", 0),
    ] {
        expect(&format!("{p}.{suffix}"), json!(count));
    }
    for suffix in [
        "language_present",
        "preset_selection_data_present",
        "tag_present",
        "selection_updates_present",
        "extension_present",
        "groups[0].extension_present",
    ] {
        expect(&format!("{p}.{suffix}"), json!(false));
    }
    let controls = format!("{p}.groups[0].controls[0]");
    for suffix in [
        "primary_flag",
        "secondary_flag",
        "range_0_present",
        "range_1_present",
        "range_2_present",
        "range_3_present",
    ] {
        expect(&format!("{controls}.{suffix}"), json!(false));
    }
    expect(&format!("{controls}.parameters_present"), json!(true));
    expect(&format!("{controls}.parameter_0"), json!(0));
    for i in 1..=5 {
        expect(&format!("{controls}.parameter_{i}_present"), json!(false));
    }
    // Labels and preset characteristics remain encoded values: accepting this
    // route does not assign unverified meanings to arbitrary scene controls.
    for (item, word, has_presence) in [
        ("nonlanguage_items[0]", 21, true),
        ("groups[0]", 21, true),
        ("presets[0]", 97, false),
    ] {
        let prefix = format!("{p}.{item}");
        if has_presence {
            expect(&format!("{prefix}.tag_present"), json!(true));
        }
        expect(&format!("{prefix}.tag.word_count_minus_one"), json!(0));
        expect(&format!("{prefix}.tag.words[0]"), json!(word));
        expect(&format!("{prefix}.tag.fallback_present"), json!(false));
        expect(&format!("{prefix}.tag.extension_present"), json!(false));
    }
    let preset = format!("{p}.presets[0]");
    expect(&format!("{preset}.characteristics_size_index"), json!(1));
    for i in 0..16 {
        expect(&format!("{preset}.characteristics[{i}]"), json!(i >= 13));
    }
    expect(&format!("{preset}.language.terminator"), json!(0));
    expect(&format!("{preset}.language.flag"), json!(false));
    for field in fields.iter().filter(|f| {
        f.name
            .starts_with(&format!("{root}.{preset}.language.characters["))
    }) {
        rejected.push(format!(
            "{}={} at {origin} bit {} (expected empty preset language)",
            field.name, field.value, field.bit_offset
        ));
    }
    rejected
}

impl PacketConfiguration {
    pub fn from_cookie(parsed: &CookieReport) -> Self {
        let fields = &parsed.fields;
        let mut rejected = Vec::new();
        for (name, value) in [
            ("global.profile_id", 31),
            ("global.level_id", 0),
            ("global.parameter_b", 2),
            ("box.version_flags", 0),
            ("bitstream_version", 0x0800),
            ("global.frame_size_index", 0),
            ("global.channel_count", 2),
            ("global.component_count", 1),
            ("components[0].type", 0),
            ("components[0].lowest_channel_index", 0),
            ("components[0].tce_count", 1),
            ("components[0].tce[0].type", 1),
            ("components[0].parameter_0", 0),
            ("components[0].parameter_1", 0),
            ("components[0].layout_family", 101),
        ] {
            check(fields, name, json!(value), "cookie", &mut rejected);
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
            rejected.extend(neutral_scene(fields, "cookie"));
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
        check(
            fields,
            "ancillary.loudness_drc_present",
            json!(false),
            "cookie",
            &mut rejected,
        );
        Self {
            syntax_rejection,
            scene_present,
            rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
        }
    }
}
