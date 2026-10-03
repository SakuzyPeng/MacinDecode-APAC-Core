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
        Ok(super::parse_cookie_and_config(cookie)?.1)
    }
    /// Nothing read yet; the parser fills fields as the syntax reaches them.
    pub(super) fn empty(report: &CookieReport) -> Self {
        Self {
            cookie_sha256: report.cookie_sha256.clone(),
            cookie_bytes: report.cookie_bytes,
            status: report.status.clone(),
            first_unknown_bit: None,
            version_flags: None,
            bitstream_version: None,
            global: Global::default(),
            components: Vec::new(),
            additional_components: Vec::new(),
            ancillary: Ancillary::default(),
            extensions: Vec::new(),
        }
    }
    /// Completion state and the recorded syntax carried for reports.
    pub(super) fn finish(&mut self, report: &CookieReport) {
        self.status = report.status.clone();
        self.first_unknown_bit = report.unknown_ranges.first().map(|u| u.bit_offset);
        self.ancillary.drc.finish(report);
        if let Some(graph) = &mut self.ancillary.scene_graph {
            graph.trace = report
                .fields
                .iter()
                .filter(|f| f.name.starts_with("ancillary.scene_graph."))
                .cloned()
                .collect();
        }
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
}

impl DrcDeclaration {
    /// Completion, source identity and the recorded syntax carried into DRC
    /// payload reports; the parser fills the qualified values themselves.
    pub(super) fn finish(&mut self, report: &CookieReport) {
        const ROOT: &str = "ancillary.loudness_drc";
        self.complete = report.is_complete();
        self.source_sha256 = report.cookie_sha256.clone();
        self.sample_rate_hz = report.derived.get("sample_rate_hz").and_then(Value::as_u64);
        self.loudness_metadata = report
            .fields
            .iter()
            .filter(|f| f.name.starts_with(&format!("{ROOT}.loudness.")))
            .cloned()
            .collect();
        self.fields = report
            .fields
            .iter()
            .filter(|f| {
                f.name.starts_with(&format!("{ROOT}."))
                    && !f.name.starts_with(&format!("{ROOT}.loudness."))
            })
            .cloned()
            .collect();
    }
}
