//! HOA scan history, numeric validation and one-outer-packet overlap convergence.
use super::*;
use serde_json::Value;
fn bytes(v: &Value) -> Vec<u8> {
    let s = v.as_str().unwrap();
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn fixtures() -> Vec<Value> {
    serde_json::from_str::<Value>(include_str!("../../data/hoa-access-vectors-v1.json")).unwrap()["fixtures"].as_array().unwrap().clone()
}
fn snapshot(d: &SqDecoder) -> (String, Vec<Vec<u64>>) {
    (
        d.metadata_sha256(),
        d.channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
    )
}
fn pcm(v: Vec<f32>) -> Vec<u32> {
    v.into_iter().map(f32::to_bits).collect()
}
#[test]
fn hoa_scanning_retains_all_history_and_one_predecessor_converges() {
    for row in fixtures() {
        let name = row["name"].as_str().unwrap();
        let cookie = bytes(&row["cookie"]);
        let packets: Vec<_> = row["packets"]
            .as_array()
            .unwrap()
            .iter()
            .map(bytes)
            .collect();
        let mut sequential = SqDecoder::from_cookie(&cookie).unwrap();
        let initial = snapshot(&sequential);
        let mut outputs = Vec::new();
        let mut states = Vec::new();
        for packet in &packets {
            outputs.push(pcm(sequential.decode_frame(packet).unwrap()));
            states.push(snapshot(&sequential));
        }
        for target in 0..=packets.len() {
            let mut fast = SqDecoder::from_cookie(&cookie).unwrap();
            let start = if target == packets.len() {
                target
            } else {
                target.saturating_sub(1)
            };
            for (i, packet) in packets[..start].iter().enumerate() {
                fast.scan_frame(packet)
                    .unwrap_or_else(|e| panic!("{name} scan {i}: {e}"));
                assert_eq!(fast.metadata_sha256(), states[i].0, "{name} scan {i}");
                assert_eq!(snapshot(&fast).1, initial.1);
            }
            for (i, packet) in packets.iter().enumerate().skip(start) {
                let output = pcm(fast.decode_frame(packet).unwrap());
                assert_eq!(
                    snapshot(&fast),
                    states[i],
                    "{name} convergence {target}/{i}"
                );
                if i >= target {
                    assert_eq!(output, outputs[i], "{name} output {target}/{i}");
                }
            }
            fast.reset();
            assert_eq!(snapshot(&fast), initial);
        }
    }
}
#[test]
fn hoa_failed_scan_matches_first_error_and_keeps_every_state() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["packets"][0]);
        let good = bytes(&row["packets"][1]);
        let mut fast = SqDecoder::from_cookie(&cookie).unwrap();
        let mut sequential = SqDecoder::from_cookie(&cookie).unwrap();
        fast.decode_frame(&first).unwrap();
        sequential.decode_frame(&first).unwrap();
        let before = snapshot(&fast);
        let mut tail = good.clone();
        tail.push(165);
        for packet in [
            Vec::new(),
            good[..good.len() / 2].to_vec(),
            good[..good.len() - 1].to_vec(),
            tail,
        ] {
            let expected = sequential.decode_frame(&packet).unwrap_err();
            let actual = fast.scan_frame(&packet).err().unwrap();
            assert_eq!(
                serde_json::to_value(actual).unwrap(),
                serde_json::to_value(expected).unwrap(),
                "{}",
                row["name"]
            );
            assert_eq!(snapshot(&fast), before);
        }
        fast.scan_frame(&good).unwrap();
        sequential.decode_frame(&good).unwrap();
        assert_eq!(fast.metadata_sha256(), sequential.metadata_sha256());
        fast.decode_frame(&first).unwrap();
        sequential.decode_frame(&first).unwrap();
        assert_eq!(snapshot(&fast), snapshot(&sequential));
        assert_eq!(
            pcm(fast.decode_frame(&good).unwrap()),
            pcm(sequential.decode_frame(&good).unwrap())
        );
    }
    let asp: Value =
        serde_json::from_str(include_str!("../../data/hoa-asp-vectors-v1.json")).unwrap();
    for row in asp["fixtures"].as_array().unwrap() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        let mut scan = SqDecoder::from_cookie(&cookie).unwrap();
        let mut full = SqDecoder::from_cookie(&cookie).unwrap();
        scan.decode_frame(&first).unwrap();
        full.decode_frame(&first).unwrap();
        let before = snapshot(&scan);
        for (name, value) in row["bad"].as_object().unwrap() {
            let packet = bytes(value);
            let expected = full.decode_frame(&packet).unwrap_err();
            let actual = scan.scan_frame(&packet).err().unwrap();
            assert_eq!(
                serde_json::to_value(actual).unwrap(),
                serde_json::to_value(expected).unwrap(),
                "{name}"
            );
            assert_eq!(snapshot(&scan), before, "{name}");
        }
    }
}

#[test]
fn hoa_scans_reject_frozen_component_numeric_and_late_state_errors() {
    for raw in [
        include_str!("../../data/hoa-shared-state-v1.json"),
        include_str!("../../data/hoa-controls-state-v2.json"),
        include_str!("../../data/hoa-remapping-state-v1.json"),
        include_str!("../../data/hoa-source-layout-state-v1.json"),
        include_str!("../../data/hoa-ambient-state-v1.json"),
        include_str!("../../data/hoa-salient-state-v1.json"),
    ] {
        let data: Value = serde_json::from_str(raw).unwrap();
        let rows = data["fixtures"]
            .as_array()
            .cloned()
            .unwrap_or_else(|| vec![data]);
        for row in rows {
            let cookie = bytes(&row["cookie"]);
            let first = bytes(&row["first"]);
            let mut full = SqDecoder::from_cookie(&cookie).unwrap();
            let mut scan = SqDecoder::from_cookie(&cookie).unwrap();
            full.decode_frame(&first).unwrap();
            scan.scan_frame(&first).unwrap();
            assert_eq!(full.metadata_sha256(), scan.metadata_sha256());
            let before = snapshot(&scan);
            for (key, value) in row.as_object().unwrap() {
                if !(key.ends_with("_error")
                    || ["bad", "late", "bad_graph"].contains(&key.as_str()))
                {
                    continue;
                }
                let packet = bytes(value);
                let expected = full.decode_frame(&packet).unwrap_err();
                let actual = scan.scan_frame(&packet).err().unwrap();
                assert_eq!(
                    serde_json::to_value(actual).unwrap(),
                    serde_json::to_value(expected).unwrap(),
                    "{} {key}",
                    row["name"]
                );
                assert_eq!(snapshot(&scan), before);
            }
        }
    }
}
