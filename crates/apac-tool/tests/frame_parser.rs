//! Synthetic syntax only: no real media cookie or packet is embedded here.
use apac_core::frame::{FrameContext, FrameReport, parse_frame, parse_packet};
use apac_core::synthesis::SqDecoder;
use apac_research::config::{ParseStatus, parse_cookie};
use apac_research::packets::MAX_PACKET_BUFFER;
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
    cookie_with_metadata(rate, channels, lbr, false)
}
fn cookie_with_metadata(rate: u64, channels: u64, lbr: bool, metadata: bool) -> Vec<u8> {
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
    b.fields(&[(101, 16), (0, 1), (0, 1), (0, 3), (0, 2), (0, 3)]);
    b.put(u64::from(metadata), 1);
    if metadata {
        // Uncompressed renderer configuration, without parameters or groups.
        b.fields(&[(0, 1), (1, 1), (0, 1), (0, 4)]);
    }
    b.fields(&[(0, 1), (0, 1)]); // No custom data or extensions.
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
                    .as_u64s()
                    .unwrap();
                assert_eq!(groups.iter().sum::<u64>(), if short { 8 } else { 1 });
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
        assert_eq!((error.kind, error.bit_offset), ("max-sfb", 6));
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
fn asp_reconfiguration_and_preroll_bounds_are_explicit() {
    let mut bits = Bits::default();
    bits.fields(&[(2, 2), (1, 1)]);
    let bytes = bits.opaque();
    let report = parse_frame(&context(), &bytes).unwrap();
    check_coverage(&bytes, &report);
    assert!(!report.prefix_complete);
    assert!(report.stop_reason.contains("ASP reconfiguration"));
    for (fields, kind) in [
        (vec![(3, 2)], "truncated"),
        (vec![(2, 2), (0, 1), (2, 2)], "preroll-count"),
        (vec![(2, 2), (0, 1), (1, 2), (0, 16)], "preroll-size"),
        (
            vec![(2, 2), (0, 1), (1, 2), (65535, 16), (0, 16)],
            "preroll-size",
        ),
        (vec![(2, 2), (0, 1), (1, 2), (3, 16), (0, 3)], "truncated"),
        (vec![(2, 2), (0, 1), (1, 2), (3, 16), (1, 3)], "truncated"),
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
    // A complete passive declaration does not change the stereo prefix grammar.
    let bytes = cookie_with_metadata(3, 2, false, true);
    assert!(parse_cookie(&bytes).unwrap().is_complete());
    assert!(FrameContext::from_cookie(&bytes).unwrap().is_supported());
}

#[test]
fn renderer_metadata_requires_a_complete_configuration() {
    let bytes = cookie_with_metadata(3, 2, false, true);
    let parsed = parse_cookie(&bytes).unwrap();
    let present = parsed
        .fields
        .iter()
        .find(|f| f.name == "ancillary.metadata.configuration_present")
        .unwrap();
    let mut missing = bytes.clone();
    missing[present.bit_offset / 8] &= !(1 << (7 - present.bit_offset % 8));
    let error = parse_cookie(&missing).unwrap_err();
    assert_eq!(error.kind, "metadata-presence");
    assert_eq!(error.bit_offset, present.bit_offset + 1);
    assert!(FrameContext::from_cookie(&missing).is_err());
    for end in present.bit_offset / 8 + 1..bytes.len() {
        let mut truncated = bytes[..end].to_vec();
        truncated[..4].copy_from_slice(&(end as u32).to_be_bytes());
        let error = parse_cookie(&truncated).unwrap_err();
        assert_eq!(error.kind, "truncated");
        assert!(error.bit_offset <= end * 8);
        assert!(FrameContext::from_cookie(&truncated).is_err());
    }
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
    use apac_research::model::*;
    use apac_research::packets::PacketBundle;
    use apac_research::parse_packets::parse_packets;
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
    fn marker_creation_failure_removes_only_the_new_report() {
        let t = Temp::new();
        let input = t.0.join("packets");
        build(&input, &[&[0x60, 0, 0xa5]]);
        let out = t.0.join("prefix.jsonl");
        let marker = t.0.join("prefix.jsonl.incomplete");
        let old_marker = b"existing incomplete report";
        fs::write(&marker, old_marker).unwrap();

        assert!(parse_packets(&input, &out, None, 1, 1 << 20).is_err());
        assert!(!out.exists());
        assert_eq!(fs::read(&marker).unwrap(), old_marker);

        fs::remove_file(&marker).unwrap();
        assert_eq!(parse_packets(&input, &out, None, 1, 1 << 20).unwrap().1, 2);
        let contents = fs::read(&out).unwrap();
        fs::write(&marker, old_marker).unwrap();
        assert!(parse_packets(&input, &out, None, 1, 1 << 20).is_err());
        assert_eq!(fs::read(&out).unwrap(), contents);
        assert_eq!(fs::read(&marker).unwrap(), old_marker);
    }
    #[cfg(unix)]
    #[test]
    fn overlong_marker_name_does_not_leave_an_unmarked_report() {
        let t = Temp::new();
        let input = t.0.join("packets");
        build(&input, &[&[0x60, 0, 0xa5]]);
        let filename = format!("{}.jsonl", "x".repeat(249));
        let out = t.0.join(&filename);
        // The report name fits NAME_MAX; only the appended marker suffix fails.
        fs::write(&out, b"probe").unwrap();
        fs::remove_file(&out).unwrap();

        let error = parse_packets(&input, &out, None, 1, 1 << 20).unwrap_err();
        assert!(error.operation.contains(&format!("{filename}.incomplete")));
        assert!(!out.exists());
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

mod spectrum_tests {
    use super::*;
    use apac_core::frame::parse_spectrum;

    fn book(name: &str) -> serde_json::Value {
        serde_json::from_str::<serde_json::Value>(include_str!("../../../data/sq-codebooks.json"))
            .unwrap()[name]
            .clone()
    }
    fn delta(b: &mut Bits, value: i16) {
        let table = book("scalefactor");
        let i = (value + 60) as usize;
        b.put(
            table["codes"][i].as_u64().unwrap(),
            table["bits"][i].as_u64().unwrap() as usize,
        );
    }
    fn left(max_sfb: u64, gain: u64) -> Bits {
        let mut b = Bits::default();
        b.fields(&[(1, 2), (1, 1), (0, 1), (0, 2), (max_sfb, 6), (gain, 8)]);
        b
    }
    #[test]
    fn spectrum_completion_is_separate_and_shared_ics_preserves_left_only() {
        for shared in [0, 1] {
            let mut b = left(1, 160);
            b.fields(&[(0, 4), (1, 5), (shared, 1)]);
            if shared == 0 {
                b.fields(&[(2, 2), (0, 4), (0x55, 7), (160, 8)]);
            }
            let end = b.1;
            let bytes = b.opaque();
            let parsed = parse_spectrum(&context(), &bytes).unwrap();
            assert_eq!(parsed.numeric_profile.as_deref(), Some("apac-sq-math-v1"));
            check_coverage(&bytes, &parsed.frame);
            assert_eq!(parsed.frame.payload_bit_offset, Some(12));
            assert_eq!(parsed.frame.stop_bit_offset, end);
            assert_eq!(parsed.spectrum_complete, shared == 0);
            assert_eq!(parsed.channels.len(), if shared == 0 { 2 } else { 1 });
            assert!(
                parsed
                    .channels
                    .iter()
                    .all(|c| c.quantized == vec![0; 1024] && c.scaled == vec![0.; 1024])
            );
            assert_eq!(
                parsed.frame.stop_reason,
                if shared == 0 {
                    "sq_spectra_before_tools"
                } else {
                    "shared_ics_cac_deferred"
                }
            );
        }
        for bytes in [&[0x40u8][..], &[0x70][..], &[0xa0][..]] {
            let parsed = parse_spectrum(&context(), bytes).unwrap();
            assert!(parsed.channels.is_empty());
            assert!(!parsed.spectrum_complete);
        }
    }
    #[test]
    fn invalid_sections_scalefactors_and_right_truncations_are_errors() {
        for (cb, length, kind) in [
            (12, 1, "codebook"),
            (1, 0, "section-length"),
            (1, 2, "section-length"),
        ] {
            let mut b = left(1, 160);
            b.fields(&[(cb, 4), (length, 5)]);
            let e = parse_spectrum(&context(), &b.opaque()).unwrap_err();
            assert_eq!(e.kind, kind);
            assert!(e.bit_offset >= 20);
        }
        for (gain, deltas) in [(255, vec![1]), (0, vec![-60, -60, -60, -60, -17])] {
            let mut b = left(deltas.len() as u64, gain);
            b.fields(&[(1, 4), (deltas.len() as u64, 5)]);
            for d in deltas {
                delta(&mut b, d);
            }
            assert_eq!(
                parse_spectrum(&context(), &b.opaque()).unwrap_err().kind,
                "scale-factor"
            );
        }
        // A prefix can be complete while the spectral body is truncated.
        assert!(
            parse_frame(&context(), &[0x60, 0x10])
                .unwrap()
                .prefix_complete
        );
        assert_eq!(
            parse_spectrum(&context(), &[0x60, 0x10]).unwrap_err().kind,
            "truncated"
        );
        let mut b = left(0, 160);
        b.fields(&[
            (0, 1),
            (2, 2),
            (14, 4),
            (0, 7),
            (160, 8),
            (0, 4),
            (7, 3),
            (7, 3),
            (0, 3),
        ]);
        let bytes = b.opaque();
        for end in 2..bytes.len() - 1 {
            assert!(parse_spectrum(&context(), &bytes[..end]).is_err());
        }
    }
    #[test]
    fn minimum_scale_factor_is_accepted_without_saturation() {
        let mut b = left(5, 0);
        b.fields(&[(1, 4), (5, 5)]);
        for d in [-60, -60, -60, -60, -16] {
            delta(&mut b, d);
        }
        let spectral = book("spectral");
        // Five four-line bands, each with the all-zero signed tuple.
        for _ in 0..5 {
            b.put(
                spectral[0]["codes"][40].as_u64().unwrap(),
                spectral[0]["bits"][40].as_u64().unwrap() as usize,
            );
        }
        b.put(1, 1);
        let report = parse_spectrum(&context(), &b.opaque()).unwrap();
        assert_eq!(
            report.channels[0].scale_factors[0],
            vec![Some(-60), Some(-120), Some(-180), Some(-240), Some(-256)]
        );
        assert_eq!(report.channels[0].scaled, vec![0.; 1024]);
    }
    #[test]
    fn spectrum_mutations_never_panic_or_claim_whole_frames() {
        for value in 0..=u16::MAX {
            let mut bytes = value.to_be_bytes().to_vec();
            bytes.extend([0; 16]);
            match parse_spectrum(&context(), &bytes) {
                Ok(report) => {
                    check_coverage(&bytes, &report.frame);
                    assert!(report.channels.len() <= 2);
                }
                Err(error) => assert!(error.bit_offset <= bytes.len() * 8),
            }
        }
    }
}

mod synthesis_tests {
    use super::*;
    use apac_core::frame::parse_spectrum;
    use apac_core::synthesis::SqDecoder;
    fn packet(block: u64, active: bool) -> Vec<u8> {
        let mut b = Bits::default();
        b.fields(&[
            (1, 2),
            (1, 1),
            (0, 1),
            (block, 2),
            (u64::from(active), if block == 2 { 4 } else { 6 }),
        ]);
        if block == 2 {
            b.put(0x7f, 7);
        }
        b.put(160, 8);
        if active {
            b.fields(&[(1, 4), (1, if block == 2 { 3 } else { 5 }), (0, 1)]);
            let books: serde_json::Value =
                serde_json::from_str(include_str!("../../../data/sq-codebooks.json")).unwrap();
            for _ in 0..if block == 2 { 8 } else { 1 } {
                b.put(
                    books["spectral"][0]["codes"][67].as_u64().unwrap(),
                    books["spectral"][0]["bits"][67].as_u64().unwrap() as usize,
                );
            }
        }
        b.fields(&[(0, 1), (block, 2), (0, if block == 2 { 4 } else { 6 })]);
        if block == 2 {
            b.put(0, 7);
        }
        b.fields(&[(160, 8), (0, 4)]);
        b.put(0, (8 - b.1 % 8) % 8);
        b.put(0, 8);
        b.0
    }
    #[test]
    fn independent_pcm_keeps_channels_and_window_history() {
        let mut decoder = SqDecoder::from_cookie(&cookie(3, 2, false)).unwrap();
        let mut nonzero = 0;
        for block in [0, 1, 2, 2, 3, 0, 0] {
            let samples = decoder.decode_frame(&packet(block, true)).unwrap();
            assert_eq!(samples.len(), 2048);
            assert!(samples.iter().all(|s| s.is_finite()));
            assert!(samples.iter().skip(1).step_by(2).all(|s| *s == 0.));
            nonzero += samples.iter().filter(|v| **v != 0.).count();
        }
        assert!(nonzero > 1024);
    }
    #[test]
    fn errors_do_not_advance_overlap_and_reset_restarts_exactly() {
        let c = cookie(3, 2, false);
        let mut a = SqDecoder::from_cookie(&c).unwrap();
        let mut b = SqDecoder::from_cookie(&c).unwrap();
        let active = packet(0, true);
        let zero = packet(0, false);
        let original = a.decode_frame(&active).unwrap();
        b.decode_frame(&active).unwrap();
        // APAC permits this transition; the following malformed payload must
        // still leave the now-short-window overlap unchanged.
        assert_eq!(
            a.decode_frame(&packet(2, true)).unwrap(),
            b.decode_frame(&packet(2, true)).unwrap()
        );
        let mut bad = active.clone();
        let bit = parse_spectrum(&context(), &bad)
            .unwrap()
            .frame
            .stop_bit_offset;
        bad[bit / 8] |= 1 << (7 - bit % 8);
        assert!(a.decode_frame(&bad).is_err());
        assert_eq!(
            a.decode_frame(&zero).unwrap(),
            b.decode_frame(&zero).unwrap()
        );
        a.reset();
        assert_eq!(a.decode_frame(&active).unwrap(), original);
    }
    #[test]
    fn active_tools_truncation_and_extra_tail_are_rejected() {
        let c = cookie(3, 2, false);
        let good = packet(0, true);
        let stop = parse_spectrum(&context(), &good)
            .unwrap()
            .frame
            .stop_bit_offset;
        // The right stream has max_sfb=0: its enabled flag now legally carries
        // no payload. The left flag requires LSF data, which this packet lacks.
        for bit in stop + 2..stop + 3 {
            let mut bad = good.clone();
            bad[bit / 8] |= 1 << (7 - bit % 8);
            let error = SqDecoder::from_cookie(&c)
                .unwrap()
                .decode_frame(&bad)
                .unwrap_err();
            let data_start = stop + 4;
            let expected = data_start
                + if good.len() * 8 - data_start >= 9 {
                    9
                } else {
                    0
                };
            assert_eq!(error.bit_offset, Some(expected));
        }
        for end in 0..good.len() {
            assert!(
                SqDecoder::from_cookie(&c)
                    .unwrap()
                    .decode_frame(&good[..end])
                    .is_err()
            );
        }
        let mut extra = good.clone();
        extra.push(0);
        assert!(
            SqDecoder::from_cookie(&c)
                .unwrap()
                .decode_frame(&extra)
                .is_err()
        );
        let mut shared = good.clone();
        let left_end = parse_spectrum(&context(), &good).unwrap().channels[0].end_bit_offset;
        shared[left_end / 8] |= 1 << (7 - left_end % 8);
        assert!(
            SqDecoder::from_cookie(&c)
                .unwrap()
                .decode_frame(&shared)
                .is_err()
        );
        assert!(
            SqDecoder::from_cookie(&c)
                .unwrap()
                .decode_frame(&[0x40])
                .is_err()
        );
        assert!(
            SqDecoder::from_cookie(&c)
                .unwrap()
                .decode_frame(&[0x70])
                .is_err()
        );
    }
    #[test]
    fn unverified_configurations_cannot_enter_pcm_synthesis() {
        for data in [cookie(5, 2, false), cookie(3, 8, false), cookie(3, 2, true)] {
            assert!(SqDecoder::from_cookie(&data).is_err());
        }
        let mut c = cookie(3, 2, false);
        let parsed = parse_cookie(&c).unwrap();
        let offset = parsed
            .fields
            .iter()
            .find(|f| f.name == "ancillary.metadata_present")
            .unwrap()
            .bit_offset;
        c[offset / 8] |= 1 << (7 - offset % 8);
        assert!(SqDecoder::from_cookie(&c).is_err());
    }
}

mod cac_tests {
    use super::*;
    use apac_core::frame::{parse_cac, parse_spectrum};
    use apac_core::synthesis::SqDecoder;

    pub(super) fn packet(block: u64, gain: usize) -> Vec<u8> {
        let books: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/sq-codebooks.json")).unwrap();
        let cac: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/cac-codebooks.json")).unwrap();
        let mut b = Bits::default();
        let short = block == 2;
        b.fields(&[
            (1, 2),
            (1, 1),
            (0, 1),
            (block, 2),
            (1, if short { 4 } else { 6 }),
        ]);
        if short {
            b.put(0x55, 7);
        }
        b.put(160, 8);
        for _ in 0..if short { 4 } else { 1 } {
            b.fields(&[(1, 4), (1, if short { 3 } else { 5 })]);
            b.put(
                books["scalefactor"]["codes"][60].as_u64().unwrap(),
                books["scalefactor"]["bits"][60].as_u64().unwrap() as usize,
            );
        }
        for _ in 0..if short { 8 } else { 1 } {
            b.put(
                books["spectral"][0]["codes"][67].as_u64().unwrap(),
                books["spectral"][0]["bits"][67].as_u64().unwrap() as usize,
            );
        }
        b.fields(&[(1, 1), (160, 8)]);
        for _ in 0..if short { 4 } else { 1 } {
            b.fields(&[(0, 4), (1, if short { 3 } else { 5 })]);
        }
        b.put(
            cac["gain"]["codes"][gain].as_u64().unwrap(),
            cac["gain"]["bits"][gain].as_u64().unwrap() as usize,
        );
        b.put(0, 4); // terminal repeat
        b.put(0, 4); // TNS/BWE2 flags
        b.put(0, (8 - b.1 % 8) % 8);
        b.put(0, 8);
        b.0
    }
    #[test]
    fn cac_preserves_legacy_spectrum_and_exposes_both_stages() {
        for block in [0, 1, 2, 3] {
            let bytes = packet(block, 9);
            let original = parse_spectrum(&context(), &bytes).unwrap();
            assert_eq!(original.channels.len(), 1);
            assert_eq!(original.frame.stop_reason, "shared_ics_cac_deferred");
            let result = parse_cac(&context(), &bytes).unwrap();
            assert!(result.cac_complete && result.spectrum.spectrum_complete && result.shared_ics);
            check_coverage(&bytes, &result.spectrum.frame);
            assert_eq!(
                result.spectrum.channels[0].quantized,
                original.channels[0].quantized
            );
            assert!(
                result.spectrum.channels[1]
                    .quantized
                    .iter()
                    .all(|v| *v == 0)
            );
            assert_eq!(
                result.channels_after_cac[0].scaled,
                result.channels_after_cac[1].scaled
            );
            assert_eq!(
                result.channels_after_cac[0].scaled[0],
                (32768. * std::f64::consts::FRAC_1_SQRT_2) as f32
            );
            assert_eq!(
                result.spectrum.frame.stop_bit_offset,
                result.cac.as_ref().unwrap().end_bit_offset
            );
        }
        let absent = parse_cac(&context(), &[0x40]).unwrap();
        assert!(!absent.cac_complete && absent.channels_after_cac.is_empty());
        let deferred = parse_cac(&context(), &[0x70]).unwrap();
        assert!(!deferred.cac_complete && deferred.channels_after_cac.is_empty());
    }
    #[test]
    fn cac_errors_leave_overlap_untouched_and_reset_is_exact() {
        let cookie = cookie(3, 2, false);
        let mut actual = SqDecoder::from_cookie(&cookie).unwrap();
        let mut reference = SqDecoder::from_cookie(&cookie).unwrap();
        let first = actual.decode_frame(&packet(0, 9)).unwrap();
        reference.decode_frame(&packet(0, 9)).unwrap();
        let mut bad = packet(0, 34);
        let end = parse_cac(&context(), &bad)
            .unwrap()
            .spectrum
            .frame
            .stop_bit_offset;
        let bwe = end + 2;
        bad[bwe / 8] |= 1 << (7 - bwe % 8);
        let data_start = bwe + 2;
        let expected_bit = data_start
            + if bad.len() * 8 - data_start >= 9 {
                9
            } else {
                0
            };
        assert_eq!(
            actual.decode_frame(&bad).unwrap_err().bit_offset,
            Some(expected_bit)
        );
        assert_eq!(
            actual.decode_frame(&packet(2, 26)).unwrap(),
            reference.decode_frame(&packet(2, 26)).unwrap()
        );
        assert!(actual.decode_frame(&bad[..bad.len() / 2]).is_err());
        assert_eq!(
            actual.decode_frame(&packet(0, 26)).unwrap(),
            reference.decode_frame(&packet(0, 26)).unwrap()
        );
        actual.reset();
        assert_eq!(actual.decode_frame(&packet(0, 9)).unwrap(), first);
    }
    #[test]
    fn cac_truncation_tools_and_bit_mutations_preserve_boundaries() {
        let good = packet(0, 34);
        let report = parse_cac(&context(), &good).unwrap();
        let start = report.cac.as_ref().unwrap().start_bit_offset;
        let end = report.spectrum.frame.stop_bit_offset;
        let cookie = cookie(3, 2, false);
        for cut in 0..good.len() {
            assert!(
                SqDecoder::from_cookie(&cookie)
                    .unwrap()
                    .decode_frame(&good[..cut])
                    .is_err()
            );
        }
        for bit in end + 2..end + 4 {
            let mut bad = good.clone();
            bad[bit / 8] |= 1 << (7 - bit % 8);
            let data_start = end + 4;
            let expected_bit = data_start
                + if bad.len() * 8 - data_start >= 9 {
                    9
                } else {
                    0
                };
            assert_eq!(
                SqDecoder::from_cookie(&cookie)
                    .unwrap()
                    .decode_frame(&bad)
                    .unwrap_err()
                    .bit_offset,
                Some(expected_bit)
            );
        }
        for bit in start..good.len() * 8 {
            let mut changed = good.clone();
            changed[bit / 8] ^= 1 << (7 - bit % 8);
            match parse_cac(&context(), &changed) {
                Ok(r) => {
                    check_coverage(&changed, &r.spectrum.frame);
                    assert!(
                        r.channels_after_cac
                            .iter()
                            .flat_map(|c| &c.scaled)
                            .all(|v| v.is_finite())
                    );
                }
                Err(e) => assert!(e.bit_offset <= changed.len() * 8),
            }
        }
    }
}

mod tns_tests {
    use super::*;
    use apac_core::frame::{parse_cac, parse_tns};
    use apac_core::synthesis::SqDecoder;
    fn packet(order: u64, length: u64, bwe: u64) -> Vec<u8> {
        let source = super::cac_tests::packet(0, 9);
        let end = parse_cac(&context(), &source)
            .unwrap()
            .spectrum
            .frame
            .stop_bit_offset;
        let mut b = Bits::default();
        for i in 0..end {
            b.put(u64::from((source[i / 8] >> (7 - i % 8)) & 1), 1);
        }
        b.fields(&[(1, 1), (1, 2), (1, 1), (length, 6), (order, 5)]);
        if order > 0 {
            b.fields(&[(0, 1), (0, 1)]);
            for _ in 0..order {
                b.put(1, 4);
            }
        }
        b.fields(&[(0, 1), (bwe, 1), (0, 1)]);
        b.put(0, (8 - b.1 % 8) % 8);
        b.put(0, 8);
        b.0
    }
    #[test]
    fn filtered_spectra_and_pcm_errors_are_transactional_and_reset_exactly() {
        let cookie = cookie(3, 2, false);
        let mut actual = SqDecoder::from_cookie(&cookie).unwrap();
        let mut expected = SqDecoder::from_cookie(&cookie).unwrap();
        let good = packet(3, 49, 0);
        let parsed = parse_tns(&context(), &good).unwrap();
        check_coverage(&good, &parsed.cac.spectrum.frame);
        assert!(parsed.tns_complete);
        assert_ne!(
            parsed.channels_after_tns[0].scaled,
            parsed.cac.channels_after_cac[0].scaled
        );
        let first = actual.decode_frame(&good).unwrap();
        assert_eq!(first, expected.decode_frame(&good).unwrap());
        for bad in [
            packet(1, 0, 0),
            packet(13, 49, 0),
            packet(3, 49, 1),
            good[..good.len() - 2].to_vec(),
        ] {
            assert!(actual.decode_frame(&bad).is_err());
        }
        assert_eq!(
            actual.decode_frame(&good).unwrap(),
            expected.decode_frame(&good).unwrap()
        );
        actual.reset();
        assert_eq!(actual.decode_frame(&good).unwrap(), first);
    }
    #[test]
    fn configuration_errors_name_fields_values_and_cookie_positions() {
        let bytes = cookie(5, 2, false);
        let result = SqDecoder::from_cookie(&bytes).err().unwrap();
        assert_eq!(result.operation, "SQ decoder");
        assert!(
            result
                .message
                .contains("global.sample_rate_index=5 at cookie bit")
        );
        let bytes = cookie_with_metadata(3, 2, false, true);
        let fields = parse_cookie(&bytes).unwrap().fields;
        let bit = fields
            .iter()
            .find(|f| f.name == "ancillary.metadata_present")
            .unwrap()
            .bit_offset;
        let error = SqDecoder::from_cookie(&bytes).err().unwrap();
        assert!(error.message.contains(&format!(
            "ancillary.metadata_present=true at cookie bit {bit}"
        )));
    }
}

mod bwe2_tests {
    use super::*;
    use apac_core::frame::parse_bwe2;
    use apac_core::synthesis::SqDecoder;
    fn bytes(hex: &str) -> Vec<u8> {
        let (pairs, remainder) = hex.as_bytes().as_chunks::<2>();
        assert!(remainder.is_empty());
        pairs
            .iter()
            .map(|p| u8::from_str_radix(std::str::from_utf8(p).unwrap(), 16).unwrap())
            .collect()
    }
    #[test]
    fn bwe2_tail_errors_roll_back_and_all_window_types_reset_exactly() {
        // Reproducible fixtures: bwe2_vectors.packet(source_case()), then
        // independent right parameters [1,2], and source_case(2,0x55).
        let active = bytes("614640988442c814808000080000");
        let independent = bytes("61464098844028c81310881800010004050000");
        let short = bytes("696ab204120824104820942108421085902850a16020000208208000");
        let cookie = cookie(3, 2, false);
        let mut actual = SqDecoder::from_cookie(&cookie).unwrap();
        let mut expected = SqDecoder::from_cookie(&cookie).unwrap();
        let report = parse_bwe2(&context(), &active).unwrap();
        assert!(report.bwe2_complete);
        assert!(report.channels_after_bwe2[0].processing_applied);
        check_coverage(&active, &report.tns.cac.spectrum.frame);
        let first = actual.decode_frame(&active).unwrap();
        assert_eq!(first, expected.decode_frame(&active).unwrap());
        let mut tail = active.clone();
        *tail.last_mut().unwrap() = 0x80;
        assert!(actual.decode_frame(&tail).is_err());
        assert!(actual.decode_frame(&active[..active.len() - 2]).is_err());
        assert_eq!(
            actual.decode_frame(&short).unwrap(),
            expected.decode_frame(&short).unwrap()
        );
        assert_eq!(
            actual.decode_frame(&independent).unwrap(),
            expected.decode_frame(&independent).unwrap()
        );
        assert_eq!(
            actual.decode_frame(&active).unwrap(),
            expected.decode_frame(&active).unwrap()
        );
        actual.reset();
        assert_eq!(actual.decode_frame(&active).unwrap(), first);
    }
}

// Deliberately independent wire construction for the stateful library contract.
fn packet_test_core(bits: &mut Bits, present: bool) {
    bits.put(u64::from(present), 1);
    if !present {
        return;
    }
    bits.fields(&[(0, 1), (0, 2), (1, 6), (160, 8), (1, 4), (1, 5)]);
    let tables: serde_json::Value =
        serde_json::from_str(include_str!("../../../data/sq-codebooks.json")).unwrap();
    let sf = &tables["scalefactor"];
    bits.put(
        sf["codes"][60].as_u64().unwrap(),
        sf["bits"][60].as_u64().unwrap() as usize,
    );
    let book = &tables["spectral"][0];
    let index = 2 * 27 + 9 + 3 + 1; // Codebook 1 tuple [1, 0, 0, 0].
    bits.put(
        book["codes"][index].as_u64().unwrap(),
        book["bits"][index].as_u64().unwrap() as usize,
    );
    bits.fields(&[(0, 1), (0, 2), (0, 6), (160, 8), (0, 4)]); // Independent right, no bands, TNS/BWE2 off.
}
fn packet_test_frame(present: bool, internal: Option<&[u8]>) -> Vec<u8> {
    let mut bits = Bits::default();
    if let Some(frame) = internal {
        bits.fields(&[(2, 2), (0, 1), (1, 2), (frame.len() as u64, 16)]);
        while bits.1 % 8 != 0 {
            bits.put(0, 1);
        }
        for &byte in frame {
            bits.put(u64::from(byte), 8);
        }
    } else {
        bits.put(1, 2);
    }
    packet_test_core(&mut bits, present);
    while bits.1 % 8 != 0 {
        bits.put(0, 1);
    }
    bits.put(0, 8); // No scene configuration: trimming flag and final padding.
    bits.0
}

#[test]
fn complete_packet_absence_preserves_empty_spectral_reports() {
    let bytes = packet_test_frame(false, None);
    let old = parse_frame(&context(), &bytes).unwrap();
    assert_eq!(old.stop_reason, "cpe_absent");
    assert_eq!(old.status, ParseStatus::Partial);
    let packet = parse_packet(&context(), &bytes).unwrap();
    assert!(packet.packet_complete && packet.cpe_absent());
    assert_eq!(packet.frame().status, ParseStatus::Complete);
    assert_eq!(packet.frame().component_end_bit_offset, Some(8));
    assert!(packet.bwe2.tns.cac.spectrum.channels.is_empty());
    assert!(!packet.bwe2.bwe2_complete);
    assert!(packet.frame().unknown_ranges.is_empty());
}

#[test]
fn asp_type_aliases_and_preroll_padding_preserve_complete_packets() {
    let config = cookie(3, 2, false);
    let active = packet_test_frame(true, None);
    let absent = packet_test_frame(false, None);
    let mut control = SqDecoder::from_cookie(&config).unwrap();
    let expected = control.decode_frame(&active).unwrap();
    let expected_tail = control.decode_frame(&absent).unwrap();
    for code in [0u8, 1, 3] {
        let mut frame = active.clone();
        frame[0] = (frame[0] & 0x3f) | (code << 6);
        let packet = parse_packet(&context(), &frame).unwrap();
        assert!(packet.packet_complete);
        if code == 3 {
            assert_eq!(
                packet.frame().derived["asp.frame_type_profile"],
                "apac-asp-boundaries-v1"
            );
        }
        let mut decoder = SqDecoder::from_cookie(&config).unwrap();
        assert_eq!(decoder.decode_frame(&frame).unwrap(), expected);
        for padding in [0u8, 1, 7] {
            let mut outer = packet_test_frame(false, Some(&frame));
            outer[2] = (outer[2] & !7) | padding;
            let packet = parse_packet(&context(), &outer).unwrap();
            assert!(packet.packet_complete);
            assert!(
                packet
                    .embedded_preroll
                    .as_ref()
                    .unwrap()
                    .report
                    .packet_complete
            );
            let field = packet
                .frame()
                .fields
                .iter()
                .find(|f| f.name == "asp.preroll.alignment_padding")
                .unwrap();
            assert_eq!(field.value, padding);
            if padding != 0 {
                assert_eq!(
                    packet.frame().derived["asp.alignment_profile"],
                    "apac-asp-boundaries-v1"
                );
            }
            let mut decoder = SqDecoder::from_cookie(&config).unwrap();
            assert_eq!(decoder.decode_frame(&outer).unwrap(), expected_tail);
        }
    }
}

#[test]
fn missing_element_outputs_the_tail_once_and_reset_clears_it() {
    let config = cookie(3, 2, false);
    let mut decoder = SqDecoder::from_cookie(&config).unwrap();
    let active = packet_test_frame(true, None);
    let absent = packet_test_frame(false, None);
    decoder.decode_frame(&active).unwrap();
    let tail = decoder.decode_frame(&absent).unwrap();
    assert!(tail.iter().any(|v| *v != 0.));
    assert!(tail.iter().skip(1).step_by(2).all(|v| v.to_bits() == 0));
    assert!(
        decoder
            .decode_frame(&absent)
            .unwrap()
            .iter()
            .all(|v| v.to_bits() == 0)
    );
    decoder.decode_frame(&active).unwrap();
    decoder.reset();
    assert!(
        decoder
            .decode_frame(&absent)
            .unwrap()
            .iter()
            .all(|v| v.to_bits() == 0)
    );
}

#[test]
fn invalid_outer_tail_and_invalid_internal_frame_do_not_commit_state() {
    let config = cookie(3, 2, false);
    let active = packet_test_frame(true, None);
    let absent = packet_test_frame(false, None);
    let mut decoder = SqDecoder::from_cookie(&config).unwrap();
    let mut control = SqDecoder::from_cookie(&config).unwrap();
    decoder.decode_frame(&active).unwrap();
    control.decode_frame(&active).unwrap();
    let mut bad = packet_test_frame(true, Some(&active));
    *bad.last_mut().unwrap() = 0x80;
    assert!(decoder.decode_frame(&bad).is_err());
    let bad_inner = packet_test_frame(true, Some(&[0x60]));
    let error = decoder.decode_frame(&bad_inner).unwrap_err();
    assert!(error.message.contains("embedded preroll"));
    assert_eq!(error.bit_offset, Some(30));
    let after = decoder.decode_frame(&absent).unwrap();
    let expected = control.decode_frame(&absent).unwrap();
    assert_eq!(
        after.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
        expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>()
    );
}

#[test]
fn internal_preroll_emits_only_current_pcm_and_has_bounded_coordinates() {
    let config = cookie(3, 2, false);
    let active = packet_test_frame(true, None);
    let absent = packet_test_frame(false, None);
    let outer = packet_test_frame(false, Some(&active));
    let report = parse_packet(&context(), &outer).unwrap();
    assert!(report.packet_complete);
    let internal = report.embedded_preroll.as_ref().unwrap();
    assert_eq!(internal.start_bit_offset, 24);
    assert_eq!(internal.end_bit_offset, 24 + active.len() * 8);
    assert!(internal.report.packet_complete);
    assert_eq!(internal.report.frame().packet_bytes, active.len());
    let mut expected = SqDecoder::from_cookie(&config).unwrap();
    expected.decode_frame(&active).unwrap();
    let expected = expected.decode_frame(&absent).unwrap();
    let mut decoder = SqDecoder::from_cookie(&config).unwrap();
    let output = decoder.decode_frame(&outer).unwrap();
    assert_eq!(output.len(), 2048);
    assert_eq!(
        output.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
        expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>()
    );
}
