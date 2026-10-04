//! HOA and all sixteen overlaps commit together only after the complete packet.
use crate::prelude::*;
pub(super) const EXPANDED_BACKEND: &str = "rust_hoa_expanded_orders_sq_drc_off_f64_fft_v1";
pub(super) const PARTIAL_BACKEND: &str = "rust_hoa_partial_domain_sq_drc_off_f64_fft_v1";
pub(super) const CONTROLS_BACKEND: &str = "rust_hoa_spatial_controls_sq_drc_off_f64_fft_v1";
pub(super) const FRAME_CONTROLS_BACKEND: &str = "rust_hoa_spatial_controls_sq_drc_off_f64_fft_v2";
pub(super) const TRANSPORT_BACKEND: &str = "rust_hoa_transports_sq_cac_tns_bwe2_drc_off_f64_fft_v1";
pub(super) const QUANTIZATION_BACKEND: &str = "rust_hoa_salient_quantization_sq_drc_off_f64_fft_v1";
pub(super) const AMBIENT_COUNTS_BACKEND: &str = "rust_hoa_ambient_counts_sq_drc_off_f64_fft_v1";
pub(super) const COUNTS_BACKEND: &str = "rust_hoa_salient_counts_sq_drc_off_f64_fft_v1";
use crate::{
    error::{DecodeError, Result},
    frame::{DrcState, HoaFrameContext, HoaPacketReport, HoaState, parse_hoa_packet_with_state},
};
pub(super) const COMPONENT_ORDERS_BACKEND: &str = "rust_hoa_component_orders_sq_drc_off_f64_fft_v1";
pub(super) const BACKEND: &str = "rust_hoa_ambient_sq_drc_off_f64_fft_v1";
pub(super) const SALIENT_BACKEND: &str = "rust_hoa_salient_sq_drc_off_f64_fft_v1";
pub(super) const MIXED_BACKEND: &str = "rust_hoa_mixed_sq_drc_off_f64_fft_v1";
pub(super) const STATIC_AMBIENT_BACKEND: &str = "rust_hoa_static_ambient_sq_drc_off_f64_fft_v1";
pub(super) const ADDITIVE_BACKEND: &str = "rust_hoa_additive_sq_drc_off_f64_fft_v1";
pub(super) const DYNAMIC_BACKEND: &str = "rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v1";
pub(super) const DYNAMIC_DOMAINS_BACKEND: &str = "rust_hoa_dynamic_domains_sq_drc_off_f64_fft_v1";
/// Parse one packet against copies of the DRC and HOA state; nothing is committed.
pub(super) fn parse(
    context: &HoaFrameContext,
    drc: &DrcState,
    hoa: &HoaState,
    packet: &[u8],
) -> Result<(HoaPacketReport, DrcState, HoaState)> {
    let mut next_drc = drc.clone();
    let mut next_hoa = hoa.clone();
    let report = parse_hoa_packet_with_state(context, packet, &mut next_drc, &mut next_hoa)
        .map_err(|e| {
            let mut error = DecodeError::new("HOA packet", e.to_string());
            error.bit_offset = Some(e.bit_offset);
            error
        })?;
    if !report.packet.packet_complete || !report.hoa().hoa_complete {
        let mut error = DecodeError::new(
            "HOA decoder",
            format!("unsupported frame: {}", report.packet.frame.stop_reason),
        );
        error.bit_offset = Some(report.packet.frame.stop_bit_offset);
        return Err(error);
    }
    Ok((report, next_drc, next_hoa))
}
