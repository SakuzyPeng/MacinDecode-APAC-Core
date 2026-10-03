//! Bounded stereo SQ prefixes, ASP framing, raw spectra, CAC, TNS and BWE2 before core alignment.
use crate::prelude::*;
mod auxiliary;
pub use auxiliary::{AuxiliaryPayload, SceneGraphPayload, TrimmingDeclaration};
mod channels;
mod hoa;
mod hoa_remapping;
pub use hoa_remapping::{
    HoaStaticRemapping, PROFILE as HOA_STATIC_REMAPPING_PROFILE,
    format_sha256 as hoa_static_remapping_format_sha256,
};
mod hoa_source;
pub use hoa_source::{
    HoaSourceChannelSpectrum, HoaSourceLayoutData, PROFILE as HOA_SOURCE_LAYOUT_PROFILE,
    format_sha256 as hoa_source_layout_format_sha256,
};
mod hoa_controls;
pub use hoa_controls::PROFILE as HOA_SPATIAL_CONTROLS_PROFILE;
pub use hoa_controls::format_sha256 as hoa_spatial_controls_format_sha256;
pub use hoa_controls::frame_state_sha256 as hoa_frame_configuration_state_sha256;
pub use hoa_controls::{HoaFrameConfigurationReport, HoaSpatialControls};
pub use hoa_dynamic::{
    DOMAINS_PROFILE as HOA_DYNAMIC_DOMAINS_PROFILE,
    domains_format_sha256 as hoa_dynamic_domains_format_sha256,
};
mod hoa_additive;
mod hoa_ambient;
pub use hoa_additive::{AmbientCombination, AmbientContribution, HoaAdditiveData};
mod hoa_dynamic;
mod hoa_salient;
mod hoa_salient_subbands;
mod hoa_transport;
pub use channels::STATE_PROFILE as CHANNEL_STATE_PROFILE;
pub use channels::{
    ChannelFrameContext, ChannelPacketReport, ElementConfiguration, ElementKind, ElementReport,
    parse_channel_packet,
};
#[doc(hidden)]
pub use channels::{
    ScanWorkspace, parse_channel_packet_with_state, scan_channel_packet, scan_hoa_packet,
};
pub use hoa_salient::ORDER1_PROFILE as HOA_SALIENT_ORDER1_PROFILE;
#[doc(hidden)]
pub use hoa_salient::{
    EXPANDED_PROFILE as HOA_EXPANDED_ORDERS_PROFILE,
    expanded_math_sha256 as hoa_expanded_math_sha256,
};
pub use hoa_salient::{
    SalientComponentOrderInfo, SalientDescriptor, SalientSpatialData, SalientSubbandInfo,
};
#[doc(hidden)]
pub use hoa_salient::{
    format_sha256 as hoa_salient_format_sha256, math_sha256 as hoa_salient_math_sha256,
};
pub use hoa_salient_subbands::PARTITION_PROFILE as HOA_SALIENT_PARTITION_PROFILE;
pub use hoa_salient_subbands::SUBBAND_PROFILE as HOA_SALIENT_SUBBAND_PROFILE;
#[doc(hidden)]
pub use hoa_salient_subbands::format_sha256 as hoa_salient_subbands_format_sha256;
pub use hoa_transport::HoaExtensionData;
#[doc(hidden)]
pub use hoa_transport::format_sha256 as hoa_transport_format_sha256;
mod bwe2;
mod cac;
#[doc(hidden)]
pub mod drc;
mod drc_shared;
pub use drc::DrcGainExtension;
pub use drc::RULES_VERSION as DRC_RULES_VERSION;
#[doc(hidden)]
pub use drc::{DrcState, codebook_sha256 as drc_codebook_sha256};
pub use drc_shared::{DrcGainSequence, DrcSequenceParameters};
pub use drc_shared::{
    PROFILE as HOA_SHARED_DRC_PROFILE, format_sha256 as hoa_shared_drc_format_sha256,
};
#[doc(hidden)]
pub use packet::parse_packet_with_state;
mod packet;
mod packet_config;
#[doc(hidden)]
pub mod sfb;
mod spectrum;
#[doc(hidden)]
pub mod stream;
pub use sfb::{
    PROFILE as HOA_SHARED_CONFIG_PROFILE, format_sha256 as hoa_shared_config_format_sha256,
};
pub use stream::{
    AdditionalComponentConfiguration, StreamComponentConfiguration, StreamComponentReport,
    StreamFrameContext, StreamOutputRange, StreamPacketReport, parse_stream_packet,
};
mod tns;
pub use crate::bwe2_math::Analysis as Bwe2Analysis;
pub use bwe2::ElementBwe2Data;
pub use bwe2::NUMERIC_PROFILE as BWE2_NUMERIC_PROFILE;
pub use bwe2::{
    Bwe2ChannelData, Bwe2ChannelSpectrum, Bwe2Data, Bwe2Parameters, Bwe2Region, Bwe2Report,
    parse_bwe2,
};
pub use cac::NUMERIC_PROFILE as CAC_NUMERIC_PROFILE;
#[doc(hidden)]
pub use cac::math_sha256 as cac_math_sha256;
pub use cac::{CacChannelSpectrum, CacData, CacReport, CacRun, parse_cac};
pub use drc::{
    DrcConfiguration, DrcNode, DrcParameters, DrcPayload, DrcReport, DrcTimeDelta, parse_drc,
};
pub use hoa::PARTIAL_PROFILE as HOA_PARTIAL_PROFILE;
pub use hoa::TRANSPORT_PROFILE as HOA_TRANSPORT_PROFILE;
#[doc(hidden)]
pub use hoa::{DecodedFrameContext, HoaState, parse_hoa_packet_with_state};
pub use hoa::{
    HoaCoefficientSpectrum, HoaFrameContext, HoaFrameInfo, HoaMixedMapping, HoaPacketReport,
    HoaSpatialData, RecoverySlotSpectrum, SalientComponentConfiguration, parse_hoa_packet,
};
pub use hoa::{NUMERIC_PROFILE as HOA_NUMERIC_PROFILE, STATE_PROFILE as HOA_STATE_PROFILE};
pub use hoa_ambient::{AmbientSpectrum, AmbientTransform, StaticAmbientData};
#[doc(hidden)]
pub use hoa_ambient::{
    format_sha256 as hoa_ambient_format_sha256, math_sha256 as hoa_ambient_math_sha256,
};
pub use hoa_dynamic::SUBBAND_PROFILE as HOA_DYNAMIC_SUBBAND_PROFILE;
#[doc(hidden)]
pub use hoa_dynamic::format_sha256 as hoa_dynamic_format_sha256;
pub use hoa_dynamic::{
    DynamicBandMapping, DynamicSelectionData, DynamicSelectionEncoding, InternalAmbientData,
    InternalAmbientSpectrum,
};
pub use packet::{EmbeddedPreroll, PacketReport, PacketTail, STATE_PROFILE, parse_packet};
pub use spectrum::{ChannelSpectrum, IcsInfo, Section, SpectrumReport, parse_spectrum};
pub use tns::NUMERIC_PROFILE as TNS_NUMERIC_PROFILE;
#[doc(hidden)]
pub use tns::math_sha256 as tns_math_sha256;
pub use tns::{TnsChannel, TnsChannelSpectrum, TnsFilter, TnsReport, TnsWindow, parse_tns};

use crate::{
    config::{self, ConfigField, Diagnostic, ParseError, ParseStatus, bits::BitReader},
    model::{SCHEMA_VERSION, sha256},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

/// Largest single packet accepted by every parser and packet store.
pub const MAX_PACKET_BUFFER: usize = 16 * 1024 * 1024;

/// Immutable configuration-derived context. An unsupported context still permits
/// reporting the ASP frame type when its enclosing configuration is verified.
#[derive(Debug, Clone, Serialize)]
pub struct FrameContext {
    #[serde(skip)]
    drc: drc::DrcContext,
    cookie_sha256: String,
    sample_rate_hz: Option<u64>,
    channels: Option<u64>,
    frame_samples: Option<u64>,
    asp_frame_header: bool,
    unsupported_reason: Option<String>,
    #[serde(skip)]
    packet_configuration: packet_config::PacketConfiguration,
}
impl FrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        let parsed = config::parse_cookie(cookie)?;
        let field = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == name)
                .map(|f| &f.value)
        };
        let uint = |name| field(name).and_then(Value::as_u64);
        let flag = |name| field(name).and_then(Value::as_bool);
        let sample_rate_hz = parsed.derived.get("sample_rate_hz").and_then(Value::as_u64);
        let channels = parsed.derived.get("channels").and_then(Value::as_u64);
        let frame_samples = parsed.derived.get("frame_samples").and_then(Value::as_u64);
        let asp_frame_header = uint("box.version_flags") == Some(0)
            && uint("bitstream_version") == Some(0x0800)
            && flag("global.flag_a") == Some(false)
            && flag("global.flag_c") == Some(false)
            && uint("global.component_count").is_some_and(|n| n > 0);
        let checks = [
            (
                asp_frame_header,
                "common frame syntax is not established by this cookie",
            ),
            (
                matches!(sample_rate_hz, Some(44100 | 48000)),
                "prefix requires 44.1 or 48 kHz",
            ),
            (
                frame_samples == Some(1024),
                "prefix requires 1024-frame configuration",
            ),
            (channels == Some(2), "prefix requires two declared channels"),
            (
                uint("global.component_count") == Some(1),
                "prefix requires one ASC",
            ),
            (
                uint("components[0].type") == Some(0),
                "prefix requires a channel ASC",
            ),
            (
                flag("components[0].lbr_flag") == Some(false),
                "LBR configuration flag is unsupported or missing",
            ),
            (
                uint("components[0].lowest_channel_index") == Some(0),
                "ASC must start at channel zero",
            ),
            (
                uint("components[0].tce_count") == Some(1),
                "prefix requires one TCE",
            ),
            (
                uint("components[0].tce[0].type") == Some(1),
                "prefix requires a CPE",
            ),
            (
                flag("global.additional_asc_present") == Some(false),
                "additional ASC branch is unsupported or missing",
            ),
            (
                uint("components[0].parameter_0") == Some(0)
                    && uint("components[0].parameter_1") == Some(0),
                "nonzero or missing common component parameters are unverified",
            ),
        ];
        let unsupported_reason = checks
            .into_iter()
            .find(|(ok, _)| !ok)
            .map(|(_, reason)| reason.into());
        Ok(Self {
            drc: drc::DrcContext::from_cookie(&parsed),
            packet_configuration: packet_config::PacketConfiguration::from_cookie(&parsed),
            cookie_sha256: parsed.cookie_sha256,
            sample_rate_hz,
            channels,
            frame_samples,
            asp_frame_header,
            unsupported_reason,
        })
    }
    pub fn is_supported(&self) -> bool {
        self.unsupported_reason.is_none()
    }
    pub fn cookie_sha256(&self) -> &str {
        &self.cookie_sha256
    }
    pub(crate) fn packet_rejection(&self) -> Option<&str> {
        self.packet_configuration
            .rejection
            .as_deref()
            .or(self.drc.rejection.as_deref())
            .or(self.unsupported_reason.as_deref())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnparsedRange {
    pub bit_offset: usize,
    pub bit_length: usize,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrameReport {
    pub schema_version: u32,
    pub cookie_sha256: String,
    pub packet_sha256: String,
    pub packet_bytes: usize,
    /// Whole-packet syntax status; reaching a prefix boundary is still partial.
    pub status: ParseStatus,
    pub prefix_complete: bool,
    pub fields: Vec<ConfigField>,
    pub derived: BTreeMap<String, Value>,
    pub stop_reason: String,
    pub stop_bit_offset: usize,
    /// Only the first present core payload's start is known, never its end.
    pub payload_bit_offset: Option<usize>,
    pub component_end_bit_offset: Option<usize>,
    pub unknown_ranges: Vec<UnparsedRange>,
    pub diagnostics: Vec<Diagnostic>,
}

struct Parser<'a> {
    capture: bool,
    bits: BitReader<'a>,
    report: FrameReport,
}
#[cfg(test)]
mod asp_tests;
impl Parser<'_> {
    fn take(&mut self, name: &str, width: usize) -> Result<u64, ParseError> {
        let start = self.bits.position();
        let value = self.bits.read(width)?;
        if self.capture {
            self.report.fields.push(ConfigField {
                name: name.into(),
                bit_offset: start,
                bit_length: width,
                value: json!(value),
            });
        }
        Ok(value)
    }
    fn flag(&mut self, name: &str) -> Result<bool, ParseError> {
        let value = self.take(name, 1)? != 0;
        if self.capture {
            self.report.fields.last_mut().expect("recorded field").value = json!(value);
        }
        Ok(value)
    }
    fn derived(&mut self, name: impl Into<String>, value: Value) {
        let name = name.into();
        if self.capture || name.starts_with("asp.") {
            self.report.derived.insert(name, value);
        }
    }
    fn member(&mut self, prefix: &str, suffix: &str, width: usize) -> Result<u64, ParseError> {
        if self.capture {
            self.take(&format!("{prefix}.{suffix}"), width)
        } else {
            self.bits.read(width)
        }
    }
    fn escaped(&mut self, name: &str, widths: &[usize]) -> Result<u64, ParseError> {
        let start = self.bits.position();
        let mut value = 0u64;
        for &width in widths {
            let part = self.bits.read(width)?;
            value = value.checked_add(part).ok_or_else(|| {
                ParseError::new(start, "overflow", "escaped payload value overflow")
            })?;
            if part < (1u64 << width) - 1 {
                break;
            }
        }
        if self.capture {
            self.report.fields.push(ConfigField {
                name: name.into(),
                bit_offset: start,
                bit_length: self.bits.position() - start,
                value: json!(value),
            });
        }
        Ok(value)
    }
    /// Public APAC packets use ASP framing, not APACDecoder's internal one-bit
    /// framing. Only explicit byte lengths permit skipping embedded preroll.
    fn asp(&mut self, frame_type: u64) -> Result<Option<&'static str>, ParseError> {
        self.asp_bounded(frame_type, 4096)
    }
    fn asp_bounded(
        &mut self,
        frame_type: u64,
        maximum: u64,
    ) -> Result<Option<&'static str>, ParseError> {
        if frame_type == 3 {
            self.derived("asp.frame_type_profile", json!("apac-asp-boundaries-v1"));
        }
        if frame_type == 2 {
            if self.flag("asp.reconfiguration_present")? {
                return Ok(Some(
                    "bound reference codec does not implement ASP reconfiguration",
                ));
            }
            let count_offset = self.bits.position();
            let count = self.take("asp.preroll_count", 2)?;
            if count > 1 {
                return Err(ParseError::new(
                    count_offset,
                    "preroll-count",
                    "bound reference codec supports at most one embedded preroll frame",
                ));
            }
            if count == 1 {
                let size_offset = self.bits.position();
                let mut bytes = self.take("asp.preroll.bytes", 16)?;
                if bytes == 65535 {
                    bytes += self.take("asp.preroll.extra_bytes", 16)?;
                }
                // The verified stereo ASP decoder initializes 2 * 2048 bytes.
                if bytes == 0 || bytes > maximum {
                    return Err(ParseError::new(
                        size_offset,
                        "preroll-size",
                        if maximum == 4096 {
                            "embedded preroll exceeds the verified stereo bound or is empty"
                        } else {
                            "embedded preroll exceeds the verified layout bound or is empty"
                        },
                    ));
                }
                let padding = (8 - self.bits.position() % 8) % 8;
                if padding != 0 && self.take("asp.preroll.alignment_padding", padding)? != 0 {
                    self.derived("asp.alignment_profile", json!("apac-asp-boundaries-v1"));
                }
                let start = self.bits.position();
                let bits = bytes as usize * 8;
                if bits >= self.bits.remaining() {
                    return Err(ParseError::new(
                        start,
                        "truncated",
                        "embedded preroll leaves no current frame",
                    ));
                }
                let embedded_type = self.take("asp.preroll.frame_type_code", 2)?;
                if embedded_type == 2 {
                    return Err(ParseError::new(
                        start,
                        "nested-preroll",
                        "an ASP preroll frame cannot contain another ASP preroll",
                    ));
                }
                let opaque_start = self.bits.position();
                self.bits.skip(bits - 2)?;
                self.report.unknown_ranges.push(UnparsedRange {
                    bit_offset: opaque_start,
                    bit_length: bits - 2,
                    reason: "length-delimited embedded preroll payload is not parsed".into(),
                });
                self.derived("asp.preroll.start_bit", json!(start));
                self.derived("asp.preroll.end_bit", json!(start + bits));
            }
        }
        self.derived("core_frame_start_bit", json!(self.bits.position()));
        Ok(None)
    }
    fn ics(&mut self, prefix: &str) -> Result<IcsInfo, ParseError> {
        self.ics_at_rate(prefix, 48000)
    }
    fn ics_at_rate(&mut self, prefix: &str, rate: u64) -> Result<IcsInfo, ParseError> {
        let block = self.take(&format!("{prefix}.block_type"), 2)?;
        self.ics_with_block_at_rate(prefix, block as u8, rate)
    }
    fn ics_with_block_at_rate(
        &mut self,
        prefix: &str,
        block: u8,
        rate: u64,
    ) -> Result<IcsInfo, ParseError> {
        let short = block == 2;
        let start = self.bits.position();
        let max_sfb = self.take(&format!("{prefix}.max_sfb"), if short { 4 } else { 6 })?;
        if max_sfb as usize >= sfb::offsets(rate, short).len() {
            return Err(ParseError::new(
                start,
                "max-sfb",
                "max_sfb exceeds the confirmed sample-rate table",
            ));
        }
        let grouping = if short {
            self.take(&format!("{prefix}.scale_factor_grouping"), 7)?
        } else {
            0
        };
        let mut groups = vec![1u32];
        if short {
            for shift in (0..7).rev() {
                if grouping & (1 << shift) != 0 {
                    *groups.last_mut().expect("one group") += 1;
                } else {
                    groups.push(1);
                }
            }
        }
        self.derived(format!("{prefix}.max_sfb"), json!(max_sfb));
        self.derived(format!("{prefix}.window_groups"), json!(groups));
        self.derived(
            format!("{prefix}.active_group_count"),
            json!(if max_sfb == 0 { 0 } else { groups.len() }),
        );
        Ok(IcsInfo {
            block_type: block,
            max_sfb: max_sfb as usize,
            window_groups: groups,
        })
    }
    fn finish(
        mut self,
        reason: &str,
        complete: bool,
        payload: bool,
    ) -> Result<FrameReport, ParseError> {
        let position = self.bits.position();
        if payload && self.bits.remaining() == 0 {
            return Err(ParseError::new(
                position,
                "truncated",
                "core payload is missing at the confirmed entry point",
            ));
        }
        self.report.status = if self.report.fields.is_empty() {
            ParseStatus::Unsupported
        } else {
            ParseStatus::Partial
        };
        self.report.prefix_complete = complete;
        self.report.stop_reason = reason.into();
        self.report.stop_bit_offset = position;
        self.report.payload_bit_offset = payload.then_some(position);
        if self.bits.remaining() > 0 {
            self.report.unknown_ranges.push(UnparsedRange {
                bit_offset: position,
                bit_length: self.bits.remaining(),
                reason: reason.into(),
            });
        }
        self.report.diagnostics.push(Diagnostic {
            bit_offset: position,
            message: reason.into(),
        });
        Ok(self.report)
    }
}

pub fn parse_frame(context: &FrameContext, packet: &[u8]) -> Result<FrameReport, ParseError> {
    if packet.len() > MAX_PACKET_BUFFER {
        return Err(ParseError::new(0, "input-limit", "packet exceeds 16 MiB"));
    }
    if packet.is_empty() {
        return Err(ParseError::new(0, "truncated", "empty packet"));
    }
    let mut parser = Parser {
        capture: true,
        bits: BitReader::new(packet),
        report: FrameReport {
            schema_version: SCHEMA_VERSION,
            cookie_sha256: context.cookie_sha256.clone(),
            packet_sha256: sha256(packet),
            packet_bytes: packet.len(),
            status: ParseStatus::Partial,
            prefix_complete: false,
            fields: vec![],
            derived: BTreeMap::new(),
            stop_reason: String::new(),
            stop_bit_offset: 0,
            payload_bit_offset: None,
            component_end_bit_offset: None,
            unknown_ranges: vec![],
            diagnostics: vec![],
        },
    };
    let frame_type = if context.asp_frame_header {
        Some(parser.take("frame.type_code", 2)?)
    } else {
        None
    };
    if let Some(reason) = &context.unsupported_reason {
        return parser.finish(reason, false, false);
    }
    if let Some(reason) = parser.asp(frame_type.expect("supported ASP context"))? {
        return parser.finish(reason, false, false);
    }
    let prefix = "components[0].tce[0]";
    if !parser.flag(&format!("{prefix}.present"))? {
        return parser.finish("cpe_absent", true, false);
    }
    let lrvq = parser.take(&format!("{prefix}.coding_type"), 1)? != 0;
    parser.derived("coding_type", json!(if lrvq { "lrvq" } else { "sq" }));
    if lrvq {
        // TODO: LRVQ is disabled by the registered encoder's default route.
        // Keep the dispatch explicit without treating exploratory support as a milestone.
        return parser.finish("lrvq_prefix_deferred", false, false);
    }
    parser.ics(&format!("{prefix}.left_ics"))?;
    parser.finish("sq_left_channel_stream", true, true)
}
