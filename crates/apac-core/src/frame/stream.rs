//! Multiple coded ASCs, independent core states, and one outer packet transaction.
use super::{
    ChannelFrameContext, ElementReport, FrameReport, HoaFrameContext, HoaFrameInfo, HoaState,
    PacketTail, Parser, UnparsedRange,
    channels::{self, ScanWorkspace},
    drc::{self, DrcContext, DrcState},
    packet_config,
};
use crate::prelude::*;
use crate::{
    config::{self, FieldExt, ParseError, ParseStatus, bits::BitReader},
    model::{ChannelLayout, SCHEMA_VERSION, sha256},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const PROFILE: &str = "apac-hoa-multiple-asc-v1";
pub const STATE_PROFILE: &str = "apac-hoa-multiple-asc-state-v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamOutputRange {
    pub source_start: usize,
    pub output_start: usize,
    pub channels: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamComponentConfiguration {
    pub component_index: usize,
    pub component_type: u8,
    pub declared_lowest_channel_index: u8,
    pub output_start: usize,
    pub output_channels: usize,
    pub output_ranges: Vec<StreamOutputRange>,
    /// Coded source dimension; a bounded output route can omit a whole component.
    pub source_channels: usize,
    pub layout: ChannelLayout,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hoa_coefficient_count: Option<usize>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub declaration_aliases: Vec<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub alias_of: Option<usize>,
    pub parameter_0: u64,
    pub parameter_1: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdditionalComponentConfiguration {
    pub component_index: usize,
    pub component_type: u8,
    pub lowest_channel_index: u8,
    pub channels: usize,
    pub parameter_0: u64,
    pub parameter_1: u64,
}
#[derive(Debug, Clone)]
struct Component {
    information: StreamComponentConfiguration,
    core: ChannelFrameContext,
    hoa: Option<Box<HoaFrameContext>>,
}
#[derive(Debug, Clone)]
pub struct StreamFrameContext {
    cookie_sha256: String,
    sample_rate_hz: u64,
    channels: u32,
    layout: ChannelLayout,
    components: Vec<Component>,
    information: Vec<StreamComponentConfiguration>,
    additional: Vec<AdditionalComponentConfiguration>,
    drc: DrcContext,
    auxiliary: super::auxiliary::AuxiliaryConfiguration,
    scene_present: bool,
    scene_sources: usize,
    rejection: Option<String>,
}

impl StreamFrameContext {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self, ParseError> {
        Self::from_config(&config::Config::parse(cookie)?)
    }
    pub fn from_config(config: &config::Config) -> Result<Self, ParseError> {
        let global = &config.global;
        let channels = global.channels.unwrap_or(0).min(255) as u32;
        let rate = global.sample_rate_hz.unwrap_or(48000);
        let count = global.component_count.get().unwrap_or(0) as usize;
        let mut rejected = Vec::new();
        if !config.is_complete() {
            rejected.push("multiple-ASC cookie syntax is incomplete".into());
        }
        if super::sfb::index(rate).is_none() {
            rejected.push("unsupported shared sample rate".into());
        }
        for (name, field, value) in [
            ("box.version_flags", config.version_flags, 0),
            ("bitstream_version", config.bitstream_version, 0x800),
            ("global.frame_size_index", global.frame_size_index, 0),
            (
                "global.parameter_b",
                global.parameter_b,
                global.parameter_b.get().unwrap_or(2).min(2),
            ),
        ] {
            packet_config::check(name, field, value, "cookie", &mut rejected);
        }
        packet_config::check(
            "global.flag_c",
            global.flag_c,
            false,
            "cookie",
            &mut rejected,
        );
        let additional = global.additional_asc_present.is(true);
        let additional_information: Vec<AdditionalComponentConfiguration> = (0..global
            .additional_component_count
            .get()
            .unwrap_or(0)
            .min(255)
            as usize)
            .map(|i| {
                let declared = config.additional_components.get(i);
                let uint = |read: fn(&config::AdditionalComponent) -> config::Field<u64>| {
                    declared.and_then(read).get().unwrap_or(0)
                };
                AdditionalComponentConfiguration {
                    component_index: i,
                    component_type: uint(|c| c.kind) as u8,
                    lowest_channel_index: uint(|c| c.lowest_channel_index) as u8,
                    channels: declared.and_then(|c| c.channels).unwrap_or(0) as usize,
                    parameter_0: uint(|c| c.parameter_0),
                    parameter_1: uint(|c| c.parameter_1),
                }
            })
            .collect();
        if count == 0 || (count < 2 && !additional) {
            rejected.push("stream context requires multiple or additional components".into());
        }
        let profile = global.profile_id.get().unwrap_or(255) as u8;
        let level = global.level_id.get().unwrap_or(255) as u8;
        let drc = DrcContext::for_channels(config, u64::from(channels));
        if let Some(reason) = &drc.rejection {
            rejected.push(reason.clone());
        }
        let scene = config.ancillary.audio_scenes_present.is(true);
        let mut components = Vec::new();
        let mut hoa_found = false;
        for index in 0..count {
            let declared = config.component(index);
            let kind = declared.kind.get().unwrap_or(255) as u8;
            let n = declared.channels.unwrap_or(0);
            let tag = declared.layout_tag.unwrap_or(0) as u32;
            if !matches!(kind, 0 | 2) || n == 0 || n > 121 {
                rejected.push(format!("unqualified component {index} type/dimension"));
                continue;
            }
            if !super::sfb::supports_component(profile, level, kind, n, tag) {
                rejected.push(format!("profile/level does not admit component {index}"));
            }
            let view = config.component_view(index, kind, n);
            let (mut core, coefficients, hoa_context) = if kind == 2 {
                let hoa = HoaFrameContext::from_config(&view);
                hoa_found = true;
                (
                    hoa.transport.clone(),
                    Some(hoa.recovery_slot_count()),
                    Some(Box::new(hoa)),
                )
            } else {
                (ChannelFrameContext::from_config(&view), None, None)
            };
            core.sample_rate_hz = rate;
            if let Some(reason) = core.rejection() {
                rejected.push(format!("component {index}: {reason}"));
            }
            let layout = core
                .channel_layout()
                .cloned()
                .unwrap_or_else(|| ChannelLayout::tagged((147 << 16) | n as u32, n as u32, None));
            let information = StreamComponentConfiguration {
                component_index: index,
                component_type: kind,
                declared_lowest_channel_index: declared.lowest_channel_index.get().unwrap_or(0)
                    as u8,
                output_start: 0,
                output_channels: 0,
                output_ranges: vec![],
                source_channels: n as usize,
                layout,
                hoa_coefficient_count: coefficients,
                declaration_aliases: (0..count)
                    .filter(|&i| {
                        config.component(i).effective_component_index == Some(index as u64)
                    })
                    .collect(),
                alias_of: declared.effective_component_index.map(|i| i as usize),
                parameter_0: declared.parameter_0.get().unwrap_or(0),
                parameter_1: declared.parameter_1.get().unwrap_or(0),
            };
            components.push(Component {
                information,
                core,
                hoa: hoa_context,
            });
        }
        if !hoa_found {
            rejected.push("stream requires at least one HOA component".into());
        }
        let source_channels: usize = components
            .iter()
            .map(|c| c.information.source_channels)
            .sum();
        if source_channels > 8192 {
            return Err(ParseError::new(
                0,
                "input-limit",
                "coded stream exceeds the 8192-channel working-state limit",
            ));
        }
        let scene_sources = if additional {
            additional_information.len()
        } else {
            components.len()
        };
        let mut mapping = vec![None; channels as usize];
        if scene {
            rejected.extend(packet_config::neutral_scene_sources(
                &config.ancillary.audio_scenes,
                "cookie",
                scene_sources,
            ));
            // Scene source chunks follow the active descriptor list. Scatter
            // those chunks to their declared ranges; later aliases replace an
            // earlier range. This does not change core body or synthesis order.
            let spans: Vec<(usize, usize)> = if additional {
                additional_information
                    .iter()
                    .map(|c| (usize::from(c.lowest_channel_index), c.channels))
                    .collect()
            } else {
                components
                    .iter()
                    .map(|c| {
                        (
                            usize::from(c.information.declared_lowest_channel_index),
                            c.information.source_channels,
                        )
                    })
                    .collect()
            };
            let mut source = 0usize;
            for (target, length) in spans {
                if source + length > source_channels || target + length > mapping.len() {
                    rejected.push(
                        "scene descriptor is outside the neutral full-source routing bounds".into(),
                    );
                    break;
                }
                for (i, slot) in mapping[target..target + length].iter_mut().enumerate() {
                    *slot = Some(source + i);
                }
                source += length;
            }
        } else {
            let (mut source, mut target) = (0, 0);
            for component in &components {
                let n = component.information.source_channels;
                if target + n <= mapping.len() {
                    for (i, slot) in mapping[target..target + n].iter_mut().enumerate() {
                        *slot = Some(source + i);
                    }
                    target += n;
                }
                source += n;
            }
        }
        if mapping.iter().any(Option::is_none) {
            rejected.push("source routes do not completely cover the output".into());
        }
        let mut source = 0usize;
        let source_descriptions: Vec<_> = components
            .iter()
            .flat_map(|c| {
                super::hoa_source::descriptions(
                    &c.information.layout,
                    c.information.source_channels as u32,
                )
            })
            .collect();
        for component in &mut components {
            let information = &mut component.information;
            let mut ranges: Vec<StreamOutputRange> = vec![];
            for local in 0..information.source_channels {
                if let Some(target) = mapping.iter().position(|&v| v == Some(source + local)) {
                    if let Some(last) = ranges.last_mut()
                        && last.source_start + last.channels == local
                        && last.output_start + last.channels == target
                    {
                        last.channels += 1;
                    } else {
                        ranges.push(StreamOutputRange {
                            source_start: local,
                            output_start: target,
                            channels: 1,
                        });
                    }
                }
            }
            source += information.source_channels;
            information.output_start = ranges.first().map_or(0, |r| r.output_start);
            information.output_channels = ranges.iter().map(|r| r.channels).sum();
            information.output_ranges = ranges;
        }
        let mut layout = ChannelLayout::tagged(0, channels, None);
        layout.descriptions = mapping
            .iter()
            .filter_map(|&i| i.and_then(|i| source_descriptions.get(i).cloned()))
            .collect();
        let routed: Vec<_> = components
            .iter()
            .filter(|c| c.information.output_channels != 0)
            .collect();
        if let [component] = routed.as_slice()
            && let [range] = component.information.output_ranges.as_slice()
            && range.source_start == 0
            && range.output_start == 0
            && range.channels == component.information.source_channels
            && range.channels == channels as usize
        {
            layout = component.information.layout.clone();
        }
        let information = components.iter().map(|c| c.information.clone()).collect();
        Ok(Self {
            auxiliary: super::auxiliary::AuxiliaryConfiguration::from_config(config),
            cookie_sha256: config.cookie_sha256.clone(),
            sample_rate_hz: rate,
            channels,
            layout,
            components,
            information,
            additional: additional_information,
            drc,
            scene_present: scene,
            scene_sources,
            rejection: (!rejected.is_empty()).then(|| rejected.join("; ")),
        })
    }
    pub fn is_supported(&self) -> bool {
        self.rejection.is_none()
    }
    pub fn rejection(&self) -> Option<&str> {
        self.rejection.as_deref()
    }
    pub fn components(&self) -> &[StreamComponentConfiguration] {
        &self.information
    }
    pub fn additional_components(&self) -> &[AdditionalComponentConfiguration] {
        &self.additional
    }
    /// Cookie configuration and actual HOA dimensions for a declared component.
    pub fn hoa_component(&self, index: usize) -> Option<&HoaFrameContext> {
        self.components.get(index).and_then(|c| c.hoa.as_deref())
    }
    pub fn channel_count(&self) -> u32 {
        self.channels
    }
    pub fn sample_rate_hz(&self) -> u64 {
        self.sample_rate_hz
    }
    pub fn channel_layout(&self) -> &ChannelLayout {
        &self.layout
    }
    pub fn initial_state(&self) -> StreamState {
        StreamState {
            hoa: self
                .components
                .iter()
                .map(|c| c.hoa.as_ref().map(|_| HoaState::default()))
                .collect(),
        }
    }
    pub fn initial_drc_state(&self) -> DrcState {
        DrcState {
            channels: u64::from(self.channels),
            configuration: self.drc.configuration.clone(),
            previous_nodes: vec![],
            previous_sequences: vec![],
            shared_syntax_used: false,
            scene_graph: None,
        }
    }
    pub(crate) fn first_core(&self) -> &ChannelFrameContext {
        &self.components[0].core
    }
    pub(crate) fn synthesis_channel_count(&self) -> usize {
        self.components
            .iter()
            .map(|c| c.information.source_channels)
            .sum()
    }
}
#[derive(Debug, Clone, Serialize, Default)]
pub struct StreamState {
    hoa: Vec<Option<HoaState>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamComponentReport {
    pub configuration: StreamComponentConfiguration,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub core_complete: bool,
    pub elements: Vec<ElementReport>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hoa: Option<HoaFrameInfo>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamPreroll {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub report: Box<StreamPacketReport>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamPacketReport {
    #[serde(flatten)]
    pub frame: FrameReport,
    pub packet_complete: bool,
    pub packet_state_profile: String,
    pub channel_count: u32,
    pub components: Vec<StreamComponentReport>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub additional_components: Vec<AdditionalComponentConfiguration>,
    pub embedded_preroll: Option<StreamPreroll>,
    pub packet_tail: Option<PacketTail>,
    pub drc: Option<super::DrcPayload>,
    pub drc_complete: Option<bool>,
    pub drc_history_sufficient: Option<bool>,
    pub drc_processing_applied: bool,
}

pub fn parse_stream_packet(
    context: &StreamFrameContext,
    packet: &[u8],
) -> Result<StreamPacketReport, ParseError> {
    parse_with_state(
        context,
        packet,
        &mut context.initial_drc_state(),
        &mut context.initial_state(),
        true,
        &mut ScanWorkspace::default(),
    )
}
pub fn parse_with_state(
    context: &StreamFrameContext,
    packet: &[u8],
    drc_state: &mut DrcState,
    state: &mut StreamState,
    capture: bool,
    scratch: &mut ScanWorkspace,
) -> Result<StreamPacketReport, ParseError> {
    if packet.is_empty() || packet.len() > super::MAX_PACKET_BUFFER {
        return Err(ParseError::new(
            0,
            "input-limit",
            "packet must contain 1..16 MiB",
        ));
    }
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
    let mut report = StreamPacketReport {
        frame,
        packet_complete: false,
        packet_state_profile: STATE_PROFILE.into(),
        channel_count: context.channels,
        components: vec![],
        additional_components: context.additional.clone(),
        embedded_preroll: None,
        packet_tail: None,
        drc: None,
        drc_complete: context.drc.present.then_some(false),
        drc_history_sufficient: None,
        drc_processing_applied: false,
    };
    let code = parser.take("frame.type_code", 2)?;
    if let Some(reason) = context.rejection() {
        parser.report.status = ParseStatus::Unsupported;
        return finish(report, parser, reason);
    }
    if let Some(reason) = parser.asp_bounded(code, u64::from(context.channels) * 2048)? {
        return finish(report, parser, reason);
    }
    let mut next = state.clone();
    let mut next_drc = drc_state.clone();
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
        let child = parse_with_state(
            context,
            &packet[start / 8..end / 8],
            &mut next_drc,
            &mut next,
            capture,
            scratch,
        )
        .map_err(|mut e| {
            e.bit_offset += start;
            e.message = format!("embedded preroll: {}", e.message);
            e
        })?;
        parser
            .report
            .unknown_ranges
            .retain(|r| r.bit_offset < start || r.bit_offset >= end);
        for f in &child.frame.fields {
            if f.name != "frame.type_code" {
                let mut f = f.clone();
                f.name = format!("asp.preroll.{}", f.name);
                f.bit_offset += start;
                parser.report.fields.push(f);
            }
        }
        let complete = child.packet_complete;
        report.embedded_preroll = Some(StreamPreroll {
            start_bit_offset: start,
            end_bit_offset: end,
            report: Box::new(child),
        });
        if !complete {
            return finish(report, parser, "embedded_preroll_incomplete");
        }
    }
    for (index, component) in context.components.iter().enumerate() {
        let declared_index = component.information.component_index;
        let start = parser.bits.position();
        let core = channels::parse_core_at(
            &component.core,
            packet,
            start,
            code,
            &mut next.hoa[index],
            capture,
            scratch,
        )
        .map_err(|mut error| {
            error.message = format!("component {declared_index}: {}", error.message);
            error
        })?;
        let end = core.frame.stop_bit_offset;
        parser.bits.skip(end - start)?;
        let rename = |name: &str| {
            if let Some(tail) = name.strip_prefix("components[0].") {
                format!("components[{declared_index}].{tail}")
            } else {
                format!("components[{declared_index}].{name}")
            }
        };
        for f in &core.frame.fields {
            let mut f = f.clone();
            f.name = rename(&f.name);
            parser.report.fields.push(f);
        }
        for (key, value) in &core.frame.derived {
            parser.report.derived.insert(rename(key), value.clone());
        }
        let complete = core.packet_complete;
        let reason = core.frame.stop_reason.clone();
        report.components.push(StreamComponentReport {
            configuration: component.information.clone(),
            start_bit_offset: start,
            end_bit_offset: end,
            core_complete: complete,
            elements: core.elements,
            hoa: core.hoa,
        });
        if !complete {
            return finish(
                report,
                parser,
                &format!("component {declared_index}: {reason}"),
            );
        }
    }
    let core_end = parser.bits.position();
    parser.report.component_end_bit_offset = Some(core_end);
    let scene_graph = super::auxiliary::read_graph(&mut parser, &context.auxiliary, &mut next_drc)?;
    let mut scene = None;
    if context.scene_present {
        let present = parser.flag("ancillary.audio_scenes_update_present")?;
        scene = Some(present);
        if present {
            let (update, end) = config::parse_scene_at(packet, parser.bits.position())?;
            parser.bits.skip(end - parser.bits.position())?;
            if capture {
                parser.report.fields.extend(update.fields.clone());
            }
            if !update.is_complete()
                || !packet_config::neutral_scene_sources(
                    &config::AudioScenes::from_fields(&update.fields),
                    "packet",
                    context.scene_sources,
                )
                .is_empty()
            {
                return finish(report, parser, "non-neutral audio scene update");
            }
        }
    }
    if context.drc.present {
        let data = drc::read_payload(&mut parser, &mut next_drc, context.sample_rate_hz)?;
        report.drc_history_sufficient = Some(next_drc.history_sufficient());
        next_drc.advance(&data);
        report.drc = Some(data);
        report.drc_complete = Some(true);
    }
    let trimming = super::auxiliary::read_trimming(&mut parser)?;
    let custom_data = super::auxiliary::read(&mut parser, &context.auxiliary)?;
    let ancillary_end = parser.bits.position();
    if context.auxiliary.present
        && parser.bits.remaining() != 0
        && parser.flag("packet.extension_terminator")?
    {
        return finish(
            report,
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
            report,
            parser,
            "nonzero disabled custom-data flag is unsupported",
        );
    }
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("packet.alignment_padding", padding)? != 0 {
        return finish(report, parser, "nonzero packet alignment is unsupported");
    }
    if parser.bits.remaining() != 0 {
        return finish(report, parser, "unparsed trailing bytes");
    }
    report.packet_tail = Some(PacketTail {
        core_end_bit_offset: core_end,
        ancillary_start_bit_offset: core_end,
        scene_update_present: scene,
        neutral_scene_restatement: scene == Some(true),
        trimming_present: trimming.is_some(),
        trimming,
        custom_data,
        scene_graph,
        ancillary_end_bit_offset: ancillary_end,
        packet_end_bit_offset: parser.bits.position(),
    });
    report.packet_complete = true;
    parser.report.status = ParseStatus::Complete;
    parser.report.prefix_complete = true;
    parser.report.stop_reason = "packet_complete".into();
    parser.report.stop_bit_offset = parser.bits.position();
    parser.report.fields.sort_by_key(|f| f.bit_offset);
    report.frame = parser.report;
    *state = next;
    *drc_state = next_drc;
    Ok(report)
}
fn finish(
    mut result: StreamPacketReport,
    mut parser: Parser<'_>,
    reason: &str,
) -> Result<StreamPacketReport, ParseError> {
    parser.report.stop_reason = reason.into();
    parser.report.stop_bit_offset = parser.bits.position();
    if parser.bits.remaining() != 0 {
        parser.report.unknown_ranges.push(UnparsedRange {
            bit_offset: parser.bits.position(),
            bit_length: parser.bits.remaining(),
            reason: reason.into(),
        });
    }
    result.frame = parser.report;
    Ok(result)
}
