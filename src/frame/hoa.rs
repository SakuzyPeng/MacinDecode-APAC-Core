//! Qualified HOA configurations with distinct transport, recovery and output domains.
use super::{
    AmbientTransform, ChannelFrameContext, ChannelPacketReport, Parser, StaticAmbientData,
    drc::{DrcContext, DrcState},
    packet_config::{self, PacketConfiguration},
};
use crate::{
    config::{self, ParseError},
    model::ChannelLayout,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

pub const NUMERIC_PROFILE: &str = "apac-hoa-ambient-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-ambient-state-v1";
pub const MIXED_NUMERIC_PROFILE: &str = "apac-hoa-mixed-math-v1";
pub const MIXED_STATE_PROFILE: &str = "apac-hoa-mixed-state-v1";

/// One salient component's actual descriptor shape, in cookie order.
///
/// This does not describe the overall recovery domain or output layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct SalientComponentConfiguration {
    pub order: u8,
    pub subband_count: usize,
    pub coefficient_count: usize,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum HoaPath {
    Ambient,
    Salient,
    Mixed,
}
#[derive(Debug, Clone)]
pub(super) struct HoaConfiguration {
    pub order: u8,
    pub profile_id: u8,
    pub level_id: u8,
    /// Output ACN coefficients; transport and core dimensions are independent.
    pub channels: u8,
    pub recovery_slots: u8,
    pub dynamic_method: Option<u8>,
    pub dynamic_subbands: Option<u8>,
    pub transport_channels: u8,
    pub core_channels: u8,
    pub salient_components: u8,
    pub quantization_bits: u8,
    pub salient_configurations: Vec<SalientComponentConfiguration>,
    pub salient_partition_method: u8,
    pub ambient_components: u8,
    pub path: HoaPath,
    pub ambient_selection: Vec<u8>,
    pub explicit_ambient_selection: bool,
    pub ambient_transform: AmbientTransform,
    pub static_ambient: bool,
    pub ambient_combination: super::AmbientCombination,
    pub sample_rate_hz: u64,
    pub preroll_bytes: u64,
}
impl HoaConfiguration {
    fn selected(parsed: &config::CookieReport) -> Self {
        let value = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == name)
                .and_then(|f| f.value.as_u64())
        };
        // Unsupported values select a bounded diagnostic shape, then fail the
        // exact field checks below. No untrusted dimension is used to allocate.
        let dynamic = parsed.fields.iter().any(|f| {
            f.name == "components[0].hoa.dynamic_selection_config_present" && f.value == json!(true)
        });
        let order = if dynamic {
            2
        } else {
            match value("components[0].hoa.order") {
                Some(order @ 0..=10) => order as u8,
                _ => 3,
            }
        };
        let declared_salient = value("components[0].hoa.max_salient_components").unwrap_or(0);
        let salient = declared_salient != 0;
        let declared_ambient = value("components[0].hoa.ambient_components_encoded").unwrap_or(0)
            + u64::from(!salient);
        let path = if salient && declared_ambient != 0 {
            HoaPath::Mixed
        } else if salient {
            HoaPath::Salient
        } else {
            HoaPath::Ambient
        };
        let recovery_slots = (order + 1).pow(2);
        let channels = if dynamic { 16 } else { recovery_slots };
        // The bound decoder allocates 2048 bytes per declared output channel
        // (ASP Initialize); capacity probes independently verify the new sizes.
        let preroll_bytes = u64::from(channels) * 2048;
        // Keep the diagnostic shape bounded; exact cookie checks reject excess counts.
        let salient_components = declared_salient.min(u64::from(recovery_slots)) as u8;
        let ambient_components = declared_ambient.min(u64::from(recovery_slots)) as u8;
        let flag = |name: &str| {
            parsed
                .fields
                .iter()
                .any(|f| f.name == name && f.value == json!(true))
        };
        let explicit_ambient_selection = flag("components[0].hoa.ambient_selection_present");
        let transform_present = flag("components[0].hoa.parameter_3_present");
        let mut ambient_selection: Vec<u8> = (0..recovery_slots).collect();
        if let Some(indices) = parsed
            .derived
            .get("components[0].hoa.ambient_selection")
            .and_then(|v| v.as_array())
        {
            for (slot, value) in ambient_selection.iter_mut().zip(indices) {
                *slot = value
                    .as_u64()
                    .and_then(|v| u8::try_from(v).ok())
                    .unwrap_or(u8::MAX);
            }
        }
        let ambient_transform = match parsed
            .derived
            .get("components[0].hoa.parameter_3")
            .and_then(|v| v.as_u64())
        {
            Some(value @ 1..=3) => AmbientTransform::Fixed {
                index: (value - 1) as u8,
            },
            Some(4) => AmbientTransform::PerFrame,
            _ => AmbientTransform::Disabled,
        };
        Self {
            order,
            profile_id: value("global.profile_id").unwrap_or(5) as u8,
            level_id: value("global.level_id").unwrap_or(0) as u8,
            channels,
            recovery_slots,
            dynamic_method: dynamic
                .then(|| value("components[0].hoa.dynamic_selection.parameter").unwrap_or(3) as u8),
            dynamic_subbands: dynamic.then(|| {
                (value("components[0].hoa.dynamic_selection.subbands_minus_one")
                    .unwrap_or(7)
                    .min(7)
                    + 1) as u8
            }),
            transport_channels: channels,
            core_channels: salient_components + ambient_components,
            salient_components,
            quantization_bits: value("components[0].hoa.parameter_2_minus_six")
                .unwrap_or(0)
                .min(3) as u8
                + 6,
            salient_configurations: (0..salient_components)
                .map(|i| {
                    let order = match value(&format!("components[0].hoa.salient[{i}].order")) {
                        Some(component_order)
                            if salient && (1..=u64::from(order)).contains(&component_order) =>
                        {
                            component_order as u8
                        }
                        _ => order,
                    };
                    SalientComponentConfiguration {
                        order,
                        coefficient_count: (usize::from(order) + 1).pow(2),
                        subband_count: (value(&format!(
                            "components[0].hoa.salient[{i}].subbands_minus_one"
                        ))
                        .unwrap_or(3)
                        .min(15)
                            + 1) as usize,
                    }
                })
                .collect(),
            salient_partition_method: match value("components[0].hoa.parameter_1") {
                Some(method @ 1..=2) if salient => method as u8,
                _ => 0,
            },
            ambient_components,
            path,
            ambient_selection,
            explicit_ambient_selection,
            ambient_transform,
            static_ambient: explicit_ambient_selection
                || transform_present
                || (!salient && ambient_components != channels),
            ambient_combination: if flag("components[0].hoa.flag_d") {
                super::AmbientCombination::Add
            } else {
                super::AmbientCombination::Replace
            },
            sample_rate_hz: if value("global.sample_rate_index") == Some(4) {
                44100
            } else {
                48000
            },
            preroll_bytes,
        }
    }
    pub fn numeric_profile(&self) -> &'static str {
        if self.dynamic_method.is_some() {
            return super::hoa_dynamic::NUMERIC_PROFILE;
        }
        self.recovery_numeric_profile()
    }
    pub fn recovery_numeric_profile(&self) -> &'static str {
        if self.expanded_orders() {
            return super::hoa_salient::EXPANDED_NUMERIC_PROFILE;
        }
        if self.quantization_extended() {
            return super::hoa_salient::QUANTIZATION_NUMERIC_PROFILE;
        }
        if self.ambient_count_extended() {
            return super::hoa_ambient::COUNTS_NUMERIC_PROFILE;
        }
        if self.component_count_extended() {
            return super::hoa_salient::COUNTS_NUMERIC_PROFILE;
        }
        if self.component_orders_extended() {
            return super::hoa_salient::COMPONENT_ORDERS_NUMERIC_PROFILE;
        }
        if self.ambient_combination == super::AmbientCombination::Add {
            return super::hoa_additive::NUMERIC_PROFILE;
        }
        if self.static_ambient {
            return super::hoa_ambient::NUMERIC_PROFILE;
        }
        match self.path {
            HoaPath::Ambient => NUMERIC_PROFILE,
            HoaPath::Salient => {
                super::hoa_salient::numeric_profile(usize::from(self.recovery_slots))
            }
            HoaPath::Mixed => MIXED_NUMERIC_PROFILE,
        }
    }
    pub fn state_profile(&self) -> &'static str {
        if self.expanded_orders() {
            return super::hoa_salient::EXPANDED_STATE_PROFILE;
        }
        if self.quantization_extended() {
            return super::hoa_salient::QUANTIZATION_STATE_PROFILE;
        }
        if self.ambient_count_extended() {
            return super::hoa_ambient::COUNTS_STATE_PROFILE;
        }
        if self.component_count_extended() {
            return super::hoa_salient::COUNTS_STATE_PROFILE;
        }
        if self.component_orders_extended() {
            return super::hoa_salient::COMPONENT_ORDERS_STATE_PROFILE;
        }
        if self.ambient_combination == super::AmbientCombination::Add {
            return super::hoa_additive::STATE_PROFILE;
        }
        if self.dynamic_method.is_some() {
            return super::hoa_dynamic::STATE_PROFILE;
        }
        if self.static_ambient {
            return super::hoa_ambient::STATE_PROFILE;
        }
        match self.path {
            HoaPath::Ambient => STATE_PROFILE,
            HoaPath::Salient => super::hoa_salient::STATE_PROFILE,
            HoaPath::Mixed => MIXED_STATE_PROFILE,
        }
    }
    pub fn mixed_mapping(&self) -> Option<HoaMixedMapping> {
        (self.path == HoaPath::Mixed && self.dynamic_method.is_none()).then(|| HoaMixedMapping {
            ambient_transport_channels: (0..self.ambient_components).collect(),
            salient_transport_channels: (self.ambient_components..self.core_channels).collect(),
            ambient_output_coefficients: self.ambient_indices().to_vec(),
            unused_transport_channels: (self.core_channels..self.transport_channels).collect(),
            descriptor_numeric_profile: self.descriptor_numeric_profile().into(),
        })
    }
    pub fn ambient_indices(&self) -> &[u8] {
        &self.ambient_selection[..usize::from(self.ambient_components)]
    }
    pub fn component_orders_extended(&self) -> bool {
        self.salient_components != 0
            && (self.expanded_orders()
                || self.quantization_extended()
                || self.component_count_extended()
                || self
                    .salient_configurations
                    .iter()
                    .any(|c| c.order != self.order))
    }
    pub fn expanded_orders(&self) -> bool {
        self.order == 0 || self.order > 3
    }
    pub fn quantization_extended(&self) -> bool {
        self.salient_components != 0 && self.quantization_bits != 6
    }
    pub fn ambient_count_extended(&self) -> bool {
        (self.path == HoaPath::Mixed && self.ambient_components != 4)
            || (self.path == HoaPath::Ambient
                && (self.order == 2 || self.ambient_components != self.channels))
    }
    pub fn component_count_extended(&self) -> bool {
        self.salient_components != 0 && self.salient_components != 5
    }
    pub fn salient_dimensions(&self) -> Vec<usize> {
        self.salient_configurations
            .iter()
            .map(|c| c.coefficient_count)
            .collect()
    }
    pub fn descriptor_numeric_profile(&self) -> &'static str {
        if self.salient_configurations.iter().any(|c| c.order > 3) {
            return super::hoa_salient::EXPANDED_NUMERIC_PROFILE;
        }
        if self.quantization_extended() {
            return super::hoa_salient::QUANTIZATION_NUMERIC_PROFILE;
        }
        if self.component_orders_extended() {
            super::hoa_salient::COMPONENT_ORDERS_NUMERIC_PROFILE
        } else {
            super::hoa_salient::numeric_profile(usize::from(self.recovery_slots))
        }
    }
    pub fn component_order_info(&self) -> Option<Vec<super::SalientComponentOrderInfo>> {
        self.component_orders_extended().then(|| {
            let orders: Vec<_> = self
                .salient_configurations
                .iter()
                .map(|c| c.order)
                .collect();
            super::hoa_salient::component_information(&orders, self.quantization_bits)
        })
    }
}

fn profile_channel_limit(profile: u8, level: u8) -> Option<u64> {
    #[derive(Deserialize)]
    struct Entry {
        profile_id: u8,
        maximum_output_channels_by_level: Vec<u64>,
    }
    #[derive(Deserialize)]
    struct Table {
        profiles: Vec<Entry>,
    }
    static TABLE: std::sync::OnceLock<Table> = std::sync::OnceLock::new();
    TABLE
        .get_or_init(|| {
            serde_json::from_str(include_str!("../../data/hoa-profile-levels-v1.json"))
                .expect("HOA profile limits")
        })
        .profiles
        .iter()
        .find(|e| e.profile_id == profile)?
        .maximum_output_channels_by_level
        .get(usize::from(level))
        .copied()
}

#[derive(Debug, Clone, Serialize)]
pub struct HoaFrameContext {
    pub(crate) transport: ChannelFrameContext,
    #[serde(skip)]
    pub(super) configuration: HoaConfiguration,
}
impl HoaFrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        let parsed = config::parse_cookie(cookie)?;
        let shape = HoaConfiguration::selected(&parsed);
        let salient = shape.salient_components != 0;
        let channels = u64::from(shape.channels);
        let mut rejected = Vec::new();
        if profile_channel_limit(shape.profile_id, shape.level_id)
            .is_none_or(|maximum| channels > maximum)
        {
            rejected.push(format!(
                "HOA profile {} level {} does not allow {channels} output channels",
                shape.profile_id, shape.level_id
            ));
        }
        if salient && shape.salient_configurations.iter().any(|c| c.order == 0) {
            rejected.push("zero-order salient dictionaries are not qualified".into());
        }

        if shape.core_channels > shape.transport_channels {
            rejected.push("HOA core channels exceed available transport channels".into());
        }
        for (name, value) in [
            ("box.version_flags", 0),
            ("bitstream_version", 0x800),
            ("global.profile_id", u64::from(shape.profile_id)),
            ("global.level_id", u64::from(shape.level_id)),
            (
                "global.sample_rate_index",
                if shape.sample_rate_hz == 44100 { 4 } else { 3 },
            ),
            ("global.frame_size_index", 0),
            ("global.channel_count", channels),
            ("global.parameter_b", 2),
            ("global.component_count", 1),
            ("components[0].lowest_channel_index", 0),
            ("components[0].type", 2),
            ("components[0].parameter_0", 0),
            ("components[0].parameter_1", 0),
            ("components[0].hoa.parameter_0", 1),
            (
                "components[0].hoa.parameter_1",
                u64::from(shape.salient_partition_method),
            ),
            (
                "components[0].hoa.parameter_2_minus_six",
                u64::from(shape.quantization_bits - 6),
            ),
            ("components[0].hoa.order", u64::from(shape.order)),
            (
                "components[0].hoa.max_salient_components",
                u64::from(shape.salient_components),
            ),
            (
                "components[0].hoa.ambient_components_encoded",
                if salient {
                    u64::from(shape.ambient_components)
                } else {
                    u64::from(shape.ambient_components) - 1
                },
            ),
            (
                "components[0].hoa.tce_count",
                u64::from(shape.transport_channels),
            ),
            ("components[0].hoa.layout_family", 190),
            ("components[0].hoa.layout_channels", channels),
        ] {
            packet_config::check(&parsed.fields, name, json!(value), "cookie", &mut rejected);
        }
        for name in [
            "global.flag_a",
            "global.flag_c",
            "global.additional_asc_present",
            "ancillary.scene_graph_present",
            "ancillary.metadata_present",
            "ancillary.custom_data_present",
            "components[0].hoa.flag_b",
            "components[0].hoa.custom_layout_present",
            "components[0].hoa.remapping_present",
        ] {
            packet_config::check(&parsed.fields, name, json!(false), "cookie", &mut rejected);
        }
        packet_config::check(
            &parsed.fields,
            "components[0].hoa.flag_d",
            json!(
                shape.path == HoaPath::Mixed
                    && shape.ambient_combination == super::AmbientCombination::Add
            ),
            "cookie",
            &mut rejected,
        );
        packet_config::check(
            &parsed.fields,
            "components[0].hoa.dynamic_selection_config_present",
            json!(shape.dynamic_method.is_some()),
            "cookie",
            &mut rejected,
        );
        if let Some(method) = shape.dynamic_method {
            if method > 2 {
                let position = parsed
                    .fields
                    .iter()
                    .find(|f| f.name == "components[0].hoa.dynamic_selection.parameter")
                    .map_or(0, |f| f.bit_offset);
                rejected.push(format!("components[0].hoa.dynamic_selection.parameter={method} at cookie bit {position} (expected 0..2)"));
            }
            // All 1..=8 counts retain eight wire mappings. The new counts and
            // capacities were checked with hash-bound native instances.
            packet_config::check(
                &parsed.fields,
                "components[0].hoa.dynamic_selection.subbands_minus_one",
                json!(shape.dynamic_subbands.expect("dynamic bands") - 1),
                "cookie",
                &mut rejected,
            );
        }
        if salient {
            for (i, component) in shape.salient_configurations.iter().enumerate() {
                for (field, value) in [
                    ("subbands_minus_one", component.subband_count - 1),
                    ("order", usize::from(component.order)),
                ] {
                    packet_config::check(
                        &parsed.fields,
                        &format!("components[0].hoa.salient[{i}].{field}"),
                        json!(value),
                        "cookie",
                        &mut rejected,
                    );
                }
            }
        }
        if shape.ambient_components == 0 {
            packet_config::check(
                &parsed.fields,
                "components[0].hoa.ambient_selection_present",
                json!(false),
                "cookie",
                &mut rejected,
            );
        }
        let indices = shape.ambient_indices();
        let declared = parsed
            .derived
            .get("components[0].hoa.ambient_selection")
            .and_then(|v| v.as_array());
        if declared.is_none_or(|v| v.len() != indices.len())
            || indices.iter().any(|&v| v >= shape.recovery_slots)
            || indices.windows(2).any(|w| w[0] >= w[1])
        {
            let position = parsed
                .fields
                .iter()
                .find(|f| f.name == "components[0].hoa.ambient_selection_present")
                .map_or(0, |f| f.bit_offset);
            rejected.push(format!("components[0].hoa.ambient_selection={} at cookie bit {position} (expected {} strictly increasing distinct indices below {})", json!(indices), shape.ambient_components, shape.recovery_slots));
        }
        for name in ["full_order", "flag_a", "flag_e", "flag_f"] {
            packet_config::check(
                &parsed.fields,
                &format!("components[0].hoa.{name}"),
                json!(true),
                "cookie",
                &mut rejected,
            );
        }
        for i in 0..shape.transport_channels {
            packet_config::check(
                &parsed.fields,
                &format!("components[0].hoa.tce[{i}].type"),
                json!(0),
                "cookie",
                &mut rejected,
            );
        }
        let field = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == name)
                .map(|f| &f.value)
        };
        let scene = field("ancillary.audio_scenes_present") == Some(&json!(true));
        let drc = DrcContext::for_channels(&parsed, channels);
        if scene {
            rejected.extend(packet_config::neutral_scene(
                &parsed.fields,
                "cookie",
                drc.present,
            ));
        } else {
            packet_config::check(
                &parsed.fields,
                "ancillary.audio_scenes_present",
                json!(false),
                "cookie",
                &mut rejected,
            );
        }
        for f in parsed
            .fields
            .iter()
            .filter(|f| f.name.starts_with("extensions[") && f.name.ends_with(".type"))
        {
            packet_config::check(&parsed.fields, &f.name, json!(3), "cookie", &mut rejected);
        }
        if !parsed.is_complete() {
            rejected.push(format!(
                "cookie status={:?} at cookie bit {} (expected complete)",
                parsed.status,
                parsed
                    .unknown_ranges
                    .first()
                    .map_or(parsed.cookie_bytes * 8, |v| v.bit_offset)
            ));
        }
        let configuration = PacketConfiguration {
            scene_present: scene,
            rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
            syntax_rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
        };
        let transport = ChannelFrameContext::hoa_transport(
            parsed.cookie_sha256,
            configuration,
            drc,
            shape.clone(),
        );
        Ok(Self {
            transport,
            configuration: shape,
        })
    }
    pub fn is_supported(&self) -> bool {
        self.transport.is_supported()
    }
    pub fn rejection(&self) -> Option<&str> {
        self.transport.rejection()
    }
    pub fn channel_count(&self) -> u32 {
        u32::from(self.configuration.channels)
    }
    pub fn sample_rate_hz(&self) -> u64 {
        self.configuration.sample_rate_hz
    }
    pub fn cookie_sha256(&self) -> &str {
        self.transport.cookie_sha256()
    }
    pub fn channel_layout(&self) -> &ChannelLayout {
        self.transport.channel_layout().expect("fixed HOA layout")
    }
    pub fn maximum_preroll_bytes(&self) -> u64 {
        self.transport.maximum_preroll_bytes()
    }
    pub fn profile_id(&self) -> u8 {
        self.configuration.profile_id
    }
    pub fn level_id(&self) -> u8 {
        self.configuration.level_id
    }
    pub(crate) fn expanded_orders(&self) -> bool {
        self.configuration.expanded_orders()
    }
    pub fn quantization_bits(&self) -> u8 {
        self.configuration.quantization_bits
    }
    pub(crate) fn quantization_extended(&self) -> bool {
        self.configuration.quantization_extended()
    }
    pub fn salient_components(&self) -> usize {
        usize::from(self.configuration.salient_components)
    }
    pub(crate) fn ambient_count_extended(&self) -> bool {
        self.configuration.ambient_count_extended()
    }
    pub fn ambient_components(&self) -> usize {
        usize::from(self.configuration.ambient_components)
    }
    pub fn core_channels(&self) -> usize {
        usize::from(self.configuration.core_channels)
    }
    pub fn transport_channels(&self) -> usize {
        usize::from(self.configuration.transport_channels)
    }
    pub fn order(&self) -> u8 {
        self.configuration.order
    }
    pub fn output_order(&self) -> u8 {
        if self.dynamic_selection_enabled() {
            3
        } else {
            self.order()
        }
    }
    pub fn recovery_slot_count(&self) -> usize {
        usize::from(self.configuration.recovery_slots)
    }
    /// Effective frequency bands; every dynamic frame still carries eight mapping rows.
    pub fn dynamic_subband_count(&self) -> Option<usize> {
        self.configuration.dynamic_subbands.map(usize::from)
    }
    pub fn dynamic_selection_enabled(&self) -> bool {
        self.configuration.dynamic_method.is_some()
    }
    pub fn recovery_numeric_profile(&self) -> &'static str {
        self.configuration.recovery_numeric_profile()
    }
    pub fn numeric_profile(&self) -> &'static str {
        self.configuration.numeric_profile()
    }
    pub fn static_ambient_enabled(&self) -> bool {
        self.configuration.static_ambient
    }
    pub fn ambient_combination(&self) -> super::AmbientCombination {
        self.configuration.ambient_combination
    }
    pub fn ambient_selection(&self) -> &[u8] {
        self.configuration.ambient_indices()
    }
    pub fn ambient_transform(&self) -> AmbientTransform {
        self.configuration.ambient_transform
    }
    /// Actual-length component configurations; empty only when there is no salient.
    pub fn salient_component_configurations(&self) -> &[SalientComponentConfiguration] {
        &self.configuration.salient_configurations
    }
    /// Legacy five-component view. `None` also means a different component count;
    /// use `salient_component_configurations` to distinguish it from no salient.
    pub fn salient_subband_counts(&self) -> Option<[usize; 5]> {
        let components: &[SalientComponentConfiguration; 5] =
            self.salient_component_configurations().try_into().ok()?;
        Some(components.map(|c| c.subband_count))
    }
    /// Cookie spatial partition, independent of the dynamic selection partition.
    pub fn salient_partition_method(&self) -> Option<u8> {
        (self.configuration.salient_components != 0)
            .then_some(self.configuration.salient_partition_method)
    }
    /// Legacy five-component orders, distinct from the overall/output order.
    /// For other counts use `salient_component_configurations`.
    pub fn salient_component_orders(&self) -> Option<[u8; 5]> {
        let components: &[SalientComponentConfiguration; 5] =
            self.salient_component_configurations().try_into().ok()?;
        Some(components.map(|c| c.order))
    }
    pub(crate) fn component_orders_extended(&self) -> bool {
        self.configuration.component_orders_extended()
    }
    pub(crate) fn component_order_info(&self) -> Option<Vec<super::SalientComponentOrderInfo>> {
        self.configuration.component_order_info()
    }
    pub fn descriptor_numeric_profile(&self) -> Option<&'static str> {
        (self.configuration.salient_components != 0)
            .then(|| self.configuration.descriptor_numeric_profile())
    }
    pub fn state_profile(&self) -> &'static str {
        self.configuration.state_profile()
    }
    pub(crate) fn initial_drc_state(&self) -> DrcState {
        self.transport.initial_state()
    }
}

/// Ambient keeps its global SD mode; salient also retains descriptor history.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub(crate) struct HoaState {
    pub last_global_coding_mode: u8,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub salient: Option<Box<super::hoa_salient::SalientState>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_ambient_transform: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_dynamic_mapping: Option<[[u8; 9]; 8]>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaSpatialData {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub single_coding_mode: bool,
    pub coding_mode: Option<u8>,
    pub effective_global_coding_mode: u8,
    pub ambient_indices: Vec<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub salient: Option<super::hoa_salient::SalientSpatialData>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ambient: Option<StaticAmbientData>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaCoefficientSpectrum {
    pub acn_index: u8,
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecoverySlotSpectrum {
    pub slot_index: u8,
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaMixedMapping {
    pub ambient_transport_channels: Vec<u8>,
    pub salient_transport_channels: Vec<u8>,
    pub ambient_output_coefficients: Vec<u8>,
    pub unused_transport_channels: Vec<u8>,
    pub descriptor_numeric_profile: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaFrameInfo {
    pub numeric_profile: String,
    pub order: u8,
    pub channel_order: String,
    pub normalization: String,
    pub coefficient_count: usize,
    pub core_channels: usize,
    pub transport_channels: usize,
    pub common_window: Option<u8>,
    pub spatial: Option<HoaSpatialData>,
    pub hoa_complete: bool,
    pub spectral_stage: String,
    pub channels_after_hoa: Vec<HoaCoefficientSpectrum>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mixed: Option<HoaMixedMapping>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_order: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_coefficient_count: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dynamic_selection: Option<super::hoa_dynamic::DynamicSelectionData>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub additive: Option<super::hoa_additive::HoaAdditiveData>,
}
impl Default for HoaFrameInfo {
    fn default() -> Self {
        Self {
            numeric_profile: NUMERIC_PROFILE.into(),
            order: 3,
            channel_order: "ACN".into(),
            normalization: "SN3D".into(),
            coefficient_count: 16,
            core_channels: 16,
            transport_channels: 16,
            common_window: None,
            spatial: None,
            hoa_complete: false,
            spectral_stage: "hoa_coefficients_before_synthesis".into(),
            channels_after_hoa: vec![],
            mixed: None,
            output_order: None,
            output_coefficient_count: None,
            dynamic_selection: None,
            additive: None,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaPacketReport {
    #[serde(flatten)]
    pub packet: ChannelPacketReport,
}
impl HoaPacketReport {
    pub fn hoa(&self) -> &HoaFrameInfo {
        self.packet.hoa.as_ref().expect("HOA parser report")
    }
}

/// Parse one outer packet, including embedded preroll, from initial HOA/DRC
/// state. This is an initial-packet entry point, not random access to differential
/// salient frames. Use `parse-packets --depth hoa` for stateful report sequences
/// and `SqDecoder` for sequential PCM.
pub fn parse_hoa_packet(
    context: &HoaFrameContext,
    packet: &[u8],
) -> Result<HoaPacketReport, ParseError> {
    parse_hoa_packet_with_state(
        context,
        packet,
        &mut context.initial_drc_state(),
        &mut HoaState::default(),
    )
}
pub(crate) fn parse_hoa_packet_with_state(
    context: &HoaFrameContext,
    packet: &[u8],
    drc: &mut DrcState,
    state: &mut HoaState,
) -> Result<HoaPacketReport, ParseError> {
    super::channels::parse_hoa_transport(&context.transport, packet, drc, state)
        .map(|packet| HoaPacketReport { packet })
}

pub(super) fn spatial(
    parser: &mut Parser<'_>,
    state: &mut HoaState,
    configuration: &HoaConfiguration,
    block: u8,
) -> Result<HoaSpatialData, ParseError> {
    let start = parser.bits.position();
    // Static selection comes from the cookie. A per-frame transform always
    // consumes its selector, even when every transport SCE is absent.
    let ambient = if configuration.static_ambient {
        let (index, source, begin, end) = match configuration.ambient_transform {
            AmbientTransform::Disabled => (3, "disabled", None, None),
            AmbientTransform::Fixed { index } => (index, "cookie", None, None),
            AmbientTransform::PerFrame => {
                let begin = parser.bits.position();
                let index = parser.take("hoa.ambient.transform_index", 2)? as u8;
                (index, "frame", Some(begin), Some(parser.bits.position()))
            }
        };
        state.last_ambient_transform = Some(index);
        Some(StaticAmbientData {
            explicit_selection: configuration.explicit_ambient_selection,
            selection: configuration.ambient_indices().to_vec(),
            transform_config: configuration.ambient_transform,
            effective_index: index,
            index_source: source.into(),
            index_start_bit_offset: begin,
            index_end_bit_offset: end,
            channels_after_transform: vec![],
        })
    } else {
        None
    };
    let single = parser.flag("hoa.spatial.single_coding_mode")?;
    let mode = if single {
        let position = parser.bits.position();
        let mode = parser.take("hoa.spatial.coding_mode", 3)? as u8;
        if mode >= 6 {
            return Err(ParseError::new(
                position,
                "hoa-coding-mode",
                "global spatial coding mode must be 0..5",
            ));
        }
        state.last_global_coding_mode = mode;
        Some(mode)
    } else {
        None
    };
    let descriptors = if configuration.salient_components != 0 {
        Some(super::hoa_salient::read(
            parser,
            mode,
            block,
            state.salient.get_or_insert_with(|| {
                Box::new(super::hoa_salient::SalientState::with_dimensions(
                    configuration.salient_dimensions(),
                    configuration
                        .salient_configurations
                        .iter()
                        .map(|c| c.subband_count)
                        .collect::<Vec<_>>(),
                ))
            }),
            configuration,
        )?)
    } else {
        None
    };
    Ok(HoaSpatialData {
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        single_coding_mode: single,
        coding_mode: mode,
        effective_global_coding_mode: state.last_global_coding_mode,
        ambient_indices: configuration.ambient_indices().to_vec(),
        salient: descriptors,
        ambient,
    })
}

pub(super) fn restore(
    report: &ChannelPacketReport,
    configuration: &HoaConfiguration,
) -> Result<Vec<RecoverySlotSpectrum>, ParseError> {
    // P^-1 I P = I for the qualified identity ambient map, including the
    // native short-window transpose pair. No unverified matrix or extra gain.
    let mut result = Vec::with_capacity(usize::from(report.channel_count));
    for (index, e) in report
        .elements
        .iter()
        .take(usize::from(configuration.recovery_slots))
        .enumerate()
    {
        let scaled = if e.present
            && !configuration.static_ambient
            && index < usize::from(configuration.ambient_components)
        {
            e.channels_after_bwe2[0].scaled.clone()
        } else {
            vec![0.; 1024]
        };
        if scaled.len() != 1024 || scaled.iter().any(|v| !v.is_finite()) {
            return Err(ParseError::new(
                e.end_bit_offset.unwrap_or(e.start_bit_offset),
                "hoa-numeric",
                "invalid ambient spectrum",
            ));
        }
        result.push(RecoverySlotSpectrum {
            slot_index: index as u8,
            scaled: scaled
                .into_iter()
                .map(|v| if v == 0. { 0. } else { v })
                .collect(),
        });
    }
    Ok(result)
}

/// Container/decoder dispatch uses the ASC discriminator, not channel count.
pub(crate) enum DecodedFrameContext {
    Channels(ChannelFrameContext),
    Hoa(HoaFrameContext),
}
impl DecodedFrameContext {
    pub(crate) fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        let parsed = config::parse_cookie(cookie)?;
        if parsed
            .fields
            .iter()
            .any(|f| f.name == "components[0].type" && f.value == json!(2))
        {
            HoaFrameContext::from_cookie(cookie).map(Self::Hoa)
        } else {
            ChannelFrameContext::from_cookie(cookie).map(Self::Channels)
        }
    }
    pub(crate) fn rejection(&self) -> Option<&str> {
        match self {
            Self::Channels(c) => c.rejection(),
            Self::Hoa(c) => c.rejection(),
        }
    }
    pub(crate) fn sample_rate_hz(&self) -> u64 {
        match self {
            Self::Channels(c) => c.sample_rate_hz(),
            Self::Hoa(c) => c.sample_rate_hz(),
        }
    }
    pub(crate) fn channel_count(&self) -> u32 {
        match self {
            Self::Channels(c) => c.channel_count(),
            Self::Hoa(c) => c.channel_count(),
        }
    }
    pub(crate) fn channel_layout(&self) -> Option<&ChannelLayout> {
        match self {
            Self::Channels(c) => c.channel_layout(),
            Self::Hoa(c) => Some(c.channel_layout()),
        }
    }
}
