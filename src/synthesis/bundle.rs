use super::SqDecoder;
use crate::{
    error::{Error, Result},
    model::*,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
    packets::PacketBundle,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Write, path::Path};

pub fn decode_sq(directory: &Path, destination: &Path, limit: u64) -> Result<Value> {
    let mut bundle = PacketBundle::open(directory)?;
    if bundle.manifest().start_packet != 0 {
        return Err(Error::new(
            "SQ decoder",
            "decode from source packet zero; random-access state is not implemented",
        ));
    }
    let info = bundle.manifest().file.clone();
    let table = info
        .packet_table
        .value
        .clone()
        .ok_or_else(|| Error::new("SQ decoder", "missing packet table"))?;
    let range = bundle.range(Some(0), (table.valid_frames as u64).max(1))?;
    let mut decoder = SqDecoder::from_cookie(bundle.cookie())?;
    let out = OutputDir::create(destination, Budget::new(limit))?;
    out.budget.ensure(
        pcm_bytes(range.frames, 2)?
            .checked_add(65536)
            .ok_or_else(|| Error::new("SQ decoder", "output size overflow"))?,
    )?;
    let mut writer = out.writer("pcm.f32le")?;
    let mut hash = Sha256::new();
    let mut saved = 0;
    while let Some((packet, bytes)) = bundle.next_packet()? {
        let samples = decoder.decode_frame(&bytes).map_err(|mut e| {
            e.packet_index = Some(packet.packet_index);
            e
        })?;
        let raw = packet.raw_frame()?;
        let first = raw.max(range.raw_start);
        let last = (raw + 1024).min(range.raw_end);
        if first < last {
            let bytes =
                pcm_to_le(&samples[((first - raw) * 2) as usize..((last - raw) * 2) as usize])?;
            writer.write_all(&bytes)?;
            hash.update(&bytes);
            saved += last - first;
        }
    }
    bundle.verify_remaining()?;
    writer.finish()?;
    if saved != range.frames {
        return Err(Error::new("SQ decoder", "incomplete PCM frame range"));
    }
    let pcm = PcmInfo {
        schema_version: SCHEMA_VERSION,
        complete: true,
        pcm_file: "pcm.f32le".into(),
        encoding: "f32le".into(),
        interleaved: true,
        sample_rate: info.format.sample_rate,
        channels: 2,
        layout: info.layout,
        start_frame: range.start_frame,
        requested_frames: range.requested_frames,
        frames: saved,
        bytes: pcm_bytes(saved, 2)?,
        sha256: format!("{:x}", hash.finalize()),
        source: None,
        source_cookie_sha256: Some(crate::model::sha256(bundle.cookie())),
        source_packet_table: Some(table),
        environment: Environment::current(),
        decoder_settings: BTreeMap::from([(
            "implementation".into(),
            Property::known(json!({
                "backend":"rust_sq_f32_modulation_f64_fft_v1", "experimental":true,
                "qualification":"native high-amplitude comparison pending"
            })),
        )]),
        all_finite: true,
    };
    out.json("pcm.json", &pcm)?;
    let report = json!({"schema_version":SCHEMA_VERSION,"complete":true,"experimental":true,"numerical_qualification":"high_amplitude_comparison_pending","backend":"rust_sq_f32_modulation_f64_fft_v1","native_apis_used":false,
        "packets":bundle.consumed_packets(),"range":range,"saved_frames":saved,"tail_policy":"no implicit flush or added frames","pcm":pcm});
    out.json("decode-sq.json", &report)?;
    out.complete()?;
    Ok(report)
}
