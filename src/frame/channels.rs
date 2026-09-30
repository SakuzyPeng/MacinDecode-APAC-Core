//! Restricted channel ASC: each element is immediately followed by its BWE2 data.
use super::{
    CacChannelSpectrum, CacData, ChannelSpectrum, DrcPayload, FrameReport, PacketTail, Parser,
    TnsChannel, TnsChannelSpectrum, UnparsedRange,
    bwe2::{self, Bwe2ChannelSpectrum, ElementBwe2Data},
    cac,
    drc::{self, DrcContext, DrcState},
    packet_config::{self, PacketConfiguration},
    tns,
};
use crate::{
    config::{self, Diagnostic, ParseError, ParseStatus, bits::BitReader},
    model::{ChannelLayout, SCHEMA_VERSION, sha256},
    packets::MAX_PACKET_BUFFER,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::BTreeMap;

pub const STATE_PROFILE: &str = "apac-channel-state-v1";
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ElementKind {
    Sce,
    Cpe,
    Lfe,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ElementConfiguration {
    pub element_index: u8,
    pub kind: ElementKind,
    pub tce_type: u8,
    /// Absolute output channel indices in the declared tagged layout.
    pub output_channels: Vec<u8>,
    /// HOA salient carriers have no direct output-channel mapping.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transport_channels: Option<Vec<u8>>,
}
#[derive(Debug, Clone, Serialize)]
pub struct ChannelFrameContext {
    pub(crate) cookie_sha256: String,
    pub(crate) sample_rate_hz: u64,
    pub(crate) channel_count: u8,
    pub(crate) layout: Option<ChannelLayout>,
    pub(crate) channel_labels: Vec<String>,
    pub(crate) elements: Vec<ElementConfiguration>,
    pub(crate) maximum_preroll_bytes: u64,
    pub(crate) rejection: Option<String>,
    #[serde(skip)]
    asp_header: bool,
    #[serde(skip)]
    hoa: Option<super::hoa::HoaConfiguration>,
    #[serde(skip)]
    configuration: PacketConfiguration,
    #[serde(skip)]
    drc: DrcContext,
}
impl ChannelFrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        let parsed = config::parse_cookie(cookie)?;
        let count = parsed
            .derived
            .get("channels")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let layout = crate::channel_layout::layout(count);
        let (family, level, types, labels, capacity) =
            layout.as_ref().map_or((0, 0, &[][..], &[][..], 0), |l| {
                (l.family, l.level, l.types, l.labels, l.preroll_bytes)
            });
        let configuration = PacketConfiguration::for_layout(&parsed, count, family, level, types);
        let drc = DrcContext::for_channels(&parsed, count);
        let field = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == name)
                .map(|f| &f.value)
        };
        let asp_header = field("box.version_flags") == Some(&json!(0))
            && field("bitstream_version") == Some(&json!(0x800))
            && field("global.flag_a") == Some(&json!(false))
            && field("global.flag_c") == Some(&json!(false));
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
                    element_index: index as u8,
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
        Ok(Self {
            cookie_sha256: parsed.cookie_sha256,
            sample_rate_hz: parsed
                .derived
                .get("sample_rate_hz")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
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
        })
    }
    pub(super) fn hoa_transport(
        cookie_sha256: String,
        configuration: PacketConfiguration,
        drc: DrcContext,
        hoa: super::hoa::HoaConfiguration,
    ) -> Self {
        let rejection = configuration.rejection.clone().or(drc.rejection.clone());
        Self {
            cookie_sha256,
            sample_rate_hz: hoa.sample_rate_hz,
            channel_count: hoa.channels,
            layout: Some(ChannelLayout::tagged(
                (190 << 16) | u32::from(hoa.channels),
                u32::from(hoa.channels),
                Some("HOA ACN/SN3D".into()),
            )),
            channel_labels: (0..hoa.channels).map(|i| format!("ACN{i}")).collect(),
            elements: (0..hoa.transport_channels)
                .map(|i| ElementConfiguration {
                    element_index: i,
                    kind: ElementKind::Sce,
                    tce_type: 0,
                    output_channels: if hoa.salient_components != 0 {
                        vec![]
                    } else {
                        vec![i]
                    },
                    transport_channels: (hoa.salient_components != 0).then(|| vec![i]),
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
    pub(crate) fn initial_state(&self) -> DrcState {
        DrcState {
            channels: u64::from(self.channel_count),
            configuration: self.drc.configuration.clone(),
            previous_nodes: vec![],
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
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
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelPreroll {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub report: Box<ChannelPacketReport>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelPacketReport {
    #[serde(flatten)]
    pub frame: FrameReport,
    pub packet_complete: bool,
    pub packet_state_profile: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub channel_layout_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
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
fn element_error(mut error: ParseError, index: u8) -> ParseError {
    error.element_index = Some(index);
    error.message = format!("element {index}: {}", error.message);
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
    let prefix = format!("components[0].tce[{index}]");
    element.present = parser.flag(&format!("{prefix}.present"))?;
    if !element.present {
        element.end_bit_offset = Some(parser.bits.position());
        element.element_complete = true;
        return Ok(true);
    }
    let coding = parser.take(&format!("{prefix}.coding_type"), 1)? as u8;
    element.coding_type = Some(coding);
    if coding != 0 {
        return Ok(false);
    }
    let cpe = element.configuration.kind == ElementKind::Cpe;
    let ics_name = format!("{prefix}.{}", if cpe { "left_ics" } else { "ics" });
    let left = if let Some(block) = common_window {
        parser.ics_with_block(&ics_name, block)?
    } else {
        parser.ics(&ics_name)?
    };
    let buffer = if parser.capture {
        Vec::new()
    } else {
        scratch.quantized.pop().unwrap_or_default()
    };
    element.channels.push(parser.stream_buffer(
        &format!("{prefix}.channels[0]"),
        left.clone(),
        0,
        buffer,
    )?);
    if cpe {
        let shared = parser.flag(&format!("{prefix}.shared_ics"))?;
        element.shared_ics = Some(shared);
        let right = if shared {
            left.clone()
        } else {
            parser.ics(&format!("{prefix}.right_ics"))?
        };
        let buffer = if parser.capture {
            Vec::new()
        } else {
            scratch.quantized.pop().unwrap_or_default()
        };
        element.channels.push(parser.stream_buffer(
            &format!("{prefix}.channels[1]"),
            right,
            1,
            buffer,
        )?);
        if shared {
            let data = cac::read_data_at(parser, &left, &format!("{prefix}.cac"))?;
            if parser.capture {
                element.channels_after_cac = cac::apply_channels(&element.channels, &data)?;
            }
            element.cac = Some(data);
        }
    }
    if parser.capture && element.channels_after_cac.is_empty() {
        element.channels_after_cac = element
            .channels
            .iter()
            .map(|c| CacChannelSpectrum {
                channel_index: c.channel_index,
                scaled: c.scaled.clone(),
            })
            .collect();
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
            if parser.capture {
                parser.report.fields.push(config::ConfigField {
                    name: format!("{prefix}.tns[{channel_index}]"),
                    bit_offset: data.start_bit_offset,
                    bit_length: data.end_bit_offset - data.start_bit_offset,
                    value: serde_json::to_value(&data).expect("finite TNS"),
                });
            }
            let scaled = if parser.capture || tns::effective(&data) {
                ensure_numeric(element, scratch)?;
                tns::apply(&element.channels_after_cac[index].scaled, &data)?
            } else {
                Vec::new()
            };
            element.tns.push(data);
            scaled
        } else if parser.capture {
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
) -> Result<(), ParseError> {
    if !element.channels_after_cac.is_empty() {
        return Ok(());
    }
    scratch.numeric_elements += 1;
    for channel in &mut element.channels {
        super::spectrum::materialize(channel);
    }
    element.channels_after_cac = if let Some(data) = &element.cac {
        cac::apply_channels(&element.channels, data)?
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
) -> Result<(), ParseError> {
    if !element.present {
        return Ok(());
    }
    element.bwe2_applicable = element.configuration.kind != ElementKind::Lfe;
    if element.bwe2_applicable {
        let ics: Vec<_> = element.channels.iter().map(|c| c.ics.clone()).collect();
        let data = bwe2::read_element_data(&mut parser.bits, &ics)?;
        if parser.capture {
            parser.report.fields.push(config::ConfigField {
                name: format!(
                    "components[0].bwe2[{}]",
                    element.configuration.element_index
                ),
                bit_offset: data.start_bit_offset,
                bit_length: data.end_bit_offset - data.start_bit_offset,
                value: serde_json::to_value(&data).expect("finite BWE2 data"),
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
            ensure_numeric(element, scratch)?;
            if element.channels_after_tns[index].scaled.is_empty() {
                // This channel had no effective TNS. Other channels may already
                // have undergone their checks, in the original error order.
                element.channels_after_tns[index].scaled =
                    element.channels_after_cac[index].scaled.clone();
            }
            let ics = &element.channels[index].ics;
            let (cutoff, regions) = if parser.capture {
                bwe2::regions(ics)
            } else {
                (bwe2::cutoff(ics), Vec::new())
            };
            let (scaled, analysis) = crate::bwe2_math::restore_captured(
                &element.channels_after_tns[index].scaled,
                ics.block_type == 2,
                cutoff,
                &ics.window_groups,
                p.lsf_indices,
                &p.gain_indices,
                parser.capture,
            )
            .map_err(|s| ParseError::new(parser.bits.position(), "bwe2-numeric", s))?;
            (scaled, analysis, regions)
        } else if parser.capture {
            (
                element.channels_after_tns[index].scaled.clone(),
                None,
                vec![],
            )
        } else {
            (Vec::new(), None, Vec::new())
        };
        if parser.capture {
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
pub(crate) fn parse_channel_packet_with_state(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
) -> Result<ChannelPacketReport, ParseError> {
    parse_impl(
        context,
        packet,
        state,
        true,
        &mut ScanWorkspace::default(),
        &mut None,
    )
}

pub(super) fn parse_hoa_transport(
    context: &ChannelFrameContext,
    packet: &[u8],
    drc: &mut DrcState,
    hoa: &mut super::hoa::HoaState,
) -> Result<ChannelPacketReport, ParseError> {
    let mut next = Some(hoa.clone());
    let report = parse_impl(
        context,
        packet,
        drc,
        true,
        &mut ScanWorkspace::default(),
        &mut next,
    )?;
    if report.packet_complete {
        *hoa = next.expect("HOA state");
    }
    Ok(report)
}

#[derive(Default)]
pub(crate) struct ScanWorkspace {
    quantized: Vec<Vec<i32>>,
    pub numeric_elements: u64,
}

pub(crate) fn scan_channel_packet(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    scratch: &mut ScanWorkspace,
) -> Result<ChannelPacketReport, ParseError> {
    scratch.numeric_elements = 0;
    parse_impl(context, packet, state, false, scratch, &mut None)
}

fn parse_impl(
    context: &ChannelFrameContext,
    packet: &[u8],
    state: &mut DrcState,
    capture: bool,
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
        cookie_sha256: if capture {
            context.cookie_sha256.clone()
        } else {
            String::new()
        },
        packet_sha256: if capture || context.drc.present {
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
        payload_bit_offset: None,
        component_end_bit_offset: None,
        unknown_ranges: vec![],
        diagnostics: vec![],
    };
    let mut parser = Parser {
        capture,
        bits: BitReader::new(packet),
        report: frame.clone(),
    };
    let mut result = ChannelPacketReport {
        frame,
        packet_complete: false,
        packet_state_profile: context
            .hoa
            .map_or(STATE_PROFILE, |h| h.state_profile())
            .into(),
        hoa: context.hoa.map(|_| super::hoa::HoaFrameInfo::default()),
        channel_layout_profile: crate::channel_layout::profile(u64::from(context.channel_count))
            .map(str::to_owned),
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
    if let Some(shape) = context.hoa {
        let hoa = result.hoa.as_mut().expect("HOA context");
        hoa.numeric_profile = shape.numeric_profile().into();
        hoa.order = shape.order;
        hoa.coefficient_count = usize::from(shape.channels);
        hoa.transport_channels = usize::from(shape.transport_channels);
        hoa.core_channels = usize::from(shape.core_channels);
        hoa.mixed = shape.mixed_mapping();
    }
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
    if let Some(start) = parser
        .report
        .derived
        .get("asp.preroll.start_bit")
        .and_then(|v| v.as_u64())
    {
        let start = start as usize;
        let end = parser.report.derived["asp.preroll.end_bit"]
            .as_u64()
            .unwrap() as usize;
        let nested = parse_impl(
            context,
            &packet[start / 8..end / 8],
            &mut next,
            capture,
            scratch,
            &mut next_hoa,
        )
        .map_err(|mut e| {
            let local = e.bit_offset;
            e.bit_offset += start;
            e.message = format!("embedded preroll at local bit {local}: {}", e.message);
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
        };
        let supported = read_element(&mut parser, context, &mut element, scratch, common_window)
            .map_err(|e| element_error(e, configuration.element_index))?;
        result.elements.push(element);
        if !supported {
            return finish(
                result,
                parser,
                &format!("lrvq_element_{}_deferred", configuration.element_index),
            );
        }
        let element = result.elements.last_mut().expect("recorded element");
        extensions(&mut parser, element, scratch)
            .map_err(|e| element_error(e, element.configuration.element_index))?;
        if !capture {
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
    if let Some(shape) = context.hoa {
        let state = next_hoa.as_mut().expect("HOA state");
        let mut spatial = super::hoa::spatial(
            &mut parser,
            state,
            shape,
            common_window.expect("HOA window"),
        )?;
        let restored = if let Some(data) = &mut spatial.salient {
            // Set the current packet identity before recording the history source.
            result.frame.packet_sha256 = parser.report.packet_sha256.clone();
            super::hoa_salient::restore(
                &result,
                data,
                state.salient.as_mut().expect("salient state"),
            )?
        } else {
            super::hoa::restore(&result)?
        };
        let hoa = result.hoa.as_mut().expect("HOA context");
        hoa.spatial = Some(spatial);
        hoa.channels_after_hoa = restored;
        hoa.hoa_complete = true;
    }
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("core.alignment_padding", padding)? != 0 {
        return finish(result, parser, "nonzero core alignment is unsupported");
    }
    let core_end = parser.bits.position();
    parser.report.component_end_bit_offset = Some(core_end);
    let mut scene_update = None;
    if context.configuration.scene_present {
        let present = parser.flag("ancillary.audio_scenes_update_present")?;
        scene_update = Some(present);
        if present {
            let (scene, end) = config::parse_scene_at(packet, parser.bits.position())?;
            parser.bits.skip(end - parser.bits.position())?;
            if capture {
                parser.report.fields.extend(scene.fields.iter().cloned());
            }
            if !scene.is_complete() {
                return finish(result, parser, "unsupported audio scene update");
            }
            let rejected =
                packet_config::neutral_scene(&scene.fields, "packet", context.drc.present);
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
        result.drc_history_sufficient = Some(next.previous_nodes.iter().any(|n| n.time < 1024));
        let payload = drc::read_payload(&mut parser, &mut next, context.sample_rate_hz)?;
        if capture {
            next.previous_nodes = payload.nodes.clone();
            result.drc = Some(payload);
        } else {
            next.previous_nodes = payload.nodes;
        }
        result.drc_complete = Some(true);
    }
    if parser.flag("ancillary.trimming_present")? {
        return finish(result, parser, "nonzero ancillary trimming is unsupported");
    }
    let ancillary_end = parser.bits.position();
    if context.drc.present
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
        trimming_present: false,
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
