//! Checkpoints restore exactly the state fast access reaches, across clones,
//! and only into decoders that share the checkpoint's construction.
use super::*;
use serde_json::Value;

fn bytes(v: &Value) -> Vec<u8> {
    let s = v.as_str().unwrap();
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn snapshot(d: &Decoder) -> (String, Vec<Vec<u64>>) {
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
/// Packet sequences that decode in order: channel, layout and HOA state
/// fixtures, the HOA access vectors and a sample of the shared-configuration
/// (composite) fixtures.
fn streams() -> Vec<(String, Vec<u8>, Vec<Vec<u8>>)> {
    let mut out = vec![];
    for (label, text, step) in [
        (
            "channel",
            include_str!("../../../../data/channel-state-fixtures-v1.json"),
            1,
        ),
        (
            "layout",
            include_str!("../../../../data/layout-state-fixtures-v1.json"),
            1,
        ),
        (
            "surround916",
            include_str!("../../../../data/surround916-state-fixtures-v1.json"),
            1,
        ),
        (
            "hoa-mixed",
            include_str!("../../../../data/hoa-mixed-state-v1.json"),
            1,
        ),
        (
            "hoa-dynamic",
            include_str!("../../../../data/hoa-dynamic-state-v1.json"),
            1,
        ),
        (
            "hoa-access",
            include_str!("../../../../data/hoa-access-vectors-v1.json"),
            1,
        ),
        (
            "hoa-shared",
            include_str!("../../../../data/hoa-shared-state-v1.json"),
            25,
        ),
    ] {
        let value: Value = serde_json::from_str(text).unwrap();
        for (i, row) in value["fixtures"]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
            .step_by(step)
        {
            let cookie = bytes(&row["cookie"]);
            let packets: Vec<Vec<u8>> = if let Some(packets) = row["packets"].as_array() {
                packets.iter().map(bytes).collect()
            } else {
                let first = bytes(&row["first"]);
                let next = ["next", "good"]
                    .iter()
                    .find_map(|key| row.get(*key))
                    .map_or_else(|| first.clone(), bytes);
                vec![first, next.clone(), next.clone(), next]
            };
            let Ok(mut decoder) = Decoder::from_cookie(&cookie) else {
                continue;
            };
            if packets.iter().all(|p| decoder.decode_vec(p).is_ok()) {
                out.push((format!("{label}[{i}]"), cookie, packets));
            }
        }
    }
    out
}

#[test]
fn a_restored_checkpoint_equals_fast_access_and_converges_after_one_packet() {
    let streams = streams();
    let kinds: Vec<_> = streams
        .iter()
        .map(|(_, cookie, _)| Decoder::from_cookie(cookie).unwrap().info().kind)
        .collect();
    for kind in [
        StreamKind::Stereo,
        StreamKind::Channels,
        StreamKind::Hoa,
        StreamKind::Composite,
    ] {
        assert!(kinds.contains(&kind), "no {kind:?} stream in {kinds:?}");
    }
    for (name, cookie, packets) in &streams {
        let mut sequential = Decoder::from_cookie(cookie).unwrap();
        let mut checkpoints = vec![];
        let mut outputs = vec![];
        for packet in packets {
            checkpoints.push(sequential.checkpoint());
            outputs.push(pcm(sequential.decode_vec(packet).unwrap()));
        }
        checkpoints.push(sequential.checkpoint());
        for (k, checkpoint) in checkpoints.iter().enumerate() {
            let mut fast = Decoder::from_cookie(cookie).unwrap();
            for packet in &packets[..k] {
                fast.advance(packet).unwrap();
            }
            // Restore into a clone taken after the whole sequence, so the
            // restored overlap and history must not leak from the end state.
            let mut restored = sequential.clone();
            restored.restore(checkpoint).unwrap();
            assert_eq!(snapshot(&restored), snapshot(&fast), "{name} restore {k}");
            for (i, packet) in packets.iter().enumerate().skip(k) {
                let output = pcm(restored.decode_vec(packet).unwrap());
                assert_eq!(
                    output,
                    pcm(fast.decode_vec(packet).unwrap()),
                    "{name} {k}/{i}"
                );
                if i > k || k == 0 {
                    assert_eq!(output, outputs[i], "{name} output {k}/{i}");
                }
            }
            if k < packets.len() {
                assert_eq!(snapshot(&restored), snapshot(&sequential), "{name} end {k}");
            }
        }
    }
}

#[test]
fn checkpoints_cross_clones_but_not_unrelated_decoders() {
    for (name, cookie, packets) in streams().iter().filter(|s| s.2.len() >= 2).take(12) {
        let mut original = Decoder::from_cookie(cookie).unwrap();
        original.decode_vec(&packets[0]).unwrap();
        let mut clone = original.clone();
        let from_original = original.checkpoint();
        clone.decode_vec(&packets[1]).unwrap();
        let from_clone = clone.checkpoint();
        clone.restore(&from_original).unwrap();
        original.restore(&from_clone).unwrap();
        // A decoder built separately from the same cookie rejects both and
        // keeps its state.
        let mut other = Decoder::from_cookie(cookie).unwrap();
        other.decode_vec(&packets[0]).unwrap();
        let before = snapshot(&other);
        for checkpoint in [&from_original, &from_clone] {
            let error = other.restore(checkpoint).unwrap_err();
            assert_eq!(
                (error.operation, error.message.as_str()),
                ("SQ decoder", "checkpoint belongs to a different decoder"),
                "{name}"
            );
            assert_eq!(snapshot(&other), before, "{name}");
        }
    }
}

#[test]
fn restoring_invalidates_packets_parsed_before() {
    for (name, cookie, packets) in streams().iter().filter(|s| s.2.len() >= 2).take(12) {
        let mut decoder = Decoder::from_cookie(cookie).unwrap();
        let start = decoder.checkpoint();
        decoder.decode_vec(&packets[0]).unwrap();
        let parsed = decoder.parse(&packets[1]).unwrap();
        decoder.restore(&start).unwrap();
        let before = snapshot(&decoder);
        let mut out = vec![f32::NAN; 1024 * decoder.info().channel_count as usize];
        let error = decoder.synthesize(parsed, &mut out).unwrap_err();
        assert!(error.message.contains("stale"), "{name}: {error}");
        assert_eq!(snapshot(&decoder), before, "{name}");
        assert!(out.iter().all(|v| v.is_nan()), "{name}");
        // The restored initial state decodes the first packet directly.
        let mut fresh = Decoder::from_cookie(cookie).unwrap();
        assert_eq!(
            pcm(decoder.decode_vec(&packets[0]).unwrap()),
            pcm(fresh.decode_vec(&packets[0]).unwrap()),
            "{name}"
        );
    }
}
