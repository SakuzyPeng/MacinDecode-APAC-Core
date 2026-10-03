//! Corpus checks for the typed configuration.
use super::*;
use serde_json::Value;
use std::collections::BTreeSet;

/// Every hex cookie in the frozen fixtures: values under keys ending in `cookie`.
pub(crate) fn corpus() -> Vec<Vec<u8>> {
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

/// The parser's typed configuration equals the former name-based extraction
/// for every corpus cookie, every truncated prefix of it and single-bit
/// variants (a quarter of the positions per cookie, rotating, to bound the
/// run time): partial syntax, missing fields, other values and stop positions.
#[test]
fn parsed_configuration_matches_the_report_oracle() {
    let mut checked = 0usize;
    let mut partial = 0usize;
    let mut check = |data: &[u8]| {
        if let Ok((report, config)) = parse_cookie_and_config(data) {
            assert_eq!(config, Config::from_report(&report), "{data:02x?}");
            checked += 1;
            partial += usize::from(!report.is_complete());
        }
    };
    for (index, cookie) in corpus().into_iter().enumerate() {
        for end in 0..cookie.len() {
            check(&cookie[..end]);
        }
        let mut variant = cookie.clone();
        for bit in (index % 4..cookie.len() * 8).step_by(4) {
            variant[bit / 8] ^= 0x80 >> (bit % 8);
            check(&variant);
            variant[bit / 8] ^= 0x80 >> (bit % 8);
        }
    }
    std::eprintln!("oracle checked {checked} parses, {partial} incomplete");
    assert!(
        checked > 10_000 && partial > 1_000,
        "{checked} parses, {partial} incomplete"
    );
}

/// The in-band entry points read the same grammar from a packet offset. Start
/// them where a corpus cookie (or a single-bit variant of it) declares scenes
/// or DRC; each call checks its typed result against the oracle in test builds.
#[test]
fn in_band_scene_and_drc_syntax_match_the_report_oracle() {
    let (mut scenes, mut headers) = (0usize, 0usize);
    for (index, cookie) in corpus().into_iter().enumerate() {
        let Ok(original) = parse_cookie(&cookie) else {
            continue;
        };
        let start = |prefix: &str| {
            original
                .fields
                .iter()
                .find(|f| f.name.starts_with(prefix))
                .map(|f| f.bit_offset)
        };
        let scene = start("ancillary.audio_scenes.");
        let drc = start("ancillary.loudness_drc.");
        if scene.is_none() && drc.is_none() {
            continue;
        }
        let rate = original.derived["sample_rate_hz"].as_u64().unwrap_or(0);
        let channels = original.derived["channels"].as_u64().unwrap_or(0);
        let mut variant = cookie.clone();
        for bit in std::iter::once(None).chain((index % 8..cookie.len() * 8).step_by(8).map(Some)) {
            if let Some(bit) = bit {
                variant[bit / 8] ^= 0x80 >> (bit % 8);
            }
            if let Some(offset) = scene {
                scenes += usize::from(parse_scene_at(&variant, offset).is_ok());
            }
            if let Some(offset) = drc {
                headers +=
                    usize::from(parse_drc_header_at(&variant, offset, rate, channels).is_ok());
            }
            if let Some(bit) = bit {
                variant[bit / 8] ^= 0x80 >> (bit % 8);
            }
        }
    }
    assert!(
        scenes > 100 && headers > 100,
        "{scenes} scenes, {headers} headers"
    );
}
