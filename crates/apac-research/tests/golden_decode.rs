//! Implementation regression snapshot for the restructuring work.
//!
//! Every frozen fixture that carries a hex cookie is replayed through the
//! cookie parser, the per-packet parse reports and the experimental decoder.
//! The recorded digests describe this implementation, not independent truth:
//! they only prove that a refactor left the observable results unchanged.
//! Regenerate deliberately with `APAC_GOLDEN_WRITE=1 cargo test --test golden_decode`.
use apac_core::config::parse_cookie;
use apac_core::frame;
use apac_core::synthesis::SqDecoder;
use serde::Serialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path};

const GOLDEN: &str = "tests/golden/refactor-v1.json";

fn sha<T: Serialize>(value: &T) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).expect("finite report"))
    )
}

fn hex(text: &str) -> Option<Vec<u8>> {
    if text.is_empty() || !text.len().is_multiple_of(2) {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
        .collect()
}

fn outcome<T: Serialize, E: ToString>(result: Result<T, E>) -> Value {
    match result {
        Ok(value) => json!({"ok": sha(&value)}),
        Err(error) => json!({"error": error.to_string()}),
    }
}

/// Packet fields in a deterministic order: `first` leads, then sorted keys.
fn packets(fixture: &Map<String, Value>) -> Vec<(String, Vec<u8>)> {
    let mut keys: Vec<&String> = fixture
        .keys()
        .filter(|k| k.as_str() != "cookie" && !k.ends_with("cookie") && !k.ends_with("sha256"))
        .collect();
    keys.sort_by_key(|k| (k.as_str() != "first", k.as_str()));
    let mut out = Vec::new();
    for key in keys {
        match &fixture[key] {
            Value::String(text) => {
                if let Some(bytes) = hex(text) {
                    out.push((key.clone(), bytes));
                }
            }
            Value::Array(items) => {
                let decoded: Option<Vec<_>> =
                    items.iter().map(|v| v.as_str().and_then(hex)).collect();
                if let Some(decoded) = decoded.filter(|d| !d.is_empty()) {
                    for (i, bytes) in decoded.into_iter().enumerate() {
                        out.push((format!("{key}[{i}]"), bytes));
                    }
                }
            }
            _ => {}
        }
    }
    out
}

fn cookie_record(cookie: &[u8]) -> Value {
    json!({
        "cookie_report": outcome(parse_cookie(cookie)),
        "decoder": match SqDecoder::from_cookie(cookie) {
            Ok(d) => json!({
                "channels": d.channel_count(),
                "backend": d.backend(),
                "state_profile": d.state_profile(),
                "support_scope": d.support_scope(),
                "layout": sha(d.channel_layout()),
            }),
            Err(e) => json!({"error": e.to_string()}),
        },
    })
}

fn report(cookie: &[u8], packet: &[u8]) -> Value {
    let Ok(parsed) = parse_cookie(cookie) else {
        return Value::Null;
    };
    let field = |name: &str| {
        parsed
            .fields
            .iter()
            .find(|f| f.name == name)
            .map(|f| f.value.clone())
    };
    let stream = field("global.component_count").is_some_and(|v| v.as_u64() > Some(1))
        || field("global.additional_asc_present") == Some(json!(true));
    if stream {
        return match frame::StreamFrameContext::from_cookie(cookie) {
            Ok(c) => outcome(frame::parse_stream_packet(&c, packet)),
            Err(e) => json!({"context_error": e.to_string()}),
        };
    }
    if field("components[0].type") == Some(json!(2)) {
        return match frame::HoaFrameContext::from_cookie(cookie) {
            Ok(c) => outcome(frame::parse_hoa_packet(&c, packet)),
            Err(e) => json!({"context_error": e.to_string()}),
        };
    }
    let channels = match frame::ChannelFrameContext::from_cookie(cookie) {
        Ok(c) => outcome(frame::parse_channel_packet(&c, packet)),
        Err(e) => json!({"context_error": e.to_string()}),
    };
    let stereo = match frame::FrameContext::from_cookie(cookie) {
        Ok(c) => outcome(frame::parse_packet(&c, packet)),
        Err(e) => json!({"context_error": e.to_string()}),
    };
    json!({"channels": channels, "stereo": stereo})
}

fn replay(cookie: &[u8], fixture: &Map<String, Value>) -> Value {
    let mut record = cookie_record(cookie);
    let mut decoder = SqDecoder::from_cookie(cookie).ok();
    let mut rows = Vec::new();
    for (key, packet) in packets(fixture) {
        let decoded = decoder.as_mut().map(|d| match d.decode_frame(&packet) {
            Ok(pcm) => {
                let bytes: Vec<u8> = pcm.iter().flat_map(|v| v.to_le_bytes()).collect();
                json!({"pcm": format!("{:x}", Sha256::digest(&bytes)), "samples": pcm.len()})
            }
            Err(e) => json!({"error": e.to_string()}),
        });
        rows.push(json!({"packet": key, "decode": decoded, "report": report(cookie, &packet)}));
    }
    record["packets"] = Value::Array(rows);
    let mut alternates = BTreeMap::new();
    for (key, value) in fixture {
        if key != "cookie"
            && key.ends_with("cookie")
            && let Some(bytes) = value.as_str().and_then(hex)
        {
            alternates.insert(key.clone(), cookie_record(&bytes));
        }
    }
    if !alternates.is_empty() {
        record["alternate_cookies"] = json!(alternates);
    }
    record
}

fn walk(value: &Value, path: &str, out: &mut BTreeMap<String, Value>) {
    match value {
        Value::Object(map) => {
            if let Some(cookie) = map.get("cookie").and_then(Value::as_str).and_then(hex) {
                out.insert(path.to_owned(), replay(&cookie, map));
            }
            for (key, child) in map {
                walk(child, &format!("{path}.{key}"), out);
            }
        }
        Value::Array(items) => {
            for (i, child) in items.iter().enumerate() {
                walk(child, &format!("{path}[{i}]"), out);
            }
        }
        _ => {}
    }
}

fn snapshot() -> BTreeMap<String, Value> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut names: Vec<_> = std::fs::read_dir(root.join("data"))
        .expect("data directory")
        .map(|e| e.expect("entry").file_name().into_string().expect("UTF-8"))
        .filter(|n| n.ends_with(".json"))
        .collect();
    names.sort();
    let mut out = BTreeMap::new();
    for name in names {
        let text = std::fs::read_to_string(root.join("data").join(&name)).expect("fixture");
        let value: Value = serde_json::from_str(&text).expect("fixture JSON");
        walk(&value, &name, &mut out);
    }
    out
}

#[test]
fn frozen_fixtures_keep_identical_reports_and_pcm() {
    let actual = snapshot();
    assert!(actual.len() > 1000, "fixture walk found {}", actual.len());
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(GOLDEN);
    if std::env::var_os("APAC_GOLDEN_WRITE").is_some() {
        let document = json!({
            "note": "implementation regression snapshot, not independent truth",
            "fixtures": actual,
        });
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, serde_json::to_string(&document).unwrap() + "\n").unwrap();
        return;
    }
    let expected: Value =
        serde_json::from_str(&std::fs::read_to_string(&path).expect("golden file")).unwrap();
    let expected = expected["fixtures"].as_object().expect("fixtures");
    let mut mismatches = Vec::new();
    for key in expected
        .keys()
        .chain(actual.keys().filter(|k| !expected.contains_key(*k)))
    {
        if expected.get(key) != actual.get(key) {
            mismatches.push(key.clone());
        }
    }
    assert!(
        mismatches.is_empty(),
        "{} fixtures differ, first: {:?}",
        mismatches.len(),
        &mismatches[..mismatches.len().min(10)]
    );
}
