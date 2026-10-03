#![cfg(target_os = "macos")]

use apac_native::collect::collect_configs;
use apac_native::native::NativeFile;
use apac_native::research::{self, FixtureOptions};
use apac_research::config::parse_cookie;
use apac_research::model::*;
use apac_research::signal::{LayoutPreset, Signal};
use serde_json::Value;
use std::{
    fs,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "apac-native-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn fixture(&self) -> PathBuf {
        let out = self.0.join("中文 日本語 fixture");
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

#[test]
fn drc_cli_controls_preserve_requests_readback_and_cookie_presence() {
    let t = Temp::new();
    for (index, mode) in [
        None,
        Some("none"),
        Some("music"),
        Some("speech"),
        Some("movie"),
        Some("capture"),
    ]
    .into_iter()
    .enumerate()
    {
        let out = t.0.join(format!("DRC 对照 {index}"));
        let mut command = Command::new(env!("CARGO_BIN_EXE_apac-tool"));
        command.args([
            "fixture",
            "--signals",
            "sine",
            "--duration",
            "0.125",
            "--max-output-mib",
            "2",
            "--out",
        ]);
        command.arg(&out);
        if let Some(mode) = mode {
            command.args(["--drc-configuration", mode]);
        }
        let result = command.output().unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let manifest: Value =
            serde_json::from_slice(&fs::read(out.join("sine/manifest.json")).unwrap()).unwrap();
        assert_eq!(
            manifest["requested"]["drc_configuration"],
            serde_json::json!(mode)
        );
        if mode.is_some() {
            assert_eq!(manifest["drc_configuration_verified"], true);
            assert_eq!(
                manifest["actual_encoder_settings"]["cdrc"]["value"],
                index - 1
            );
            assert!(manifest["actual_encoder_settings"]["cdrc"]["error"].is_null());
        } else {
            assert!(manifest["drc_configuration_verified"].is_null());
        }
        let file = NativeFile::open(&out.join("sine/encoded.caf")).unwrap();
        let cookie = file.cookie().unwrap();
        let parsed = parse_cookie(&cookie).unwrap();
        assert!(parsed.is_complete());
        let present = parsed
            .fields
            .iter()
            .find(|f| f.name == "ancillary.loudness_drc_present")
            .unwrap();
        assert_eq!(present.value, mode != Some("none"));
        assert_eq!(
            manifest["encoded"]["cookie"]["value"]["sha256"],
            sha256(&cookie)
        );
    }
    let out = t.0.join("invalid DRC");
    let invalid = Command::new(env!("CARGO_BIN_EXE_apac-tool"))
        .args(["fixture", "--drc-configuration", "invalid", "--out"])
        .arg(&out)
        .output()
        .unwrap();
    assert_eq!(invalid.status.code(), Some(2));
    assert!(!out.exists());
}

#[test]
fn hoa_fixture_layouts_keep_acn_sn3d_and_parse_without_container_input() {
    let t = Temp::new();
    for (layout, order, channels) in [("hoa1", 1, 4), ("hoa2", 2, 9), ("hoa3", 3, 16)] {
        let out = t.0.join(layout);
        let result = Command::new(env!("CARGO_BIN_EXE_apac-tool"))
            .args([
                "fixture",
                "--layout",
                layout,
                "--signals",
                "channel-solo",
                "--duration",
                "0.125",
                "--drc-configuration",
                "none",
                "--max-output-mib",
                "2",
                "--out",
            ])
            .arg(&out)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let manifest: Value =
            serde_json::from_slice(&fs::read(out.join("channel-solo/manifest.json")).unwrap())
                .unwrap();
        assert_eq!(manifest["encoded"]["format"]["channels"], channels);
        assert_eq!(
            manifest["encoded"]["layout"]["value"]["ambisonic_order"],
            order
        );
        assert_eq!(
            manifest["encoded"]["layout"]["value"]["ambisonic_channel_order"],
            "ACN"
        );
        assert_eq!(
            manifest["encoded"]["layout"]["value"]["ambisonic_normalization"],
            "SN3D"
        );
        let file = NativeFile::open(&out.join("channel-solo/encoded.caf")).unwrap();
        let parsed = parse_cookie(&file.cookie().unwrap()).unwrap();
        assert!(parsed.is_complete());
        assert_eq!(parsed.derived["components[0].hoa.order"], order);
        assert_eq!(parsed.derived["components[0].channels"], channels);
        assert_eq!(
            parsed.derived["components[0].ambisonic_normalization"],
            "SN3D"
        );
    }
}

#[test]
fn collect_deduplicates_verifies_hashes_and_records_read_failures() {
    let t = Temp::new();
    let path = t.fixture();
    let info = research::inspect(&path).unwrap();
    let good = serde_json::json!({"schema_version":1,"status":"ok","file":info});
    let mut missing = good.clone();
    missing["file"]["source"] = serde_json::json!(t.0.join("missing.caf"));
    missing["file"]["file_bytes"] = serde_json::json!(0);
    let manifest = t.0.join("sources.jsonl");
    fs::write(&manifest, format!("{missing}\n{good}\n{good}\n")).unwrap();
    let out = t.0.join("collected");
    let (summary, passed) = collect_configs(&manifest, &out, 1024 * 1024).unwrap();
    assert!(passed);
    assert_eq!(summary["source_records"], 3);
    assert_eq!(summary["unique_configs"], 1);
    let index: Value = serde_json::from_slice(&fs::read(out.join("index.json")).unwrap()).unwrap();
    let config = &index["configs"][0];
    assert_eq!(config["failed_attempts"].as_array().unwrap().len(), 1);
    let cookie = fs::read(out.join(config["cookie_file"].as_str().unwrap())).unwrap();
    assert_eq!(sha256(&cookie), config["sha256"].as_str().unwrap());
    assert!(collect_configs(&manifest, &out, 1024 * 1024).is_err());
    let mut mismatch = good.clone();
    mismatch["file"]["cookie"]["value"]["sha256"] = serde_json::json!("0".repeat(64));
    fs::write(
        &manifest,
        format!("{mismatch}\n{{\"schema_version\":99}}\n"),
    )
    .unwrap();
    let failed = t.0.join("failed");
    let (summary, passed) = collect_configs(&manifest, &failed, 1024 * 1024).unwrap();
    assert!(!passed);
    assert_eq!(summary["failed"], 1);
    assert_eq!(summary["input_error_count"], 1);
    let result: Value =
        serde_json::from_slice(&fs::read(failed.join("index.json")).unwrap()).unwrap();
    assert!(result["configs"][0]["cookie_file"].is_null());
    let limited = t.0.join("quota");
    assert!(collect_configs(&manifest, &limited, 256).is_err());
    assert!(limited.join(".incomplete.json").exists());
}

#[test]
fn collect_rejects_incomplete_scan_without_creating_output() {
    let t = Temp::new();
    let info = research::inspect(&t.fixture()).unwrap();
    let record = serde_json::json!({"schema_version":1,"status":"ok","file":info});
    let manifest = t.0.join("索引.jsonl");
    let contents = format!("{record}\n");
    fs::write(&manifest, &contents).unwrap();
    let marker = t.0.join("索引.jsonl.incomplete");
    fs::write(&marker, []).unwrap();
    let out = t.0.join("collected");
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_apac-tool"))
            .arg("collect-configs")
            .arg(&manifest)
            .arg("--out")
            .arg(&out)
            .output()
            .unwrap()
    };
    let result = run();
    assert_eq!(result.status.code(), Some(1));
    assert!(result.stdout.is_empty());
    let error: Value = serde_json::from_slice(&result.stderr).unwrap();
    assert_eq!(error["error"]["operation"], "collect-configs");
    assert!(
        error["error"]["message"]
            .as_str()
            .unwrap()
            .contains("incomplete")
    );
    assert!(!out.exists());
    assert_eq!(fs::read_to_string(&manifest).unwrap(), contents);
    assert!(marker.exists());

    fs::remove_file(&marker).unwrap();
    let result = run();
    assert!(result.status.success());
    let summary: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(summary["all_collected"], true);
    assert_eq!(summary["unique_configs"], 1);
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn native_roundtrip_packet_tail_and_bounds() {
    let t = Temp::new();
    let path = t.fixture();
    let info = research::inspect(&path).unwrap();
    assert_eq!(info.format.format_fourcc, "apac");
    assert_eq!(
        info.packet_table.value.as_ref().unwrap().valid_frames,
        12000
    );
    let count = info.packet_count.value.unwrap();
    // Ask the native API past EOF to exercise partial packet reads, not just CLI clamping.
    let mut native = NativeFile::open(&path).unwrap();
    let (bytes, packets, _) = native.read_packets(count - 2, 64).unwrap();
    assert_eq!(packets.len(), 2);
    assert_eq!(
        packets.iter().map(|p| p.bytes as usize).sum::<usize>(),
        bytes.len()
    );
    assert!(native.property(b"zzzz").unwrap_err().os_status.is_some());
    for _ in 0..32 {
        assert!(NativeFile::open(&t.0.join("missing")).is_err());
    }
    native.finish().unwrap();
    let out = t.0.join("tail");
    let pcm = research::decode(&path, &out, 11983, 8192, 1024 * 1024).unwrap();
    assert_eq!(pcm.frames, 17);
    assert_eq!(pcm.bytes, 17 * 8);
    let old = fs::read(out.join("pcm.f32le")).unwrap();
    assert!(research::decode(&path, &out, 0, 64, 1024 * 1024).is_err());
    assert_eq!(old, fs::read(out.join("pcm.f32le")).unwrap());
    let bad = t.0.join("out-of-range");
    assert!(research::decode(&path, &bad, 12001, 64, 1024 * 1024).is_err());
    assert!(bad.join(".incomplete.json").exists());
    let dump = t.0.join("dump");
    let manifest = research::dump(&path, &dump, count - 2, 150, 1024 * 1024).unwrap();
    assert_eq!(manifest["actual_packets"], 2);
    assert_eq!(
        sha256(&fs::read(dump.join("cookie.bin")).unwrap()),
        info.cookie.value.unwrap().sha256
    );
}

#[test]
fn corrupt_file_scan_errors_and_cli_exit_statuses() {
    let t = Temp::new();
    let path = t.fixture();
    fs::write(t.0.join("坏文件.caf"), b"not audio").unwrap();
    let output = t.0.join("index.jsonl");
    let (summary, ok) = research::scan(&t.0, &output, 1024 * 1024).unwrap();
    assert!(!ok);
    assert_eq!(summary["errors"], 1);
    assert_eq!(summary["successful"], 1);
    let rows: Vec<Value> = fs::read_to_string(&output)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(rows.len(), 2);
    let metadata = path.parent().unwrap().join("reference/pcm.json");
    let exe = env!("CARGO_BIN_EXE_apac-tool");
    let result = Command::new(exe)
        .args([
            "compare",
            metadata.to_str().unwrap(),
            metadata.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(result.status.success());
    assert!(
        serde_json::from_slice::<Value>(&result.stdout).unwrap()["bit_identical"]
            .as_bool()
            .unwrap()
    );
    let source = path.parent().unwrap().join("source.json");
    let result = Command::new(exe)
        .args([
            "compare",
            source.to_str().unwrap(),
            metadata.to_str().unwrap(),
            "--atol",
            "0",
            "--rtol",
            "0",
        ])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    let result = Command::new(exe)
        .args(["inspect", t.0.join("坏文件.caf").to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(1));
    assert!(
        serde_json::from_slice::<Value>(&result.stderr).unwrap()["error"]["os_status"].is_number()
    );
}

#[test]
fn output_quota_includes_native_encoder_and_partial_exports() {
    let t = Temp::new();
    let limited = t.0.join("limited.caf");
    // A quota smaller than the CAF header must be enforced even in native callbacks.
    let result = NativeFile::create_encoder(
        &limited,
        48000.,
        2,
        LayoutPreset::Stereo.tag(),
        &Default::default(),
        128,
    );
    if let Ok(encoder) = result {
        assert!(encoder.finish().is_err());
    }
    assert!(fs::metadata(&limited).unwrap().len() <= 128);
    let source = t.fixture();
    let destination = t.0.join("bounded");
    assert!(research::decode(&source, &destination, 0, 12000, 4096).is_err());
    assert!(destination.join(".incomplete.json").exists());
    assert!(!destination.join("pcm.json").exists());
    let total: u64 = fs::read_dir(&destination)
        .unwrap()
        .map(|e| e.unwrap().metadata().unwrap().len())
        .sum();
    assert!(total <= 4096);
}
