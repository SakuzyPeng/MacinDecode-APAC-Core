#![cfg(target_os = "macos")]
use macindecode_apac_tools::{
    compare,
    model::sha256,
    replay::replay,
    research::{self, FixtureOptions},
    signal::{LayoutPreset, Signal},
};
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
            "apac-replay-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn fixture(&self) -> PathBuf {
        let out = self.0.join("中文 日本語");
        research::fixture(
            &out,
            &FixtureOptions {
                layout: LayoutPreset::Stereo,
                sample_rate: 48000,
                duration: 0.25,
                seed: 1,
                signals: vec![Signal::Sine],
                bitrate: None,
                quality: None,
                drc_configuration: None,
            },
            2 * 1024 * 1024,
        )
        .unwrap();
        out.join("sine/encoded.caf")
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn detached_middle_replay_is_batch_invariant_and_matches_file_reference() {
    let t = Temp::new();
    let source = t.fixture();
    let bundle = t.0.join("packets");
    let dump = research::dump_with_options(&source, &bundle, 3, 7, true, 2 * 1024 * 1024).unwrap();
    assert_eq!(dump["start_packet"], 2);
    assert_eq!(dump["actual_packets"], 8);
    assert_eq!(dump["replay_window"]["requested_start_packet"], 3);
    let reference = t.0.join("reference");
    research::decode(&source, &reference, 1225, 4097, 2 * 1024 * 1024).unwrap();
    fs::remove_file(source).unwrap();
    let mut first: Option<PathBuf> = None;
    for batch in [1, 7, 64] {
        let out = t.0.join(format!("replay-{batch}"));
        let report = replay(&bundle, &out, Some(1225), 4097, batch, 2 * 1024 * 1024).unwrap();
        assert_eq!(report["saved_frames"], 4097);
        assert_eq!(report["original_source_accessed"], false);
        assert!(
            compare::compare(
                &reference.join("pcm.json"),
                &out.join("pcm.json"),
                1e-6,
                1e-5
            )
            .unwrap()
            .passed
        );
        if let Some(first) = &first {
            assert!(
                compare::compare(first, &out.join("pcm.json"), 1e-6, 1e-5)
                    .unwrap()
                    .passed
            );
        } else {
            first = Some(out.join("pcm.json"));
        }
        assert!(replay(&bundle, &out, Some(1225), 4097, batch, 2 * 1024 * 1024).is_err());
    }
    assert!(
        replay(
            &bundle,
            &t.0.join("outside"),
            Some(0),
            1,
            1,
            2 * 1024 * 1024
        )
        .is_err()
    );
    let quota = t.0.join("quota");
    assert!(replay(&bundle, &quota, None, 8192, 1, 1024).is_err());
    assert!(quota.join(".incomplete.json").exists());
    assert!(!quota.join("pcm.json").exists());
}

#[test]
fn legacy_full_bundle_drains_eof_and_removes_container_padding() {
    let t = Temp::new();
    let source = t.fixture();
    let bundle = t.0.join("legacy");
    research::dump(&source, &bundle, 0, 150, 2 * 1024 * 1024).unwrap();
    let output = t.0.join("replay");
    let report = replay(&bundle, &output, None, 20000, 7, 2 * 1024 * 1024).unwrap();
    let manifest: Value =
        serde_json::from_slice(&fs::read(bundle.join("manifest.json")).unwrap()).unwrap();
    let table = &manifest["file"]["packet_table"]["value"];
    assert_eq!(report["saved_frames"], 12000);
    assert_eq!(report["eof_drained"], true);
    assert_eq!(report["discarded_before_frames"], table["priming_frames"]);
    assert_eq!(report["discarded_after_frames"], table["remainder_frames"]);
    assert_eq!(report["range"]["clipped_by"], "source_eof");
    assert!(
        compare::compare(
            &source.parent().unwrap().join("reference/pcm.json"),
            &output.join("pcm.json"),
            1e-6,
            1e-5
        )
        .unwrap()
        .passed
    );
}

#[test]
fn unspecified_packet_duration_replays_but_conflicting_timing_fails_before_output() {
    let t = Temp::new();
    let source = t.fixture();
    let bundle = t.0.join("packets");
    let mut manifest =
        research::dump_with_options(&source, &bundle, 3, 7, true, 2 * 1024 * 1024).unwrap();
    manifest["file"]["format"]["frames_per_packet"] = json!(0);
    fs::write(
        bundle.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    let reference = t.0.join("reference");
    research::decode(&source, &reference, 1225, 128, 2 * 1024 * 1024).unwrap();
    fs::remove_file(source).unwrap();
    let output = t.0.join("valid");
    replay(&bundle, &output, Some(1225), 128, 1, 2 * 1024 * 1024).unwrap();
    assert!(
        compare::compare(
            &reference.join("pcm.json"),
            &output.join("pcm.json"),
            1e-6,
            1e-5
        )
        .unwrap()
        .passed
    );

    // Keep cookie and compressed bytes unchanged, but make all advertised packet
    // timing consistently twice as long. A short replay used to accept this and
    // label audio from valid frame 2048 as starting at frame 4096.
    let table = &mut manifest["file"]["packet_table"]["value"];
    let raw_total = table["priming_frames"].as_u64().unwrap()
        + table["valid_frames"].as_u64().unwrap()
        + table["remainder_frames"].as_u64().unwrap();
    table["valid_frames"] = json!(table["valid_frames"].as_u64().unwrap() + raw_total);
    for key in ["target_raw_start", "target_raw_end"] {
        manifest["replay_window"][key] =
            json!(manifest["replay_window"][key].as_u64().unwrap() * 2);
    }
    fs::write(
        bundle.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    let mut rows: Vec<Value> = fs::read_to_string(bundle.join("packets.jsonl"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    for row in &mut rows {
        row["frames"] = json!(row["frames"].as_u64().unwrap() * 2);
        row["raw_frame_position"]["value"] =
            json!(row["raw_frame_position"]["value"].as_u64().unwrap() * 2);
    }
    fs::write(
        bundle.join("packets.jsonl"),
        rows.iter()
            .map(|row| format!("{row}\n"))
            .collect::<String>(),
    )
    .unwrap();
    let invalid = t.0.join("invalid");
    let error = replay(&bundle, &invalid, None, 128, 1, 2 * 1024 * 1024).unwrap_err();
    assert_eq!(error.operation, "packet bundle");
    assert!(!invalid.exists());
}

#[test]
fn native_cookie_rejection_preserves_status_and_incomplete_output() {
    let t = Temp::new();
    let source = t.fixture();
    let bundle = t.0.join("packets");
    research::dump(&source, &bundle, 0, 150, 2 * 1024 * 1024).unwrap();
    let mut cookie = fs::read(bundle.join("cookie.bin")).unwrap();
    cookie[12] = 0xff;
    cookie[13] = 0xff;
    fs::write(bundle.join("cookie.bin"), &cookie).unwrap();
    let mut manifest: Value =
        serde_json::from_slice(&fs::read(bundle.join("manifest.json")).unwrap()).unwrap();
    manifest["file"]["cookie"]["value"]["sha256"] = json!(sha256(&cookie));
    fs::write(
        bundle.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    let out = t.0.join("bad");
    let error = replay(&bundle, &out, None, 8192, 1, 2 * 1024 * 1024).unwrap_err();
    assert!(error.os_status.is_some());
    assert!(error.operation.contains("MagicCookie"));
    assert!(out.join(".incomplete.json").exists());
    assert!(!out.join("pcm.json").exists());
}
