//! HOA and all sixteen overlaps commit together only after the complete packet.
use crate::frame::ParseMode;
use crate::prelude::*;
use crate::{
    error::{DecodeError, Result},
    frame::{DrcState, HoaFrameContext, HoaPacketReport, HoaState},
};
/// Parse one packet against copies of the DRC and HOA state; nothing is committed.
pub(super) fn parse(
    context: &HoaFrameContext,
    drc: &DrcState,
    hoa: &HoaState,
    packet: &[u8],
    mode: ParseMode,
) -> Result<(HoaPacketReport, DrcState, HoaState)> {
    let mut next_drc = drc.clone();
    let mut next_hoa = hoa.clone();
    let report = crate::frame::parse_hoa_packet_with_mode(
        context,
        packet,
        &mut next_drc,
        &mut next_hoa,
        mode,
    )
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
