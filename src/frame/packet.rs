//! Complete, bounded ASP packets for the qualified stereo DRC-off route.
use super::{
    Bwe2Report, FrameContext, FrameReport, Parser, UnparsedRange, packet_config, parse_bwe2,
};
use crate::config::{self, Diagnostic, ParseError, ParseStatus, bits::BitReader};
use serde::{Deserialize, Serialize};

pub const STATE_PROFILE: &str = "apac-asp-state-v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PacketTail {
    pub core_end_bit_offset: usize,
    pub ancillary_start_bit_offset: usize,
    pub scene_update_present: Option<bool>,
    pub neutral_scene_restatement: bool,
    pub trimming_present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trimming: Option<super::TrimmingDeclaration>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub custom_data: Option<super::AuxiliaryPayload>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene_graph: Option<super::SceneGraphPayload>,
    pub ancillary_end_bit_offset: usize,
    pub packet_end_bit_offset: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddedPreroll {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    /// Nested report coordinates are local to this explicitly bounded frame.
    pub report: Box<PacketReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PacketReport {
    #[serde(flatten)]
    pub bwe2: Bwe2Report,
    pub packet_complete: bool,
    pub packet_state_profile: String,
    pub packet_tail: Option<PacketTail>,
    pub embedded_preroll: Option<EmbeddedPreroll>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drc: Option<super::drc::DrcPayload>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drc_complete: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drc_history_sufficient: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drc_processing_applied: Option<bool>,
}
impl PacketReport {
    pub fn frame(&self) -> &FrameReport {
        &self.bwe2.tns.cac.spectrum.frame
    }
    fn frame_mut(&mut self) -> &mut FrameReport {
        &mut self.bwe2.tns.cac.spectrum.frame
    }
    pub fn cpe_absent(&self) -> bool {
        self.frame().fields.iter().any(|f| {
            f.name == "components[0].tce[0].present" && f.value == serde_json::json!(false)
        })
    }
}

fn partial(
    mut result: PacketReport,
    parser: Parser<'_>,
    reason: &str,
) -> Result<PacketReport, ParseError> {
    let prefix_complete = result.frame().prefix_complete;
    *result.frame_mut() = parser.finish(reason, prefix_complete, false)?;
    result.frame_mut().fields.sort_by_key(|f| f.bit_offset);
    result
        .frame_mut()
        .unknown_ranges
        .sort_by_key(|r| r.bit_offset);
    Ok(result)
}

/// Reaching complete here proves the declared packet syntax, not support for
/// other configurations or arbitrary stateful media tools. Older depths retain
/// their original boundaries, status and CPE-absence behavior.
pub fn parse_packet(context: &FrameContext, packet: &[u8]) -> Result<PacketReport, ParseError> {
    parse_packet_with_state(context, packet, &mut super::DrcState::new(context))
}

pub(crate) fn parse_packet_with_state(
    context: &FrameContext,
    packet: &[u8],
    state: &mut super::DrcState,
) -> Result<PacketReport, ParseError> {
    let mut next_state = state.clone();
    let mut result = PacketReport {
        bwe2: parse_bwe2(context, packet)?,
        packet_complete: false,
        packet_state_profile: STATE_PROFILE.into(),
        packet_tail: None,
        embedded_preroll: None,
        drc: None,
        drc_complete: context.drc.present.then_some(false),
        drc_history_sufficient: None,
        drc_processing_applied: context.drc.present.then_some(false),
    };
    if let Some(reason) = context.packet_rejection() {
        let frame = result.frame_mut();
        frame.status = ParseStatus::Unsupported;
        frame.stop_reason = format!("unsupported packet configuration: {reason}");
        frame.diagnostics.push(Diagnostic {
            bit_offset: frame.stop_bit_offset,
            message: frame.stop_reason.clone(),
        });
        return Ok(result);
    }
    if !result.bwe2.bwe2_complete && !result.cpe_absent() {
        return Ok(result);
    }
    let position = result.frame().stop_bit_offset;
    result.frame_mut().diagnostics.clear();
    // Replace only the current core's opaque tail. An embedded frame has a
    // distinct explicit range, and cannot be declared parsed by skipping it.
    result
        .frame_mut()
        .unknown_ranges
        .retain(|r| !(r.bit_offset == position && r.bit_length == packet.len() * 8 - position));
    let embedded_range = result
        .frame()
        .derived
        .get("asp.preroll.start_bit")
        .and_then(|v| v.as_u64())
        .zip(
            result
                .frame()
                .derived
                .get("asp.preroll.end_bit")
                .and_then(|v| v.as_u64()),
        );
    if let Some((start, end)) = embedded_range {
        let (start, end) = (start as usize, end as usize);
        let nested = parse_packet_with_state(context, &packet[start / 8..end / 8], &mut next_state)
            .map_err(|mut error| {
                error.message = format!(
                    "embedded preroll at relative bit {}: {}",
                    error.bit_offset, error.message
                );
                error.bit_offset += start;
                error
            })?;
        let frame = result.frame_mut();
        frame
            .unknown_ranges
            .retain(|r| r.bit_offset < start || r.bit_offset >= end);
        for field in &nested.frame().fields {
            if field.name == "frame.type_code" {
                continue;
            } // Already recorded by the outer ASP grammar.
            let mut field = field.clone();
            field.name = format!("asp.preroll.{}", field.name);
            field.bit_offset += start;
            frame.fields.push(field);
        }
        for range in &nested.frame().unknown_ranges {
            frame.unknown_ranges.push(UnparsedRange {
                bit_offset: start + range.bit_offset,
                bit_length: range.bit_length,
                reason: format!("embedded preroll: {}", range.reason),
            });
        }
        for diagnostic in &nested.frame().diagnostics {
            frame.diagnostics.push(Diagnostic {
                bit_offset: start + diagnostic.bit_offset,
                message: format!("embedded preroll: {}", diagnostic.message),
            });
        }
        result.embedded_preroll = Some(EmbeddedPreroll {
            start_bit_offset: start,
            end_bit_offset: end,
            report: Box::new(nested),
        });
    }
    let mut parser = Parser {
        capture: true,
        bits: BitReader::new(packet),
        report: result.frame().clone(),
    };
    parser.bits.skip(position)?;
    // Absent CPEs consume no SQ/CAC/TNS/BWE2 bits. The native LBR core branches
    // directly from the presence bit to its (empty here) extension list/alignment.
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("core.alignment_padding", padding)? != 0 {
        return partial(result, parser, "nonzero core alignment is unsupported");
    }
    let core_end = parser.bits.position();
    parser.report.component_end_bit_offset = Some(core_end);
    let mut scene_update = None;
    if context.packet_configuration.scene_present {
        let present = parser.flag("ancillary.audio_scenes_update_present")?;
        scene_update = Some(present);
        if present {
            let (scene, end) = config::parse_scene_at(packet, parser.bits.position())?;
            parser.bits.skip(end - parser.bits.position())?;
            parser.report.fields.extend(scene.fields.iter().cloned());
            if !scene.is_complete() {
                let reason = scene
                    .diagnostics
                    .first()
                    .map_or("unsupported audio scene update", |d| d.message.as_str());
                return partial(result, parser, reason);
            }
            let rejected =
                packet_config::neutral_scene(&scene.fields, "packet", context.drc.present);
            if !rejected.is_empty() {
                return partial(
                    result,
                    parser,
                    &format!("non-neutral audio scene update: {}", rejected.join("; ")),
                );
            }
        }
    }
    if context.drc.present {
        let payload = super::drc::read_payload(
            &mut parser,
            &mut next_state,
            context.sample_rate_hz.unwrap_or(0),
        )?;
        result.drc_history_sufficient = Some(next_state.history_sufficient());
        next_state.advance(&payload);
        result.drc = Some(payload);
        result.drc_complete = Some(true);
    }
    let trimming = super::auxiliary::read_trimming(&mut parser)?;
    let ancillary_end = parser.bits.position();
    // The APAC ancillary writer emits a zero custom-data presence flag even
    // when that tool is disabled in the cookie; the decoder then returns before
    // reading it. Usually it fits in byte padding. DRC can leave trimming exactly
    // byte-aligned, exposing this flag in one additional padded byte. Accept
    // only that verified zero form, keeping the native ancillary endpoint.
    if context.drc.present
        && parser.bits.remaining() != 0
        && parser.flag("packet.disabled_custom_data_flag")?
    {
        return partial(
            result,
            parser,
            "nonzero disabled custom-data flag is unsupported",
        );
    }
    let padding = (8 - parser.bits.position() % 8) % 8;
    if padding != 0 && parser.take("packet.alignment_padding", padding)? != 0 {
        return partial(result, parser, "nonzero packet alignment is unsupported");
    }
    if parser.bits.remaining() != 0 {
        return partial(result, parser, "unparsed trailing bytes");
    }
    result.packet_tail = Some(PacketTail {
        core_end_bit_offset: core_end,
        ancillary_start_bit_offset: core_end,
        scene_update_present: scene_update,
        neutral_scene_restatement: scene_update == Some(true),
        trimming_present: trimming.is_some(),
        trimming,
        custom_data: None,
        scene_graph: None,
        ancillary_end_bit_offset: ancillary_end,
        packet_end_bit_offset: parser.bits.position(),
    });
    result.packet_complete = result
        .embedded_preroll
        .as_ref()
        .is_none_or(|p| p.report.packet_complete);
    parser.report.status = if result.packet_complete {
        ParseStatus::Complete
    } else {
        ParseStatus::Partial
    };
    parser.report.stop_reason = if result.packet_complete {
        "packet_complete"
    } else {
        "embedded_preroll_incomplete"
    }
    .into();
    parser.report.stop_bit_offset = parser.bits.position();
    parser.report.fields.sort_by_key(|f| f.bit_offset);
    parser.report.unknown_ranges.sort_by_key(|r| r.bit_offset);
    *result.frame_mut() = parser.report;
    if result.packet_complete {
        *state = next_state;
    }
    Ok(result)
}
