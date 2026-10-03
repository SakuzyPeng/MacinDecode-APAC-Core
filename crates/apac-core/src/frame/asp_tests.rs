//! Header-only length controls independently encode both escape tiers.
use super::*;
fn header(size: u64, maximum: u64, fill: u64) -> Result<FrameReport, ParseError> {
    let mut wire = Vec::new();
    let mut put = |value: u64, width: usize| {
        for bit in (0..width).rev() {
            wire.push((value >> bit) & 1 != 0);
        }
    };
    put(2, 2);
    put(0, 1);
    put(1, 2);
    put(size.min(65535), 16);
    if size >= 65535 {
        put(size - 65535, 16);
    }
    put(fill, 3);
    let mut packet = vec![0u8; wire.len() / 8 + size as usize + 1];
    for (at, set) in wire.into_iter().enumerate() {
        if set {
            packet[at / 8] |= 1 << (7 - at % 8);
        }
    }
    let mut parser = Parser {
        capture: true,
        bits: BitReader::new(&packet),
        report: FrameReport {
            schema_version: SCHEMA_VERSION,
            cookie_sha256: String::new(),
            packet_sha256: String::new(),
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
        },
    };
    assert_eq!(parser.take("frame.type_code", 2)?, 2);
    assert_eq!(parser.asp_bounded(2, maximum)?, None);
    Ok(parser.report)
}
#[test]
fn bounded_asp_lengths_and_escape_tiers() {
    for size in [1, 4095, 4096, 65534, 65535, 65536, 131070] {
        for fill in [0, 7] {
            let report = header(size, 131070, fill).unwrap();
            let start = if size < 65535 { 24 } else { 40 };
            assert_eq!(report.derived["asp.preroll.start_bit"], start);
            assert_eq!(report.derived["asp.preroll.end_bit"], start + size * 8);
        }
    }
    for size in [0, 4097, 65535, 131070] {
        assert_eq!(header(size, 4096, 0).unwrap_err().kind, "preroll-size");
    }
}
