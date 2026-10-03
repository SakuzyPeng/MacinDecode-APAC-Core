//! Compact exhaustive syntax acceptance. Input templates/truth are Python-authored.
use apac_core::frame::{ChannelFrameContext, parse_channel_packet};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs::OpenOptions, io::Write, path::PathBuf};
fn bytes(s: &str) -> Vec<u8> {
    s.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect()
}
fn word(hash: &mut Sha256, v: u64) {
    hash.update(v.to_le_bytes());
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some("--report") {
        return Err("expected --report PATH".into());
    }
    let destination = PathBuf::from(args.next().ok_or("missing report path")?);
    if args.next().is_some() {
        return Err("unexpected argument".into());
    }
    // Reserve the output first; an interrupted or failed run never reports success.
    let mut out = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    let frozen: Value =
        serde_json::from_str(include_str!("../../../data/layout-presence-v1.json"))?;
    let mut rows = Vec::new();
    let mut total = 0;
    for row in frozen["layouts"].as_array().unwrap() {
        let n = row["channels"].as_u64().unwrap();
        let rate = row["rate"].as_u64().unwrap();
        let context = ChannelFrameContext::from_cookie(&bytes(row["cookie"].as_str().unwrap()))?;
        assert!(context.is_supported());
        let templates = row["templates"].as_array().unwrap();
        let count = row["cases"].as_u64().unwrap();
        assert_eq!(count, 1 << templates.len());
        let mut hash = Sha256::new();
        for mask in 0..count {
            let mut wire = String::from("01");
            let mut expected = Vec::new();
            for (i, t) in templates.iter().enumerate() {
                let start = wire.len();
                let present = mask & (1 << i) == 0;
                wire.push_str(if present {
                    t["wire"].as_str().unwrap()
                } else {
                    "0"
                });
                let end = if present {
                    start + t["body_bits"].as_u64().unwrap() as usize
                } else {
                    start + 1
                };
                expected.push((start, end, wire.len()));
            }
            while !wire.len().is_multiple_of(8) {
                wire.push('0');
            }
            let core = wire.len();
            wire.push('0');
            while !wire.len().is_multiple_of(8) {
                wire.push('0');
            }
            let packet: Vec<u8> = wire
                .as_bytes()
                .as_chunks::<8>()
                .0
                .iter()
                .map(|b| b.iter().fold(0, |a, &v| (a << 1) | (v - b'0')))
                .collect();
            let r = parse_channel_packet(&context, &packet)?;
            assert!(r.packet_complete && r.frame.unknown_ranges.is_empty());
            assert_eq!(
                r.channel_layout_profile.as_deref(),
                Some("apac-channel-layout-v2")
            );
            assert_eq!(r.frame.component_end_bit_offset, Some(core));
            assert_eq!(r.frame.stop_bit_offset, wire.len());
            assert_eq!(r.elements.len(), templates.len());
            for v in [
                n,
                rate,
                mask,
                packet.len() as u64,
                core as u64,
                wire.len() as u64,
            ] {
                word(&mut hash, v);
            }
            hash.update(Sha256::digest(&packet));
            for (i, (e, t)) in r.elements.iter().zip(templates).enumerate() {
                assert_eq!(serde_json::to_value(&e.configuration)?, t["configuration"]);
                assert_eq!(e.present, mask & (1 << i) == 0);
                assert_eq!(e.spectrum_complete, e.present);
                assert!(e.element_complete);
                let end = e.end_bit_offset.unwrap();
                let tools = e.bwe2.as_ref().map_or(end, |b| b.end_bit_offset);
                assert_eq!((e.start_bit_offset, end, tools), expected[i]);
                assert_eq!(
                    e.channels.len(),
                    if e.present {
                        e.configuration.output_channels.len()
                    } else {
                        0
                    }
                );
                for c in &e.channels {
                    assert!(c.quantized.iter().all(|v| *v == 0));
                    assert!(c.scaled.iter().all(|v| v.to_bits() == 0));
                }
                for c in &e.channels_after_bwe2 {
                    assert!(c.scaled.iter().all(|v| v.to_bits() == 0));
                }
                for v in [e.start_bit_offset, end, tools] {
                    word(&mut hash, v as u64);
                }
            }
        }
        let digest = format!("{:x}", hash.finalize());
        assert_eq!(digest, row["stage_sha256"].as_str().unwrap());
        rows.push(json!({"channels":n,"rate":rate,"cases":count,"stage_sha256":digest}));
        total += count;
    }
    assert_eq!(total, 131328);
    let result = json!({"passed":true,"profile":"apac-channel-layout-v2","cases":total,"layouts":rows,"vector_manifest_sha256":frozen["sha256"],"compiler":env!("APAC_BUILD_RUSTC"),"debug_assertions":cfg!(debug_assertions)});
    serde_json::to_writer_pretty(&mut out, &result)?;
    out.write_all(b"\n")?;
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
