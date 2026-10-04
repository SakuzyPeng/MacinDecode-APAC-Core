//! Restricted channel ASC: each element is immediately followed by its BWE2 data.
use super::ParseMode;
use super::{
    CacChannelSpectrum, CacData, ChannelSpectrum, DrcPayload, FrameReport, PacketTail, Parser,
    TnsChannel, TnsChannelSpectrum, UnparsedRange,
    bwe2::{self, Bwe2ChannelSpectrum, ElementBwe2Data},
    cac,
    drc::{self, DrcContext, DrcState},
    packet_config::{self, PacketConfiguration},
    tns,
};
use crate::prelude::*;
use crate::record::FieldValue;
use crate::{
    config::{self, Diagnostic, FieldExt, ParseError, ParseStatus, bits::BitReader},
    frame::MAX_PACKET_BUFFER,
    model::{ChannelLayout, SCHEMA_VERSION, sha256},
};
use std::collections::BTreeMap;

pub const STATE_PROFILE: &str = "apac-channel-state-v1";
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "lowercase"))]
pub enum ElementKind {
    Sce,
    Cpe,
    Lfe,
    Extension,
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct ElementConfiguration {
    pub element_index: usize,
    pub kind: ElementKind,
    pub tce_type: u8,
    /// Absolute output channel indices in the declared tagged layout.
    pub output_channels: Vec<u8>,
    /// HOA salient carriers have no direct output-channel mapping.
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub transport_channels: Option<Vec<u8>>,
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct ChannelFrameContext {
    pub(crate) cookie_sha256: String,
    pub(crate) sample_rate_hz: u64,
    pub(crate) channel_count: u8,
    pub(crate) layout: Option<ChannelLayout>,
    pub(crate) channel_labels: Vec<String>,
    pub(crate) elements: Vec<ElementConfiguration>,
    pub(crate) maximum_preroll_bytes: u64,
    pub(crate) rejection: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip))]
    asp_header: bool,
    #[cfg_attr(feature = "serde", serde(skip))]
    hoa: Option<super::hoa::HoaConfiguration>,
    #[cfg_attr(feature = "serde", serde(skip))]
    configuration: PacketConfiguration,
    #[cfg_attr(feature = "serde", serde(skip))]
    drc: DrcContext,
    #[cfg_attr(feature = "serde", serde(skip))]
    auxiliary: super::auxiliary::AuxiliaryConfiguration,
}
impl ChannelFrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        Ok(Self::from_config(&config::Config::parse(cookie)?))
    }
    pub fn from_config(config: &config::Config) -> Self {
        let count = config.global.channels.unwrap_or(0);
        let layout = crate::channel_layout::layout(count);
        let (family, level, types, labels, capacity) =
            layout.as_ref().map_or((0, 0, &[][..], &[][..], 0), |l| {
                (l.family, l.level, l.types, l.labels, l.preroll_bytes)
            });
        let configuration = PacketConfiguration::for_layout(config, count, family, level, types);
        let drc = DrcContext::for_channels(config, count);
        let asp_header = config.version_flags.is(0)
            && config.bitstream_version.is(0x800)
            && config.global.flag_a.is(false)
            && config.global.flag_c.is(false);
        let rejection = if types.is_empty() {
            Some(format!(
                "unsupported declared channel count {count}; expected 1, 2, 6, 8, 12 or 24"
            ))
        } else {
            configuration.rejection.clone().or(drc.rejection.clone())
        };
        let mut channel = 0u8;
        let elements = types
            .iter()
            .enumerate()
            .map(|(index, &typ)| {
                let width = if typ == 1 { 2 } else { 1 };
                let output_channels = (channel..channel + width).collect();
                channel += width;
                ElementConfiguration {
                    element_index: index,
                    kind: match typ {
                        0 => ElementKind::Sce,
                        1 => ElementKind::Cpe,
                        _ => ElementKind::Lfe,
                    },
                    tce_type: typ,
                    output_channels,
                    transport_channels: None,
                }
            })
            .collect();
        Self {
            auxiliary: super::auxiliary::AuxiliaryConfiguration::from_config(config),
            cookie_sha256: config.cookie_sha256.clone(),
            sample_rate_hz: config.global.sample_rate_hz.unwrap_or(0),
            channel_count: count as u8,
            layout: (!types.is_empty()).then(|| {
                ChannelLayout::tagged((family as u32) << 16 | count as u32, count as u32, None)
            }),
            channel_labels: labels.iter().map(|v| (*v).into()).collect(),
            elements,
            maximum_preroll_bytes: capacity,
            rejection,
            asp_header,
            hoa: None,
            configuration,
            drc,
        }
    }
    pub(super) fn hoa_transport(
        cookie_sha256: String,
        configuration: PacketConfiguration,
        drc: DrcContext,
        hoa: super::hoa::HoaConfiguration,
    ) -> Self {
        let rejection = configuration.rejection.clone().or(drc.rejection.clone());
        Self {
            auxiliary: hoa.auxiliary.clone(),
            cookie_sha256,
            sample_rate_hz: hoa.sample_rate_hz,
            channel_count: hoa.channels,
            layout: Some(hoa.source_layout.layout.clone()),
            channel_labels: hoa.source_layout.channel_labels(),
            elements: hoa
                .transport_types
                .iter()
                .enumerate()
                .scan(0usize, |first, (i, &kind)| {
                    let width = match kind {
                        0 | 3 => 1,
                        1 => 2,
                        _ => 0,
                    };
                    let mapped: Vec<_> = (*first..*first + width)
                        .map(|v| u8::try_from(v).unwrap_or(u8::MAX))
                        .collect();
                    *first += width;
                    Some(ElementConfiguration {
                        element_index: i,
                        kind: match kind {
                            1 => ElementKind::Cpe,
                            3 => ElementKind::Lfe,
                            6 => ElementKind::Extension,
                            _ => ElementKind::Sce,
                        },
                        tce_type: kind,
                        output_channels: if hoa.salient_components != 0
                            || hoa.static_ambient
                            || hoa.source_layout.extended
                            || hoa.static_remapping.is_some()
                        {
                            vec![]
                        } else {
                            mapped.clone()
                        },
                        transport_channels: (hoa.salient_components != 0
                            || hoa.static_ambient
                            || hoa.transport_extended()
                            || hoa.source_layout.extended
                            || hoa.static_remapping.is_some())
                        .then_some(mapped),
                    })
                })
                .collect(),
            maximum_preroll_bytes: hoa.preroll_bytes,
            rejection,
            asp_header: true,
            hoa: Some(hoa),
            configuration,
            drc,
        }
    }
    pub fn channel_count(&self) -> u32 {
        u32::from(self.channel_count)
    }
    pub fn channel_layout(&self) -> Option<&ChannelLayout> {
        self.layout.as_ref()
    }
    #[doc(hidden)]
    pub fn channel_layout_profile(&self) -> Option<&'static str> {
        if self.hoa.is_some() || !self.is_supported() {
            return None;
        }
        crate::channel_layout::profile(self.channel_layout()?.tag)
    }
    pub fn channel_labels(&self) -> &[String] {
        &self.channel_labels
    }
    pub fn elements(&self) -> &[ElementConfiguration] {
        &self.elements
    }
    pub fn sample_rate_hz(&self) -> u64 {
        self.sample_rate_hz
    }
    pub fn cookie_sha256(&self) -> &str {
        &self.cookie_sha256
    }
    pub fn maximum_preroll_bytes(&self) -> u64 {
        self.maximum_preroll_bytes
    }
    pub fn rejection(&self) -> Option<&str> {
        self.rejection.as_deref()
    }
    pub fn is_supported(&self) -> bool {
        self.rejection.is_none()
    }
    pub fn initial_state(&self) -> DrcState {
        DrcState {
            channels: u64::from(self.channel_count),
            configuration: self.drc.configuration.clone(),
            previous_nodes: vec![],
            previous_sequences: vec![],
            shared_syntax_used: false,
            scene_graph: None,
        }
    }
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct ElementReport {
    pub configuration: ElementConfiguration,
    pub present: bool,
    pub coding_type: Option<u8>,
    pub start_bit_offset: usize,
    /// Element return, before its immediately following BWE2 payload.
    pub end_bit_offset: Option<usize>,
    pub spectrum_complete: bool,
    pub element_complete: bool,
    pub shared_ics: Option<bool>,
    /// All channel_index fields below are local to this element.
    pub channels: Vec<ChannelSpectrum>,
    pub cac: Option<CacData>,
    pub channels_after_cac: Vec<CacChannelSpectrum>,
    pub tns_applicable: bool,
    pub tns: Vec<TnsChannel>,
    pub channels_after_tns: Vec<TnsChannelSpectrum>,
    pub bwe2_applicable: bool,
    pub bwe2_complete: bool,
    pub bwe2: Option<ElementBwe2Data>,
    pub channels_after_bwe2: Vec<Bwe2ChannelSpectrum>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub extension: Option<super::HoaExtensionData>,
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct ChannelPreroll {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub report: Box<ChannelPacketReport>,
}
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct ChannelPacketReport {
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub frame: FrameReport,
    pub packet_complete: bool,
    pub packet_state_profile: String,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub channel_layout_profile: Option<String>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub hoa: Option<super::hoa::HoaFrameInfo>,
    pub channel_count: u8,
    pub channel_labels: Vec<String>,
    pub elements: Vec<ElementReport>,
    pub numeric_profile: String,
    pub cac_numeric_profile: String,
    pub tns_numeric_profile: String,
    pub bwe2_numeric_profile: String,
    pub packet_tail: Option<PacketTail>,
    pub embedded_preroll: Option<ChannelPreroll>,
    pub drc: Option<DrcPayload>,
    pub drc_complete: Option<bool>,
    pub drc_history_sufficient: Option<bool>,
    pub drc_processing_applied: bool,
}
fn finish(
    mut report: ChannelPacketReport,
    parser: Parser<'_>,
    reason: &str,
) -> Result<ChannelPacketReport, ParseError> {
    report.frame = parser.finish(reason, false, false)?;
    report.frame.fields.sort_by_key(|f| f.bit_offset);
    report.frame.unknown_ranges.sort_by_key(|r| r.bit_offset);
    Ok(report)
}
fn element_error(mut error: ParseError, index: usize) -> ParseError {
    error.element_index = Some(index);
    error.message = format!("element {index}: {}", error.message).into();
    error
}

fn read_element(
    parser: &mut Parser<'_>,
    context: &ChannelFrameContext,
    element: &mut ElementReport,
    scratch: &mut ScanWorkspace,
    common_window: Option<u8>,
) -> Result<bool, ParseError> {
    let index = element.configuration.element_index;
    let prefix = format_args!("components[0].tce[{index}]");
    element.present = parser.flag(format_args!("{prefix}.present"))?;
    if !element.present {
        element.end_bit_offset = Some(parser.bits.position());
        element.element_complete = true;
        return Ok(true);
    }
    if element.configuration.kind == ElementKind::Extension {
        element.extension = Some(super::hoa_transport::read(parser, &prefix)?);
        element.end_bit_offset = Some(parser.bits.position());
        element.element_complete = true;
        return Ok(true);
    }
    let coding = parser.take(format_args!("{prefix}.coding_type"), 1)? as u8;
    element.coding_type = Some(coding);
    if coding != 0 {
        return Ok(false);
    }
    let cpe = element.configuration.kind == ElementKind::Cpe;
    let side = if cpe { "left_ics" } else { "ics" };
    let ics_name = format_args!("{prefix}.{side}");
    let left = if let Some(block) = common_window {
        parser.ics_with_block_at_rate(&ics_name, block, context.sample_rate_hz)?
    } else {
        parser.ics_at_rate(&ics_name, context.sample_rate_hz)?
    };
    let buffer = if parser.mode.spectra() {
        Vec::new()
    } else {
        scratch.quantized.pop().unwrap_or_default()
    };
    element.channels.push(parser.stream_buffer_at_rate(
        &format_args!("{prefix}.channels[0]"),
        left.clone(),
        0,
        buffer,
        context.sample_rate_hz,
    )?);
    if cpe {
        let shared = parser.flag(format_args!("{prefix}.shared_ics"))?;
        element.shared_ics = Some(shared);
        let right = if shared {
            left.clone()
        } else if let Some(block) = common_window {
            parser.ics_with_block_at_rate(
                &format_args!("{prefix}.right_ics"),
                block,
                context.sample_rate_hz,
            )?
        } else {
            parser.ics_at_rate(&format_args!("{prefix}.right_ics"), context.sample_rate_hz)?
        };
        let buffer = if parser.mode.spectra() {
            Vec::new()
        } else {
            scratch.quantized.pop().unwrap_or_default()
        };
        element.channels.push(parser.stream_buffer_at_rate(
            &format_args!("{prefix}.channels[1]"),
            right,
            1,
            buffer,
            context.sample_rate_hz,
        )?);
        if shared {
            let data = cac::read_data_at(parser, &left, &format_args!("{prefix}.cac"))?;
            if parser.mode.spectra() {
                element.channels_after_cac =
                    cac::apply_channels_at_rate(&element.channels, &data, context.sample_rate_hz)?;
            }
            element.cac = Some(data);
        }
    }
    if parser.mode.spectra() && element.channels_after_cac.is_empty() {
        element.channels_after_cac = element
            .channels
            .iter()
            .map(|c| CacChannelSpectrum {
                channel_index: c.channel_index,
                scaled: c.scaled.clone(),
            })
            .collect();
    }
    // Capturing syntax/report details is independent of evaluating spectra.
    // HOA restoration and its numeric checks always require every carrier.
    let evaluate = parser.mode.spectra() || context.hoa.is_some();
    if evaluate && !parser.mode.spectra() {
        ensure_numeric(element, scratch, context.sample_rate_hz)?;
    }
    element.spectrum_complete = true;
    element.tns_applicable = element.configuration.kind != ElementKind::Lfe;
    for index in 0..element.channels.len() {
        let channel_index = element.channels[index].channel_index;
        let scaled = if element.tns_applicable {
            let data = tns::read_channel(
                &mut parser.bits,
                &element.channels[index].ics,
                context.sample_rate_hz,
                channel_index,
            )?;
            if parser.mode.record() {
                parser.report.fields.push(config::ConfigField {
                    name: format!("{prefix}.tns[{channel_index}]"),
                    bit_offset: data.start_bit_offset,
                    bit_length: data.end_bit_offset - data.start_bit_offset,
                    value: FieldValue::Tns(Box::new(data.clone())),
                });
            }
            let scaled = if evaluate || tns::effective(&data) {
                ensure_numeric(element, scratch, context.sample_rate_hz)?;
                tns::apply(&element.channels_after_cac[index].scaled, &data)?
            } else {
                Vec::new()
            };
            element.tns.push(data);
            scaled
        } else if evaluate {
            element.channels_after_cac[index].scaled.clone()
        } else {
            Vec::new()
        };
        element.channels_after_tns.push(TnsChannelSpectrum {
            channel_index,
            scaled,
        });
    }
    element.end_bit_offset = Some(parser.bits.position());
    element.element_complete = true;
    Ok(true)
}
fn ensure_numeric(
    element: &mut ElementReport,
    scratch: &mut ScanWorkspace,
    rate: u64,
) -> Result<(), ParseError> {
    if !element.channels_after_cac.is_empty() {
        return Ok(());
    }
    scratch.numeric_elements += 1;
    for channel in &mut element.channels {
        super::spectrum::materialize_at_rate(channel, rate);
    }
    element.channels_after_cac = if let Some(data) = &element.cac {
        cac::apply_channels_at_rate(&element.channels, data, rate)?
    } else {
        element
            .channels
            .iter()
            .map(|c| CacChannelSpectrum {
                channel_index: c.channel_index,
                scaled: c.scaled.clone(),
            })
            .collect()
    };
    Ok(())
}

fn extensions(
    parser: &mut Parser<'_>,
    element: &mut ElementReport,
    scratch: &mut ScanWorkspace,
    rate: u64,
    evaluate: bool,
) -> Result<(), ParseError> {
    if !element.present || element.configuration.kind == ElementKind::Extension {
        return Ok(());
    }
    element.bwe2_applicable = element.configuration.kind != ElementKind::Lfe;
    if element.bwe2_applicable {
        let ics: Vec<_> = element.channels.iter().map(|c| c.ics.clone()).collect();
        let data = bwe2::read_element_data(&mut parser.bits, &ics)?;
        if parser.mode.record() {
            parser.report.fields.push(config::ConfigField {
                name: format!(
                    "components[0].bwe2[{}]",
                    element.configuration.element_index
                ),
                bit_offset: data.start_bit_offset,
                bit_length: data.end_bit_offset - data.start_bit_offset,
                value: FieldValue::ElementBwe2(Box::new(data.clone())),
            });
        }
        element.bwe2 = Some(data);
        element.bwe2_complete = true;
    }
    for index in 0..element.channels.len() {
        let channel_index = element.channels[index].channel_index;
        let parameters = element
            .bwe2
            .as_ref()
            .and_then(|d| d.channels[index].parameters.as_ref())
            .filter(|_| element.channels[index].ics.max_sfb > 0)
            .cloned();
        let (scaled, analysis, regions) = if let Some(p) = parameters {
            ensure_numeric(element, scratch, rate)?;
            if element.channels_after_tns[index].scaled.is_empty() {
                // This channel had no effective TNS. Other channels may already
                // have undergone their checks, in the original error order.
                element.channels_after_tns[index].scaled =
                    element.channels_after_cac[index].scaled.clone();
            }
            let ics = &element.channels[index].ics;
            let (cutoff, regions) = if parser.mode.spectra() {
                bwe2::regions_at_rate(ics, rate)
            } else {
                (bwe2::cutoff_at_rate(ics, rate), Vec::new())
            };
            let (scaled, analysis) = crate::bwe2_math::restore_captured(
                &element.channels_after_tns[index].scaled,
                ics.block_type == 2,
                cutoff,
                &ics.window_groups,
                p.lsf_indices,
                &p.gain_indices,
                parser.mode.spectra(),
            )
            .map_err(|s| ParseError::new(parser.bits.position(), "bwe2-numeric", s))?;
            (scaled, analysis, regions)
        } else if evaluate {
            (
                element.channels_after_tns[index].scaled.clone(),
                None,
                vec![],
            )
        } else {
            (Vec::new(), None, Vec::new())
        };
        if evaluate {
            element.channels_after_bwe2.push(Bwe2ChannelSpectrum {
                channel_index,
                processing_applied: analysis.is_some(),
                regions,
                analysis,
                scaled,
            });
        }
    }
    Ok(())
}

pub fn parse_channel_packet(
    context: &ChannelFrameContext,
    packet: &[u8],
) -> Result<ChannelPacketReport, ParseError> {
    parse_channel_packet_with_state(context, packet, &mut context.initial_state())
}
pub fn parse_channel_packet_with_state(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
) -> Result<ChannelPacketReport, ParseError> {
    parse_channel_packet_with_mode(context, packet, state, ParseMode::Report)
}
pub(crate) fn parse_channel_packet_with_mode(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    mode: ParseMode,
) -> Result<ChannelPacketReport, ParseError> {
    parse_impl(
        context,
        packet,
        state,
        mode,
        &mut ScanWorkspace::default(),
        &mut None,
    )
}

pub(super) fn parse_hoa_transport(
    context: &ChannelFrameContext,
    packet: &[u8],
    drc: &mut DrcState,
    hoa: &mut super::hoa::HoaState,
    mode: ParseMode,
) -> Result<ChannelPacketReport, ParseError> {
    let mut next = Some(hoa.clone());
    let report = parse_impl(
        context,
        packet,
        drc,
        mode,
        &mut ScanWorkspace::default(),
        &mut next,
    )?;
    if report.packet_complete {
        *hoa = next.expect("HOA state");
    }
    Ok(report)
}

#[derive(Default)]
pub struct ScanWorkspace {
    quantized: Vec<Vec<i32>>,
    pub numeric_elements: u64,
}

pub fn scan_channel_packet(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    scratch: &mut ScanWorkspace,
) -> Result<ChannelPacketReport, ParseError> {
    scratch.numeric_elements = 0;
    parse_impl(context, packet, state, ParseMode::Scan, scratch, &mut None)
}

pub fn scan_hoa_packet(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    hoa: &mut super::hoa::HoaState,
    scratch: &mut ScanWorkspace,
) -> Result<ChannelPacketReport, ParseError> {
    let mut next = Some(hoa.clone());
    let report = parse_impl(context, packet, state, ParseMode::Scan, scratch, &mut next)?;
    if report.packet_complete {
        *hoa = next.expect("HOA state");
    }
    Ok(report)
}

fn parse_impl(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    mode: ParseMode,
    scratch: &mut ScanWorkspace,
    hoa_state: &mut Option<super::hoa::HoaState>,
) -> Result<ChannelPacketReport, ParseError> {
    if packet.is_empty() || packet.len() > MAX_PACKET_BUFFER {
        return Err(ParseError::new(
            0,
            "input-limit",
            "packet must contain 1..16 MiB",
        ));
    }
    let frame = FrameReport {
        schema_version: SCHEMA_VERSION,
        cookie_sha256: if mode.spectra() {
            context.cookie_sha256.clone()
        } else {
            String::new()
        },
        packet_sha256: if mode.spectra() || context.drc.present || context.hoa.is_some() {
            sha256(packet)
        } else {
            String::new()
        },
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
    };
    let mut parser = Parser {
        mode,
        bits: BitReader::new(packet),
        report: frame.clone(),
    };
    let mut result = core_report(context, frame);
    let code = if context.asp_header {
        parser.take("frame.type_code", 2)?
    } else {
        0
    };
    if let Some(reason) = &context.rejection {
        let mut report = finish(result, parser, reason)?;
        report.frame.status = ParseStatus::Unsupported;
        return Ok(report);
    }
    if let Some(reason) = parser.asp_bounded(code, context.maximum_preroll_bytes)? {
        return finish(result, parser, reason);
    }
    let mut next = state.clone();
    let mut next_hoa = hoa_state.clone();
    if let Some((start, end)) = parser.report.preroll {
        let nested = parse_impl(
            context,
            &packet[start / 8..end / 8],
            &mut next,
            mode,
            scratch,
            &mut next_hoa,
        )
        .map_err(|mut e| {
            let local = e.bit_offset;
            e.bit_offset += start;
            e.message = format!("embedded preroll at local bit {local}: {}", e.message).into();
            e
        })?;
        parser
            .report
            .unknown_ranges
            .retain(|r| !(r.bit_offset >= start && r.bit_offset < end));
        for field in &nested.frame.fields {
            if field.name == "frame.type_code" {
                continue;
            }
            let mut field = field.clone();
            field.name = format!("asp.preroll.{}", field.name);
            field.bit_offset += start;
            parser.report.fields.push(field);
        }
        for r in &nested.frame.unknown_ranges {
            parser.report.unknown_ranges.push(UnparsedRange {
                bit_offset: start + r.bit_offset,
                bit_length: r.bit_length,
                reason: format!("embedded preroll: {}", r.reason),
            });
        }
        for d in &nested.frame.diagnostics {
            parser.report.diagnostics.push(Diagnostic {
                bit_offset: start + d.bit_offset,
                message: format!("embedded preroll: {}", d.message),
            });
        }
        let complete = nested.packet_complete;
        result.embedded_preroll = Some(ChannelPreroll {
            start_bit_offset: start,
            end_bit_offset: end,
            report: Box::new(nested),
        });
        if !complete {
            return finish(result, parser, "embedded_preroll_incomplete");
        }
    }
    if let Some(reason) = read_core(
        context,
        &mut parser,
        &mut result,
        &mut next_hoa,
        scratch,
        code,
    )? {
        return finish(result, parser, &reason);
    }
    let core_end = parser.bits.position();
    let scene_graph = super::auxiliary::read_graph(&mut parser, &context.auxiliary, &mut next)?;
    let mut scene_update = None;
    if context.configuration.scene_present {
        let present = parser.flag("ancillary.audio_scenes_update_present")?;
        scene_update = Some(present);
        if present {
            let (scene, scenes, end) =
                config::parse_scene_at(packet, parser.bits.position(), mode.record())?;
            parser.bits.skip(end - parser.bits.position())?;
            if mode.record() {
                parser.report.fields.extend(scene.fields);
            }
            if !scene.complete {
                return finish(result, parser, "unsupported audio scene update");
            }
            let rejected = packet_config::neutral_scene(&scenes, "packet");
            if !rejected.is_empty() {
                return finish(
                    result,
                    parser,
                    &format!("non-neutral audio scene update: {}", rejected.join("; ")),
                );
            }
        }
    }
    if context.drc.present {
        let payload = drc::read_payload(&mut parser, &mut next, context.sample_rate_hz)?;
        result.drc_history_sufficient = Some(next.history_sufficient());
        next.advance(&payload);
        if mode.spectra() {
            result.drc = Some(payload);
        }
        result.drc_complete = Some(true);
    }
    let trimming = super::auxiliary::read_trimming(&mut parser)?;
    let custom_data = super::auxiliary::read(&mut parser, &context.auxiliary)?;
    let ancillary_end = parser.bits.position();
    if context.auxiliary.present
        && parser.bits.remaining() != 0
        && parser.flag("packet.extension_terminator")?
    {
        return finish(
            result,
            parser,
            "nonzero packet extension terminator is unsupported",
        );
    }
    if !context.auxiliary.present
        && context.drc.present
        && parser.bits.remaining() != 0
        && parser.flag("packet.disabled_custom_data_flag")?
    {
        return finish(
            result,
            parser,
            "nonzero disabled custom-data flag is unsupported",
        );
    }
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("packet.alignment_padding", padding)? != 0 {
        return finish(result, parser, "nonzero packet alignment is unsupported");
    }
    if parser.bits.remaining() != 0 {
        return finish(result, parser, "unparsed trailing bytes");
    }
    result.packet_tail = Some(PacketTail {
        core_end_bit_offset: core_end,
        ancillary_start_bit_offset: core_end,
        scene_update_present: scene_update,
        neutral_scene_restatement: scene_update == Some(true),
        trimming_present: trimming.is_some(),
        trimming,
        custom_data,
        scene_graph,
        ancillary_end_bit_offset: ancillary_end,
        packet_end_bit_offset: parser.bits.position(),
    });
    result.packet_complete = true;
    parser.report.status = ParseStatus::Complete;
    parser.report.prefix_complete = true;
    parser.report.stop_reason = "packet_complete".into();
    parser.report.stop_bit_offset = parser.bits.position();
    parser.report.fields.sort_by_key(|f| f.bit_offset);
    parser.report.unknown_ranges.sort_by_key(|r| r.bit_offset);
    result.frame = parser.report;
    *state = next;
    *hoa_state = next_hoa;
    Ok(result)
}

fn read_core(
    context: &ChannelFrameContext,
    parser: &mut Parser<'_>,
    result: &mut ChannelPacketReport,
    next_hoa: &mut Option<super::hoa::HoaState>,
    scratch: &mut ScanWorkspace,
    code: u64,
) -> Result<Option<String>, ParseError> {
    let common_window = if context.hoa.is_some() {
        let block = parser.take("hoa.common_window", 2)? as u8;
        result.hoa.as_mut().expect("HOA context").common_window = Some(block);
        Some(block)
    } else {
        None
    };
    for configuration in &context.elements {
        let mut element = ElementReport {
            configuration: configuration.clone(),
            present: false,
            coding_type: None,
            start_bit_offset: parser.bits.position(),
            end_bit_offset: None,
            spectrum_complete: false,
            element_complete: false,
            shared_ics: None,
            channels: vec![],
            cac: None,
            channels_after_cac: vec![],
            tns_applicable: false,
            tns: vec![],
            channels_after_tns: vec![],
            bwe2_applicable: false,
            bwe2_complete: false,
            bwe2: None,
            channels_after_bwe2: vec![],
            extension: None,
        };
        let supported = read_element(parser, context, &mut element, scratch, common_window)
            .map_err(|e| element_error(e, configuration.element_index))?;
        result.elements.push(element);
        if !supported {
            return Ok(Some(format!(
                "lrvq_element_{}_deferred",
                configuration.element_index
            )));
        }
        let element = result.elements.last_mut().expect("recorded element");
        let evaluate = parser.mode.spectra() || context.hoa.is_some();
        extensions(parser, element, scratch, context.sample_rate_hz, evaluate)
            .map_err(|e| element_error(e, element.configuration.element_index))?;
        if !parser.mode.spectra() {
            for channel in element.channels.drain(..) {
                scratch.quantized.push(channel.quantized);
            }
            element.cac = None;
            element.tns.clear();
            element.bwe2 = None;
            element.channels_after_cac.clear();
            element.channels_after_tns.clear();
        }
    }
    if let Some(shape) = &context.hoa {
        let state = next_hoa.as_mut().expect("HOA state");
        let (effective, frame_configuration) =
            super::hoa_controls::effective_configuration(parser, state, shape, code)?;
        let shape = &effective;
        let mut spatial =
            super::hoa::spatial(parser, state, shape, common_window.expect("HOA window"))?;
        if let Some(report) = &frame_configuration {
            spatial.start_bit_offset = report.start_bit_offset;
        }
        spatial.frame_configuration = frame_configuration;
        result.frame.packet_sha256 = parser.report.packet_sha256.clone();
        let restored = if shape.ambient_combination == super::AmbientCombination::Add
            && shape.salient_components != 0
            && shape.ambient_components != 0
        {
            let (restored, additive) =
                super::hoa_additive::restore(result, &mut spatial, state, shape)?;
            if parser.mode.spectra() {
                result.hoa.as_mut().expect("HOA context").additive = Some(additive);
            }
            restored
        } else {
            let mut restored = if let Some(data) = &mut spatial.salient {
                super::hoa_salient::restore(
                    result,
                    data,
                    state.salient.as_mut().expect("salient state"),
                    usize::from(shape.recovery_slots),
                    (shape.salient_components != 0 && shape.ambient_components != 0)
                        .then(|| shape.ambient_indices()),
                    !shape.controls.flag_a,
                )?
            } else {
                super::hoa::restore(result, shape)?
            };
            if let Some(data) = &mut spatial.ambient {
                super::hoa_ambient::restore(result, data, &mut restored, spatial.end_bit_offset)?;
            }
            restored
        };
        if shape.controls.flag_b
            && let Some(history) = state.salient.as_mut()
        {
            history.finish_active_components(
                &shape.salient_configurations,
                &result.frame.packet_sha256,
            );
        }
        let internal = (parser.mode.spectra() && shape.source_layout.extended).then(|| {
            restored
                .iter()
                .map(|s| super::HoaCoefficientSpectrum {
                    acn_index: s.slot_index,
                    scaled: s.scaled.clone(),
                })
                .collect()
        });
        let mut restored = if shape.source_layout.extended {
            shape
                .source_layout
                .convert(&restored, spatial.end_bit_offset)?
        } else {
            restored
        };
        if shape.dynamic_method.is_some() {
            restored.truncate(usize::from(shape.recovery_slots));
        } else if shape.source_layout.extended {
            restored.truncate(usize::from(shape.channels));
        }
        let (dynamic, restored) = if shape.dynamic_method.is_some() {
            let (data, spectra) = super::hoa_dynamic::read_and_apply(
                parser,
                shape,
                common_window.expect("HOA window"),
                restored,
                spatial.ambient.take(),
                state,
            )?;
            spatial.end_bit_offset = data.end_bit_offset;
            (Some(data), spectra)
        } else {
            (
                None,
                restored
                    .into_iter()
                    .map(|s| super::hoa::HoaCoefficientSpectrum {
                        acn_index: s.slot_index,
                        scaled: s.scaled,
                    })
                    .collect(),
            )
        };
        let hoa = result.hoa.as_mut().expect("HOA context");
        if shape.controls.flag_b {
            hoa.core_channels = usize::from(shape.core_channels);
            hoa.mixed = shape.mixed_mapping();
        }
        if parser.mode.spectra() {
            hoa.dynamic_selection = dynamic;
            hoa.spatial = Some(spatial);
        }
        if let Some(internal) = internal {
            hoa.source_layout = Some(
                shape.source_layout.report(
                    restored
                        .into_iter()
                        .map(|s| super::HoaSourceChannelSpectrum {
                            channel_index: s.acn_index,
                            scaled: s.scaled,
                        })
                        .collect(),
                ),
            );
            hoa.channels_after_hoa = internal;
            hoa.spectral_stage = "hoa_internal_coefficients_before_source_layout".into();
        } else if parser.mode.spectra() {
            hoa.channels_after_hoa = restored;
        }
        hoa.hoa_complete = true;
    }
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("core.alignment_padding", padding)? != 0 {
        return Ok(Some("nonzero core alignment is unsupported".into()));
    }
    let core_end = parser.bits.position();
    parser.report.component_end_bit_offset = Some(core_end);
    Ok(None)
}

fn core_report(context: &ChannelFrameContext, frame: FrameReport) -> ChannelPacketReport {
    let mut result = ChannelPacketReport {
        frame,
        packet_complete: false,
        packet_state_profile: context
            .hoa
            .as_ref()
            .map_or(STATE_PROFILE, |h| h.state_profile())
            .into(),
        hoa: context
            .hoa
            .as_ref()
            .map(|_| super::hoa::HoaFrameInfo::default()),
        channel_layout_profile: context.channel_layout_profile().map(str::to_owned),
        channel_count: context.channel_count,
        channel_labels: context.channel_labels.clone(),
        elements: vec![],
        numeric_profile: crate::numeric::PROFILE.into(),
        cac_numeric_profile: cac::NUMERIC_PROFILE.into(),
        tns_numeric_profile: tns::NUMERIC_PROFILE.into(),
        bwe2_numeric_profile: bwe2::NUMERIC_PROFILE.into(),
        packet_tail: None,
        embedded_preroll: None,
        drc: None,
        drc_complete: context.drc.present.then_some(false),
        drc_history_sufficient: None,
        drc_processing_applied: false,
    };
    if let Some(shape) = &context.hoa {
        let hoa = result.hoa.as_mut().expect("HOA context");
        hoa.numeric_profile = shape.numeric_profile().into();
        hoa.order = shape.order;
        hoa.full_order = (!shape.full_order).then_some(false);
        hoa.coefficient_count = usize::from(shape.recovery_slots);
        hoa.transport_channels = usize::from(shape.transport_channels);
        if shape.transport_extended() {
            hoa.transport_profile = Some(super::hoa::TRANSPORT_PROFILE.into());
            hoa.transport_format_sha256 = Some(super::hoa_transport::format_sha256().into());
            hoa.transport_element_count = Some(shape.transport_types.len());
        }
        hoa.core_channels = usize::from(shape.core_channels);
        hoa.static_remapping = shape.static_remapping.as_deref().cloned();
        hoa.mixed = shape.mixed_mapping();
        if shape.dynamic_method.is_some() {
            hoa.output_order = shape
                .source_layout
                .layout
                .ambisonic_order
                .map(|order| order as u8);
            hoa.output_coefficient_count = Some(usize::from(shape.channels));
        }
    }
    result
}

pub(super) fn parse_core_at(
    context: &ChannelFrameContext,
    packet: &[u8],
    start: usize,
    code: u64,
    state: &mut Option<super::hoa::HoaState>,
    mode: ParseMode,
    scratch: &mut ScanWorkspace,
) -> Result<ChannelPacketReport, ParseError> {
    let frame = FrameReport {
        schema_version: SCHEMA_VERSION,
        cookie_sha256: context.cookie_sha256.clone(),
        packet_sha256: sha256(packet),
        packet_bytes: packet.len(),
        status: ParseStatus::Partial,
        prefix_complete: false,
        fields: vec![],
        derived: BTreeMap::new(),
        stop_reason: String::new(),
        stop_bit_offset: start,
        cpe_absent: false,
        preroll: None,
        left_ics_bit_offset: None,
        payload_bit_offset: Some(start),
        component_end_bit_offset: None,
        unknown_ranges: vec![],
        diagnostics: vec![],
    };
    let mut parser = Parser {
        mode,
        bits: BitReader::new(packet),
        report: frame.clone(),
    };
    parser.bits.skip(start)?;
    let mut result = core_report(context, frame);
    let mut next = state.clone();
    if let Some(reason) = read_core(context, &mut parser, &mut result, &mut next, scratch, code)? {
        return finish(result, parser, &reason);
    }
    result.packet_complete = true;
    parser.report.status = ParseStatus::Complete;
    parser.report.prefix_complete = true;
    parser.report.stop_reason = "core_complete".into();
    parser.report.stop_bit_offset = parser.bits.position();
    result.frame = parser.report;
    *state = next;
    Ok(result)
}
