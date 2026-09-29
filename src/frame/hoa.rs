//! Restricted third-order ACN/SN3D: fixed ambient or salient SQ transport.
use super::{
    ChannelFrameContext, ChannelPacketReport, Parser,
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
pub const CHANNELS: usize = 16;

#[derive(Debug, Clone, Serialize)]
pub struct HoaFrameContext {
    pub(crate) transport: ChannelFrameContext,
    #[serde(skip)]
    pub(crate) salient: bool,
}
impl HoaFrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        let parsed = config::parse_cookie(cookie)?;
        let salient = parsed
            .fields
            .iter()
            .any(|f| f.name == "components[0].hoa.max_salient_components" && f.value == json!(5));
        let mut rejected = Vec::new();
        for (name, value) in [
            ("box.version_flags", 0),
            ("bitstream_version", 0x800),
            ("global.profile_id", 5),
            ("global.level_id", 0),
            ("global.sample_rate_index", 3),
            ("global.frame_size_index", 0),
            ("global.channel_count", 16),
            ("global.parameter_b", 2),
            ("global.component_count", 1),
            ("components[0].lowest_channel_index", 0),
            ("components[0].type", 2),
            ("components[0].parameter_0", 0),
            ("components[0].parameter_1", 0),
            ("components[0].hoa.parameter_0", 1),
            ("components[0].hoa.parameter_1", 0),
            ("components[0].hoa.parameter_2_minus_six", 0),
            ("components[0].hoa.order", 3),
            (
                "components[0].hoa.max_salient_components",
                if salient { 5 } else { 0 },
            ),
            (
                "components[0].hoa.ambient_components_encoded",
                if salient { 0 } else { 15 },
            ),
            ("components[0].hoa.tce_count", 16),
            ("components[0].hoa.layout_family", 190),
            ("components[0].hoa.layout_channels", 16),
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
            "components[0].hoa.flag_d",
            "components[0].hoa.dynamic_selection_config_present",
            "components[0].hoa.ambient_selection_present",
            "components[0].hoa.custom_layout_present",
            "components[0].hoa.remapping_present",
        ] {
            packet_config::check(&parsed.fields, name, json!(false), "cookie", &mut rejected);
        }
        if salient {
            for i in 0..5 {
                for (field, value) in [("subbands_minus_one", 3), ("order", 3)] {
                    packet_config::check(
                        &parsed.fields,
                        &format!("components[0].hoa.salient[{i}].{field}"),
                        json!(value),
                        "cookie",
                        &mut rejected,
                    );
                }
            }
        } else {
            packet_config::check(
                &parsed.fields,
                "components[0].hoa.parameter_3_present",
                json!(false),
                "cookie",
                &mut rejected,
            );
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
        for i in 0..CHANNELS {
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
        let drc = DrcContext::for_channels(&parsed, 16);
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
        let transport =
            ChannelFrameContext::hoa_transport(parsed.cookie_sha256, configuration, drc, salient);
        Ok(Self { transport, salient })
    }
    pub fn is_supported(&self) -> bool {
        self.transport.is_supported()
    }
    pub fn rejection(&self) -> Option<&str> {
        self.transport.rejection()
    }
    pub fn channel_count(&self) -> u32 {
        16
    }
    pub fn sample_rate_hz(&self) -> u64 {
        48000
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
    pub fn salient_components(&self) -> usize {
        if self.salient { 5 } else { 0 }
    }
    pub fn numeric_profile(&self) -> &'static str {
        if self.salient {
            super::hoa_salient::NUMERIC_PROFILE
        } else {
            NUMERIC_PROFILE
        }
    }
    pub fn state_profile(&self) -> &'static str {
        if self.salient {
            super::hoa_salient::STATE_PROFILE
        } else {
            STATE_PROFILE
        }
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
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaCoefficientSpectrum {
    pub acn_index: u8,
    pub scaled: Vec<f32>,
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
    salient: bool,
    block: u8,
) -> Result<HoaSpatialData, ParseError> {
    let start = parser.bits.position();
    // flag_b=false fixes the configuration from the cookie. parameter_3 is
    // absent (no ambient transform), and dynamic selection is off. The salient
    // branch adds its descriptors after the common global-mode header.
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
    let descriptors = if salient {
        Some(super::hoa_salient::read(
            parser,
            mode,
            block,
            state.salient.get_or_insert_default(),
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
        ambient_indices: if salient { vec![] } else { (0..16).collect() },
        salient: descriptors,
    })
}

pub(super) fn restore(
    report: &ChannelPacketReport,
) -> Result<Vec<HoaCoefficientSpectrum>, ParseError> {
    // P^-1 I P = I for the qualified identity ambient map, including the
    // native short-window transpose pair. No unverified matrix or extra gain.
    let mut result = Vec::with_capacity(CHANNELS);
    for e in &report.elements {
        let index = e.configuration.output_channels[0];
        let scaled = if e.present {
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
        result.push(HoaCoefficientSpectrum {
            acn_index: index,
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
