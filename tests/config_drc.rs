//! Independent hand-written wire vectors; no media cookies are embedded here.
use macindecode_apac_tools::config::{CookieReport, ParseStatus, parse_cookie};
use serde_json::json;

const DRC: &str = "ancillary.loudness_drc";

struct Wire {
    bytes: Vec<u8>,
    bit: usize,
}
impl Wire {
    fn new() -> Self {
        Self {
            bytes: vec![0, 0, 0, 0, b'd', b'a', b'p', b'a', 0, 0, 0, 0],
            bit: 96,
        }
    }
    fn put(&mut self, value: u64, width: usize) {
        for shift in (0..width).rev() {
            if self.bit / 8 == self.bytes.len() {
                self.bytes.push(0);
            }
            self.bytes[self.bit / 8] |= (((value >> shift) & 1) as u8) << (7 - self.bit % 8);
            self.bit += 1;
        }
    }
    fn fields(&mut self, fields: &[(u64, usize)]) {
        for &(value, width) in fields {
            self.put(value, width);
        }
    }
    fn finish(mut self) -> Vec<u8> {
        let len = self.bytes.len() as u32;
        self.bytes[..4].copy_from_slice(&len.to_be_bytes());
        self.bytes
    }
}

fn prefix() -> Wire {
    let mut w = Wire::new();
    w.fields(&[
        (0x0800, 16),
        (31, 6),
        (2, 4),
        (0, 1),
        (3, 6),
        (0, 6),
        (2, 8),
        (2, 8),
        (0, 1),
    ]);
    w.fields(&[
        (1, 3),
        (0, 8),
        (0, 3),
        (0, 1),
        (1, 5),
        (1, 3),
        (101, 16),
        (0, 1),
    ]);
    w.fields(&[(0, 1), (0, 3), (0, 2), (0, 1), (0, 1), (1, 1)]);
    w
}

fn config_header(w: &mut Wire) {
    w.fields(&[(1, 1), (1, 1), (1, 1), (47000, 18), (0, 1), (2, 10), (0, 1)]);
}

fn finish(w: &mut Wire) {
    w.fields(&[(0, 1), (0, 1)]); // metadata, custom data
    w.fields(&[(1, 1), (3, 4), (4, 4)]); // ContentOrigin with a five-byte payload
    w.fields(&[(4, 8), (2, 3), (9, 10), (10, 10), (20, 8), (0, 1), (0, 1)]);
}

fn rich_cookie() -> Vec<u8> {
    let mut w = prefix();
    config_header(&mut w);
    w.fields(&[(1, 3), (1, 4), (1, 1), (1023, 15)]); // one coefficient, explicit frame size
    w.fields(&[(1, 1), (1, 4), (0, 1), (18, 6), (3, 4), (2, 4), (0, 1)]); // left parametric curve
    w.fields(&[
        (1, 1),
        (1, 4),
        (1, 1),
        (1, 2),
        (1, 5),
        (128, 8),
        (2, 5),
        (129, 8),
    ]); // right, two nodes
    w.fields(&[(1, 1), (1, 4)]); // one shape filter
    w.fields(&[
        (1, 1),
        (3, 3),
        (2, 2),
        (0, 1),
        (1, 1),
        (4, 3),
        (1, 2),
        (0, 1),
    ]);
    w.fields(&[(2, 6), (2, 6)]); // two sequences and gain sets
    for i in 0..2 {
        w.fields(&[(0, 2), (0, 1), (1, 1), (1, 1), (1, 1), (31, 11), (1, 4)]);
        w.fields(&[(1, 1), (i, 6), (1, 1), (0, 1), (1, 4), (1, 4)]);
    }
    w.put(1, 8); // one instruction
    w.fields(&[
        (1, 1),
        (2, 4),
        (0, 4),
        (2, 4),
        (1, 6),
        (5, 4),
        (1, 4),
        (0, 1),
        (2, 16),
    ]);
    w.fields(&[
        (1, 1),
        (40, 8),
        (1, 1),
        (35, 6),
        (1, 1),
        (20, 6),
        (0, 1),
        (0, 1),
        (0, 1),
    ]);
    w.fields(&[(2, 6), (0, 1), (1, 6), (0, 1)]); // groups appear in reverse gain-set order
    for i in 0..2 {
        w.fields(&[(1, 1), (11 + i, 5), (1, 1), (1, 4), (1, 1), (1, 4)]);
        w.fields(&[(1, 1), (3, 4), (12, 4), (1, 1), (39, 6), (1, 1), (1, 4)]);
    }
    w.fields(&[(0, 1), (0, 1), (0, 1)]); // config extensions absent
    w.fields(&[(1, 1), (0, 1), (1, 8), (1, 8)]); // both loudness lists
    w.fields(&[(1, 4), (2, 4), (1, 1), (17, 6), (1, 1)]); // entry 0, MP4 loudness
    w.fields(&[
        (1, 6),
        (0, 6),
        (0, 7),
        (1, 1),
        (500, 12),
        (1, 1),
        (600, 12),
        (2, 4),
        (3, 2),
        (3, 4),
    ]);
    for (method, value, width) in [(7, 16, 5), (8, 2, 2), (1, 144, 8)] {
        w.fields(&[(method, 4), (value, width), (2, 4), (3, 2)]);
    }
    w.put(0, 1); // no compositions in entry 0
    w.fields(&[(0, 4), (0, 1), (0, 1), (1, 1)]); // entry 1, composition loudness
    w.fields(&[(63, 6), (63, 6), (127, 7), (2, 6), (2, 4)]);
    for method in [1, 4] {
        w.fields(&[(method, 4), (2, 4), (3, 2), (140, 8), (150, 8), (1, 6)]);
        for value in [160, 170] {
            w.fields(&[(value, 8), (42, 6), (17, 6)]);
        }
    }
    w.fields(&[(1, 1), (0, 1), (2, 8)]); // two explicit sources
    w.fields(&[
        (0, 8),
        (2, 8),
        (1, 1),
        (1, 8),
        (1, 8),
        (3, 8),
        (1, 1),
        (160, 8),
        (1, 1),
        (130, 8),
    ]);
    w.fields(&[(2, 8), (4, 8), (0, 1), (0, 1)]);
    w.put(0, 1); // loudness extensions
    finish(&mut w);
    w.finish()
}

fn multiband_cookie(crossover: bool) -> Vec<u8> {
    let mut w = prefix();
    config_header(&mut w);
    w.fields(&[
        (1, 3),
        (1, 4),
        (0, 1),
        (0, 1),
        (0, 1),
        (0, 1),
        (2, 6),
        (1, 6),
    ]);
    w.fields(&[
        (1, 2),
        (1, 1),
        (1, 1),
        (0, 1),
        (0, 1),
        (2, 4),
        (u64::from(crossover), 1),
    ]);
    // Both sequence indices are inferred from preceding bands, not read as bits.
    w.fields(&[(0, 1), (0, 1), (0, 1), (0, 1)]);
    w.put(
        if crossover { 4 } else { 100 },
        if crossover { 4 } else { 10 },
    );
    w.fields(&[
        (0, 8),
        (0, 1),
        (0, 1),
        (0, 1),
        (0, 1),
        (0, 1),
        (0, 8),
        (0, 8),
        (0, 1),
        (0, 1),
    ]);
    finish(&mut w);
    w.finish()
}

fn field<'a>(r: &'a CookieReport, name: &str) -> &'a macindecode_apac_tools::config::ConfigField {
    let name = format!("{DRC}.{name}");
    r.fields.iter().find(|f| f.name == name).unwrap()
}

fn replace(data: &mut [u8], offset: usize, width: usize, value: u64) {
    for i in 0..width {
        let mask = 1 << (7 - (offset + i) % 8);
        let byte = &mut data[(offset + i) / 8];
        *byte &= !mask;
        if value & (1 << (width - i - 1)) != 0 {
            *byte |= mask;
        }
    }
}

fn coverage(bytes: &[u8], r: &CookieReport) {
    assert_eq!(r.status, ParseStatus::Complete);
    assert!(r.unknown_ranges.is_empty());
    let mut end = 0;
    for f in &r.fields {
        assert_eq!(f.bit_offset, end, "{}", f.name);
        end += f.bit_length;
    }
    assert_eq!(end, bytes.len() * 8);
}

#[test]
fn nested_drc_values_groups_loudness_and_outer_extension_are_preserved() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    coverage(&bytes, &r);
    assert_eq!(field(&r, "header_present").bit_offset, 201);
    assert_eq!(field(&r, "sample_rate_minus_1000").bit_offset, 204);
    assert_eq!(r.derived[&format!("{DRC}.sample_rate_hz")], 48000);
    assert_eq!(
        r.derived[&format!("{DRC}.coefficients[0].frame_samples")],
        1024
    );
    assert_eq!(
        r.derived[&format!("{DRC}.instructions[0].channel_gain_set_indices")],
        json!([1, 0])
    );
    assert_eq!(
        r.derived[&format!("{DRC}.instructions[0].groups[0].gain_set_index")],
        1
    );
    assert_eq!(
        field(
            &r,
            "coefficients[0].right_characteristics[0].nodes[1].gain_encoded"
        )
        .value,
        129
    );
    assert_eq!(
        field(
            &r,
            "loudness.entries_0[0].mp4_info.measurements[0].value_encoded"
        )
        .bit_length,
        5
    );
    assert_eq!(
        field(
            &r,
            "loudness.entries_0[0].mp4_info.measurements[1].value_encoded"
        )
        .bit_length,
        2
    );
    assert_eq!(
        field(
            &r,
            "loudness.entries_1[0].compositions.measurements[1].mixes[1].parameters[1]"
        )
        .value,
        17
    );
    assert_eq!(field(&r, "loudness.sources[0].value_b_encoded").value, 130);
    assert_eq!(r.derived["extensions[0].content_origin.values[0]"], 3);
}

#[test]
fn multiband_boundaries_and_implicit_sequence_indices_have_correct_widths() {
    for crossover in [false, true] {
        let bytes = multiband_cookie(crossover);
        let r = parse_cookie(&bytes).unwrap();
        coverage(&bytes, &r);
        assert_eq!(
            r.derived[&format!("{DRC}.coefficients[0].gain_sets[0].bands[1].sequence_index")],
            1
        );
        assert_eq!(
            field(&r, "coefficients[0].gain_sets[0].bands[1].boundary_encoded").bit_length,
            if crossover { 4 } else { 10 }
        );
    }
}

#[test]
fn drc_truncation_at_every_byte_is_an_error_with_a_bounded_position() {
    let bytes = rich_cookie();
    for end in 0..bytes.len() {
        for fix_length in [false, true] {
            let mut cut = bytes[..end].to_vec();
            if fix_length && end >= 4 {
                cut[..4].copy_from_slice(&(end as u32).to_be_bytes());
            }
            let error = parse_cookie(&cut).expect_err("truncation accepted");
            assert!(error.bit_offset <= end * 8);
        }
    }
}

#[test]
fn drc_counts_rates_and_references_are_checked() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    for (name, value) in [
        ("sample_rate_minus_1000", 47001),
        ("base_channel_count", 3),
        ("coefficients[0].gain_sequence_count", 1),
        ("coefficients[0].gain_sets[0].bands[0].left_index", 2),
        ("instructions[0].location", 2),
        ("instructions[0].channel_runs[0].gain_set_index_plus_one", 3),
        ("instructions[0].groups[0].shape_filter_index", 2),
        ("loudness.count_0", 255),
        ("instruction_count", 255),
        (
            "loudness.entries_1[0].compositions.measurements[0].mix_count_minus_one",
            63,
        ),
    ] {
        let f = field(&r, name);
        let mut changed = bytes.clone();
        replace(&mut changed, f.bit_offset, f.bit_length, value);
        assert!(parse_cookie(&changed).is_err(), "{name}");
    }
    let f = field(&r, "instructions[0].channel_runs[0].repeat_present");
    let mut changed = bytes.clone();
    replace(&mut changed, f.bit_offset, 1, 1);
    replace(&mut changed, f.bit_offset + 1, 5, 31);
    assert_eq!(
        parse_cookie(&changed).unwrap_err().kind,
        "drc-channel-repeat"
    );
}

#[test]
fn passive_eq_requirement_preserves_following_wire_boundaries() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    let f = field(&r, "instructions[0].requires_eq");
    let mut changed = bytes.clone();
    replace(&mut changed, f.bit_offset, 1, 1);
    let parsed = parse_cookie(&changed).unwrap();
    coverage(&changed, &parsed);
    for (before, after) in r.fields.iter().zip(&parsed.fields) {
        assert_eq!(before.name, after.name);
        assert_eq!(before.bit_offset, after.bit_offset);
        assert_eq!(before.bit_length, after.bit_length);
        if before.bit_offset != f.bit_offset {
            assert_eq!(before.value, after.value);
        }
    }
}

#[test]
fn absent_header_and_nonzero_tail_padding_are_distinguished() {
    let mut w = prefix();
    w.put(0, 1); // header is absent; there is no implied configuration payload
    finish(&mut w);
    let bytes = w.finish();
    coverage(&bytes, &parse_cookie(&bytes).unwrap());
    let mut changed = rich_cookie();
    let r = parse_cookie(&changed).unwrap();
    let padding = r
        .fields
        .iter()
        .find(|f| f.name == "alignment_padding")
        .unwrap();
    assert!(padding.bit_length > 0);
    replace(&mut changed, padding.bit_offset, padding.bit_length, 1);
    assert_eq!(parse_cookie(&changed).unwrap().status, ParseStatus::Partial);
}

#[test]
fn bit_mutations_and_deterministic_short_inputs_never_panic() {
    let bytes = rich_cookie();
    for bit in 0..bytes.len() * 8 {
        let mut changed = bytes.clone();
        changed[bit / 8] ^= 1 << (7 - bit % 8);
        let _ = parse_cookie(&changed);
    }
    let mut seed = 0x91a2b3c4u32;
    for size in 0..256 {
        let mut bytes = Vec::new();
        for _ in 0..size {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            bytes.push(seed as u8);
        }
        let _ = parse_cookie(&bytes);
    }
}
