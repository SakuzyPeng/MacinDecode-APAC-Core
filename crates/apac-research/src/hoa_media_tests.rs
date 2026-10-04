//! Real-media streaming check driven by validate_hoa_media.py.
use crate::{input::Input, synthesis::Decoder};
use apac_container::{Access, Reader};

fn check_media_decoder(decoder: &Decoder) {
    assert!(crate::implementation::hoa_numeric_profile(decoder).is_some());
    assert_eq!(
        decoder.info().channel_count,
        16,
        "HOA media validation requires exactly 16 output channels"
    );
}
fn media_decoder(cookie: &[u8]) -> Decoder {
    let decoder = Decoder::from_cookie(cookie).unwrap();
    check_media_decoder(&decoder);
    decoder
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
    let source = Input::open(&path).unwrap();
    let info = source.info().clone();
    let table = info.packet_table.value.clone().unwrap();
    // The whole valid audio, decoded sequentially from packet zero.
    let mut reader = Reader::open(source, None, None, Access::Sequential).unwrap();
    check_media_decoder(reader.decoder());
    let mut hash = Sha256::new();
    let mut frames = 0u64;
    let mut samples = vec![0f32; 16384];
    loop {
        let n = reader.read(&mut samples).unwrap_or_else(|e| {
            let e = crate::error::Error::from(e);
            panic!("packet {:?}: {e}", e.packet_index)
        });
        if n == 0 {
            break;
        }
        let mut buffer = Vec::with_capacity(n * 64);
        for v in &samples[..n * 16] {
            buffer.extend(v.to_le_bytes());
        }
        hash.update(buffer);
        frames += n as u64;
    }
    let (source, decoder, stats) = reader.finish().unwrap();
    let packets = stats.decoded_packets;
    let drc_payload_frames = stats.drc_payload_frames;
    let embedded_frames = stats.embedded_preroll_frames;
    assert_eq!(frames, table.valid_frames as u64);
    assert_eq!(Some(packets), info.packet_count.value);
    let report = serde_json::json!({"passed":true,"packets":packets,"valid_frames":frames,"pcm_sha256":format!("{:x}",hash.finalize()),"channels":16,"numeric_profile":crate::frame::HOA_NUMERIC_PROFILE,"layout":decoder.info().layout,"input":source.report(),"drc_payload_frames":drc_payload_frames,"embedded_frames":embedded_frames,"compiler":env!("APAC_BUILD_RUSTC"),"debug_assertions":cfg!(debug_assertions)});
    serde_json::to_writer_pretty(&mut output, &report).unwrap();
    output.write_all(b"\n").unwrap();
}

#[test]
fn media_validation_requires_sixteen_hoa_channels() {
    use serde_json::Value;
    let bytes = |value: &Value| {
        let text = value.as_str().unwrap();
        (0..text.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
            .collect::<Vec<_>>()
    };
    let orders: Value =
        serde_json::from_str(include_str!("../../../data/hoa-orders-state-v1.json")).unwrap();
    for row in orders["fixtures"].as_array().unwrap() {
        let cookie = bytes(&row["cookie"]);
        // These are supported 4- and 9-channel HOA streams, but cannot be
        // cropped or hashed using the media check's 16-channel stride.
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        assert!(decoder.decode_vec(&bytes(&row["first"])).is_ok());
        assert!(std::panic::catch_unwind(|| media_decoder(&cookie)).is_err());
    }
    let row: Value =
        serde_json::from_str(include_str!("../../../data/hoa-ambient-state-v1.json")).unwrap();
    let mut decoder = media_decoder(&bytes(&row["cookie"]));
    assert_eq!(
        decoder.decode_vec(&bytes(&row["first"])).unwrap().len(),
        16384
    );
}
