//! Bounded stereo SQ prefixes, ASP framing, raw spectra, CAC, TNS and BWE2 before core alignment.
use crate::prelude::*;
use crate::record::FieldValue;
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
pub(crate) use channels::parse_channel_packet_with_mode;
pub use drc::{
    DrcConfiguration, DrcNode, DrcParameters, DrcPayload, DrcReport, DrcTimeDelta, parse_drc,
};
pub use hoa::PARTIAL_PROFILE as HOA_PARTIAL_PROFILE;
pub use hoa::TRANSPORT_PROFILE as HOA_TRANSPORT_PROFILE;
pub(crate) use hoa::parse_hoa_packet_with_mode;
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
pub(crate) use packet::parse_packet_with_mode;
pub use packet::{EmbeddedPreroll, PacketReport, PacketTail, STATE_PROFILE, parse_packet};
pub use spectrum::{ChannelSpectrum, IcsInfo, Section, SpectrumReport, parse_spectrum};
pub use tns::NUMERIC_PROFILE as TNS_NUMERIC_PROFILE;
#[doc(hidden)]
pub use tns::math_sha256 as tns_math_sha256;
pub use tns::{TnsChannel, TnsChannelSpectrum, TnsFilter, TnsReport, TnsWindow, parse_tns};

use crate::{
    config::{self, ConfigField, Diagnostic, FieldExt, ParseError, ParseStatus, bits::BitReader},
    model::{SCHEMA_VERSION, sha256},
};
use std::collections::BTreeMap;

/// Largest single packet accepted by every parser and packet store.
pub const MAX_PACKET_BUFFER: usize = 16 * 1024 * 1024;

/// Immutable configuration-derived context. An unsupported context still permits
/// reporting the ASP frame type when its enclosing configuration is verified.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct FrameContext {
    #[cfg_attr(feature = "serde", serde(skip))]
    drc: drc::DrcContext,
    cookie_sha256: String,
    sample_rate_hz: Option<u64>,
    // Reported by `parse-packets`; decoding reads the configuration instead.
    #[cfg_attr(not(feature = "serde"), allow(dead_code))]
    channels: Option<u64>,
    #[cfg_attr(not(feature = "serde"), allow(dead_code))]
    frame_samples: Option<u64>,
    asp_frame_header: bool,
    unsupported_reason: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip))]
    packet_configuration: packet_config::PacketConfiguration,
}
impl FrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        Ok(Self::from_config(&config::Config::parse(cookie)?))
    }
    pub fn from_config(config: &config::Config) -> Self {
        let global = &config.global;
        let component = config.component(0);
        let sample_rate_hz = global.sample_rate_hz;
        let channels = global.channels;
        let frame_samples = global.frame_samples;
        let asp_frame_header = config.version_flags.get() == Some(0)
            && config.bitstream_version.get() == Some(0x0800)
            && global.flag_a.get() == Some(false)
            && global.flag_c.get() == Some(false)
            && global.component_count.get().is_some_and(|n| n > 0);
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
                global.component_count.get() == Some(1),
                "prefix requires one ASC",
            ),
            (
                component.kind.get() == Some(0),
                "prefix requires a channel ASC",
            ),
            (
                component.lbr_flag.get() == Some(false),
                "LBR configuration flag is unsupported or missing",
            ),
            (
                component.lowest_channel_index.get() == Some(0),
                "ASC must start at channel zero",
            ),
            (
                component.tce_count.get() == Some(1),
                "prefix requires one TCE",
            ),
            (
                component.tce_types.first().copied().get() == Some(1),
                "prefix requires a CPE",
            ),
            (
                global.additional_asc_present.get() == Some(false),
                "additional ASC branch is unsupported or missing",
            ),
            (
                component.parameter_0.get() == Some(0) && component.parameter_1.get() == Some(0),
                "nonzero or missing common component parameters are unverified",
            ),
        ];
        let unsupported_reason = checks
            .into_iter()
            .find(|(ok, _)| !ok)
            .map(|(_, reason)| reason.into());
        Self {
            drc: drc::DrcContext::from_config(config),
            packet_configuration: packet_config::PacketConfiguration::from_config(config),
            cookie_sha256: config.cookie_sha256.clone(),
            sample_rate_hz,
            channels,
            frame_samples,
            asp_frame_header,
            unsupported_reason,
        }
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

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct UnparsedRange {
    pub bit_offset: usize,
    pub bit_length: usize,
    pub reason: String,
}

#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct FrameReport {
    pub schema_version: u32,
    pub cookie_sha256: String,
    pub packet_sha256: String,
    pub packet_bytes: usize,
    /// Whole-packet syntax status; reaching a prefix boundary is still partial.
    pub status: ParseStatus,
    pub prefix_complete: bool,
    pub fields: Vec<ConfigField>,
    pub derived: BTreeMap<String, FieldValue>,
    pub stop_reason: String,
    pub stop_bit_offset: usize,
    /// The first channel element is absent: no core payload follows.
    #[cfg_attr(feature = "serde", serde(skip))]
    pub cpe_absent: bool,
    /// The embedded ASP preroll frame's bit range, when one is present.
    #[cfg_attr(feature = "serde", serde(skip))]
    pub preroll: Option<(usize, usize)>,
    /// Where the stereo prefix's left ICS starts; the spectrum stage resumes there.
    #[cfg_attr(feature = "serde", serde(skip))]
    pub left_ics_bit_offset: Option<usize>,
    /// Only the first present core payload's start is known, never its end.
    pub payload_bit_offset: Option<usize>,
    pub component_end_bit_offset: Option<usize>,
    pub unknown_ranges: Vec<UnparsedRange>,
    pub diagnostics: Vec<Diagnostic>,
}

/// What a packet parse produces besides its decisions and state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[doc(hidden)]
pub enum ParseMode {
    /// Packet reports: recorded syntax and every spectrum.
    Report,
    /// Synthesis: every spectrum, no recorded syntax.
    Decode,
    /// Fast access: state only; spectra only where state depends on them.
    Scan,
}
impl ParseMode {
    /// Record field events and derived values.
    pub fn record(self) -> bool {
        self == Self::Report
    }
    /// Evaluate dequantized spectra and the values synthesis reads.
    pub fn spectra(self) -> bool {
        self != Self::Scan
    }
}

struct Parser<'a> {
    mode: ParseMode,
    bits: BitReader<'a>,
    report: FrameReport,
}
#[cfg(test)]
mod asp_tests;
impl Parser<'_> {
    fn take(&mut self, name: impl core::fmt::Display, width: usize) -> Result<u64, ParseError> {
        let start = self.bits.position();
        let value = self.bits.read(width)?;
        if self.mode.record() {
            self.report.fields.push(ConfigField {
                name: name.to_string(),
                bit_offset: start,
                bit_length: width,
                value: FieldValue::from(value),
            });
        }
        Ok(value)
    }
    fn flag(&mut self, name: impl core::fmt::Display) -> Result<bool, ParseError> {
        let value = self.take(name, 1)? != 0;
        if self.mode.record() {
            self.report.fields.last_mut().expect("recorded field").value = FieldValue::from(value);
        }
        Ok(value)
    }
    /// A derived value, kept only when recording.
    fn derived(&mut self, name: impl core::fmt::Display, value: impl FnOnce() -> FieldValue) {
        if self.mode.record() {
            self.report.derived.insert(name.to_string(), value());
        }
    }
    fn member(
        &mut self,
        prefix: impl core::fmt::Display,
        suffix: &str,
        width: usize,
    ) -> Result<u64, ParseError> {
        self.take(format_args!("{prefix}.{suffix}"), width)
    }
    fn escaped(
        &mut self,
        name: impl core::fmt::Display,
        widths: &[usize],
    ) -> Result<u64, ParseError> {
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
        if self.mode.record() {
            self.report.fields.push(ConfigField {
                name: name.to_string(),
                bit_offset: start,
                bit_length: self.bits.position() - start,
                value: FieldValue::from(value),
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
            self.derived("asp.frame_type_profile", || {
                FieldValue::from("apac-asp-boundaries-v1")
            });
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
                    self.derived("asp.alignment_profile", || {
                        FieldValue::from("apac-asp-boundaries-v1")
                    });
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
                self.report.preroll = Some((start, start + bits));
                self.derived("asp.preroll.start_bit", || FieldValue::from(start));
                self.derived("asp.preroll.end_bit", || FieldValue::from(start + bits));
            }
        }
        let core_start = self.bits.position();
        self.derived("core_frame_start_bit", || FieldValue::from(core_start));
        Ok(None)
    }
    fn ics(&mut self, prefix: &dyn core::fmt::Display) -> Result<IcsInfo, ParseError> {
        self.ics_at_rate(prefix, 48000)
    }
    fn ics_at_rate(
        &mut self,
        prefix: &dyn core::fmt::Display,
        rate: u64,
    ) -> Result<IcsInfo, ParseError> {
        let block = self.take(format_args!("{prefix}.block_type"), 2)?;
        self.ics_with_block_at_rate(prefix, block as u8, rate)
    }
    fn ics_with_block_at_rate(
        &mut self,
        prefix: &dyn core::fmt::Display,
        block: u8,
        rate: u64,
    ) -> Result<IcsInfo, ParseError> {
        let short = block == 2;
        let start = self.bits.position();
        let max_sfb = self.take(format_args!("{prefix}.max_sfb"), if short { 4 } else { 6 })?;
        if max_sfb as usize >= sfb::offsets(rate, short).len() {
            return Err(ParseError::new(
                start,
                "max-sfb",
                "max_sfb exceeds the confirmed sample-rate table",
            ));
        }
        let grouping = if short {
            self.take(format_args!("{prefix}.scale_factor_grouping"), 7)?
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
        self.derived(format_args!("{prefix}.max_sfb"), || {
            FieldValue::from(max_sfb)
        });
        self.derived(format_args!("{prefix}.window_groups"), || {
            FieldValue::from(&groups[..])
        });
        self.derived(format_args!("{prefix}.active_group_count"), || {
            FieldValue::from(if max_sfb == 0 { 0 } else { groups.len() })
        });
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
    parse_frame_with(context, packet, ParseMode::Report)
}
pub(crate) fn parse_frame_with(
    context: &FrameContext,
    packet: &[u8],
    mode: ParseMode,
) -> Result<FrameReport, ParseError> {
    if packet.len() > MAX_PACKET_BUFFER {
        return Err(ParseError::new(0, "input-limit", "packet exceeds 16 MiB"));
    }
    if packet.is_empty() {
        return Err(ParseError::new(0, "truncated", "empty packet"));
    }
    let mut parser = Parser {
        mode,
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
            cpe_absent: false,
            preroll: None,
            left_ics_bit_offset: None,
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
    if !parser.flag(format_args!("{prefix}.present"))? {
        parser.report.cpe_absent = true;
        return parser.finish("cpe_absent", true, false);
    }
    let lrvq = parser.take(format_args!("{prefix}.coding_type"), 1)? != 0;
    parser.derived("coding_type", || {
        FieldValue::from(if lrvq { "lrvq" } else { "sq" })
    });
    if lrvq {
        // TODO: LRVQ is disabled by the registered encoder's default route.
        // Keep the dispatch explicit without treating exploratory support as a milestone.
        return parser.finish("lrvq_prefix_deferred", false, false);
    }
    parser.report.left_ics_bit_offset = Some(parser.bits.position());
    parser.ics(&format_args!("{prefix}.left_ics"))?;
    parser.finish("sq_left_channel_stream", true, true)
}
