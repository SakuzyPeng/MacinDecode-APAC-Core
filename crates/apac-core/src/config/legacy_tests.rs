//! The former name-based extraction from a recorded cookie report, kept as
//! the oracle for the typed configuration the parser builds directly.
use super::*;
use crate::prelude::*;

impl Config {
    /// Centralized name-based extraction from a recorded parse; the cookie
    /// digest, size and status come from the parse, as the cookie report took them.
    pub(crate) fn from_report(config: &Config, report: &Recording) -> Self {
        let r = Lookup::new(config, report);
        let global = Global {
            profile_id: r.u64("global.profile_id"),
            level_id: r.u64("global.level_id"),
            flag_a: r.flag("global.flag_a"),
            sample_rate_index: r.u64("global.sample_rate_index"),
            frame_size_index: r.u64("global.frame_size_index"),
            channel_count: r.u64("global.channel_count"),
            parameter_b: r.u64("global.parameter_b"),
            flag_c: r.flag("global.flag_c"),
            component_count: r.u64("global.component_count"),
            additional_asc_present: r.flag("global.additional_asc_present"),
            additional_component_count: r.u64("global.additional_component_count"),
            sample_rate_hz: r.derived_u64("sample_rate_hz"),
            channels: r.derived_u64("channels"),
            frame_samples: r.derived_u64("frame_samples"),
        };
        let components = (0..)
            .map_while(|i| {
                let p = format!("components[{i}]");
                r.has(&format!("{p}.lowest_channel_index"))
                    .then(|| component(&r, &p))
            })
            .collect();
        let additional_components = (0..)
            .map_while(|i| {
                let p = format!("additional_components[{i}]");
                r.has(&format!("{p}.lowest_channel_index"))
                    .then(|| AdditionalComponent {
                        lowest_channel_index: r.u64(&format!("{p}.lowest_channel_index")),
                        kind: r.u64(&format!("{p}.type")),
                        parameter_0: r.u64(&format!("{p}.parameter_0")),
                        parameter_1: r.u64(&format!("{p}.parameter_1")),
                        channels: r.derived_u64(&format!("{p}.channels")),
                    })
            })
            .collect();
        let extensions = (0..)
            .map_while(|n| {
                let p = format!("extensions[{n}]");
                r.has(&format!("{p}.type")).then(|| Extension {
                    kind: r.u64(&format!("{p}.type")),
                    opaque_payload: r.has(&format!("{p}.opaque_payload")),
                    extra_payload: r.has(&format!("{p}.extra_payload")),
                    padding: r.u64(&format!("{p}.padding")).get(),
                })
            })
            .collect();
        let ancillary = Ancillary {
            scene_graph_present: r.flag("ancillary.scene_graph_present"),
            audio_scenes_present: r.flag("ancillary.audio_scenes_present"),
            loudness_drc_present: r.flag("ancillary.loudness_drc_present"),
            metadata_present: r.flag("ancillary.metadata_present"),
            custom_data_present: r.flag("ancillary.custom_data_present"),
            custom_data_flag_a: r.flag("ancillary.custom_data.flag_a"),
            scene_graph: report
                .derived
                .get("ancillary.scene_graph.syntax")
                .map(|syntax| SceneGraph {
                    positions: match syntax {
                        FieldValue::Positions(positions) => positions.clone(),
                        other => panic!("position syntax {other:?}"),
                    },
                    trace_sha256: crate::model::sha256(
                        &serde_json::to_vec(
                            &report
                                .fields
                                .iter()
                                .filter(|f| f.name.starts_with("ancillary.scene_graph."))
                                .collect::<Vec<_>>(),
                        )
                        .unwrap(),
                    ),
                }),
            audio_scenes: AudioScenes::from_lookup(&r),
            drc: DrcDeclaration::from_lookup(&r),
        };
        Self {
            cookie_sha256: config.cookie_sha256.clone(),
            cookie_bytes: config.cookie_bytes,
            status: config.status.clone(),
            first_unknown_bit: report.unknown_ranges.first().map(|u| u.bit_offset),
            version_flags: r.u64("box.version_flags"),
            bitstream_version: r.u64("bitstream_version"),
            global,
            components,
            additional_components,
            ancillary,
            extensions,
        }
    }
}

fn component(r: &Lookup<'_>, p: &str) -> Component {
    let h = format!("{p}.hoa");
    let hoa = HoaAsc {
        full_order: r.flag(&format!("{h}.full_order")),
        flag_a: r.flag(&format!("{h}.flag_a")),
        flag_b: r.flag(&format!("{h}.flag_b")),
        flag_c: r.flag(&format!("{h}.flag_c")),
        flag_d: r.flag(&format!("{h}.flag_d")),
        flag_e: r.flag(&format!("{h}.flag_e")),
        flag_f: r.flag(&format!("{h}.flag_f")),
        dynamic_selection_config_present: r.flag(&format!("{h}.dynamic_selection_config_present")),
        dynamic_selection_parameter: r.u64(&format!("{h}.dynamic_selection.parameter")),
        dynamic_selection_subbands_minus_one: r
            .u64(&format!("{h}.dynamic_selection.subbands_minus_one")),
        parameter_0: r.u64(&format!("{h}.parameter_0")),
        parameter_1: r.u64(&format!("{h}.parameter_1")),
        parameter_2_minus_six: r.u64(&format!("{h}.parameter_2_minus_six")),
        order: r.u64(&format!("{h}.order")),
        coefficient_count_minus_one: r.u64(&format!("{h}.coefficient_count_minus_one")),
        max_salient_components: r.u64(&format!("{h}.max_salient_components")),
        ambient_components_encoded: r.u64(&format!("{h}.ambient_components_encoded")),
        salient: (0..)
            .map_while(|i| {
                let q = format!("{h}.salient[{i}]");
                r.has(&format!("{q}.subbands_minus_one"))
                    .then(|| HoaSalientDeclaration {
                        subbands_minus_one: r.u64(&format!("{q}.subbands_minus_one")),
                        order: r.u64(&format!("{q}.order")),
                    })
            })
            .collect(),
        ambient_selection_present: r.flag(&format!("{h}.ambient_selection_present")),
        parameter_3_present: r.flag(&format!("{h}.parameter_3_present")),
        tce_count: r.u64(&format!("{h}.tce_count")),
        tce_types: r.located_u64_matching(|n| {
            n.strip_prefix(h.as_str())
                .is_some_and(|n| n.starts_with(".tce[") && n.ends_with("].type"))
        }),
        remapping: r.u64_matching(&format!("{h}.remapping[")),
        remapping_tail: r.u64_matching(&format!("{h}.remapping_tail[")),
        coefficient_count: r.derived_u64(&format!("{h}.coefficient_count")),
        ambient_selection: r.derived_u64_array(&format!("{h}.ambient_selection")),
        parameter_3: r.derived_u64(&format!("{h}.parameter_3")),
        remapping_core_to_transport: r
            .derived_u64_array(&format!("{h}.remapping_core_to_transport")),
        channel_labels: r.derived_u64_array(&format!("{p}.channel_labels")),
    };
    Component {
        lowest_channel_index: r.u64(&format!("{p}.lowest_channel_index")),
        kind: r.u64(&format!("{p}.type")),
        parameter_0: r.u64(&format!("{p}.parameter_0")),
        parameter_1: r.u64(&format!("{p}.parameter_1")),
        channels: r.derived_u64(&format!("{p}.channels")),
        layout_tag: r.derived_u64(&format!("{p}.layout_tag")),
        effective_component_index: r.derived_u64(&format!("{p}.effective_component_index")),
        lbr_flag: r.flag(&format!("{p}.lbr_flag")),
        tce_count: r.u64(&format!("{p}.tce_count")),
        tce_types: (0..)
            .map_while(|t| r.u64(&format!("{p}.tce[{t}].type")))
            .collect(),
        layout_family: r.u64(&format!("{p}.layout_family")),
        remapping_present: r.flag(&format!("{p}.remapping_present")),
        hoa,
    }
}

impl AudioScenes {
    /// The scene syntax of a cookie or of an in-band update report.
    pub(crate) fn from_fields(fields: &[ConfigField]) -> Self {
        Self::from_lookup(&Lookup::fields(fields))
    }
    fn from_lookup(r: &Lookup<'_>) -> Self {
        let c = "ancillary.audio_scenes.compositions[0]";
        let number = |name: &str| r.u64(name).get();
        let flag = |name: &str| r.flag(name).get();
        let indices = |prefix: &str| -> Vec<u64> {
            (0..)
                .map_while(|i| number(&format!("{prefix}[{i}]")))
                .collect()
        };
        let sources = |p: &str| SceneSources {
            source_count: number(&format!("{p}.source_count")),
            source_indices: indices(&format!("{p}.source_indices")),
        };
        Self {
            composition_count: r.u64("ancillary.audio_scenes.composition_count"),
            flag: r.flag(&format!("{c}.flag")),
            nonlanguage_item_count: number(&format!("{c}.nonlanguage_item_count")),
            language_item_count: number(&format!("{c}.language_item_count")),
            selection_item_count: number(&format!("{c}.selection_item_count")),
            group_count: number(&format!("{c}.group_count")),
            preset_count: number(&format!("{c}.preset_count")),
            category_count: number(&format!("{c}.category_count")),
            nonlanguage_items: (0..)
                .map_while(|i| {
                    let p = format!("{c}.nonlanguage_items[{i}]");
                    r.has(&format!("{p}.source_count")).then(|| sources(&p))
                })
                .collect(),
            language_items: (0..)
                .map_while(|i| {
                    let p = format!("{c}.language_items[{i}]");
                    r.has(&format!("{p}.source_count"))
                        .then(|| SceneLanguageItem {
                            sources: sources(&p),
                            flag_a: flag(&format!("{p}.flag_a")),
                        })
                })
                .collect(),
            selection_items: (0..)
                .map_while(|i| {
                    let p = format!("{c}.selection_items[{i}]");
                    r.has(&format!("{p}.language_item_count"))
                        .then(|| SceneSelection {
                            language_item_count: number(&format!("{p}.language_item_count")),
                            language_item_indices: indices(&format!("{p}.language_item_indices")),
                        })
                })
                .collect(),
            groups: (0..)
                .map_while(|g| {
                    let p = format!("{c}.groups[{g}]");
                    r.has(&format!("{p}.item_count")).then(|| SceneGroup {
                        item_count: number(&format!("{p}.item_count")),
                        item_indices: indices(&format!("{p}.item_indices")),
                        selection_indices: indices(&format!("{p}.selection_indices")),
                        controls: (0..)
                            .map_while(|preset| {
                                let q = format!("{p}.controls[{preset}]");
                                r.has(&format!("{q}.primary_flag")).then(|| SceneControls {
                                    parameters_present: flag(&format!("{q}.parameters_present")),
                                    parameter_0: number(&format!("{q}.parameter_0")),
                                    parameter_1_present: flag(&format!("{q}.parameter_1_present")),
                                    parameter_1: r.u64(&format!("{q}.parameter_1")),
                                })
                            })
                            .collect(),
                    })
                })
                .collect(),
            categories: (0..)
                .map_while(|i| {
                    let p = format!("{c}.categories[{i}]");
                    r.has(&format!("{p}.group_count")).then(|| SceneCategory {
                        group_count: number(&format!("{p}.group_count")),
                        group_indices: indices(&format!("{p}.group_indices")),
                        members_present: flag(&format!("{p}.members_present")),
                        members: indices(&format!("{p}.members")),
                    })
                })
                .collect(),
        }
    }
}

impl DrcDeclaration {
    /// The DRC declaration of a cookie or of an in-band header report.
    pub(crate) fn from_report(config: &Config, report: &Recording) -> Self {
        Self::from_lookup(&Lookup::new(config, report))
    }
    fn from_lookup(r: &Lookup<'_>) -> Self {
        const ROOT: &str = "ancillary.loudness_drc";
        let fields = r.report_fields;
        Self {
            complete: r.complete,
            source_sha256: r.sha256.into(),
            sample_rate_hz: r.derived_u64("sample_rate_hz"),
            header_present: r.flag(&format!("{ROOT}.header_present")),
            config_present: r.flag(&format!("{ROOT}.config_present")),
            coefficient_count: r.u64(&format!("{ROOT}.coefficient_count")),
            base_channel_count: r.u64(&format!("{ROOT}.base_channel_count")),
            coefficients: (0..)
                .map_while(|i| {
                    let p = format!("{ROOT}.coefficients[{i}]");
                    r.has(&format!("{p}.location")).then(|| DrcCoefficients {
                        location: r.u64(&format!("{p}.location")),
                        frame_size_present: r.flag(&format!("{p}.frame_size_present")),
                        frame_size_minus_one: r.u64(&format!("{p}.frame_size_minus_one")),
                        gain_sequence_count: r.u64(&format!("{p}.gain_sequence_count")),
                        gain_set_count: r.u64(&format!("{p}.gain_set_count")),
                        gain_sets: (0..)
                            .map_while(|j| {
                                let q = format!("{p}.gain_sets[{j}]");
                                r.has(&format!("{q}.coding_profile")).then(|| DrcGainSet {
                                    coding_profile: r.u64(&format!("{q}.coding_profile")),
                                    band_count: r.u64(&format!("{q}.band_count")),
                                    interpolation_type: r.flag(&format!("{q}.interpolation_type")),
                                    full_frame: r.flag(&format!("{q}.full_frame")),
                                    time_alignment: r.flag(&format!("{q}.time_alignment")),
                                    time_delta_min_present: r
                                        .flag(&format!("{q}.time_delta_min_present")),
                                    time_delta_min_minus_one: r
                                        .u64(&format!("{q}.time_delta_min_minus_one")),
                                    band_sequence_indices: (0..)
                                        .map_while(|b| {
                                            r.derived_u64(&format!("{q}.bands[{b}].sequence_index"))
                                        })
                                        .collect(),
                                })
                            })
                            .collect(),
                    })
                })
                .collect(),
            instruction_effects: fields
                .iter()
                .filter(|f| {
                    f.name.starts_with(&format!("{ROOT}.instructions["))
                        && f.name.ends_with(".effect")
                })
                .map(|f| f.value.as_u64().unwrap_or(u64::MAX))
                .collect(),
            channel_layout_present: r.flag(&format!("{ROOT}.channel_layout_present")),
            downmix_instructions_present: r.flag(&format!("{ROOT}.downmix_instructions_present")),
            loudness_eq_present: r.flag(&format!("{ROOT}.loudness_eq_present")),
            eq_present: r.flag(&format!("{ROOT}.eq_present")),
            scene_extension_present: r.flag(&format!("{ROOT}.scene_extension_present")),
            loudness_extensions_present: r.flag(&format!("{ROOT}.loudness.extensions_present")),
            nested_declarations: fields.iter().any(|f| {
                f.value == FieldValue::Bool(true)
                    && (f.name.ends_with(".downmix_id_present")
                        || f.name.ends_with(".requires_eq")
                        || f.name.ends_with(".depends_on_set_present"))
            }),
            loudness_metadata: fields
                .iter()
                .filter(|f| f.name.starts_with(&format!("{ROOT}.loudness.")))
                .cloned()
                .collect(),
            fields: fields
                .iter()
                .filter(|f| {
                    f.name.starts_with(&format!("{ROOT}."))
                        && !f.name.starts_with(&format!("{ROOT}.loudness."))
                })
                .cloned()
                .collect(),
        }
    }
}

/// First-match name index over a report, the lookup rule `check` used.
struct Lookup<'a> {
    index: BTreeMap<&'a str, &'a ConfigField>,
    report_fields: &'a [ConfigField],
    derived: Option<&'a BTreeMap<String, FieldValue>>,
    complete: bool,
    sha256: &'a str,
}
impl<'a> Lookup<'a> {
    fn new(config: &'a Config, report: &'a Recording) -> Self {
        Self {
            derived: Some(&report.derived),
            complete: config.status == ParseStatus::Complete,
            sha256: &config.cookie_sha256,
            ..Self::fields(&report.fields)
        }
    }
    fn fields(fields: &'a [ConfigField]) -> Self {
        let mut index = BTreeMap::new();
        for field in fields {
            index.entry(field.name.as_str()).or_insert(field);
        }
        Self {
            index,
            report_fields: fields,
            derived: None,
            complete: true,
            sha256: "",
        }
    }
    fn has(&self, name: &str) -> bool {
        self.index.contains_key(name)
    }
    fn located<T>(&self, name: &str, read: impl Fn(&FieldValue) -> Option<T>) -> Field<T> {
        let field = self.index.get(name)?;
        Some(Located {
            value: read(&field.value)?,
            bit_offset: field.bit_offset,
        })
    }
    fn u64(&self, name: &str) -> Field<u64> {
        self.located(name, FieldValue::as_u64)
    }
    fn flag(&self, name: &str) -> Field<bool> {
        self.located(name, FieldValue::as_bool)
    }
    fn located_u64_matching(&self, matches: impl Fn(&str) -> bool) -> Vec<Located<u64>> {
        self.report_fields
            .iter()
            .filter(|f| matches(&f.name))
            .map(|f| Located {
                value: f.value.as_u64().unwrap_or(u64::MAX),
                bit_offset: f.bit_offset,
            })
            .collect()
    }
    fn u64_matching(&self, prefix: &str) -> Vec<u64> {
        self.report_fields
            .iter()
            .filter(|f| f.name.starts_with(prefix))
            .filter_map(|f| f.value.as_u64())
            .collect()
    }
    fn derived_u64(&self, key: &str) -> Option<u64> {
        self.derived?.get(key)?.as_u64()
    }
    fn derived_u64_array(&self, key: &str) -> Option<Vec<u64>> {
        match self.derived?.get(key)? {
            FieldValue::U64s(values) => Some(values.clone()),
            FieldValue::I64s(values) => values.iter().map(|&v| u64::try_from(v).ok()).collect(),
            _ => None,
        }
    }
}
