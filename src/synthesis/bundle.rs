use super::{SqDecoder, input::Input};
use crate::{
    error::{Error, Result},
    model::*,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Write, path::Path};

#[derive(Debug, Clone, Copy, Default)]
pub struct SqDecodeOptions {
    /// Absolute valid-audio coordinate; omitted starts at the input target window.
    pub start_frame: Option<u64>,
    /// Omitted exports the remaining target window (whole valid audio for CAF). Zero is rejected.
    pub frames: Option<u64>,
}

pub fn decode_sq(input: &Path, destination: &Path, limit: u64) -> Result<Value> {
    decode_sq_with_options(input, destination, SqDecodeOptions::default(), limit)
}

pub fn decode_sq_with_options(
    input: &Path,
    destination: &Path,
    options: SqDecodeOptions,
    limit: u64,
) -> Result<Value> {
    let mut bundle = Input::open(input)?;
    let info = bundle.info().clone();
    let table = info
        .packet_table
        .value
        .clone()
        .ok_or_else(|| Error::new("SQ decoder", "missing packet table"))?;
    let range = bundle.range(
        options.start_frame,
        options.frames.unwrap_or((table.valid_frames as u64).max(1)),
    )?;
    let mut decoder = SqDecoder::from_cookie(bundle.cookie())?;
    let channels = decoder.channel_count();
    let backend = decoder.backend();
    let state_profile = decoder.state_profile();
    let support_scope = decoder.support_scope();
    if info.format.channels != channels {
        return Err(Error::new(
            "SQ decoder",
            "input channel count disagrees with decoder",
        ));
    }
    if let Some(layout) = &info.layout.value
        && !layout.equivalent(decoder.channel_layout())
    {
        return Err(Error::new(
            "SQ decoder",
            format!(
                "input channel layout disagrees with decoder: expected cookie layout tag {:#010x}, zero bitmap and no descriptions",
                decoder.channel_layout().tag
            ),
        ));
    }
    let mut absent_elements = 0u64;
    let mut embedded_absent_elements = 0u64;
    let out = OutputDir::create(destination, Budget::new(limit))?;
    out.budget.ensure(
        pcm_bytes(range.frames, channels)?
            .checked_add(65536)
            .ok_or_else(|| Error::new("SQ decoder", "output size overflow"))?,
    )?;
    let mut writer = out.writer("pcm.f32le")?;
    let mut hash = Sha256::new();
    let mut saved = 0;
    let (mut drc_frames, mut drc_missing_history) = (0u64, 0u64);
    let (
        mut decoded_packets,
        mut warmup_packets,
        mut absent_packets,
        mut embedded_frames,
        mut embedded_absent,
    ) = (0u64, 0u64, 0u64, 0u64, 0u64);
    while let Some((packet_index, raw, bytes)) = bundle.next_packet()? {
        if !range.drain_to_eof && raw >= range.raw_end {
            break;
        }
        let (samples, counts) = decoder.decode_frame_report(&bytes).map_err(|mut e| {
            e.packet_index = Some(packet_index);
            e
        })?;
        drc_frames += counts.drc_payload_frames;
        drc_missing_history += counts.drc_missing_history_frames;
        decoded_packets += 1;
        warmup_packets += u64::from(raw + 1024 <= range.raw_start);
        absent_packets += u64::from(counts.cpe_absent);
        absent_elements += counts.absent_elements;
        embedded_absent_elements += counts.embedded_absent_elements;
        embedded_frames += counts.embedded_preroll_frames;
        embedded_absent += counts.embedded_cpe_absent;
        let first = raw.max(range.raw_start);
        let last = (raw + 1024).min(range.raw_end);
        if first < last {
            let bytes = pcm_to_le(
                &samples[((first - raw) * u64::from(channels)) as usize
                    ..((last - raw) * u64::from(channels)) as usize],
            )?;
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
        channels,
        layout: info.layout,
        start_frame: range.start_frame,
        requested_frames: range.requested_frames,
        frames: saved,
        bytes: pcm_bytes(saved, channels)?,
        sha256: format!("{:x}", hash.finalize()),
        source: None,
        source_cookie_sha256: Some(crate::model::sha256(bundle.cookie())),
        source_packet_table: Some(table),
        environment: Environment::current(),
        decoder_settings: BTreeMap::from([(
            "implementation".into(),
            Property::known(json!({
                "backend":backend, "experimental":true,
                "packet_state_profile":state_profile,
                "support_scope":support_scope,
                "drc_processing":"off", "loudness_normalization":"off",
                "drc_rules_version":crate::frame::DRC_RULES_VERSION,
                "drc_codebook_sha256":crate::frame::drc_codebook_sha256(),
                "drc_payloads_complete":true,
                "numeric_profile":super::NUMERIC_PROFILE,
                "cac_numeric_profile":crate::frame::CAC_NUMERIC_PROFILE,
                "cac_tables_sha256":crate::frame::cac_math_sha256(),
                "tns_numeric_profile":crate::frame::TNS_NUMERIC_PROFILE,
                "tns_tables_sha256":crate::frame::tns_math_sha256(),
                "bwe2_numeric_profile":crate::frame::BWE2_NUMERIC_PROFILE,
                "bwe2_format_sha256":crate::bwe2_math::format_sha256(),
                "bwe2_tables_sha256":crate::bwe2_math::math_sha256(),
                "qualification":super::QUALIFICATION,
                "compiler":env!("APAC_BUILD_RUSTC"),
                "debug_assertions":cfg!(debug_assertions),
                "tables_sha256":crate::numeric::tables().sha256
            })),
        )]),
        all_finite: true,
    };
    out.json("pcm.json", &pcm)?;
    let mut report = json!({"schema_version":SCHEMA_VERSION,"complete":true,"experimental":true,"numeric_profile":super::NUMERIC_PROFILE,"cac_numeric_profile":crate::frame::CAC_NUMERIC_PROFILE,"tns_numeric_profile":crate::frame::TNS_NUMERIC_PROFILE,"tns_tables_sha256":crate::frame::tns_math_sha256(),"bwe2_numeric_profile":crate::frame::BWE2_NUMERIC_PROFILE,"bwe2_format_sha256":crate::bwe2_math::format_sha256(),"bwe2_tables_sha256":crate::bwe2_math::math_sha256(),"numerical_qualification":super::QUALIFICATION,"backend":backend,"native_apis_used":false,
        "packet_state_profile":state_profile,
        "drc_processing":"off","loudness_normalization":"off",
        "drc_rules_version":crate::frame::DRC_RULES_VERSION,
        "drc_payloads_complete":true,"drc_payload_frames":drc_frames,
        "drc_frames_without_prior_gain_node":drc_missing_history,
        "packets":decoded_packets,"integrity_checked_packets":bundle.consumed_packets(),
        "warmup_packets":warmup_packets,"cpe_absent_packets":absent_packets,
        "embedded_preroll_frames":embedded_frames,"embedded_cpe_absent_frames":embedded_absent,
        "raw_frames_decoded":decoded_packets*1024,
        "input":bundle.report(),"range":range,"saved_frames":saved,"tail_policy":"no implicit flush or added frames","pcm":pcm});
    if channels != 2 {
        report["channel_count"] = json!(channels);
        report["channel_layout"] = json!(decoder.channel_layout());
        report["absent_elements"] = json!(absent_elements);
        report["embedded_absent_elements"] = json!(embedded_absent_elements);
    }
    out.json("decode-sq.json", &report)?;
    out.complete()?;
    Ok(report)
}
