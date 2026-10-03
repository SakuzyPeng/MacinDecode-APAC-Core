//! Corpus checks for the typed configuration.
use super::*;
use serde_json::Value;
use std::collections::BTreeSet;

/// Every hex cookie in the frozen fixtures: values under keys ending in `cookie`.
pub(super) fn corpus() -> Vec<Vec<u8>> {
    fn hex(text: &str) -> Option<Vec<u8>> {
        if text.is_empty() || !text.len().is_multiple_of(2) {
            return None;
        }
        (0..text.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
            .collect()
    }
    fn walk(value: &Value, out: &mut BTreeSet<Vec<u8>>) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    if key.ends_with("cookie")
                        && let Some(bytes) = child.as_str().and_then(hex)
                    {
                        out.insert(bytes);
                    }
                    walk(child, out);
                }
            }
            Value::Array(items) => items.iter().for_each(|v| walk(v, out)),
            _ => {}
        }
    }
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data");
    let mut out = BTreeSet::new();
    for entry in std::fs::read_dir(root).expect("data directory") {
        let path = entry.expect("entry").path();
        if path.extension().is_some_and(|e| e == "json") {
            let text = std::fs::read_to_string(&path).expect("fixture");
            walk(
                &serde_json::from_str(&text).expect("fixture JSON"),
                &mut out,
            );
        }
    }
    out.into_iter().collect()
}

/// The former consumers disagreed on duplicate names (first match, last match,
/// any true); typed lookup is only equivalent because a cookie never repeats one.
#[test]
fn cookie_field_names_are_unique_across_the_corpus() {
    let cookies = corpus();
    assert!(cookies.len() > 100, "corpus has {} cookies", cookies.len());
    for cookie in &cookies {
        let Ok(report) = parse_cookie(cookie) else {
            continue;
        };
        let mut names = BTreeSet::new();
        for field in &report.fields {
            assert!(names.insert(&field.name), "duplicate {}", field.name);
        }
    }
}
