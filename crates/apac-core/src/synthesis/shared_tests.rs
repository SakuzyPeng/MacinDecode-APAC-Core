use super::*;
use serde_json::Value;

fn bytes(value: &Value) -> Vec<u8> {
    value
        .as_str()
        .unwrap()
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
fn snapshot(decoder: &Decoder) -> (String, Vec<Vec<u64>>) {
    (
        decoder.metadata_sha256(),
        decoder
            .channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
    )
}
#[test]
fn shared_rates_components_and_all_histories_commit_atomically() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-shared-state-v1.json")).unwrap();
    for row in data["fixtures"].as_array().unwrap() {
        let name = row["name"].as_str().unwrap();
        let cookie = bytes(&row["cookie"]);
        let Some(mut decoder) = crate::frame::hoa_support_tests::decoder_or_order_limit(&cookie)
        else {
            continue;
        };
        assert_eq!(
            decoder.info().channel_count as u64,
            row["channels"].as_u64().unwrap()
        );
        if let Some(components) = decoder.components() {
            assert_eq!(
                components.len() as u64,
                row["declared_components"].as_u64().unwrap()
            );
            let mut covered = vec![false; decoder.info().channel_count as usize];
            for component in components {
                assert!(component.source_channels > 0);
                for range in &component.output_ranges {
                    assert!(range.source_start + range.channels <= component.source_channels);
                    for c in &mut covered[range.output_start..range.output_start + range.channels] {
                        assert!(!*c);
                        *c = true;
                    }
                }
            }
            assert!(covered.iter().all(|&v| v));
        }
        let initial = snapshot(&decoder);
        let first = bytes(&row["first"]);
        let first_pcm = decoder
            .decode_vec(&first)
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(
            first_pcm.len(),
            decoder.info().channel_count as usize * 1024
        );
        let before = snapshot(&decoder);
        for key in ["bad", "late", "bad_graph"]
            .into_iter()
            .filter(|key| row.get(key).is_some())
        {
            assert!(
                decoder.decode_vec(&bytes(&row[key])).is_err(),
                "{name}: {key}"
            );
            assert_eq!(before, snapshot(&decoder), "{name}: {key}");
        }
        for end in 0..row["required_bytes"].as_u64().unwrap() as usize {
            assert!(
                decoder.decode_vec(&first[..end]).is_err(),
                "{name} truncated {end}"
            );
            assert_eq!(before, snapshot(&decoder), "{name} truncated {end}");
        }
        let mut extra = first.clone();
        extra.push(0xa5);
        assert!(
            decoder.decode_vec(&extra).is_err(),
            "{name}: trailing marker"
        );
        assert_eq!(before, snapshot(&decoder));
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        clean.decode_vec(&first).unwrap();
        let next = bytes(&row["good"]);
        assert_eq!(
            decoder.decode_vec(&next).unwrap(),
            clean.decode_vec(&next).unwrap(),
            "{name}"
        );
        assert_eq!(snapshot(&decoder), snapshot(&clean));
        decoder.reset();
        assert_eq!(snapshot(&decoder), initial);
        assert_eq!(decoder.decode_vec(&first).unwrap(), first_pcm);
    }
}
#[test]
fn shared_cookie_truncation_never_becomes_a_qualified_component() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-shared-state-v1.json")).unwrap();
    for row in data["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| !r["name"].as_str().unwrap().starts_with("rate-"))
    {
        let cookie = bytes(&row["cookie"]);
        for end in 0..cookie.len() {
            assert!(
                Decoder::from_cookie(&cookie[..end]).is_err(),
                "{} truncated {end}",
                row["name"]
            );
        }
    }
}
