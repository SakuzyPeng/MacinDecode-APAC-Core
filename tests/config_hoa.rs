//! Hand-constructed HOA syntax vectors; no media cookies or vendor output.
use macindecode_apac_tools::config::{CookieReport, ParseStatus, parse_cookie};
use serde_json::json;

struct Wire {
    data: Vec<u8>,
    bit: usize,
}
impl Wire {
    fn new(channels: u64, components: u64) -> Self {
        let mut w = Self {
            data: vec![0, 0, 0, 0, b'd', b'a', b'p', b'a', 0, 0, 0, 0],
            bit: 96,
        };
        w.fields(&[
            (0x0800, 16),
            (5, 6),
            (0, 4),
            (0, 1),
            (3, 6),
            (0, 6),
            (channels, 8),
            (2, 8),
            (0, 1),
        ]);
        w.esc(components, [3, 6, 12]);
        w
    }
    fn put(&mut self, value: u64, width: usize) {
        for shift in (0..width).rev() {
            if self.bit / 8 == self.data.len() {
                self.data.push(0);
            }
            self.data[self.bit / 8] |= (((value >> shift) & 1) as u8) << (7 - self.bit % 8);
            self.bit += 1;
        }
    }
    fn fields(&mut self, fields: &[(u64, usize)]) {
        for &(value, width) in fields {
            self.put(value, width);
        }
    }
    fn esc(&mut self, mut value: u64, widths: [usize; 3]) {
        for width in widths {
            let max = (1 << width) - 1;
            self.put(value.min(max), width);
            if value < max {
                return;
            }
            value -= max;
        }
        assert_eq!(value, 0);
    }
    fn finish(mut self) -> Vec<u8> {
        let len = self.data.len() as u32;
        self.data[..4].copy_from_slice(&len.to_be_bytes());
        self.data
    }
}

fn width(count: u64) -> usize {
    (64 - (count - 1).leading_zeros()) as usize
}

fn hoa_header(w: &mut Wire, full: bool) {
    w.fields(&[
        (u64::from(full), 1),
        (1, 1),
        (0, 1),
        (0, 1),
        (1, 1),
        (1, 1),
        (0, 1),
    ]);
    w.fields(&[(1, 2), (0, 2), (0, 2)]);
}

fn layout(w: &mut Wire, channels: u64, remap: bool) {
    w.fields(&[(0, 1), (190, 16), (channels, 16), (u64::from(remap), 1)]);
    if remap {
        for c in (0..channels).rev() {
            w.put(c, width(channels));
        }
    }
}

fn ambient_component(w: &mut Wire, order: u64, remap: bool) {
    let channels = (order + 1) * (order + 1);
    hoa_header(w, true);
    w.esc(order, [4, 6, 8]);
    w.esc(0, [4, 6, 8]); // no salient components
    w.put(channels - 1, width(channels));
    w.put(0, 1); // implicit ambient indices
    if channels > 3 {
        w.put(0, 1);
    }
    w.esc(channels, [5, 10, 16]);
    for _ in 0..channels {
        w.put(0, 3);
    }
    layout(w, channels, remap);
}

fn tail(w: &mut Wire, components: usize) {
    w.put(0, 1); // additional ASC
    for _ in 0..components {
        w.fields(&[(0, 3), (0, 2)]);
    }
    for _ in 0..5 {
        w.put(0, 1);
    } // ancillary flags
    w.fields(&[(1, 1), (3, 4), (4, 4)]); // ContentOrigin, five bytes
    w.fields(&[(3, 8), (1, 3), (8, 10), (11, 10), (19, 8), (0, 1), (0, 1)]);
}

fn full_cookie(order: u64, remap: bool) -> Vec<u8> {
    let channels = (order + 1) * (order + 1);
    let mut w = Wire::new(channels, 1);
    w.fields(&[(0, 8), (2, 3)]);
    ambient_component(&mut w, order, remap);
    tail(&mut w, 1);
    w.finish()
}

fn rich_cookie() -> Vec<u8> {
    let mut w = Wire::new(9, 1);
    w.fields(&[(0, 8), (2, 3)]);
    // Conditional header flag and its payload; dynamic selection at its upper bound.
    w.fields(&[
        (1, 1),
        (1, 1),
        (1, 1),
        (1, 1),
        (0, 1),
        (1, 1),
        (0, 1),
        (1, 1),
        (2, 2),
    ]);
    w.esc(7, [4, 6, 8]);
    w.fields(&[(2, 2), (1, 2), (3, 2)]);
    w.esc(2, [4, 6, 8]); // order 2, nine coefficients
    w.esc(1, [4, 6, 8]); // one salient component
    w.put(2, 4); // two ambient components (no +1 when salient is nonzero)
    w.esc(15, [4, 6, 8]); // sixteen subbands, crosses the escape boundary
    w.put(1, 2); // salient order 1, four coefficients
    w.fields(&[(1, 1), (8, 4), (3, 4)]); // explicit selection [3,8], read in reverse order
    w.fields(&[(1, 1), (2, 2)]); // conditional parameter 3
    w.esc(3, [5, 10, 16]);
    w.fields(&[(0, 3), (1, 3), (6, 3)]); // 1 + 2 + 0 transport channels
    layout(&mut w, 9, false);
    tail(&mut w, 1);
    w.finish()
}

fn field<'a>(r: &'a CookieReport, name: &str) -> &'a macindecode_apac_tools::config::ConfigField {
    r.fields.iter().find(|f| f.name == name).unwrap()
}
fn hoa<'a>(r: &'a CookieReport, name: &str) -> &'a macindecode_apac_tools::config::ConfigField {
    field(r, &format!("components[0].hoa.{name}"))
}
fn replace(data: &mut [u8], offset: usize, width: usize, value: u64) {
    for i in 0..width {
        let mask = 1 << (7 - (offset + i) % 8);
        data[(offset + i) / 8] &= !mask;
        if value & (1 << (width - i - 1)) != 0 {
            data[(offset + i) / 8] |= mask;
        }
    }
}
fn coverage(bytes: &[u8], r: &CookieReport) {
    assert!(r.is_complete(), "{:?}", r.diagnostics);
    assert!(r.unknown_ranges.is_empty());
    let mut position = 0;
    for f in &r.fields {
        assert_eq!(f.bit_offset, position, "{}", f.name);
        position += f.bit_length;
    }
    assert_eq!(position, bytes.len() * 8);
}

#[test]
fn complete_orders_include_zero_width_indices_and_escaped_tce_counts() {
    for order in [0, 1, 2, 3, 10] {
        let bytes = full_cookie(order, true);
        let r = parse_cookie(&bytes).unwrap();
        coverage(&bytes, &r);
        let channels = (order + 1) * (order + 1);
        assert_eq!(r.derived["components[0].hoa.order"], order);
        assert_eq!(r.derived["components[0].hoa.coefficient_count"], channels);
        assert_eq!(r.derived["components[0].channels"], channels);
        assert_eq!(r.derived["components[0].ambisonic_order"], order);
        assert_eq!(r.derived["components[0].ambisonic_channel_order"], "ACN");
        assert_eq!(r.derived["components[0].ambisonic_normalization"], "SN3D");
        assert_eq!(hoa(&r, "full_order").bit_offset, 166);
        assert_eq!(hoa(&r, "order").bit_offset, 179);
        assert_eq!(hoa(&r, "remapping[0]").value, channels - 1);
        assert_eq!(hoa(&r, "remapping[0]").bit_length, width(channels));
        if order == 10 {
            assert_eq!(hoa(&r, "tce_count").bit_length, 15);
        }
    }
}

#[test]
fn conditional_fields_and_salient_arrays_do_not_replace_output_channel_count() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    coverage(&bytes, &r);
    assert_eq!(r.derived["components[0].hoa.core_channels"], 3);
    assert_eq!(r.derived["components[0].hoa.transport_channels"], 3);
    assert_eq!(r.derived["components[0].channels"], 9);
    assert_eq!(
        r.derived["components[0].hoa.ambient_selection"],
        json!([3, 8])
    );
    assert_eq!(
        r.derived["components[0].hoa.salient[0].coefficient_count"],
        4
    );
    assert_eq!(r.derived["components[0].hoa.dynamic_selection.subbands"], 8);
    assert_eq!(r.derived["components[0].hoa.salient[0].subbands"], 16);
    assert_eq!(hoa(&r, "salient[0].subbands_minus_one").bit_length, 10);
    assert_eq!(r.derived["components[0].hoa.parameter_2"], 9);
    assert_eq!(r.derived["components[0].hoa.parameter_3"], 3);
    assert_eq!(r.derived["extensions[0].content_origin.values[0]"], 2);
}

#[test]
fn explicit_coefficient_counts_and_ambient_selection_shortcut_are_bounded() {
    let mut w = Wire::new(9, 1);
    w.fields(&[(0, 8), (2, 3)]);
    hoa_header(&mut w, false);
    w.esc(5, [7, 12, 17]); // six coefficients, enclosing order two
    w.esc(0, [4, 6, 8]);
    w.put(2, 3); // three ambient components
    w.fields(&[(1, 1), (2, 3)]); // last index is 2, lower indices are implicit identity
    w.esc(3, [5, 10, 16]);
    for _ in 0..3 {
        w.put(0, 3);
    }
    layout(&mut w, 9, false);
    tail(&mut w, 1);
    let bytes = w.finish();
    let r = parse_cookie(&bytes).unwrap();
    coverage(&bytes, &r);
    assert_eq!(r.derived["components[0].hoa.coefficient_count"], 6);
    assert_eq!(r.derived["components[0].hoa.order"], 2);
    assert_eq!(
        r.derived["components[0].hoa.ambient_selection"],
        json!([0, 1, 2])
    );
    assert!(
        !r.fields
            .iter()
            .any(|f| f.name == "components[0].hoa.ambient_selection[0]")
    );
}

#[test]
fn component_coverage_and_overlap_checks_are_shared_with_lbr() {
    let mut w = Wire::new(6, 2);
    w.fields(&[(0, 8), (0, 3), (0, 1), (1, 5), (1, 3), (101, 16), (0, 1)]);
    w.fields(&[(2, 8), (2, 3)]);
    ambient_component(&mut w, 1, false);
    tail(&mut w, 2);
    let bytes = w.finish();
    let r = parse_cookie(&bytes).unwrap();
    coverage(&bytes, &r);
    assert_eq!(r.derived["components[0].channels"], 2);
    assert_eq!(r.derived["components[1].channels"], 4);
    for (name, value) in [
        ("components[1].lowest_channel_index", 1),
        ("components[1].lowest_channel_index", 3),
        ("global.channel_count", 7),
    ] {
        let f = field(&r, name);
        let mut changed = bytes.clone();
        replace(&mut changed, f.bit_offset, f.bit_length, value);
        assert!(parse_cookie(&changed).is_err(), "{name}");
    }
}

#[test]
fn hoa_count_mapping_and_layout_limits_are_enforced() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    for (name, value) in [
        ("dynamic_selection.subbands_minus_one", 8),
        ("max_salient_components", 10),
        ("ambient_components_encoded", 15),
        ("salient[0].order", 3),
        ("salient[0].subbands_minus_one", (15 << 6) | 1),
        ("ambient_selection[1]", 15),
        ("tce_count", 0),
        ("tce[1].type", 6),
        ("layout_channels", 0),
        ("layout_channels", 10),
    ] {
        let f = hoa(&r, name);
        let mut changed = bytes.clone();
        replace(&mut changed, f.bit_offset, f.bit_length, value);
        assert!(parse_cookie(&changed).is_err(), "{name}");
    }
    let mut bytes = full_cookie(2, true);
    let r = parse_cookie(&bytes).unwrap();
    let f = hoa(&r, "remapping[0]");
    replace(&mut bytes, f.bit_offset, f.bit_length, 15);
    assert_eq!(parse_cookie(&bytes).unwrap_err().kind, "hoa-remapping");
    let mut bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    let f = hoa(&r, "layout_channels");
    replace(&mut bytes, f.bit_offset, f.bit_length, 65535);
    let f = hoa(&r, "remapping_present");
    replace(&mut bytes, f.bit_offset, 1, 1);
    assert_eq!(parse_cookie(&bytes).unwrap_err().kind, "channel-range");
}

#[test]
fn out_of_range_orders_and_escaped_counts_fail_before_allocation() {
    for order in [11, 15, 78, 333] {
        let mut w = Wire::new(16, 1);
        w.fields(&[(0, 8), (2, 3)]);
        hoa_header(&mut w, true);
        w.esc(order, [4, 6, 8]);
        assert_eq!(parse_cookie(&w.finish()).unwrap_err().kind, "hoa-order");
    }
    for count in [122, 128, 4223, 135294] {
        let mut w = Wire::new(16, 1);
        w.fields(&[(0, 8), (2, 3)]);
        hoa_header(&mut w, false);
        w.esc(count - 1, [7, 12, 17]);
        assert_eq!(
            parse_cookie(&w.finish()).unwrap_err().kind,
            "hoa-coefficient-count"
        );
    }
    let mut w = Wire::new(1, 1);
    w.fields(&[(0, 8), (2, 3)]);
    hoa_header(&mut w, true);
    w.esc(0, [4, 6, 8]);
    w.esc(0, [4, 6, 8]);
    w.put(0, 1);
    w.esc(31 + 1023 + 65535, [5, 10, 16]);
    assert_eq!(parse_cookie(&w.finish()).unwrap_err().kind, "count-range");
}

#[test]
fn unknown_layout_tce_and_remapping_conventions_preserve_the_remaining_input() {
    let bytes = rich_cookie();
    let r = parse_cookie(&bytes).unwrap();
    for (name, value) in [
        ("custom_layout_present", 1),
        ("layout_family", 191),
        ("tce[0].type", 7),
        ("remapping_present", 1),
    ] {
        let f = hoa(&r, name);
        let mut changed = bytes.clone();
        replace(&mut changed, f.bit_offset, f.bit_length, value);
        let parsed = parse_cookie(&changed).unwrap();
        assert_eq!(parsed.status, ParseStatus::Partial, "{name}");
        let unknown = &parsed.unknown_ranges[0];
        assert_eq!(unknown.bit_offset, f.bit_offset + f.bit_length);
        assert_eq!(unknown.bit_offset + unknown.bit_length, changed.len() * 8);
        let hex: String = changed[unknown.bit_offset / 8..]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(unknown.raw_hex, hex);
    }
}

#[test]
fn every_truncated_byte_and_nonzero_padding_has_a_precise_failure() {
    let bytes = rich_cookie();
    for end in 0..bytes.len() {
        for fix_length in [false, true] {
            let mut cut = bytes[..end].to_vec();
            if fix_length && end >= 4 {
                cut[..4].copy_from_slice(&(end as u32).to_be_bytes());
            }
            let error = parse_cookie(&cut).unwrap_err();
            assert!(error.bit_offset <= end * 8);
        }
    }
    let r = parse_cookie(&bytes).unwrap();
    let f = field(&r, "alignment_padding");
    assert!(f.bit_length > 0);
    let mut changed = bytes;
    replace(&mut changed, f.bit_offset, f.bit_length, 1);
    let parsed = parse_cookie(&changed).unwrap();
    assert_eq!(parsed.status, ParseStatus::Partial);
    assert_eq!(parsed.unknown_ranges[0].bit_offset, f.bit_offset);
}

#[test]
fn single_bit_changes_do_not_panic_or_leave_complete_results_with_gaps() {
    let bytes = rich_cookie();
    for bit in 0..bytes.len() * 8 {
        let mut changed = bytes.clone();
        changed[bit / 8] ^= 1 << (7 - bit % 8);
        if let Ok(parsed) = parse_cookie(&changed)
            && parsed.is_complete()
        {
            coverage(&changed, &parsed);
        }
    }
}
