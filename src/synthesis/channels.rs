//! Atomic dynamic-channel synthesis; the existing per-channel math is unchanged.
use super::{ChannelState, FrameStateCounts};
use crate::{
    error::{Error, Result},
    frame::{ChannelFrameContext, ChannelPacketReport, DrcState, parse_channel_packet_with_state},
};
pub(super) const BACKEND: &str = "rust_channel_sq_cac_tns_bwe2_drc_off_f64_fft_v1";

pub(super) fn decode(
    context: &ChannelFrameContext,
    state: &mut DrcState,
    channels: &mut Vec<ChannelState>,
    packet: &[u8],
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let parse_timer = std::time::Instant::now();
    let mut next_state = state.clone();
    let decoded =
        parse_channel_packet_with_state(context, packet, &mut next_state).map_err(|e| {
            let mut error = Error::new("SQ channels", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
    if !decoded.packet_complete {
        let mut e = Error::new(
            "SQ decoder",
            format!("unsupported frame: {}", decoded.frame.stop_reason),
        );
        e.bit_offset = Some(decoded.frame.stop_bit_offset);
        return Err(e);
    }
    let parse_seconds = parse_timer.elapsed().as_secs_f64();
    let synthesis_timer = std::time::Instant::now();
    let mut next = channels.clone();
    let mut result = render(&mut next, &decoded)?;
    result.1.parse_seconds = parse_seconds;
    result.1.synthesis_seconds = synthesis_timer.elapsed().as_secs_f64();
    *channels = next;
    *state = next_state;
    Ok(result)
}
pub(super) fn render(
    states: &mut [ChannelState],
    packet: &ChannelPacketReport,
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let mut counts = FrameStateCounts::default();
    if let Some(inner) = &packet.embedded_preroll {
        let (_, child) = render(states, &inner.report)?;
        counts.embedded_preroll_frames = 1 + child.embedded_preroll_frames;
        counts.embedded_cpe_absent = u64::from(child.cpe_absent) + child.embedded_cpe_absent;
        counts.embedded_absent_elements = child.absent_elements + child.embedded_absent_elements;
        counts.drc_payload_frames = child.drc_payload_frames;
        counts.drc_missing_history_frames = child.drc_missing_history_frames;
    }
    counts.drc_payload_frames += u64::from(packet.drc_complete == Some(true));
    counts.drc_missing_history_frames += u64::from(packet.drc_history_sufficient == Some(false));
    if let Some(hoa) = &packet.hoa
        && hoa.spatial.as_ref().is_some_and(|s| s.salient.is_some())
    {
        counts.absent_elements += packet.elements.iter().filter(|e| !e.present).count() as u64;
        let channels = states.len();
        let mut output = vec![0.; 1024 * channels];
        for (index, state) in states.iter_mut().enumerate() {
            let samples = state.render(
                &hoa.channels_after_hoa[index].scaled,
                hoa.common_window.expect("HOA window"),
            )?;
            for (frame, value) in samples.into_iter().enumerate() {
                output[frame * channels + index] = value;
            }
        }
        return Ok((output, counts));
    }
    let mut output = vec![0f32; 1024 * states.len()];
    let mut occupied = vec![false; states.len()];
    for element in &packet.elements {
        counts.absent_elements += u64::from(!element.present);
        counts.cpe_absent |=
            element.configuration.kind == crate::frame::ElementKind::Cpe && !element.present;
        for (local, &global) in element.configuration.output_channels.iter().enumerate() {
            let index = usize::from(global);
            if index >= states.len() || std::mem::replace(&mut occupied[index], true) {
                return Err(Error::new("SQ channels", "invalid output channel map"));
            }
            let samples = if element.present {
                states[index].render(
                    if let Some(hoa) = &packet.hoa {
                        &hoa.channels_after_hoa[index].scaled
                    } else {
                        &element.channels_after_bwe2[local].scaled
                    },
                    element.channels[local].ics.block_type,
                )?
            } else {
                let samples: Vec<f32> = states[index]
                    .overlap
                    .iter()
                    .map(|&v| {
                        let v = v as f32;
                        if v == 0. { 0. } else { v }
                    })
                    .collect();
                if samples.iter().any(|v| !v.is_finite()) {
                    return Err(Error::new(
                        "SQ channels",
                        "nonfinite absent-element overlap",
                    ));
                }
                states[index].overlap.fill(0.);
                samples
            };
            for (i, value) in samples.into_iter().enumerate() {
                output[i * states.len() + index] = value;
            }
        }
    }
    if occupied.iter().any(|v| !*v) {
        return Err(Error::new("SQ channels", "incomplete output channel map"));
    }
    Ok((output, counts))
}
