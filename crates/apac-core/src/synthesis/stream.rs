//! Atomic multi-component synthesis using the unchanged per-output transforms.
use super::{ChannelState, FrameInfo};
use crate::prelude::*;
use crate::{
    error::{DecodeError, Result},
    frame::{
        DrcState, ScanWorkspace, StreamFrameContext, StreamPacketReport,
        stream::{self, StreamState},
    },
};
/// Parse one packet against copies of the DRC and component state; nothing
/// is committed.
pub(super) fn parse(
    context: &StreamFrameContext,
    drc: &DrcState,
    state: &StreamState,
    packet: &[u8],
) -> Result<(StreamPacketReport, DrcState, StreamState)> {
    let mut next_drc = drc.clone();
    let mut next_state = state.clone();
    let report = stream::parse_with_state(
        context,
        packet,
        &mut next_drc,
        &mut next_state,
        true,
        &mut ScanWorkspace::default(),
    )
    .map_err(|e| {
        let mut error = DecodeError::new("SQ stream", e.to_string());
        error.bit_offset = Some(e.bit_offset);
        error
    })?;
    if !report.packet_complete {
        let mut error = DecodeError::new(
            "SQ decoder",
            format!("unsupported frame: {}", report.frame.stop_reason),
        );
        error.bit_offset = Some(report.frame.stop_bit_offset);
        return Err(error);
    }
    Ok((report, next_drc, next_state))
}
pub(super) fn render(
    states: &mut [ChannelState],
    packet: &StreamPacketReport,
) -> Result<(Vec<f32>, FrameInfo)> {
    let mut counts = FrameInfo::default();
    if let Some(child) = &packet.embedded_preroll {
        let (_, child) = render(states, &child.report)?;
        counts.embedded_preroll_frames = 1 + child.embedded_preroll_frames;
        counts.embedded_absent_elements = child.absent_elements + child.embedded_absent_elements;
        counts.embedded_cpe_absent = u64::from(child.cpe_absent) + child.embedded_cpe_absent;
        counts.drc_payload_frames = child.drc_payload_frames;
        counts.drc_missing_history_frames = child.drc_missing_history_frames;
    }
    counts.drc_payload_frames += u64::from(packet.drc_complete == Some(true));
    counts.drc_missing_history_frames += u64::from(packet.drc_history_sufficient == Some(false));
    let channels = packet.channel_count as usize;
    let mut output = vec![0.; 1024 * channels];
    let mut used = vec![false; channels];
    let mut source = 0;
    for component in &packet.components {
        let source_channels = component.configuration.source_channels;
        if source + source_channels > states.len() {
            return Err(DecodeError::new(
                "SQ stream",
                "invalid component synthesis range",
            ));
        }
        let (pcm, part) = super::channels::render_core(
            &mut states[source..source + source_channels],
            &component.elements,
            component.hoa.as_ref(),
        )?;
        source += source_channels;
        counts.absent_elements += part.absent_elements;
        counts.cpe_absent |= part.cpe_absent;
        for range in &component.configuration.output_ranges {
            let (first, end) = (range.output_start, range.output_start + range.channels);
            if end > channels
                || range.source_start + range.channels > source_channels
                || used[first..end].iter().any(|&v| v)
            {
                return Err(DecodeError::new(
                    "SQ stream",
                    "invalid component output interval",
                ));
            }
            used[first..end].fill(true);
            for frame in 0..1024 {
                let input = frame * source_channels + range.source_start;
                output[frame * channels + first..frame * channels + end]
                    .copy_from_slice(&pcm[input..input + range.channels]);
            }
        }
    }
    if used.iter().any(|&b| !b) || source != states.len() {
        return Err(DecodeError::new("SQ stream", "uncovered component output"));
    }
    Ok((output, counts))
}
