//! HOA carrier ordering and bounded opaque extension elements.
use super::{ChannelPacketReport, Parser};
use crate::config::ParseError;
use crate::prelude::*;
use crate::record::FieldValue;
use serde::Serialize;

/// SHA-256 of `data/hoa-transports-format-v1.json`, computed at build time.
pub fn format_sha256() -> &'static str {
    crate::tables::HOA_TRANSPORT_FORMAT_SHA256
}

#[cfg(test)]
#[path = "hoa_transport_tests.rs"]
mod tests;

#[derive(Debug, Clone, Serialize)]
pub struct HoaExtensionData {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    /// Declared body bytes including its header; zero also denotes an empty body.
    pub bytes: usize,
    /// Escaped opaque parameter consumed by the reference but not used for audio.
    pub payload_parameter: Option<u32>,
    pub payload_start_bit_offset: Option<usize>,
    pub payload_sha256: Option<String>,
}

fn length(parser: &mut Parser<'_>, prefix: &str, widths: &[usize]) -> Result<usize, ParseError> {
    let mut total = 0usize;
    for (index, &width) in widths.iter().enumerate() {
        let value = parser.take(&format!("{prefix}[{index}]"), width)? as usize;
        total += value;
        if value < (1 << width) - 1 {
            break;
        }
    }
    Ok(total)
}

fn end(parser: &Parser<'_>, start: usize, bytes: usize, limit: usize) -> Result<usize, ParseError> {
    let end = start + bytes * 8;
    if end < parser.bits.position() {
        return Err(ParseError::new(
            start,
            "hoa-extension-length",
            "extension length is shorter than its header",
        ));
    }
    if end > limit {
        return Err(ParseError::new(
            parser.bits.position(),
            "truncated",
            "extension exceeds its enclosing payload",
        ));
    }
    Ok(end)
}

pub(super) fn read(parser: &mut Parser<'_>, prefix: &str) -> Result<HoaExtensionData, ParseError> {
    let start = parser.bits.position();
    if parser.flag(&format!("{prefix}.extension.format_flag"))? {
        return Err(ParseError::new(
            start,
            "hoa-extension-format",
            "extension format flag is not supported by the bound format",
        ));
    }
    let bytes = length(parser, &format!("{prefix}.extension.bytes"), &[7, 8, 16])?;
    let limit = parser.bits.position() + parser.bits.remaining();
    let end = if bytes == 0 {
        parser.bits.position()
    } else {
        end(parser, start, bytes, limit)?
    };
    parser.bits.set_end(end)?;
    let mut parameter = None;
    let mut payload_start = None;
    let mut payload_hash = None;
    if parser.bits.position() < end {
        parameter = Some(length(
            parser,
            &format!("{prefix}.extension.payload_parameter"),
            &[8, 8, 16],
        )? as u32);
        let start = parser.bits.position();
        payload_start = Some(start);
        let mut payload = Vec::with_capacity((end - start) / 8);
        while parser.bits.position() < end {
            payload.push(parser.bits.read(8)? as u8);
        }
        let sha256 = crate::model::sha256(&payload);
        if parser.capture && start != end {
            parser.report.fields.push(crate::config::ConfigField {
                name: format!("{prefix}.extension.payload_sha256"),
                bit_offset: start,
                bit_length: end - start,
                value: FieldValue::from(sha256.clone()),
            });
        }
        payload_hash = Some(sha256);
    }
    parser.bits.set_end(limit)?;
    Ok(HoaExtensionData {
        start_bit_offset: start,
        end_bit_offset: end,
        bytes,
        payload_parameter: parameter,
        payload_start_bit_offset: payload_start,
        payload_sha256: payload_hash,
    })
}

/// Physical carrier associated with a logical core slot after static remapping.
pub(super) fn physical_slot(packet: &ChannelPacketReport, core: u8) -> u8 {
    packet
        .hoa
        .as_ref()
        .and_then(|h| h.static_remapping.as_ref())
        .and_then(|m| m.core_to_transport.get(usize::from(core)))
        .copied()
        .unwrap_or(core)
}

/// Borrow validated spectra in logical core order, retaining unused carriers.
/// Missing elements supply shared positive-zero storage, never stale samples.
pub(super) fn spectra(packet: &ChannelPacketReport) -> Result<Vec<&[f32]>, ParseError> {
    static ZERO: [f32; 1024] = [0.; 1024];
    let mut output = Vec::new();
    for element in &packet.elements {
        let map = element
            .configuration
            .transport_channels
            .as_ref()
            .unwrap_or(&element.configuration.output_channels);
        for (local, &slot) in map.iter().enumerate() {
            let position = element.end_bit_offset.unwrap_or(element.start_bit_offset);
            if usize::from(slot) != output.len() {
                return Err(ParseError::new(
                    position,
                    "hoa-transport-map",
                    "carrier mapping is not contiguous",
                ));
            }
            let samples = if element.present {
                let channel = element.channels_after_bwe2.get(local).ok_or_else(|| {
                    ParseError::new(
                        position,
                        "hoa-transport-map",
                        "present carrier has no decoded spectrum",
                    )
                })?;
                channel.scaled.as_slice()
            } else {
                &ZERO
            };
            if samples.len() != 1024 || samples.iter().any(|v| !v.is_finite()) {
                return Err(ParseError::new(
                    position,
                    "hoa-numeric",
                    "invalid transport spectrum",
                ));
            }
            output.push(samples);
        }
    }
    if let Some(mapping) = packet
        .hoa
        .as_ref()
        .and_then(|h| h.static_remapping.as_ref())
    {
        let count = mapping.core_to_transport.len();
        if count > output.len() {
            return Err(ParseError::new(
                packet.frame.stop_bit_offset,
                "hoa-remapping",
                "remapping exceeds available carriers",
            ));
        }
        let before = output[..count].to_vec();
        let mut seen = vec![false; count];
        for (slot, &carrier) in mapping.core_to_transport.iter().enumerate() {
            let carrier = usize::from(carrier);
            if carrier >= count || std::mem::replace(&mut seen[carrier], true) {
                return Err(ParseError::new(
                    packet.frame.stop_bit_offset,
                    "hoa-remapping",
                    "invalid effective carrier permutation",
                ));
            }
            output[slot] = before[carrier];
        }
    }
    Ok(output)
}
