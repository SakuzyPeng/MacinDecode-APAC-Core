//! Hand-constructed syntax vectors, not copies of media cookies or vendor binaries.
use macindecode_apac_tools::config::{CookieReport, MAX_COOKIE_BYTES, ParseStatus, parse_cookie};
use serde_json::json;

struct Writer {
    data: Vec<u8>,
    bit: usize,
}
impl Writer {
    fn new() -> Self {
        Self {
            data: vec![0, 0, 0, 0, b'd', b'a', b'p', b'a', 0, 0, 0, 0],
            bit: 96,
        }
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
    fn finish(mut self) -> Vec<u8> {
        let n = self.data.len() as u32;
        self.data[..4].copy_from_slice(&n.to_be_bytes());
        self.data
    }
}
fn header(w: &mut Writer, channels: u64, rate: u64) {
    for (v, n) in [
        (0x0800, 16),
        (31, 6),
        (2, 4),
        (0, 1),
        (rate, 6),
        (0, 6),
        (channels, 8),
        (2, 8),
        (0, 1),
    ] {
        w.put(v, n);
    }
}
fn channel_config(channels: u64, rate: u64, remap: bool, origin: bool) -> Vec<u8> {
    let mut w = Writer::new();
    header(&mut w, channels, rate);
    for (v, n) in [(1, 3), (0, 8), (0, 3), (0, 1), (channels / 2, 5)] {
        w.put(v, n);
    }
    for _ in 0..channels / 2 {
        w.put(1, 3);
    }
    w.put(if channels == 2 { 101 } else { 128 }, 16);
    w.put(u64::from(remap), 1);
    if remap {
        let bits = (64 - (channels - 1).leading_zeros()) as usize;
        for channel in 0..channels {
            w.put(channel, bits);
        }
    }
    w.put(0, 1); // additional ASC
    w.put(0, 3);
    w.put(0, 2); // component parameters
    for _ in 0..5 {
        w.put(0, 1);
    } // ancillary: graph, scenes, DRC, metadata, custom data
    if origin {
        w.put(1, 1);
        w.put(3, 4);
        w.put(4, 4); // type 3, 5-byte payload
        for (value, width) in [(0, 8), (0, 3), (1, 10), (2, 10), (3, 8)] {
            w.put(value, width);
        }
        w.put(0, 1); // extension payload padding
    }
    w.put(0, 1); // extension terminator
    w.finish()
}
fn field<'a>(
    report: &'a CookieReport,
    name: &str,
) -> &'a macindecode_apac_tools::config::ConfigField {
    report.fields.iter().find(|f| f.name == name).unwrap()
}
fn set(data: &mut [u8], offset: usize, width: usize, value: u64) {
    for i in 0..width {
        let mask = 1 << (7 - (offset + i) % 8);
        data[(offset + i) / 8] &= !mask;
        if value & (1 << (width - i - 1)) != 0 {
            data[(offset + i) / 8] |= mask;
        }
    }
}

#[test]
fn short_synthetic_cookies_parse_without_size_or_hash_dispatch() {
    for (channels, rate, hz) in [(2, 3, 48000), (8, 4, 44100)] {
        let bytes = channel_config(channels, rate, false, false);
        let parsed = parse_cookie(&bytes).unwrap();
        assert_eq!(parsed.status, ParseStatus::Complete);
        assert_eq!(parsed.derived["channels"], channels);
        assert_eq!(parsed.derived["sample_rate_hz"], hz);
        assert_eq!(parsed.derived["frame_samples"], 1024);
        assert!(parsed.unknown_ranges.is_empty());
        let mut position = 0;
        for f in &parsed.fields {
            assert_eq!(f.bit_offset, position);
            position += f.bit_length;
        }
        assert_eq!(position, bytes.len() * 8);
        assert_eq!(field(&parsed, "global.sample_rate_index").bit_offset, 123);
    }
}

#[test]
fn every_truncated_byte_and_declared_length_mismatch_is_rejected() {
    let bytes = channel_config(8, 3, true, true);
    for end in 0..bytes.len() {
        assert!(
            parse_cookie(&bytes[..end]).is_err(),
            "unmodified length, cut {end}"
        );
        if end >= 12 {
            let mut truncated = bytes[..end].to_vec();
            truncated[..4].copy_from_slice(&(end as u32).to_be_bytes());
            assert!(
                parse_cookie(&truncated).is_err(),
                "adjusted length, cut {end}"
            );
        }
    }
    let mut bad = bytes.clone();
    bad[3] = 0;
    assert!(parse_cookie(&bad).is_err());
    assert!(parse_cookie(&vec![0; MAX_COOKIE_BYTES + 1]).is_err());
}

#[test]
fn unknown_version_type_and_reserved_flags_preserve_unparsed_bits() {
    let bytes = channel_config(2, 3, false, false);
    let parsed = parse_cookie(&bytes).unwrap();
    for (name, value, status) in [
        ("bitstream_version", 0x0801, ParseStatus::Unsupported),
        ("box.version_flags", 1, ParseStatus::Unsupported),
        ("components[0].type", 2, ParseStatus::Partial),
        ("global.frame_size_index", 1, ParseStatus::Partial),
    ] {
        let mut changed = bytes.clone();
        let f = field(&parsed, name);
        set(&mut changed, f.bit_offset, f.bit_length, value);
        let result = parse_cookie(&changed).unwrap();
        assert_eq!(result.status, status);
        assert!(!result.unknown_ranges.is_empty());
        for unknown in &result.unknown_ranges {
            let hex: String = changed
                [unknown.bit_offset / 8..(unknown.bit_offset + unknown.bit_length).div_ceil(8)]
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            assert_eq!(hex, unknown.raw_hex);
            assert_eq!(unknown.first_byte_skip_bits, unknown.bit_offset % 8);
        }
    }
}

#[test]
fn count_ranges_channel_totals_and_remapping_are_checked() {
    let bytes = channel_config(8, 3, true, false);
    let parsed = parse_cookie(&bytes).unwrap();
    assert!(parsed.is_complete());
    for i in 0..8 {
        assert_eq!(
            field(&parsed, &format!("components[0].remapping[{i}]")).value,
            json!(i)
        );
    }
    let mut wrong = bytes.clone();
    let f = field(&parsed, "global.channel_count");
    set(&mut wrong, f.bit_offset, f.bit_length, 2);
    assert!(parse_cookie(&wrong).is_err());
    let mut w = Writer::new();
    header(&mut w, 8, 3);
    w.put(7, 3);
    w.put(63, 6);
    w.put(4095, 12);
    let error = parse_cookie(&w.finish()).unwrap_err();
    assert_eq!(error.kind, "count-range");
    let mut w = Writer::new();
    header(&mut w, 8, 3);
    w.put(0, 3);
    assert_eq!(
        parse_cookie(&w.finish()).unwrap_err().kind,
        "component-count"
    );
}

#[test]
fn extension_bounds_sentinel_and_padding_are_accounted_for() {
    let bytes = channel_config(2, 3, false, true);
    let parsed = parse_cookie(&bytes).unwrap();
    assert!(parsed.is_complete());
    assert_eq!(parsed.derived["extensions[0].content_origin.values[0]"], -1);
    assert_eq!(parsed.derived["extensions[0].content_origin.values[2]"], 0);
    let mut short = bytes.clone();
    let f = field(&parsed, "extensions[0].bytes_minus_one");
    set(&mut short, f.bit_offset, f.bit_length, 0);
    assert_eq!(parse_cookie(&short).unwrap_err().kind, "truncated");
    let mut unknown = bytes.clone();
    let f = field(&parsed, "extensions[0].type");
    set(&mut unknown, f.bit_offset, f.bit_length, 7);
    assert_eq!(parse_cookie(&unknown).unwrap().status, ParseStatus::Partial);
    let mut pad = bytes.clone();
    let f = field(&parsed, "extensions[0].padding");
    assert_eq!(f.bit_length, 1);
    set(&mut pad, f.bit_offset, 1, 1);
    assert_eq!(parse_cookie(&pad).unwrap().status, ParseStatus::Partial);
    let mut tail = bytes.clone();
    tail.push(0);
    let length = tail.len() as u32;
    tail[..4].copy_from_slice(&length.to_be_bytes());
    assert_eq!(parse_cookie(&tail).unwrap().status, ParseStatus::Partial);
}

#[test]
fn single_bit_mutations_and_short_inputs_never_panic() {
    let source = channel_config(8, 3, true, true);
    for bit in 0..source.len() * 8 {
        let mut changed = source.clone();
        changed[bit / 8] ^= 1 << (bit % 8);
        let _ = parse_cookie(&changed);
    }
    for length in 0..=96 {
        let _ = parse_cookie(&vec![0xff; length]);
    }
}

#[test]
fn cli_exit_codes_are_platform_independent_and_errors_have_offsets() {
    use std::{
        fs,
        process::Command,
        time::{SystemTime, UNIX_EPOCH},
    };
    let dir = std::env::temp_dir().join(format!(
        "apac-parse-cli-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir(&dir).unwrap();
    struct Cleanup(std::path::PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let _guard = Cleanup(dir.clone());
    let path = dir.join("配置.bin");
    let mut bytes = channel_config(2, 3, false, false);
    fs::write(&path, &bytes).unwrap();
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_apac-tool"))
            .arg("parse-cookie")
            .arg(&path)
            .output()
            .unwrap()
    };
    let output = run();
    assert_eq!(output.status.code(), Some(0));
    bytes[12] = 9;
    fs::write(&path, &bytes).unwrap();
    let output = run();
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap()["status"],
        "unsupported"
    );
    fs::write(&path, []).unwrap();
    let output = run();
    assert_eq!(output.status.code(), Some(1));
    let error: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
    assert!(error["error"]["bit_offset"].is_number());
}

#[test]
fn unrecognized_scene_branch_stops_without_assuming_a_payload_length() {
    let mut data = channel_config(2, 3, false, false);
    let parsed = parse_cookie(&data).unwrap();
    let present = field(&parsed, "ancillary.audio_scenes_present");
    let prefix_bits = present.bit_offset;
    let mut w = Writer::new();
    for bit in 96..prefix_bits {
        w.put(u64::from((data[bit / 8] >> (7 - bit % 8)) & 1), 1);
    }
    w.put(1, 1); // audio scenes
    w.put(0, 3);
    w.put(0, 2);
    w.put(1, 6); // scene flags/parameter and one composition
    w.put(1, 1);
    w.put(0, 1); // composition flag, no language
    for (value, width) in [(0, 6), (1, 6), (0, 6), (0, 5), (0, 4)] {
        w.put(value, width);
    }
    w.put(0xabcdef, 24); // intentionally opaque language-item body
    data = w.finish();
    let result = parse_cookie(&data).unwrap();
    assert_eq!(result.status, ParseStatus::Partial);
    assert!(!result.unknown_ranges.is_empty());
    assert!(result.diagnostics[0].message.contains("language/selection"));
}

#[test]
fn scene_language_prefix_codes_and_empty_scene_lists_are_parsed() {
    let source = channel_config(2, 3, false, false);
    let report = parse_cookie(&source).unwrap();
    let prefix = field(&report, "ancillary.audio_scenes_present").bit_offset;
    let mut w = Writer::new();
    for bit in 96..prefix {
        w.put(u64::from((source[bit / 8] >> (7 - bit % 8)) & 1), 1);
    }
    w.put(1, 1);
    w.put(0, 3);
    w.put(0, 2);
    w.put(1, 6);
    w.put(1, 1);
    w.put(1, 1);
    // "en-us-jqx09": common letters, hyphen, uncommon letters and digits.
    for (v, n) in [
        (12, 5),
        (20, 5),
        (1, 3),
        (26, 5),
        (24, 5),
        (1, 3),
        (248, 8),
        (249, 8),
        (250, 8),
        (502, 9),
        (511, 9),
        (0, 3),
        (0, 1),
    ] {
        w.put(v, n);
    }
    for width in [6, 6, 6, 5, 4] {
        w.put(0, width);
    }
    w.put(0, 1);
    for _ in 0..3 {
        w.put(0, 1);
    }
    w.put(0, 1);
    w.put(0, 1);
    for _ in 0..3 {
        w.put(0, 1);
    }
    w.put(0, 1);
    let parsed = parse_cookie(&w.finish()).unwrap();
    assert!(parsed.is_complete());
    assert_eq!(
        parsed.derived["ancillary.audio_scenes.compositions[0].language"],
        "en-us-jqx09"
    );
}

#[test]
fn unknown_outer_shape_is_not_misreported_as_a_dapa_length_error() {
    let parsed = parse_cookie(b"unknown outer cookie").unwrap();
    assert_eq!(parsed.status, ParseStatus::Unsupported);
    assert!(parsed.fields.is_empty());
    assert_eq!(parsed.unknown_ranges[0].bit_offset, 0);
    assert_eq!(parsed.unknown_ranges[0].bit_length, 20 * 8);
    assert!(parsed.diagnostics[0].message.contains("standalone dapa"));
}

#[test]
fn escaped_u32_values_cannot_wrap() {
    let original = channel_config(2, 3, false, false);
    let parsed = parse_cookie(&original).unwrap();
    let position = field(&parsed, "components[0].parameter_1").bit_offset;
    let mut w = Writer::new();
    for bit in 96..position {
        w.put(u64::from((original[bit / 8] >> (7 - bit % 8)) & 1), 1);
    }
    w.put(3, 2);
    w.put(255, 8);
    w.put(u64::from(u32::MAX), 32);
    let error = parse_cookie(&w.finish()).unwrap_err();
    assert_eq!(error.kind, "overflow");
    assert_eq!(error.bit_offset, position);
}
