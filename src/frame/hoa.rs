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
pub const TRANSPORT_PROFILE: &str = "apac-hoa-transports-v1";
pub const TRANSPORT_STATE_PROFILE: &str = "apac-hoa-transports-state-v1";
pub const PARTIAL_PROFILE: &str = "apac-hoa-partial-domain-v1";
pub const PARTIAL_NUMERIC_PROFILE: &str = "apac-hoa-partial-domain-math-v1";
pub const PARTIAL_STATE_PROFILE: &str = "apac-hoa-partial-domain-state-v1";

/// One salient component's actual descriptor shape, in cookie order.
///
/// This does not describe the overall recovery domain or output layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
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
    pub full_order: bool,
    pub controls: super::HoaSpatialControls,
    pub profile_id: u8,
    pub level_id: u8,
    /// Output ACN coefficients; transport and core dimensions are independent.
    pub channels: u8,
    pub source_layout: std::sync::Arc<super::hoa_source::SourceLayout>,
    pub static_remapping: Option<std::sync::Arc<super::HoaStaticRemapping>>,
    pub recovery_slots: u8,
    pub dynamic_method: Option<u8>,
    pub dynamic_subbands: Option<u8>,
    pub transport_channels: u8,
    pub transport_types: Vec<u8>,
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
        let controls = super::HoaSpatialControls::from_cookie(parsed);
        // Unsupported values select a bounded diagnostic shape, then fail the
        // exact field checks below. No untrusted dimension is used to allocate.
        let dynamic = parsed.fields.iter().any(|f| {
            f.name == "components[0].hoa.dynamic_selection_config_present" && f.value == json!(true)
        });
        let full_order = parsed
            .fields
            .iter()
            .any(|f| f.name == "components[0].hoa.full_order" && f.value == json!(true));
        let partial_count = parsed
            .derived
            .get("components[0].hoa.coefficient_count")
            .and_then(|v| v.as_u64())
            .filter(|&n| (1..=121).contains(&n))
            .unwrap_or(16) as u8;
        let order = if !full_order {
            (partial_count - 1).isqrt()
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
        let recovery_slots = if full_order {
            (order + 1).pow(2)
        } else {
            partial_count
        };
        let source_layout = super::hoa_source::SourceLayout::selected(
            parsed,
            recovery_slots,
            dynamic,
            controls.parameter_0,
        );
        let channels = source_layout.channels;
        let transport_types: Vec<u8> = parsed
            .fields
            .iter()
            .filter(|f| f.name.starts_with("components[0].hoa.tce[") && f.name.ends_with("].type"))
            .map(|f| f.value.as_u64().unwrap_or(u64::MAX).min(255) as u8)
            .collect();
        let transport_channels = transport_types
            .iter()
            .map(|t| match t {
                0 | 3 => 1usize,
                1 => 2,
                _ => 0,
            })
            .sum::<usize>()
            .min(255) as u8;
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
            full_order,
            controls,
            profile_id: value("global.profile_id").unwrap_or(5) as u8,
            level_id: value("global.level_id").unwrap_or(0) as u8,
            channels,
            source_layout: std::sync::Arc::new(source_layout),
            static_remapping: super::HoaStaticRemapping::selected(parsed, channels)
                .map(std::sync::Arc::new),
            recovery_slots,
            dynamic_method: dynamic
                .then(|| value("components[0].hoa.dynamic_selection.parameter").unwrap_or(3) as u8),
            dynamic_subbands: dynamic.then(|| {
                (value("components[0].hoa.dynamic_selection.subbands_minus_one")
                    .unwrap_or(7)
                    .min(7)
                    + 1) as u8
            }),
            transport_channels,
            transport_types,
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
                        coefficient_count: if full_order {
                            (usize::from(order) + 1).pow(2)
                        } else {
                            usize::from(recovery_slots)
                        },
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
                Some(method @ 1..=3) if !salient || method <= 2 => method as u8,
                _ => 0,
            },
            ambient_components,
            path,
            ambient_selection,
            explicit_ambient_selection,
            ambient_transform,
            static_ambient: explicit_ambient_selection
                || transform_present
                || controls.flag_b
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
        if self.source_layout.extended {
            return super::hoa_source::NUMERIC_PROFILE;
        }
        if self.static_remapping.is_some() {
            return super::hoa_remapping::NUMERIC_PROFILE;
        }
        if self.dynamic_domains_extended() {
            return super::hoa_dynamic::DOMAINS_NUMERIC_PROFILE;
        }
        if self.dynamic_method.is_some() {
            return super::hoa_dynamic::NUMERIC_PROFILE;
        }
        self.recovery_numeric_profile()
    }
    pub fn recovery_numeric_profile(&self) -> &'static str {
        if self.controls_extended() {
            return super::hoa_controls::numeric_profile(self.controls);
        }
        if !self.full_order {
            return PARTIAL_NUMERIC_PROFILE;
        }
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
        if self.static_remapping.is_some() {
            return super::hoa_remapping::STATE_PROFILE;
        }
        if self.source_layout.extended {
            return super::hoa_source::STATE_PROFILE;
        }
        if self.dynamic_domains_extended() {
            return super::hoa_dynamic::DOMAINS_STATE_PROFILE;
        }
        if self.controls_extended() {
            return super::hoa_controls::state_profile(self.controls);
        }
        if !self.full_order {
            return PARTIAL_STATE_PROFILE;
        }
        if self.transport_extended() {
            return TRANSPORT_STATE_PROFILE;
        }
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
            ambient_transport_channels: (0..self.ambient_components)
                .map(|slot| self.transport_slot(slot))
                .collect(),
            salient_transport_channels: (self.ambient_components..self.core_channels)
                .map(|slot| self.transport_slot(slot))
                .collect(),
            ambient_output_coefficients: self.ambient_indices().to_vec(),
            unused_transport_channels: self.unused_transport_channels(),
            descriptor_numeric_profile: self.descriptor_numeric_profile().into(),
        })
    }
    pub fn transport_slot(&self, core: u8) -> u8 {
        self.static_remapping
            .as_ref()
            .and_then(|mapping| mapping.core_to_transport.get(usize::from(core)))
            .copied()
            .unwrap_or(core)
    }
    pub fn unused_transport_channels(&self) -> Vec<u8> {
        let mut used = vec![false; usize::from(self.transport_channels)];
        for core in 0..self.core_channels {
            if let Some(slot) = used.get_mut(usize::from(self.transport_slot(core))) {
                *slot = true;
            }
        }
        (0..self.transport_channels)
            .filter(|&slot| !used[usize::from(slot)])
            .collect()
    }
    pub fn ambient_indices(&self) -> &[u8] {
        &self.ambient_selection[..usize::from(self.ambient_components)]
    }
    pub fn component_orders_extended(&self) -> bool {
        self.salient_components != 0
            && (!self.full_order
                || self.expanded_orders()
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
    pub fn controls_extended(&self) -> bool {
        self.controls.extended(self.path == HoaPath::Mixed)
    }
    pub fn dynamic_domains_extended(&self) -> bool {
        self.dynamic_method.is_some()
            && (!self.full_order || self.recovery_slots != 9 || self.channels != 16)
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
    pub fn transport_extended(&self) -> bool {
        self.transport_channels != self.channels || self.transport_types.iter().any(|&t| t != 0)
    }
    pub fn salient_dimensions(&self) -> Vec<usize> {
        self.salient_configurations
            .iter()
            .map(|c| c.coefficient_count)
            .collect()
    }
    pub fn descriptor_numeric_profile(&self) -> &'static str {
        if self.controls_extended() {
            return super::hoa_controls::numeric_profile(self.controls);
        }
        if !self.full_order {
            return PARTIAL_NUMERIC_PROFILE;
        }
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
            let mut information =
                super::hoa_salient::component_information(&orders, self.quantization_bits);
            if !self.full_order {
                for (info, component) in information.iter_mut().zip(&self.salient_configurations) {
                    info.coefficient_count = component.coefficient_count;
                    info.numeric_profile = PARTIAL_NUMERIC_PROFILE.into();
                }
            }
            if self.controls_extended() {
                for info in &mut information {
                    info.numeric_profile =
                        super::hoa_controls::numeric_profile(self.controls).into();
                }
            }
            information
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
        if let Some(reason) = shape
            .source_layout
            .rejection(usize::from(shape.recovery_slots))
        {
            rejected.push(reason);
        }
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
        if shape.transport_channels == 0 || shape.transport_channels > shape.channels {
            rejected.push("HOA requires 1..output-count transport channels".into());
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
            (
                "components[0].hoa.parameter_0",
                u64::from(shape.controls.parameter_0),
            ),
            (
                "components[0].hoa.parameter_1",
                u64::from(shape.salient_partition_method),
            ),
            (
                "components[0].hoa.parameter_2_minus_six",
                u64::from(shape.quantization_bits - 6),
            ),
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
                shape.transport_types.len() as u64,
            ),
        ] {
            packet_config::check(&parsed.fields, name, json!(value), "cookie", &mut rejected);
        }
        if shape.full_order {
            packet_config::check(
                &parsed.fields,
                "components[0].hoa.order",
                json!(shape.order),
                "cookie",
                &mut rejected,
            );
        } else {
            packet_config::check(
                &parsed.fields,
                "components[0].hoa.coefficient_count_minus_one",
                json!(shape.recovery_slots - 1),
                "cookie",
                &mut rejected,
            );
        }
        for name in [
            "global.flag_a",
            "global.flag_c",
            "global.additional_asc_present",
            "ancillary.scene_graph_present",
            "ancillary.metadata_present",
            "ancillary.custom_data_present",
        ] {
            packet_config::check(&parsed.fields, name, json!(false), "cookie", &mut rejected);
        }
        packet_config::check(
            &parsed.fields,
            "components[0].hoa.flag_d",
            json!(shape.controls.flag_d),
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
        if salient && shape.full_order {
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
        for (i, &kind) in shape.transport_types.iter().enumerate() {
            if !matches!(kind, 0 | 1 | 3 | 6) {
                rejected.push(format!("components[0].hoa.tce[{i}].type={kind} is not a supported HOA transport element"));
            }
            packet_config::check(
                &parsed.fields,
                &format!("components[0].hoa.tce[{i}].type"),
                json!(kind),
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
    pub fn source_layout_enabled(&self) -> bool {
        self.configuration.source_layout.extended
    }
    pub fn static_remapping(&self) -> Option<&super::HoaStaticRemapping> {
        self.configuration.static_remapping.as_deref()
    }
    /// Source normalization when identified by a tagged layout or explicit ACN labels.
    pub fn source_normalization(&self) -> Option<&'static str> {
        self.configuration.source_layout.normalization()
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
    /// Cookie elements in wire order, with each element's carrier range.
    pub fn transport_elements(&self) -> &[super::ElementConfiguration] {
        self.transport.elements()
    }
    pub fn order(&self) -> u8 {
        self.configuration.order
    }
    /// Whether the cookie declares a complete order, rather than an explicit dimension.
    pub fn full_order(&self) -> bool {
        self.configuration.full_order
    }
    pub fn spatial_controls(&self) -> super::HoaSpatialControls {
        self.configuration.controls
    }
    pub(crate) fn controls_extended(&self) -> bool {
        self.configuration.controls_extended()
    }
    pub fn output_order(&self) -> u8 {
        (self.configuration.channels - 1).isqrt()
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
    pub(crate) fn dynamic_domains_extended(&self) -> bool {
        self.configuration.dynamic_domains_extended()
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
    pub(crate) fn transport_extended(&self) -> bool {
        self.configuration.transport_extended()
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame_configuration: Option<super::hoa_controls::HoaFrameConfiguration>,
    pub last_global_coding_mode: u8,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub salient: Option<Box<super::hoa_salient::SalientState>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_ambient_transform: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_dynamic_mapping: Option<Vec<Vec<u8>>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaSpatialData {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub controls: Option<super::HoaSpatialControls>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame_configuration: Option<super::HoaFrameConfigurationReport>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub static_remapping: Option<super::HoaStaticRemapping>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_layout: Option<super::HoaSourceLayoutData>,
    pub numeric_profile: String,
    pub order: u8,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub full_order: Option<bool>,
    pub channel_order: String,
    pub normalization: String,
    pub coefficient_count: usize,
    pub core_channels: usize,
    pub transport_channels: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transport_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transport_format_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transport_element_count: Option<usize>,
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
            static_remapping: None,
            source_layout: None,
            numeric_profile: NUMERIC_PROFILE.into(),
            order: 3,
            full_order: None,
            channel_order: "ACN".into(),
            normalization: "SN3D".into(),
            coefficient_count: 16,
            core_channels: 16,
            transport_channels: 16,
            transport_profile: None,
            transport_format_sha256: None,
            transport_element_count: None,
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
        controls: configuration
            .controls_extended()
            .then_some(configuration.controls),
        frame_configuration: None,
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
    let sources = super::hoa_transport::spectra(report)?;
    let mut result = Vec::with_capacity(usize::from(configuration.recovery_slots));
    for index in 0..usize::from(configuration.recovery_slots) {
        let scaled = if !configuration.static_ambient
            && index < usize::from(configuration.ambient_components)
        {
            sources
                .get(index)
                .expect("qualified ambient carrier")
                .to_vec()
        } else {
            vec![
                if configuration.controls.flag_a {
                    0.
                } else {
                    super::hoa_controls::mean(index) as f32
                };
                1024
            ]
        };
        if scaled.len() != 1024 || scaled.iter().any(|v| !v.is_finite()) {
            return Err(ParseError::new(
                report.frame.stop_bit_offset,
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
