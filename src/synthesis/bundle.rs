use super::{SqDecoder, input::Input};
use crate::{
    error::{Error, Result},
    model::*,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Write, path::Path, time::Instant};

#[derive(
    Debug,
    Clone,
    Copy,
    Default,
    PartialEq,
    Eq,
    serde::Serialize,
    serde::Deserialize,
    clap::ValueEnum,
)]
#[serde(rename_all = "lowercase")]
pub enum SqAccessMode {
    #[default]
    Sequential,
    Fast,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct SqDecodeOptions {
    /// Absolute valid-audio coordinate; omitted starts at the input target window.
    pub start_frame: Option<u64>,
    /// Omitted exports the remaining target window (whole valid audio for CAF/MP4). Zero is rejected.
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
    decode_with_access(input, destination, options, None, limit)
}

/// Explicit container access policy. Existing entry points retain sequential access.
pub fn decode_sq_with_access(
    input: &Path,
    destination: &Path,
    options: SqDecodeOptions,
    access: SqAccessMode,
    limit: u64,
) -> Result<Value> {
    decode_with_access(input, destination, options, Some(access), limit)
}

fn decode_with_access(
    input: &Path,
    destination: &Path,
    options: SqDecodeOptions,
    access: Option<SqAccessMode>,
    limit: u64,
) -> Result<Value> {
    let total_timer = Instant::now();
    let fast = access == Some(SqAccessMode::Fast);
    if fast && input.is_dir() {
        return Err(Error::new(
            "SQ access",
            "fast access requires a CAF/MP4 file",
        ));
    }
    let mut bundle = Input::open(input)?;
    let preparation_seconds = total_timer.elapsed().as_secs_f64();
    if fast && !bundle.is_container() {
        return Err(Error::new(
            "SQ access",
            "fast access requires a CAF/MP4 file",
        ));
    }
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
    let synthesis_start = (range.frames != 0).then(|| (range.raw_start / 1024).saturating_sub(1));
    let mut prefix_packets = 0u64;
    let mut prefix_frames = 0u64;
    let mut numeric_prefix_packets = 0u64;
    let mut numeric_prefix_elements = 0u64;
    let mut bounded_prefix_elements = 0u64;
    let mut first_synthesis_packet = None;
    let mut state_before_output = None;
    let (mut read_seconds, mut scan_seconds, mut synthesis_seconds) = (0., 0., 0.);
    let (mut full_parse_seconds, mut render_seconds) = (0., 0.);
    let mut saved = 0;
    let (mut drc_frames, mut drc_missing_history) = (0u64, 0u64);
    let (
        mut decoded_packets,
        mut warmup_packets,
        mut absent_packets,
        mut embedded_frames,
        mut embedded_absent,
    ) = (0u64, 0u64, 0u64, 0u64, 0u64);
    loop {
        let timer = Instant::now();
        let next = bundle.next_packet()?;
        read_seconds += timer.elapsed().as_secs_f64();
        let Some((packet_index, raw, bytes)) = next else {
            break;
        };
        if !range.drain_to_eof && raw >= range.raw_end {
            break;
        }
        if access.is_some() && range.frames != 0 && raw / 1024 == range.raw_start / 1024 {
            state_before_output = Some(decoder.metadata_sha256());
        }
        if fast && synthesis_start.is_none_or(|start| packet_index < start) {
            let timer = Instant::now();
            let counts = decoder.scan_frame(&bytes).map_err(|mut e| {
                e.packet_index = Some(packet_index);
                e
            })?;
            scan_seconds += timer.elapsed().as_secs_f64();
            prefix_packets += 1;
            prefix_frames += counts.frames;
            numeric_prefix_packets += u64::from(counts.numeric_elements != 0);
            numeric_prefix_elements += counts.numeric_elements;
            bounded_prefix_elements += counts.present_elements - counts.numeric_elements;
            drc_frames += counts.drc_payload_frames;
            drc_missing_history += counts.drc_missing_history_frames;
            continue;
        }
        first_synthesis_packet.get_or_insert(packet_index);
        let timer = Instant::now();
        let (samples, counts) = decoder.decode_frame_report(&bytes).map_err(|mut e| {
            e.packet_index = Some(packet_index);
            e
        })?;
        synthesis_seconds += timer.elapsed().as_secs_f64();
        full_parse_seconds += counts.parse_seconds;
        render_seconds += counts.synthesis_seconds;
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
    let timer = Instant::now();
    bundle.verify_remaining()?;
    read_seconds += timer.elapsed().as_secs_f64();
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
    if let Some(mode) = access {
        report["access"] = json!({"profile":super::ACCESS_PROFILE,"mode":mode,
            "verification_scope":"full_input","prefix_scanned_packets":prefix_packets,
            "prefix_scanned_frames":prefix_frames,"prefix_numeric_packets":numeric_prefix_packets,
            "prefix_numeric_elements":numeric_prefix_elements,"prefix_bounded_elements":bounded_prefix_elements,
            "synthesized_packets":decoded_packets,"synthesis_start_packet":first_synthesis_packet,
            "external_warmup_packets":warmup_packets,"metadata_before_output_sha256":state_before_output,
            "metadata_after_processing_sha256":decoder.metadata_sha256(),
            "timings_seconds":{"initial_verification":preparation_seconds,"read_and_final_verification":read_seconds,
                "prefix_scan":scan_seconds,"full_decode_parse":full_parse_seconds,"synthesis":render_seconds,
                "packet_decode_and_synthesis":synthesis_seconds,"total":total_timer.elapsed().as_secs_f64()}});
    }
    out.json("decode-sq.json", &report)?;
    out.complete()?;
    Ok(report)
}
