//! Independent wire construction for joint gain-configuration/overlap rollback.
use super::*;
use crate::config::ParseStatus;
use serde_json::{Value, json};
#[derive(Default)]
struct Wire(Vec<u8>, usize);
impl Wire {
    fn put(&mut self, n: u64, width: usize) {
        for shift in (0..width).rev() {
            if self.1 / 8 == self.0.len() {
                self.0.push(0);
            }
            self.0[self.1 / 8] |= (((n >> shift) & 1) as u8) << (7 - self.1 % 8);
            self.1 += 1;
        }
    }
    fn fields(&mut self, fields: &[(u64, usize)]) {
        for &(n, w) in fields {
            self.put(n, w);
        }
    }
    fn align(&mut self) {
        while !self.1.is_multiple_of(8) {
            self.put(0, 1);
        }
    }
}
fn metadata(w: &mut Wire, value: u64) {
    w.fields(&[
        (1, 1),
        (0, 1),
        (0, 8),
        (0, 8),
        (1, 1),
        (0, 1),
        (1, 8),
        (0, 8),
        (2, 8),
        (0, 1),
        (1, 1),
        (value, 8),
        (0, 1),
        (0, 1),
    ]);
}
fn header(w: &mut Wire, value: u64, full: bool, profile: u64) {
    w.fields(&[(1, 1), (u64::from(full), 1)]);
    if full {
        w.fields(&[
            (0, 1),
            (0, 1),
            (2, 10),
            (0, 1),
            (1, 3),
            (1, 4),
            (0, 1),
            (0, 1),
            (0, 1),
            (0, 1),
            (1, 6),
            (1, 6),
            (profile, 2),
            (1, 1),
            (0, 1),
            (0, 1),
            (1, 1),
            (63, 11),
            (1, 4),
            (0, 1),
            (0, 1),
            (0, 8),
            (0, 3),
        ]);
    }
    metadata(w, value);
}
fn cookie() -> Vec<u8> {
    let mut w = Wire::default();
    w.fields(&[
        (0, 32),
        (u64::from(u32::from_be_bytes(*b"dapa")), 32),
        (0, 32),
        (0x800, 16),
        (31, 6),
        (0, 4),
        (0, 1),
        (3, 6),
        (0, 6),
        (2, 8),
        (2, 8),
        (0, 1),
        (1, 3),
        (0, 8),
        (0, 3),
        (0, 1),
        (1, 5),
        (1, 3),
        (101, 16),
        (0, 1),
        (0, 1),
        (0, 3),
        (0, 2),
        (0, 1),
        (0, 1),
        (1, 1),
    ]);
    header(&mut w, 10, true, 0);
    w.fields(&[(0, 1), (0, 1), (0, 1)]);
    w.align();
    let len = w.0.len() as u32;
    w.0[..4].copy_from_slice(&len.to_be_bytes());
    w.0
}
fn core(w: &mut Wire, present: bool) {
    w.put(u64::from(present), 1);
    if !present {
        return;
    }
    w.fields(&[(0, 1), (0, 2), (1, 6), (160, 8), (1, 4), (1, 5)]);
    let tables: Value =
        serde_json::from_str(include_str!("../../../../data/sq-codebooks.json")).unwrap();
    let sf = &tables["scalefactor"];
    w.put(
        sf["codes"][60].as_u64().unwrap(),
        sf["bits"][60].as_u64().unwrap() as usize,
    );
    let book = &tables["spectral"][0];
    let index = 2 * 27 + 9 + 3 + 1;
    w.put(
        book["codes"][index].as_u64().unwrap(),
        book["bits"][index].as_u64().unwrap() as usize,
    );
    w.fields(&[(0, 1), (0, 2), (0, 6), (160, 8), (0, 4)]);
}
fn packet(
    present: bool,
    update: Option<(u64, bool, u64)>,
    inner: Option<&[u8]>,
    bad_tail: bool,
) -> Vec<u8> {
    let mut w = Wire::default();
    if let Some(inner) = inner {
        w.fields(&[(2, 2), (0, 1), (1, 2), (inner.len() as u64, 16)]);
        w.align();
        for &byte in inner {
            w.put(u64::from(byte), 8);
        }
    } else {
        w.put(1, 2);
    }
    core(&mut w, present);
    w.align();
    if let Some((v, full, profile)) = update {
        header(&mut w, v, full, profile);
    } else {
        w.put(0, 1);
    }
    w.fields(&[
        (0, 1),
        (0, 1),
        (12, 8),
        (0, 1),
        (u64::from(bad_tail), 1),
        (0, 1),
    ]);
    w.align();
    w.0
}
fn value(d: &Decoder) -> Value {
    serde_json::to_value(&d.drc.configuration).unwrap()
}
fn loudness(d: &Decoder) -> u64 {
    d.drc
        .configuration
        .as_ref()
        .unwrap()
        .loudness_metadata
        .iter()
        .find(|f| f.name.ends_with(".value_a_encoded"))
        .unwrap()
        .value
        .as_u64()
        .unwrap()
}
#[test]
fn gain_metadata_and_both_overlaps_commit_only_with_whole_outer_packet() {
    let config = cookie();
    let active = packet(true, None, None, false);
    let mut actual = Decoder::from_cookie(&config).unwrap();
    let mut control = Decoder::from_cookie(&config).unwrap();
    actual.decode_vec(&active).unwrap();
    control.decode_vec(&active).unwrap();
    let before = value(&actual);
    let child = packet(true, Some((21, false, 0)), None, false);
    let bad = packet(true, Some((55, false, 0)), Some(&child), true);
    assert!(actual.decode_vec(&bad).is_err());
    assert_eq!(value(&actual), before);
    let absent = packet(false, None, None, false);
    assert_eq!(
        actual.decode_vec(&absent).unwrap(),
        control.decode_vec(&absent).unwrap()
    );
    let mut invalid_inner = packet(false, Some((55, true, 0)), None, false);
    // The absent core ends at bit 8; the full header's base-channel field
    // occupies bits 12..22. Change its declared count from two to three.
    invalid_inner[21 / 8] |= 1 << (7 - 21 % 8);
    let rejected = packet(false, None, Some(&invalid_inner), false);
    let error = actual.decode_vec(&rejected).unwrap_err();
    assert_eq!(error.bit_offset, Some(24 + 22));
    assert_eq!(value(&actual), before);
    let good = packet(true, Some((55, false, 0)), Some(&child), false);
    let plain = packet(true, None, Some(&active), false);
    // Declarative updates, including the frames after they end, cannot alter
    // PCM under the independent off model. No native crossfade is reproduced.
    assert_eq!(
        actual.decode_vec(&good).unwrap(),
        control.decode_vec(&plain).unwrap()
    );
    assert_eq!(loudness(&actual), 55);
    assert_eq!(actual.drc.previous_nodes[0].gain_eighth_db, 12);
    for _ in 0..2 {
        assert_eq!(
            actual.decode_vec(&absent).unwrap(),
            control.decode_vec(&absent).unwrap()
        );
    }
    actual.reset();
    assert_eq!(loudness(&actual), 10);
    assert!(actual.drc.previous_nodes.is_empty());
    assert!(
        actual
            .decode_vec(&absent)
            .unwrap()
            .iter()
            .all(|v| v.to_bits() == 0)
    );
}
#[test]
fn incompatible_header_and_missing_gain_do_not_commit_metadata() {
    let config = cookie();
    let mut decoder = Decoder::from_cookie(&config).unwrap();
    let before = value(&decoder);
    let mut invalid = packet(false, Some((77, true, 0)), None, false);
    invalid[21 / 8] |= 1 << (7 - 21 % 8);
    assert!(decoder.decode_vec(&invalid).is_err());
    assert_eq!(value(&decoder), before);
    let good = packet(false, Some((77, false, 0)), None, false);
    let mut missing = good.clone();
    missing.truncate(missing.len() - 2);
    assert!(decoder.decode_vec(&missing).is_err());
    assert_eq!(value(&decoder), before);
    decoder.decode_vec(&good).unwrap();
    assert_eq!(loudness(&decoder), 77);
    assert_eq!(decoder.drc.configuration.as_ref().unwrap().source, "cookie");
    decoder
        .decode_vec(&packet(false, Some((99, true, 0)), None, false))
        .unwrap();
    assert_eq!(loudness(&decoder), 99);
    assert_eq!(decoder.drc.configuration.as_ref().unwrap().source, "packet");
}
#[test]
fn absent_core_still_parses_drc_and_only_the_encoder_zero_tail_is_allowed() {
    let context = FrameContext::from_cookie(&cookie()).unwrap();
    let raw = packet(false, None, None, false);
    let report = crate::frame::parse_packet(&context, &raw).unwrap();
    assert!(report.packet_complete && report.cpe_absent());
    assert_eq!(report.drc_complete, Some(true));
    assert_eq!(report.drc.as_ref().unwrap().nodes[0].gain_eighth_db, 12);
    assert_eq!(report.frame().status, ParseStatus::Complete);
    let mut extra = raw.clone();
    extra.push(0);
    assert!(
        !crate::frame::parse_packet(&context, &extra)
            .unwrap()
            .packet_complete
    );
    let mut nonzero = raw;
    *nonzero.last_mut().unwrap() |= 1;
    assert!(
        !crate::frame::parse_packet(&context, &nonzero)
            .unwrap()
            .packet_complete
    );
    assert!(value(&Decoder::from_cookie(&cookie()).unwrap())["parameters"] != json!(null));
}
