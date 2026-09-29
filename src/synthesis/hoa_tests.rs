use super::*;
use serde_json::Value;
fn bytes(v: &Value) -> Vec<u8> {
    v.as_str()
        .unwrap()
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
        .collect()
}
fn fixture() -> Value {
    serde_json::from_str(include_str!("../../data/hoa-ambient-state-v1.json")).unwrap()
}
fn snapshot(d: &SqDecoder) -> (Vec<Vec<u64>>, String) {
    (
        d.channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
        d.metadata_sha256(),
    )
}
#[test]
fn hoa_transactions_reset_and_explicit_fast_rejection() {
    let f = fixture();
    let mut d = SqDecoder::from_cookie(&bytes(&f["cookie"])).unwrap();
    assert_eq!(d.channel_count(), 16);
    assert_eq!(d.channel_layout().ambisonic_order, Some(3));
    assert!(d.scan_frame(&bytes(&f["first"])).is_err());
    d.decode_frame(&bytes(&f["first"])).unwrap();
    let state = snapshot(&d);
    for key in [
        "last_element_error",
        "late_spatial_error",
        "embedded_error",
        "outer_after_embedded_error",
    ] {
        assert!(d.decode_frame(&bytes(&f[key])).is_err());
        assert_eq!(state, snapshot(&d));
    }
    let first = bytes(&f["first"]);
    for end in 0..first.len() {
        assert!(d.decode_frame(&first[..end]).is_err());
        assert_eq!(state, snapshot(&d));
    }
    let mut fresh = SqDecoder::from_cookie(&bytes(&f["cookie"])).unwrap();
    fresh.decode_frame(&first).unwrap();
    assert_eq!(
        d.decode_frame(&bytes(&f["next"])).unwrap(),
        fresh.decode_frame(&bytes(&f["next"])).unwrap()
    );
    assert_eq!(snapshot(&d), snapshot(&fresh));
    d.reset();
    assert_eq!(
        snapshot(&d),
        snapshot(&SqDecoder::from_cookie(&bytes(&f["cookie"])).unwrap())
    );
}
#[test]
fn hoa_report_marks_identity_without_inventing_absent_integer_streams() {
    let f = fixture();
    let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let r = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
    assert!(r.packet.packet_complete && r.hoa().hoa_complete);
    assert_eq!(r.hoa().channels_after_hoa.len(), 16);
    assert!(r.packet.elements[1..].iter().all(|e| e.channels.is_empty()));
    assert_eq!(
        r.hoa().channels_after_hoa[0].scaled,
        r.packet.elements[0].channels_after_bwe2[0].scaled
    );
    let mut json = serde_json::to_value(&r.packet).unwrap();
    json.as_object_mut().unwrap().remove("hoa");
    let legacy: crate::frame::ChannelPacketReport = serde_json::from_value(json).unwrap();
    assert!(legacy.hoa.is_none());
}

#[test]
fn salient_history_and_all_coefficient_overlaps_roll_back_and_reset() {
    let f: Value =
        serde_json::from_str(include_str!("../../data/hoa-salient-state-v1.json")).unwrap();
    let cookie = bytes(&f["cookie"]);
    let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
    assert_eq!(
        decoder.hoa_numeric_profile(),
        Some("apac-hoa-salient-math-v1")
    );
    let first = bytes(&f["first"]);
    decoder.decode_frame(&first).unwrap();
    let before = snapshot(&decoder);
    for key in [
        "last_element_error",
        "late_spatial_error",
        "late_tail_error",
        "embedded_error",
        "outer_after_embedded_error",
    ] {
        assert!(decoder.decode_frame(&bytes(&f[key])).is_err(), "{key}");
        assert_eq!(before, snapshot(&decoder));
    }
    for end in 0..first.len() {
        assert!(decoder.decode_frame(&first[..end]).is_err(), "{end}");
        assert_eq!(before, snapshot(&decoder));
    }
    let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
    clean.decode_frame(&first).unwrap();
    assert_eq!(
        decoder.decode_frame(&bytes(&f["next"])).unwrap(),
        clean.decode_frame(&bytes(&f["next"])).unwrap()
    );
    assert_eq!(snapshot(&decoder), snapshot(&clean));
    decoder.reset();
    assert_eq!(
        snapshot(&decoder),
        snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
    );
    assert!(decoder.scan_frame(&first).is_err());
}

/// Explicit, bounded real-input check. It writes only small metadata/digests,
/// never an unbounded full-song PCM export. One source per selected class.
#[test]
#[ignore = "requires explicit APAC_HOA_MEDIA_INPUT and APAC_HOA_MEDIA_REPORT"]
fn hoa_media_stream_digest() {
    use sha2::{Digest, Sha256};
    use std::io::Write;
    let path =
        std::path::PathBuf::from(std::env::var("APAC_HOA_MEDIA_INPUT").expect("explicit source"));
    let destination =
        std::path::PathBuf::from(std::env::var("APAC_HOA_MEDIA_REPORT").expect("explicit report"));
    let mut output = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .unwrap();
    let mut source = input::Input::open(&path).unwrap();
    let info = source.info().clone();
    let table = info.packet_table.value.clone().unwrap();
    let mut decoder = SqDecoder::from_cookie(source.cookie()).unwrap();
    assert!(decoder.hoa_numeric_profile().is_some());
    let mut hash = Sha256::new();
    let mut packets = 0u64;
    let mut frames = 0u64;
    let mut drc_payload_frames = 0u64;
    let mut embedded_frames = 0u64;
    let prime = table.priming_frames as u64;
    let end = prime + table.valid_frames as u64;
    while let Some((index, raw, packet)) = source.next_packet().unwrap() {
        let (samples, counts) = decoder
            .decode_frame_report(&packet)
            .unwrap_or_else(|e| panic!("packet {index}: {e}"));
        drc_payload_frames += counts.drc_payload_frames;
        embedded_frames += counts.embedded_preroll_frames;
        assert_eq!(samples.len(), 16384);
        let first = raw.max(prime);
        let last = (raw + 1024).min(end);
        if first < last {
            let mut buffer = Vec::with_capacity((last - first) as usize * 64);
            for v in &samples[((first - raw) * 16) as usize..((last - raw) * 16) as usize] {
                buffer.extend(v.to_le_bytes());
            }
            hash.update(buffer);
            frames += last - first;
        }
        packets += 1;
    }
    source.verify_remaining().unwrap();
    assert_eq!(frames, table.valid_frames as u64);
    assert_eq!(Some(packets), info.packet_count.value);
    let report = serde_json::json!({"passed":true,"packets":packets,"valid_frames":frames,"pcm_sha256":format!("{:x}",hash.finalize()),"channels":16,"numeric_profile":crate::frame::HOA_NUMERIC_PROFILE,"layout":decoder.channel_layout(),"input":source.report(),"drc_payload_frames":drc_payload_frames,"embedded_frames":embedded_frames,"compiler":env!("APAC_BUILD_RUSTC"),"debug_assertions":cfg!(debug_assertions)});
    serde_json::to_writer_pretty(&mut output, &report).unwrap();
    output.write_all(b"\n").unwrap();
}
