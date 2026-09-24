use macindecode_apac_tools::{model::*, packets::*};
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "apac-packet-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn build(&self, start: u64, target: u64, end: u64, preroll: bool) {
        // Deliberately unsupported outer cookie syntax: the portable reader is
        // allowed to validate packets without implementing every APAC config.
        let cookie = b"\x00\x00\x00\x0ctest\x00\x00\x00\x00";
        fs::write(self.0.join("cookie.bin"), cookie).unwrap();
        let info = FileInfo {
            schema_version: 1,
            source: PathBuf::from("missing-original.caf"),
            file_bytes: 5,
            modified_unix_seconds: None,
            environment: Environment {
                tool_version: "test".into(),
                os: "test".into(),
                architecture: "test".into(),
                system_version: "test".into(),
            },
            container: Property::known("caff".into()),
            format: AudioFormat {
                sample_rate: 48000.,
                format_id: u32::from_be_bytes(*b"apac"),
                format_fourcc: "apac".into(),
                flags: 0,
                bytes_per_packet: 0,
                frames_per_packet: 4,
                bytes_per_frame: 0,
                channels: 2,
                bits_per_channel: 0,
            },
            layout: Property::known(ChannelLayout::tagged((101 << 16) | 2, 2, None)),
            packet_count: Property::known(5),
            max_packet_bytes: Property::known(1),
            packet_table: Property::known(PacketTable {
                priming_frames: 3,
                valid_frames: 14,
                remainder_frames: 3,
            }),
            cookie: Property::known(CookieInfo {
                bytes: cookie.len(),
                sha256: sha256(cookie),
            }),
            restricts_random_access: Property::known(true),
        };
        let data: Vec<_> = (start..end).map(|n| n as u8 + 10).collect();
        fs::write(self.0.join("packets.bin"), &data).unwrap();
        let rows: Vec<_>=(start..end).map(|n|json!({"schema_version":1,"packet_index":n,"export_offset":n-start,"bytes":1,"frames":4,
            "sha256":sha256(&[n as u8+10]),"raw_frame_position":Property::known((n*4) as i64),
            "dependency":Property::known(DependencyInfo {independently_decodable:true,preroll_packet_count:u32::from(n!=0)}),
            "roll_distance":Property::known(i64::from(n!=0))})).collect();
        self.rows(&rows);
        let mut manifest = json!({"schema_version":1,"complete":true,"file":info,"start_packet":start,
            "requested_packets":end-target,"actual_packets":end-start,"packet_data_file":"packets.bin","packet_index_file":"packets.jsonl",
            "cookie_file":"cookie.bin","packet_data_bytes":data.len(),"packet_data_sha256":sha256(&data)});
        if preroll {
            manifest["replay_window"] = json!(ReplayWindow {
                requested_start_packet: target,
                requested_packets: end - target,
                actual_target_packets: end - target,
                included_preroll_packets: target - start,
                target_raw_start: target * 4,
                target_raw_end: end * 4
            });
        }
        self.manifest(&manifest);
    }
    fn manifest(&self, v: &Value) {
        fs::write(self.0.join("manifest.json"), serde_json::to_vec(v).unwrap()).unwrap();
    }
    fn rows(&self, v: &[Value]) {
        fs::write(
            self.0.join("packets.jsonl"),
            v.iter().map(|v| format!("{v}\n")).collect::<String>(),
        )
        .unwrap();
    }
    fn read_manifest(&self) -> Value {
        serde_json::from_slice(&fs::read(self.0.join("manifest.json")).unwrap()).unwrap()
    }
    fn read_rows(&self) -> Vec<Value> {
        fs::read_to_string(self.0.join("packets.jsonl"))
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn legacy_origin_and_replay_windows_use_packet_table_not_fixed_delays() {
    let t = Temp::new();
    t.build(0, 0, 5, false);
    let mut bundle = PacketBundle::open(&t.0).unwrap();
    assert!(bundle.next_batch(0).is_err());
    assert!(bundle.next_batch(65).is_err());
    let range = bundle.range(None, 8192).unwrap();
    assert_eq!(
        (
            range.start_frame,
            range.frames,
            range.raw_start,
            range.raw_end
        ),
        (0, 14, 3, 17)
    );
    assert_eq!(range.clipped_by, Some("source_eof"));
    assert!(range.drain_to_eof);
    assert_eq!(bundle.next_batch(1).unwrap().data, vec![10]);
    assert_eq!(bundle.next_batch(3).unwrap().packets.len(), 3);
    assert_eq!(bundle.next_batch(64).unwrap().data, vec![14]);
    assert!(bundle.next_batch(64).unwrap().packets.is_empty());
    assert_eq!(bundle.consumed_frames(), 20);
    t.build(1, 2, 4, true);
    let bundle = PacketBundle::open(&t.0).unwrap();
    let range = bundle.range(None, 10).unwrap();
    assert_eq!((range.start_frame, range.frames), (5, 8));
    assert_eq!(range.clipped_by, Some("window_end"));
    let exact = bundle.range(Some(6), 2).unwrap();
    assert_eq!((exact.raw_start, exact.raw_end), (9, 11));
    assert!(!exact.drain_to_eof);
    assert!(bundle.range(Some(4), 1).is_err());
    assert!(bundle.range(Some(14), 1).is_err());
    assert!(bundle.range(None, 0).is_err());
    assert!(bundle.range(None, u64::MAX).is_err());
    assert_eq!(bundle.range(Some(13), 1).unwrap().frames, 0);
}

#[test]
fn integrity_failures_and_missing_time_never_become_silent_zero_values() {
    let t = Temp::new();
    t.build(0, 0, 5, false);
    let original = t.read_manifest();
    let rows = t.read_rows();
    for (key, value) in [
        ("schema_version", json!(2)),
        ("complete", json!(false)),
        ("actual_packets", json!(4)),
        ("packet_data_bytes", json!(100)),
        ("packet_data_sha256", json!("0".repeat(64))),
    ] {
        let mut m = original.clone();
        m[key] = value;
        t.manifest(&m);
        assert!(PacketBundle::open(&t.0).is_err(), "{key}");
    }
    t.manifest(&original);
    for (key, value) in [
        ("packet_index", json!(4)),
        ("export_offset", json!(3)),
        ("bytes", json!(16777217)),
        ("frames", json!(0)),
        ("sha256", json!("0".repeat(64))),
        ("raw_frame_position", json!(Property::known(9i64))),
    ] {
        let mut changed = rows.clone();
        changed[1][key] = value;
        t.rows(&changed);
        assert!(PacketBundle::open(&t.0).is_err(), "{key}");
    }
    let mut changed = rows.clone();
    changed[1]["raw_frame_position"] = json!({"value":null,"error":{"operation":"pkfr","os_status":-50,"message":"not available"}});
    t.rows(&changed);
    assert_eq!(PacketBundle::open(&t.0).err().unwrap().os_status, Some(-50));
    t.rows(&rows[..4]);
    assert!(PacketBundle::open(&t.0).is_err());
    t.rows(&rows);
    fs::write(t.0.join("packets.bin"), [10, 11]).unwrap();
    assert!(PacketBundle::open(&t.0).is_err());
    t.build(0, 0, 5, false);
    fs::write(t.0.join(".incomplete.json"), b"{}").unwrap();
    assert!(PacketBundle::open(&t.0).is_err());
}

#[test]
fn corrupt_cookie_is_rejected_even_if_its_hash_is_updated() {
    let t = Temp::new();
    t.build(0, 0, 5, false);
    let cookie = b"\x00\x00\x00\x0cdapa\x00\x00\x00\x00"; // missing bitstream version
    fs::write(t.0.join("cookie.bin"), cookie).unwrap();
    assert!(PacketBundle::open(&t.0).is_err());
    let mut m = t.read_manifest();
    m["file"]["cookie"]["value"]["sha256"] = json!(sha256(cookie));
    t.manifest(&m);
    let error = PacketBundle::open(&t.0).err().unwrap();
    assert_eq!(error.bit_offset, Some(96));
}

#[test]
fn known_cookie_duration_constrains_unspecified_packet_timing() {
    let t = Temp::new();
    t.build(1, 2, 4, true);
    // Synthetic partial cookie: 48 kHz stereo, 1024 samples per frame;
    // stop at the unimplemented global.flag_c after these confirmed fields.
    let mut cookie = b"\x00\x00\x00\x00dapa\x00\x00\x00\x00".to_vec();
    let mut bit = 96;
    for (value, width) in [
        (0x0800u64, 16),
        (31, 6),
        (2, 4),
        (0, 1),
        (3, 6),
        (0, 6),
        (2, 8),
        (2, 8),
        (1, 1),
    ] {
        for shift in (0..width).rev() {
            if bit / 8 == cookie.len() {
                cookie.push(0);
            }
            cookie[bit / 8] |= (((value >> shift) & 1) as u8) << (7 - bit % 8);
            bit += 1;
        }
    }
    let size = cookie.len() as u32;
    cookie[..4].copy_from_slice(&size.to_be_bytes());
    let parsed = macindecode_apac_tools::config::parse_cookie(&cookie).unwrap();
    assert_eq!(
        parsed.status,
        macindecode_apac_tools::config::ParseStatus::Partial
    );
    assert_eq!(parsed.derived["frame_samples"], 1024);
    fs::write(t.0.join("cookie.bin"), &cookie).unwrap();
    let mut original = t.read_manifest();
    original["file"]["cookie"]["value"] = json!({"bytes":cookie.len(),"sha256":sha256(&cookie)});
    let original_rows = t.read_rows();

    // Each invalid case is internally consistent without the cookie constraint.
    // The export ends before source EOF, so its last packet cannot verify the
    // source duration or anchor an otherwise shifted timeline.
    for (case, declared, frames, shift, total, valid) in [
        ("unspecified duration", 0, 1024u64, 0, 5120, true),
        ("matching fixed duration", 1024, 1024, 0, 5120, true),
        ("conflicting fixed duration", 2048, 2048, 0, 10240, false),
        ("stretched timeline", 0, 2048, 0, 10240, false),
        ("shortened packets", 0, 512, 0, 5120, false),
        ("shifted origin", 0, 1024, 1024, 5120, false),
        ("incorrect source duration", 0, 1024, 0, 6144, false),
    ] {
        let mut manifest = original.clone();
        manifest["file"]["format"]["frames_per_packet"] = json!(declared);
        manifest["file"]["packet_table"]["value"]["valid_frames"] = json!(total - 6);
        manifest["replay_window"]["target_raw_start"] = json!(2 * frames + shift);
        manifest["replay_window"]["target_raw_end"] = json!(4 * frames + shift);
        t.manifest(&manifest);
        let mut rows = original_rows.clone();
        for row in &mut rows {
            row["frames"] = json!(frames);
            row["raw_frame_position"]["value"] =
                json!(row["packet_index"].as_u64().unwrap() * frames + shift);
        }
        t.rows(&rows);
        let result = PacketBundle::open(&t.0);
        if valid {
            let mut bundle = result.unwrap_or_else(|error| panic!("{case}: {error}"));
            let range = bundle.range(None, 128).unwrap();
            assert_eq!((range.start_frame, range.frames), (2045, 128));
            assert!(
                bundle
                    .next_batch(64)
                    .unwrap()
                    .packets
                    .iter()
                    .all(|p| p.frames == 1024)
            );
        } else {
            assert!(result.is_err(), "accepted {case}");
        }
    }
}

#[test]
fn legacy_nonzero_and_insufficient_dependency_information_are_rejected() {
    let t = Temp::new();
    t.build(1, 1, 4, false);
    assert!(PacketBundle::open(&t.0).is_err());
    t.build(1, 1, 4, true);
    assert!(PacketBundle::open(&t.0).is_err());
    t.build(1, 2, 4, true);
    let rows = t.read_rows();
    for (i, key, value) in [
        (
            0,
            "dependency",
            json!(Property::known(DependencyInfo {
                independently_decodable: false,
                preroll_packet_count: 0
            })),
        ),
        (
            0,
            "dependency",
            json!(Property::known(DependencyInfo {
                independently_decodable: true,
                preroll_packet_count: 2
            })),
        ),
        (1, "roll_distance", json!(Property::known(2i64))),
        (1, "roll_distance", json!({"value":null,"error":null})),
    ] {
        let mut changed = rows.clone();
        changed[i][key] = value;
        t.rows(&changed);
        assert!(PacketBundle::open(&t.0).is_err());
    }
    t.rows(&rows);
    let mut m = t.read_manifest();
    m["replay_window"]["target_raw_start"] = json!(9);
    t.manifest(&m);
    assert!(PacketBundle::open(&t.0).is_err());
}

#[test]
fn bundle_paths_cannot_traverse_or_follow_links_outside_the_directory() {
    let t = Temp::new();
    t.build(0, 0, 5, false);
    let original = t.read_manifest();
    for member in ["../outside", "/outside", ""] {
        let mut m = original.clone();
        m["packet_data_file"] = json!(member);
        t.manifest(&m);
        assert!(PacketBundle::open(&t.0).is_err());
    }
    t.manifest(&original);
    #[cfg(unix)]
    {
        let outside = Temp::new();
        fs::write(outside.0.join("data"), [10, 11, 12, 13, 14]).unwrap();
        fs::remove_file(t.0.join("packets.bin")).unwrap();
        std::os::unix::fs::symlink(outside.0.join("data"), t.0.join("packets.bin")).unwrap();
        assert!(PacketBundle::open(&t.0).is_err());
    }
}

#[test]
fn bounded_lines_and_changes_during_replay_are_detected() {
    let t = Temp::new();
    t.build(0, 0, 5, false);
    fs::write(t.0.join("packets.jsonl"), vec![b' '; 65537]).unwrap();
    assert!(PacketBundle::open(&t.0).is_err());
    t.build(0, 0, 5, false);
    let mut bundle = PacketBundle::open(&t.0).unwrap();
    let mut rows = t.read_rows();
    rows[0]["sha256"] = json!(sha256(&[99]));
    t.rows(&rows);
    fs::write(t.0.join("packets.bin"), [99, 11, 12, 13, 14]).unwrap();
    assert_eq!(bundle.next_batch(1).unwrap().data, vec![99]);
    assert!(bundle.verify_remaining().is_err());
    // A changed variable-duration origin must fail before consumed_frames can
    // subtract it from the origin captured during validation.
    t.build(1, 2, 4, true);
    let mut manifest = t.read_manifest();
    manifest["file"]["format"]["frames_per_packet"] = json!(0);
    t.manifest(&manifest);
    let mut bundle = PacketBundle::open(&t.0).unwrap();
    let mut rows = t.read_rows();
    rows[0]["raw_frame_position"]["value"] = json!(0);
    rows[0]["frames"] = json!(1);
    t.rows(&rows);
    assert!(bundle.next_batch(1).is_err());
}

#[test]
fn large_variable_packets_split_batches_without_losing_cursor_or_hash_state() {
    use std::io::Write;
    let t = Temp::new();
    t.build(0, 0, 5, false);
    let large = vec![42u8; 9 * 1024 * 1024];
    let mut file = fs::File::create(t.0.join("packets.bin")).unwrap();
    file.write_all(&large).unwrap();
    file.write_all(&large).unwrap();
    file.write_all(&[12, 13, 14]).unwrap();
    drop(file);
    let mut rows = t.read_rows();
    let mut offset = 0;
    for (i, row) in rows.iter_mut().enumerate() {
        row["export_offset"] = json!(offset);
        if i < 2 {
            row["bytes"] = json!(large.len());
            row["sha256"] = json!(sha256(&large));
            offset += large.len();
        } else {
            offset += 1;
        }
    }
    t.rows(&rows);
    let mut manifest = t.read_manifest();
    manifest["packet_data_bytes"] = json!(offset);
    let data = fs::read(t.0.join("packets.bin")).unwrap();
    manifest["packet_data_sha256"] = json!(sha256(&data));
    drop(data);
    manifest["file"]["max_packet_bytes"]["value"] = json!(large.len());
    t.manifest(&manifest);
    drop(large);
    let mut bundle = PacketBundle::open(&t.0).unwrap();
    assert_eq!(bundle.next_batch(64).unwrap().packets.len(), 1);
    let second = bundle.next_batch(64).unwrap();
    assert_eq!(second.packets.len(), 4);
    assert_eq!(second.packets[1].offset, 9 * 1024 * 1024);
    assert!(bundle.next_batch(64).unwrap().packets.is_empty());
    assert_eq!(bundle.consumed_packets(), 5);
}

fn access(packet: u64, roll: i64, independent: bool, refresh: u32) -> PacketAccess {
    PacketAccess {
        packet_index: packet,
        raw_frame_position: Property::known((packet * 4) as i64),
        roll_distance: Property::known(roll),
        dependency: Property::known(DependencyInfo {
            independently_decodable: independent,
            preroll_packet_count: refresh,
        }),
    }
}

#[test]
fn access_selection_honors_both_directions_and_bounds_its_search() {
    assert_eq!(
        select_preroll(10, |p| Ok(access(p, 2, p != 8, 4)), |_| Ok(6)).unwrap(),
        6
    );
    assert_eq!(
        select_preroll(10, |p| Ok(access(p, 0, true, 1)), |p| Ok(p as i64 - 1)).unwrap(),
        9
    );
    assert_eq!(
        select_preroll(1, |p| Ok(access(p, 1, true, 1)), |_| panic!("not needed")).unwrap(),
        0
    );
    for roll in [-1, 11, 4097] {
        assert!(select_preroll(10, |p| Ok(access(p, roll, true, 0)), |_| Ok(0)).is_err());
    }
    assert!(select_preroll(10, |p| Ok(access(p, 1, false, 1)), |p| Ok(p as i64)).is_err());
    assert!(select_preroll(10, |p| Ok(access(p, 1, false, 1)), |_| Ok(-1)).is_err());
    assert!(select_preroll(10000, |p| Ok(access(p, 1, false, 1)), |_| Ok(0)).is_err());
    let mut missing = access(10, 0, true, 0);
    missing.roll_distance = Property {
        value: None,
        error: None,
    };
    assert!(select_preroll(10, |_| Ok(missing.clone()), |_| Ok(0)).is_err());
}
