//! Typed configuration: the cookie values that decoder eligibility and frame
//! contexts read, each wire field with the bit offset its rejection text cites.
//!
//! A field that the syntax did not reach (partial parse, absent branch) is
//! `None`, which keeps the `=missing at … bit unknown` texts distinct from a
//! mismatching value. Derived values carry no position.
use super::{ConfigField, CookieReport, ParseError, ParseStatus, passive::PositionSyntax};
use crate::prelude::*;
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Located<T> {
    pub value: T,
    pub bit_offset: usize,
}
pub type Field<T> = Option<Located<T>>;

pub(crate) trait FieldExt<T> {
    fn get(&self) -> Option<T>;
    fn is(&self, value: T) -> bool;
}
impl<T: Copy + PartialEq> FieldExt<T> for Field<T> {
    fn get(&self) -> Option<T> {
        self.map(|f| f.value)
    }
    fn is(&self, value: T) -> bool {
        self.is_some_and(|f| f.value == value)
    }
}

/// A parsed cookie as the decoder sees it. Built once per cookie.
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    pub(crate) cookie_sha256: String,
    pub(crate) cookie_bytes: usize,
    pub(crate) status: ParseStatus,
    pub(crate) first_unknown_bit: Option<usize>,
    pub(crate) version_flags: Field<u64>,
    pub(crate) bitstream_version: Field<u64>,
    pub(crate) global: Global,
    pub(crate) components: Vec<Component>,
    pub(crate) additional_components: Vec<AdditionalComponent>,
    pub(crate) ancillary: Ancillary,
    pub(crate) extensions: Vec<Extension>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Global {
    pub profile_id: Field<u64>,
    pub level_id: Field<u64>,
    pub flag_a: Field<bool>,
    pub sample_rate_index: Field<u64>,
    pub frame_size_index: Field<u64>,
    pub channel_count: Field<u64>,
    pub parameter_b: Field<u64>,
    pub flag_c: Field<bool>,
    pub component_count: Field<u64>,
    pub additional_asc_present: Field<bool>,
    pub additional_component_count: Field<u64>,
    pub sample_rate_hz: Option<u64>,
    pub channels: Option<u64>,
    pub frame_samples: Option<u64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Component {
    pub lowest_channel_index: Field<u64>,
    pub kind: Field<u64>,
    pub parameter_0: Field<u64>,
    pub parameter_1: Field<u64>,
    pub channels: Option<u64>,
    pub layout_tag: Option<u64>,
    pub effective_component_index: Option<u64>,
    pub lbr_flag: Field<bool>,
    pub tce_count: Field<u64>,
    pub tce_types: Vec<Located<u64>>,
    pub layout_family: Field<u64>,
    pub remapping_present: Field<bool>,
    pub hoa: HoaAsc,
}

/// The `components[i].hoa.*` syntax plus its derived values.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct HoaAsc {
    pub full_order: Field<bool>,
    pub flag_a: Field<bool>,
    pub flag_b: Field<bool>,
    pub flag_c: Field<bool>,
    pub flag_d: Field<bool>,
    pub flag_e: Field<bool>,
    pub flag_f: Field<bool>,
    pub dynamic_selection_config_present: Field<bool>,
    pub dynamic_selection_parameter: Field<u64>,
    pub dynamic_selection_subbands_minus_one: Field<u64>,
    pub parameter_0: Field<u64>,
    pub parameter_1: Field<u64>,
    pub parameter_2_minus_six: Field<u64>,
    pub order: Field<u64>,
    pub coefficient_count_minus_one: Field<u64>,
    pub max_salient_components: Field<u64>,
    pub ambient_components_encoded: Field<u64>,
    pub salient: Vec<HoaSalientDeclaration>,
    pub ambient_selection_present: Field<bool>,
    pub parameter_3_present: Field<bool>,
    pub tce_count: Field<u64>,
    pub tce_types: Vec<Located<u64>>,
    pub remapping: Vec<u64>,
    pub remapping_tail: Vec<u64>,
    pub coefficient_count: Option<u64>,
    pub ambient_selection: Option<Vec<u64>>,
    pub parameter_3: Option<u64>,
    pub remapping_core_to_transport: Option<Vec<u64>>,
    pub channel_labels: Option<Vec<u64>>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct HoaSalientDeclaration {
    pub subbands_minus_one: Field<u64>,
    pub order: Field<u64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct AdditionalComponent {
    pub lowest_channel_index: Field<u64>,
    pub kind: Field<u64>,
    pub parameter_0: Field<u64>,
    pub parameter_1: Field<u64>,
    pub channels: Option<u64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Ancillary {
    pub scene_graph_present: Field<bool>,
    pub audio_scenes_present: Field<bool>,
    pub loudness_drc_present: Field<bool>,
    pub metadata_present: Field<bool>,
    pub custom_data_present: Field<bool>,
    pub custom_data_flag_a: Field<bool>,
    pub scene_graph: Option<SceneGraph>,
    pub audio_scenes: AudioScenes,
    pub drc: DrcDeclaration,
}

/// Initial passive positions. `trace` is the recorded declaration; its JSON
/// seeds the scene-graph history digest.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneGraph {
    pub positions: Vec<PositionSyntax>,
    pub trace: Vec<ConfigField>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Extension {
    pub kind: Field<u64>,
    pub opaque_payload: bool,
    pub extra_payload: bool,
    pub padding: Option<u64>,
}

/// `ancillary.audio_scenes`, limited to what neutral routing reads from the
/// first composition. Used for the cookie and for in-band scene updates.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct AudioScenes {
    pub composition_count: Field<u64>,
    pub flag: Field<bool>,
    pub nonlanguage_item_count: Option<u64>,
    pub language_item_count: Option<u64>,
    pub selection_item_count: Option<u64>,
    pub group_count: Option<u64>,
    pub preset_count: Option<u64>,
    pub category_count: Option<u64>,
    pub nonlanguage_items: Vec<SceneSources>,
    pub language_items: Vec<SceneLanguageItem>,
    pub selection_items: Vec<SceneSelection>,
    pub groups: Vec<SceneGroup>,
    pub categories: Vec<SceneCategory>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneSources {
    pub source_count: Option<u64>,
    pub source_indices: Vec<u64>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneLanguageItem {
    pub sources: SceneSources,
    pub flag_a: Option<bool>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneSelection {
    pub language_item_count: Option<u64>,
    pub language_item_indices: Vec<u64>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneGroup {
    pub item_count: Option<u64>,
    pub item_indices: Vec<u64>,
    pub selection_indices: Vec<u64>,
    pub controls: Vec<SceneControls>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneControls {
    pub parameters_present: Option<bool>,
    pub parameter_0: Option<u64>,
    pub parameter_1_present: Option<bool>,
    pub parameter_1: Field<u64>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct SceneCategory {
    pub group_count: Option<u64>,
    pub group_indices: Vec<u64>,
    pub members_present: Option<bool>,
    pub members: Vec<u64>,
}

/// `ancillary.loudness_drc`, from the cookie or an in-band header. Only the
/// declaration subset the off-policy DRC path qualifies; `fields` and
/// `loudness_metadata` carry the recorded syntax into DRC payload reports.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct DrcDeclaration {
    pub complete: bool,
    pub source_sha256: String,
    pub sample_rate_hz: Option<u64>,
    pub header_present: Field<bool>,
    pub config_present: Field<bool>,
    pub coefficient_count: Field<u64>,
    pub base_channel_count: Field<u64>,
    pub coefficients: Vec<DrcCoefficients>,
    pub instruction_effects: Vec<u64>,
    pub channel_layout_present: Field<bool>,
    pub downmix_instructions_present: Field<bool>,
    pub loudness_eq_present: Field<bool>,
    pub eq_present: Field<bool>,
    pub scene_extension_present: Field<bool>,
    pub loudness_extensions_present: Field<bool>,
    /// A downmix target, EQ requirement or set dependency is declared.
    pub nested_declarations: bool,
    pub fields: Vec<ConfigField>,
    pub loudness_metadata: Vec<ConfigField>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct DrcCoefficients {
    pub location: Field<u64>,
    pub frame_size_present: Field<bool>,
    pub frame_size_minus_one: Field<u64>,
    pub gain_sequence_count: Field<u64>,
    pub gain_set_count: Field<u64>,
    pub gain_sets: Vec<DrcGainSet>,
}
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct DrcGainSet {
    pub coding_profile: Field<u64>,
    pub band_count: Field<u64>,
    pub interpolation_type: Field<bool>,
    pub full_frame: Field<bool>,
    pub time_alignment: Field<bool>,
    pub time_delta_min_present: Field<bool>,
    pub time_delta_min_minus_one: Field<u64>,
    pub band_sequence_indices: Vec<u64>,
}

impl Config {
    /// Parse a standalone `dapa` cookie once into its typed configuration.
    pub fn parse(cookie: &[u8]) -> Result<Self, ParseError> {
        Ok(Self::from_report(&super::parse_cookie(cookie)?))
    }
    pub fn is_complete(&self) -> bool {
        self.status == ParseStatus::Complete
    }
    pub fn status(&self) -> &ParseStatus {
        &self.status
    }
    pub fn cookie_sha256(&self) -> &str {
        &self.cookie_sha256
    }
    /// Derived global sample rate, when the syntax reached a known index.
    pub fn sample_rate_hz(&self) -> Option<u64> {
        self.global.sample_rate_hz
    }
    pub fn channels(&self) -> Option<u64> {
        self.global.channels
    }
    pub fn frame_samples(&self) -> Option<u64> {
        self.global.frame_samples
    }
    /// One declared ASC and no additional-ASC branch.
    pub fn is_single_component(&self) -> bool {
        self.global.component_count.is(1) && !self.global.additional_asc_present.is(true)
    }
    /// Multiple ASCs or an additional-ASC branch: a composite stream.
    pub fn is_composite(&self) -> bool {
        self.global.component_count.get().is_some_and(|n| n > 1)
            || self.global.additional_asc_present.is(true)
    }
    /// Whether any declared ASC is an HOA component.
    pub fn has_hoa_component(&self) -> bool {
        self.components.iter().any(|c| c.kind.is(2))
    }
    /// The derived layout tag of declared ASC `index` (tagged layouts only).
    pub fn component_layout_tag(&self, index: usize) -> Option<u64> {
        self.component(index).layout_tag
    }
    pub(crate) fn status_rejection(&self) -> Option<String> {
        (!self.is_complete()).then(|| {
            format!(
                "cookie status={:?} at cookie bit {} (expected complete)",
                self.status,
                self.first_unknown_bit.unwrap_or(self.cookie_bytes * 8)
            )
        })
    }
    /// The first declared ASC; absent syntax reads as an empty declaration.
    pub(crate) fn component(&self, index: usize) -> &Component {
        static EMPTY: Component = Component {
            lowest_channel_index: None,
            kind: None,
            parameter_0: None,
            parameter_1: None,
            channels: None,
            layout_tag: None,
            effective_component_index: None,
            lbr_flag: None,
            tce_count: None,
            tce_types: Vec::new(),
            layout_family: None,
            remapping_present: None,
            hoa: HoaAsc {
                full_order: None,
                flag_a: None,
                flag_b: None,
                flag_c: None,
                flag_d: None,
                flag_e: None,
                flag_f: None,
                dynamic_selection_config_present: None,
                dynamic_selection_parameter: None,
                dynamic_selection_subbands_minus_one: None,
                parameter_0: None,
                parameter_1: None,
                parameter_2_minus_six: None,
                order: None,
                coefficient_count_minus_one: None,
                max_salient_components: None,
                ambient_components_encoded: None,
                salient: Vec::new(),
                ambient_selection_present: None,
                parameter_3_present: None,
                tce_count: None,
                tce_types: Vec::new(),
                remapping: Vec::new(),
                remapping_tail: Vec::new(),
                coefficient_count: None,
                ambient_selection: None,
                parameter_3: None,
                remapping_core_to_transport: None,
                channel_labels: None,
            },
        };
        self.components.get(index).unwrap_or(&EMPTY)
    }

    /// A private single-component eligibility view of composite stream
    /// component `index`, so the single-ASC checks can be reused. It keeps the
    /// global syntax, status and this component (as component 0), drops the
    /// other components, extensions and ancillary syntax, and qualifies the
    /// stream-level fields those checks would otherwise reject. An overridden
    /// field keeps its position; a newly supplied one reads as bit 0.
    pub(crate) fn component_view(&self, index: usize, kind: u8, channels: u64) -> Self {
        fn set<T>(field: &mut Field<T>, value: T) {
            let bit_offset = field.as_ref().map_or(0, |f| f.bit_offset);
            *field = Some(Located { value, bit_offset });
        }
        let mut component = self.components.get(index).cloned().unwrap_or_default();
        set(&mut component.lowest_channel_index, 0);
        set(&mut component.parameter_0, 0);
        set(&mut component.parameter_1, 0);
        let mut global = self.global.clone();
        set(&mut global.component_count, 1);
        set(&mut global.channel_count, channels);
        set(&mut global.flag_a, false);
        set(&mut global.flag_c, false);
        set(&mut global.additional_asc_present, false);
        set(&mut global.parameter_b, 2);
        let mut ancillary = Ancillary::default();
        for flag in [
            &mut ancillary.scene_graph_present,
            &mut ancillary.audio_scenes_present,
            &mut ancillary.loudness_drc_present,
            &mut ancillary.metadata_present,
            &mut ancillary.custom_data_present,
        ] {
            set(flag, false);
        }
        if kind == 0 {
            if let Some(layout) = crate::channel_layout::layout(channels) {
                set(&mut global.profile_id, 31);
                set(&mut global.level_id, layout.level);
            }
            set(&mut global.sample_rate_index, 3);
            global.sample_rate_hz = Some(48000);
        }
        global.channels = Some(channels);
        Self {
            cookie_sha256: self.cookie_sha256.clone(),
            cookie_bytes: self.cookie_bytes,
            status: self.status.clone(),
            first_unknown_bit: self.first_unknown_bit,
            version_flags: self.version_flags,
            bitstream_version: self.bitstream_version,
            global,
            components: vec![component],
            additional_components: self.additional_components.clone(),
            ancillary,
            extensions: Vec::new(),
        }
    }

    /// Centralized name-based extraction from a recorded cookie report.
    pub(crate) fn from_report(report: &CookieReport) -> Self {
        let r = Lookup::new(report);
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
                    positions: serde_json::from_value(syntax.clone())
                        .expect("verified position syntax"),
                    trace: report
                        .fields
                        .iter()
                        .filter(|f| f.name.starts_with("ancillary.scene_graph."))
                        .cloned()
                        .collect(),
                }),
            audio_scenes: AudioScenes::from_lookup(&r),
            drc: DrcDeclaration::from_lookup(&r),
        };
        Self {
            cookie_sha256: report.cookie_sha256.clone(),
            cookie_bytes: report.cookie_bytes,
            status: report.status.clone(),
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
    pub(crate) fn from_report(report: &CookieReport) -> Self {
        Self::from_lookup(&Lookup::new(report))
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
                f.value == Value::Bool(true)
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
    derived: Option<&'a BTreeMap<String, Value>>,
    complete: bool,
    sha256: &'a str,
}
impl<'a> Lookup<'a> {
    fn new(report: &'a CookieReport) -> Self {
        Self {
            derived: Some(&report.derived),
            complete: report.is_complete(),
            sha256: &report.cookie_sha256,
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
    fn located<T>(&self, name: &str, read: impl Fn(&Value) -> Option<T>) -> Field<T> {
        let field = self.index.get(name)?;
        Some(Located {
            value: read(&field.value)?,
            bit_offset: field.bit_offset,
        })
    }
    fn u64(&self, name: &str) -> Field<u64> {
        self.located(name, Value::as_u64)
    }
    fn flag(&self, name: &str) -> Field<bool> {
        self.located(name, Value::as_bool)
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
        let values = self.derived?.get(key)?.as_array()?;
        values.iter().map(Value::as_u64).collect()
    }
}
