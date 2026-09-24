//! Synthetic syntax only: no real media cookie or packet is embedded here.
use macindecode_apac_tools::{
    config::{ParseStatus, parse_cookie},
    frame::{FrameContext, FrameReport, parse_frame},
    packets::MAX_PACKET_BUFFER,
};
use serde_json::json;

#[derive(Default)]
struct Bits(Vec<u8>, usize);
impl Bits {
    fn put(&mut self, value: u64, width: usize) {
        for shift in (0..width).rev() {
            if self.1 / 8 == self.0.len() {
                self.0.push(0);
            }
            self.0[self.1 / 8] |= (((value >> shift) & 1) as u8) << (7 - self.1 % 8);
            self.1 += 1;
        }
    }
    fn fields(&mut self, fields: &[(u64, usize)]) {
        for &(value, width) in fields {
            self.put(value, width);
        }
    }
    fn opaque(mut self) -> Vec<u8> {
        self.put(0xa5, 8); // Deliberately not a valid encoded spectrum.
        self.0
    }
}
fn cookie(rate: u64, channels: u64, lbr: bool) -> Vec<u8> {
    let mut b = Bits::default();
    b.fields(&[
        (0, 32),
        (u64::from(u32::from_be_bytes(*b"dapa")), 32),
        (0, 32),
        (0x0800, 16),
        (31, 6),
        (0, 4),
        (0, 1),
        (rate, 6),
        (0, 6),
        (channels, 8),
        (2, 8),
        (0, 1),
        (1, 3),
        (0, 8),
        (0, 3),
        (u64::from(lbr), 1),
        (channels / 2, 5),
    ]);
    for _ in 0..channels / 2 {
        b.put(1, 3);
    }
    b.fields(&[(101, 16), (0, 1), (0, 1), (0, 3), (0, 2), (0, 5), (0, 1)]);
    let size = b.0.len() as u32;
    b.0[..4].copy_from_slice(&size.to_be_bytes());
    b.0
}
fn context() -> FrameContext {
    FrameContext::from_cookie(&cookie(3, 2, false)).unwrap()
}
fn check_coverage(bytes: &[u8], report: &FrameReport) {
    assert_eq!(report.status, ParseStatus::Partial);
    assert!(report.component_end_bit_offset.is_none());
    assert_eq!(
        report.stop_bit_offset,
        report
            .fields
            .last()
            .map_or(0, |f| f.bit_offset + f.bit_length)
    );
    let mut spans: Vec<_> = report
        .fields
        .iter()
        .map(|f| (f.bit_offset, f.bit_length))
        .chain(
            report
                .unknown_ranges
                .iter()
                .map(|r| (r.bit_offset, r.bit_length)),
        )
        .collect();
    spans.sort_unstable();
    let mut pos = 0;
    for (offset, length) in spans {
        assert_eq!(offset, pos);
        pos += length;
    }
    assert_eq!(pos, bytes.len() * 8);
}

#[test]
fn sq_ics_and_grouping_end_before_the_first_channel_stream() {
    for rate in [3, 4] {
        let context = FrameContext::from_cookie(&cookie(rate, 2, false)).unwrap();
        for block in [0, 1, 2, 3] {
            for mask in [0, 1, 0x55, 0x7f] {
                let short = block == 2;
                let mut bits = Bits::default();
                bits.fields(&[(1, 2), (1, 1), (0, 1), (block, 2)]);
                bits.put(if short { 14 } else { 49 }, if short { 4 } else { 6 });
                if short {
                    bits.put(mask, 7);
                }
                let stop = bits.1;
                let bytes = bits.opaque();
                let report = parse_frame(&context, &bytes).unwrap();
                check_coverage(&bytes, &report);
                assert!(report.prefix_complete);
                assert_eq!(report.stop_reason, "sq_left_channel_stream");
                assert_eq!(report.payload_bit_offset, Some(if short { 17 } else { 12 }));
                assert_eq!(report.stop_bit_offset, stop);
                let groups = report.derived["components[0].tce[0].left_ics.window_groups"]
                    .as_array()
                    .unwrap();
                assert_eq!(
                    groups.iter().map(|v| v.as_u64().unwrap()).sum::<u64>(),
                    if short { 8 } else { 1 }
                );
                assert_eq!(
                    groups.len(),
                    if short {
                        8 - mask.count_ones() as usize
                    } else {
                        1
                    }
                );
            }
        }
    }
}

#[test]
fn lrvq_is_deferred_at_dispatch_without_reading_its_payload() {
    for first in [0x30, 0x3f, 0x70, 0x7f] {
        for length in [1, 2, 12] {
            let mut bytes = vec![0xff; length];
            bytes[0] = first;
            let report = parse_frame(&context(), &bytes).unwrap();
            check_coverage(&bytes, &report);
            assert!(!report.prefix_complete);
            assert_eq!(report.stop_reason, "lrvq_prefix_deferred");
            assert_eq!(report.stop_bit_offset, 4);
            assert_eq!(report.derived["coding_type"], "lrvq");
            assert!(report.payload_bit_offset.is_none());
            assert_eq!(report.fields.len(), 3);
            assert!(report.fields.iter().all(|f| !f.name.contains("ics")));
        }
    }
}

#[test]
fn absent_elements_and_unparsed_tail_are_not_whole_frame_completion() {
    for byte in [0, 0x1f, 0x40, 0x5f] {
        let bytes = [byte, 0xff];
        let report = parse_frame(&context(), &bytes).unwrap();
        check_coverage(&bytes, &report);
        assert!(report.prefix_complete);
        assert_eq!(report.stop_reason, "cpe_absent");
        assert_eq!(report.stop_bit_offset, 3);
        assert!(report.payload_bit_offset.is_none());
        assert_eq!(report.unknown_ranges[0].bit_length, 13);
    }
    // Packet tail values cannot become padding just because they happen to be zero.
    let report = parse_frame(&context(), &[0x60, 0]).unwrap();
    assert_eq!(report.status, ParseStatus::Partial);
    assert_eq!(report.unknown_ranges[0].bit_offset, 12);
}

#[test]
fn limits_truncation_and_sfb_counts_report_the_actual_location() {
    assert_eq!(parse_frame(&context(), &[]).unwrap_err().bit_offset, 0);
    assert_eq!(
        parse_frame(&context(), &vec![0; MAX_PACKET_BUFFER + 1])
            .unwrap_err()
            .kind,
        "input-limit"
    );
    for (block, value, width) in [(0, 50, 6), (2, 15, 4)] {
        let mut bits = Bits::default();
        bits.fields(&[(0, 2), (1, 1), (0, 1), (block, 2), (value, width)]);
        let error = parse_frame(&context(), &bits.opaque()).unwrap_err();
        assert_eq!((error.kind.as_str(), error.bit_offset), ("max-sfb", 6));
    }
    let mut bits = Bits::default();
    bits.fields(&[(0, 2), (1, 1), (0, 1), (2, 2), (14, 4), (0x55, 7)]);
    let bytes = bits.opaque();
    for end in 0..=2 {
        let error = parse_frame(&context(), &bytes[..end]).unwrap_err();
        assert!(error.bit_offset <= end * 8);
        assert_eq!(error.kind, "truncated");
    }
    assert!(parse_frame(&context(), &[0x60]).is_err()); // truncated SQ max_sfb
    let mut zero = Bits::default();
    zero.fields(&[(0, 2), (1, 1), (0, 1), (2, 2), (0, 4), (0, 7)]);
    let report = parse_frame(&context(), &zero.opaque()).unwrap();
    assert_eq!(
        report.derived["components[0].tce[0].left_ics.active_group_count"],
        0
    );
}

#[test]
fn asp_preroll_lengths_allow_skipping_without_claiming_payload_completion() {
    for count in [0, 1] {
        let mut bits = Bits::default();
        bits.fields(&[(2, 2), (0, 1), (count, 2)]);
        if count == 1 {
            bits.fields(&[(3, 16), (0, 3), (1, 2), (0xa5, 8), (0, 14)]);
        }
        bits.fields(&[(1, 1), (0, 1), (0, 2), (1, 6)]);
        let bytes = bits.opaque();
        let report = parse_frame(&context(), &bytes).unwrap();
        check_coverage(&bytes, &report);
        assert!(report.prefix_complete);
        assert_eq!(report.stop_bit_offset, if count == 0 { 15 } else { 58 });
        assert_eq!(report.unknown_ranges.len(), if count == 0 { 1 } else { 2 });
        if count == 1 {
            assert_eq!(report.unknown_ranges[0].bit_offset, 26);
            assert_eq!(report.unknown_ranges[0].bit_length, 22);
            assert_eq!(report.derived["core_frame_start_bit"], 48);
        }
    }
}

#[test]
fn asp_unknown_branches_reserved_alignment_and_preroll_bounds_are_explicit() {
    for fields in [
        vec![(3, 2)],
        vec![(2, 2), (1, 1)],
        vec![(2, 2), (0, 1), (1, 2), (3, 16), (1, 3)],
    ] {
        let mut bits = Bits::default();
        bits.fields(&fields);
        let bytes = bits.opaque();
        let report = parse_frame(&context(), &bytes).unwrap();
        check_coverage(&bytes, &report);
        assert!(!report.prefix_complete);
    }
    for (fields, kind) in [
        (vec![(2, 2), (0, 1), (2, 2)], "preroll-count"),
        (vec![(2, 2), (0, 1), (1, 2), (0, 16)], "preroll-size"),
        (
            vec![(2, 2), (0, 1), (1, 2), (65535, 16), (0, 16)],
            "preroll-size",
        ),
        (vec![(2, 2), (0, 1), (1, 2), (3, 16), (0, 3)], "truncated"),
        (
            vec![(2, 2), (0, 1), (1, 2), (1, 16), (0, 3), (2, 2), (0, 6)],
            "nested-preroll",
        ),
    ] {
        let mut bits = Bits::default();
        bits.fields(&fields);
        assert_eq!(
            parse_frame(&context(), &bits.opaque()).unwrap_err().kind,
            kind
        );
    }
}

#[test]
fn unsupported_cookie_fields_do_not_acquire_container_defaults() {
    for bytes in [cookie(5, 2, false), cookie(3, 8, false), cookie(3, 2, true)] {
        let c = FrameContext::from_cookie(&bytes).unwrap();
        assert!(!c.is_supported());
        let report = parse_frame(&c, &[0x60, 0]).unwrap();
        check_coverage(&[0x60, 0], &report);
        assert!(!report.prefix_complete);
        assert_eq!(report.fields.len(), 1);
        assert_eq!(report.stop_bit_offset, 2);
    }
    let mut bytes = cookie(3, 2, false);
    bytes[12] = 0xff;
    let c = FrameContext::from_cookie(&bytes).unwrap();
    let report = parse_frame(&c, &[0x60]).unwrap();
    assert_eq!(report.status, ParseStatus::Unsupported);
    assert_eq!(report.stop_bit_offset, 0);
    assert!(report.fields.is_empty());
    assert!(FrameContext::from_cookie(&[]).is_err());
    // A future ancillary branch is irrelevant to an already confirmed prefix context.
    let mut bytes = cookie(3, 2, false);
    let parsed = parse_cookie(&bytes).unwrap();
    let offset = parsed
        .fields
        .iter()
        .find(|f| f.name == "ancillary.metadata_present")
        .unwrap()
        .bit_offset;
    bytes[offset / 8] |= 1 << (7 - offset % 8);
    assert_eq!(parse_cookie(&bytes).unwrap().status, ParseStatus::Partial);
    assert!(FrameContext::from_cookie(&bytes).unwrap().is_supported());
}

#[test]
fn packet_bit_mutations_preserve_coverage_or_return_bounded_errors() {
    let context = context();
    for value in 0..=u16::MAX {
        let mut bytes = value.to_be_bytes().to_vec();
        bytes.push(0xa5);
        match parse_frame(&context, &bytes) {
            Ok(report) => {
                check_coverage(&bytes, &report);
                assert_eq!(
                    serde_json::to_value(&report).unwrap()["component_end_bit_offset"],
                    json!(null)
                );
            }
            Err(error) => assert!(error.bit_offset <= bytes.len() * 8),
        }
    }
}

mod bundles {
    use super::*;
    use macindecode_apac_tools::{frame::parse_packets, model::*, packets::PacketBundle};
    use std::{
        fs,
        path::{Path, PathBuf},
        process::Command,
        sync::atomic::{AtomicU64, Ordering},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "apac-frame-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn build(path: &Path, payloads: &[&[u8]]) {
        fs::create_dir(path).unwrap();
        let cookie = cookie(3, 2, false);
        let all = payloads.concat();
        fs::write(path.join("cookie.bin"), &cookie).unwrap();
        fs::write(path.join("packets.bin"), &all).unwrap();
        let mut offset = 0;
        let rows: Vec<_> = payloads.iter().enumerate().map(|(i, data)| {
            let row = json!({"schema_version":1,"packet_index":i,"export_offset":offset,"bytes":data.len(),
                "frames":1024,"sha256":sha256(data),"raw_frame_position":Property::known(i * 1024),
                "dependency":Property::known(json!({"independently_decodable":true,"preroll_packet_count":0})),
                "roll_distance":Property::known(0)});
            offset += data.len();
            format!("{row}\n")
        }).collect();
        fs::write(path.join("packets.jsonl"), rows.concat()).unwrap();
        let info = json!({"schema_version":1,"source":"missing-original.caf","file_bytes":all.len(),"modified_unix_seconds":null,
            "environment":{"tool_version":"test","os":"test","architecture":"test","system_version":"test"},
            "container":Property::known("caff"),"format":{"sample_rate":48000,"format_id":u32::from_be_bytes(*b"apac"),
            "format_fourcc":"apac","flags":0,"bytes_per_packet":0,"frames_per_packet":1024,"bytes_per_frame":0,"channels":2,"bits_per_channel":0},
            "layout":Property::known(ChannelLayout::tagged((101 << 16) | 2, 2, None)),"packet_count":Property::known(payloads.len()),
            "max_packet_bytes":Property::known(3),"packet_table":Property::known(PacketTable { priming_frames:0, valid_frames:payloads.len() as i64 * 1024, remainder_frames:0 }),
            "cookie":Property::known(CookieInfo{bytes:cookie.len(),sha256:sha256(&cookie)}),"restricts_random_access":Property::known(false)});
        let manifest = json!({"schema_version":1,"complete":true,"file":info,"start_packet":0,"requested_packets":payloads.len(),
            "actual_packets":payloads.len(),"packet_data_file":"packets.bin","packet_index_file":"packets.jsonl","cookie_file":"cookie.bin",
            "packet_data_bytes":all.len(),"packet_data_sha256":sha256(&all)});
        fs::write(
            path.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
    }
    #[test]
    fn portable_cli_reports_partial_and_refuses_overwrite_and_bad_ranges() {
        let t = Temp::new();
        let input = t.0.join("中文 日本語");
        build(
            &input,
            &[&[0x60, 0, 0xa5], &[0x60, 0x10, 0xa5], &[0x40, 0xff]],
        );
        let out = t.0.join("prefix.jsonl");
        let args = [
            "parse-packets",
            input.to_str().unwrap(),
            "--output",
            out.to_str().unwrap(),
            "--start-packet",
            "1",
            "--packets",
            "9",
        ];
        let result = Command::new(env!("CARGO_BIN_EXE_apac-tool"))
            .args(args)
            .output()
            .unwrap();
        assert_eq!(
            result.status.code(),
            Some(2),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let summary: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
        assert_eq!(summary["actual_packets"], 2);
        assert_eq!(summary["all_prefixes_complete"], true);
        assert_eq!(summary["whole_frame_complete_packets"], 0);
        assert!(!t.0.join("prefix.jsonl.incomplete").exists());
        let contents = fs::read(&out).unwrap();
        assert_eq!(
            Command::new(env!("CARGO_BIN_EXE_apac-tool"))
                .args(args)
                .output()
                .unwrap()
                .status
                .code(),
            Some(1)
        );
        assert_eq!(contents, fs::read(&out).unwrap());
        for (start, count) in [(3, 1), (0, 0), (1, u64::MAX)] {
            assert!(
                parse_packets(&input, &t.0.join("bad.jsonl"), Some(start), count, 1 << 20).is_err()
            );
            assert!(!t.0.join("bad.jsonl").exists());
        }
        let mut reader = PacketBundle::open(&input).unwrap();
        assert_eq!(reader.next_packet().unwrap().unwrap().0.packet_index, 0);
        reader.verify_remaining().unwrap();
    }
    #[test]
    fn syntax_errors_keep_per_packet_results_and_failure_marker() {
        let t = Temp::new();
        let input = t.0.join("packets");
        build(&input, &[&[0x60, 0, 0xa5], &[0x60], &[0x40, 0xff]]);
        let out = t.0.join("prefix.jsonl");
        let result = Command::new(env!("CARGO_BIN_EXE_apac-tool"))
            .args([
                "parse-packets",
                input.to_str().unwrap(),
                "--output",
                out.to_str().unwrap(),
            ])
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(1));
        let summary: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
        assert_eq!(summary["errors"], 1);
        assert_eq!(summary["actual_packets"], 3);
        assert_eq!(summary["complete"], false);
        let rows: Vec<serde_json::Value> = fs::read_to_string(out)
            .unwrap()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect();
        assert_eq!(rows[1]["packet_index"], 1);
        assert_eq!(rows[1]["error"]["bit_offset"], 6);
        assert_eq!(rows[2]["report"]["prefix_complete"], true);
        assert!(t.0.join("prefix.jsonl.incomplete").exists());
    }
    #[test]
    fn quota_and_unselected_corruption_cannot_produce_a_trusted_report() {
        let t = Temp::new();
        let input = t.0.join("packets");
        build(&input, &[&[0x60, 0, 0xa5], &[0x60, 0x10, 0xa5]]);
        assert!(parse_packets(&input, &t.0.join("quota.jsonl"), None, 1, 512).is_err());
        assert!(t.0.join("quota.jsonl.incomplete").exists());
        let mut data = fs::read(input.join("packets.bin")).unwrap();
        *data.last_mut().unwrap() ^= 1;
        fs::write(input.join("packets.bin"), data).unwrap();
        let error = parse_packets(&input, &t.0.join("bad.jsonl"), None, 1, 1 << 20).unwrap_err();
        assert_eq!(error.packet_index, Some(1));
        assert!(!t.0.join("bad.jsonl").exists());
    }
}
