//! Atomic multi-component synthesis using the unchanged per-output transforms.
use super::{ChannelState, FrameStateCounts};
use crate::prelude::*;
use crate::{
    error::{Error, Result},
    frame::{
        DrcState, ScanWorkspace, StreamFrameContext, StreamPacketReport,
        stream::{self, StreamState},
    },
};
pub(super) const BACKEND: &str = "rust_hoa_multiple_asc_sq_drc_off_f64_fft_v1";
pub(super) fn decode(
    context: &StreamFrameContext,
    drc: &mut DrcState,
    state: &mut StreamState,
    channels: &mut Vec<ChannelState>,
    packet: &[u8],
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let timer = std::time::Instant::now();
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
        let mut error = Error::new("SQ stream", e.to_string());
        error.bit_offset = Some(e.bit_offset);
        error
    })?;
    if !report.packet_complete {
        let mut error = Error::new(
            "SQ decoder",
            format!("unsupported frame: {}", report.frame.stop_reason),
        );
        error.bit_offset = Some(report.frame.stop_bit_offset);
        return Err(error);
    }
    let parse_seconds = timer.elapsed().as_secs_f64();
    let timer = std::time::Instant::now();
    let mut next = channels.clone();
    let mut result = render(&mut next, &report)?;
    result.1.parse_seconds = parse_seconds;
    result.1.synthesis_seconds = timer.elapsed().as_secs_f64();
    *drc = next_drc;
    *state = next_state;
    *channels = next;
    Ok(result)
}
fn render(
    states: &mut [ChannelState],
    packet: &StreamPacketReport,
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let mut counts = FrameStateCounts::default();
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
            return Err(Error::new("SQ stream", "invalid component synthesis range"));
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
                return Err(Error::new("SQ stream", "invalid component output interval"));
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
        return Err(Error::new("SQ stream", "uncovered component output"));
    }
    Ok((output, counts))
}
