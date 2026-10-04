//! Real-media streaming check driven by validate_hoa_media.py.
use crate::{input::Input, synthesis::Decoder};

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
    let mut source = Input::open(&path).unwrap();
    let info = source.info().clone();
    let table = info.packet_table.value.clone().unwrap();
    let mut decoder = Decoder::from_cookie(source.cookie()).unwrap();
    assert!(decoder.hoa_numeric_profile().is_some());
    let mut hash = Sha256::new();
    let mut packets = 0u64;
    let mut frames = 0u64;
    let mut drc_payload_frames = 0u64;
    let mut embedded_frames = 0u64;
    let prime = table.priming_frames as u64;
    let end = prime + table.valid_frames as u64;
    while let Some((index, raw, packet)) = source.next_packet().unwrap() {
        let mut samples = vec![0f32; 16384];
        let counts = decoder
            .decode(&packet, &mut samples)
            .unwrap_or_else(|e| panic!("packet {index}: {e}"));
        drc_payload_frames += counts.drc_payload_frames;
        embedded_frames += counts.embedded_preroll_frames;
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
    let report = serde_json::json!({"passed":true,"packets":packets,"valid_frames":frames,"pcm_sha256":format!("{:x}",hash.finalize()),"channels":16,"numeric_profile":crate::frame::HOA_NUMERIC_PROFILE,"layout":decoder.info().layout,"input":source.report(),"drc_payload_frames":drc_payload_frames,"embedded_frames":embedded_frames,"compiler":env!("APAC_BUILD_RUSTC"),"debug_assertions":cfg!(debug_assertions)});
    serde_json::to_writer_pretty(&mut output, &report).unwrap();
    output.write_all(b"\n").unwrap();
}
