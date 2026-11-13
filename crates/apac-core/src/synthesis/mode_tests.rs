//! Decoding parses without recording syntax. Every data fixture holding a
//! cookie and packets decodes to the same PCM bits, errors and committed state
//! when the same decoder parses its packets as recorded reports instead.
use super::*;
use serde_json::Value;
use std::collections::BTreeSet;

fn hex(text: &str) -> Option<Vec<u8>> {
    if text.is_empty() || !text.len().is_multiple_of(2) {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
        .collect()
}

/// (cookie, packets) for every object with a cookie: its other hex strings,
/// direct or in arrays, in key order.
fn sequences(value: &Value, out: &mut BTreeSet<(Vec<u8>, Vec<Vec<u8>>)>) {
    match value {
        Value::Object(map) => {
            if let Some(cookie) = map.get("cookie").and_then(Value::as_str).and_then(hex) {
                let mut packets = Vec::new();
                for (key, child) in map {
                    if key == "cookie" {
                        continue;
                    }
                    match child {
                        Value::String(text) => packets.extend(hex(text)),
                        Value::Array(items) => {
                            packets.extend(items.iter().filter_map(|v| v.as_str().and_then(hex)));
                        }
                        _ => {}
                    }
                }
                if !packets.is_empty() {
                    out.insert((cookie, packets));
                }
            }
            map.values().for_each(|v| sequences(v, out));
        }
        Value::Array(items) => items.iter().for_each(|v| sequences(v, out)),
        _ => {}
    }
}

fn corpus() -> BTreeSet<(Vec<u8>, Vec<Vec<u8>>)> {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data");
    let mut all = BTreeSet::new();
    for entry in std::fs::read_dir(root).expect("data directory") {
        let path = entry.expect("entry").path();
        if path.extension().is_some_and(|e| e == "json") {
            let text = std::fs::read_to_string(&path).unwrap();
            if let Ok(value) = serde_json::from_str::<Value>(&text) {
                sequences(&value, &mut all);
            }
        }
    }
    all
}

#[test]
fn recording_never_changes_a_decoded_packet() {
    let all = corpus();
    let (mut decoded, mut rejected) = (0usize, 0usize);
    for (cookie, packets) in &all {
        let Ok(decoder) = Decoder::from_cookie(cookie) else {
            continue;
        };
        let mut recording = decoder.clone().recording();
        let mut decoder = decoder;
        for packet in packets {
            let fast = decoder.decode_vec(packet);
            let full = recording.decode_vec(packet);
            match (&fast, &full) {
                (Ok(a), Ok(b)) => {
                    assert!(
                        a.iter()
                            .map(|v| v.to_bits())
                            .eq(b.iter().map(|v| v.to_bits())),
                        "PCM differs for cookie {cookie:02x?}"
                    );
                    decoded += 1;
                }
                (Err(a), Err(b)) => {
                    assert_eq!(a.to_string(), b.to_string());
                    assert_eq!(a.bit_offset, b.bit_offset);
                    rejected += 1;
                }
                _ => panic!("outcomes differ: {fast:?} vs {full:?}"),
            }
            assert_eq!(decoder.metadata_sha256(), recording.metadata_sha256());
        }
    }
    std::eprintln!(
        "{} sequences, {decoded} decoded, {rejected} rejected",
        all.len()
    );
    assert!(
        decoded > 500 && rejected > 100,
        "{decoded} decoded, {rejected} rejected"
    );
}

/// Decode the fixture corpus as decoding does and as recorded reports (the
/// former decode path); prints both times. Run with
/// `cargo test --release -p apac-core --lib synthesis::mode_tests::decoding_cost -- --ignored --nocapture`.
#[test]
#[ignore]
fn decoding_cost() {
    let all: Vec<_> = corpus()
        .into_iter()
        .filter(|(cookie, _)| Decoder::from_cookie(cookie).is_ok())
        .collect();
    for round in 0..3 {
        for recording in [false, true] {
            let start = std::time::Instant::now();
            let mut packets = 0usize;
            for (cookie, sequence) in &all {
                let mut decoder = Decoder::from_cookie(cookie).unwrap();
                if recording {
                    decoder = decoder.recording();
                }
                for packet in sequence {
                    let _ = std::hint::black_box(decoder.decode_vec(packet));
                    packets += 1;
                }
            }
            std::eprintln!(
                "round {round} recording={recording}: {packets} packets in {:.3} s",
                start.elapsed().as_secs_f64()
            );
        }
    }
}
