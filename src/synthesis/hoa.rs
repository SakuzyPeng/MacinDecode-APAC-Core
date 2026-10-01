//! HOA and all sixteen overlaps commit together only after the complete packet.
use super::{ChannelState, FrameStateCounts, channels};
use crate::{
    error::{Error, Result},
    frame::{DrcState, HoaFrameContext, HoaState, parse_hoa_packet_with_state},
};
pub(super) const BACKEND: &str = "rust_hoa_ambient_sq_drc_off_f64_fft_v1";
pub(super) const SALIENT_BACKEND: &str = "rust_hoa_salient_sq_drc_off_f64_fft_v1";
pub(super) const MIXED_BACKEND: &str = "rust_hoa_mixed_sq_drc_off_f64_fft_v1";
pub(super) const STATIC_AMBIENT_BACKEND: &str = "rust_hoa_static_ambient_sq_drc_off_f64_fft_v1";
pub(super) const ADDITIVE_BACKEND: &str = "rust_hoa_additive_sq_drc_off_f64_fft_v1";
pub(super) const DYNAMIC_BACKEND: &str = "rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v1";
pub(super) fn decode(
    context: &HoaFrameContext,
    drc: &mut DrcState,
    hoa: &mut HoaState,
    states: &mut Vec<ChannelState>,
    packet: &[u8],
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let parse_timer = std::time::Instant::now();
    let mut next_drc = drc.clone();
    let mut next_hoa = hoa.clone();
    let report = parse_hoa_packet_with_state(context, packet, &mut next_drc, &mut next_hoa)
        .map_err(|e| {
            let mut error = Error::new("HOA packet", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
    if !report.packet.packet_complete || !report.hoa().hoa_complete {
        let mut error = Error::new(
            "HOA decoder",
            format!("unsupported frame: {}", report.packet.frame.stop_reason),
        );
        error.bit_offset = Some(report.packet.frame.stop_bit_offset);
        return Err(error);
    }
    let parse_seconds = parse_timer.elapsed().as_secs_f64();
    let timer = std::time::Instant::now();
    let mut next = states.clone();
    let mut result = channels::render(&mut next, &report.packet)?;
    result.1.parse_seconds = parse_seconds;
    result.1.synthesis_seconds = timer.elapsed().as_secs_f64();
    *states = next;
    *drc = next_drc;
    *hoa = next_hoa;
    Ok(result)
}
