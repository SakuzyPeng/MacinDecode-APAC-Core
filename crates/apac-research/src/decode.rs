use crate::{
    error::{Error, Result},
    model::*,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
};
use crate::{input::Input, synthesis::SqDecoder};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Write, path::Path, time::Instant};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[cfg_attr(feature = "clap", derive(clap::ValueEnum))]
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
    let mut decoder = SqDecoder::from_config(bundle.config())?;
    let channels = decoder.channel_count();
    let backend = decoder.backend();
    let state_profile = decoder.state_profile();
    let support_scope = decoder.support_scope();
    if decoder.hoa_numeric_profile().is_some() && bundle.first_packet_index() != 0 {
        return Err(Error::new(
            "SQ access",
            "HOA input must include packet zero to establish sequential state",
        ));
    }
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
    let mut pcm = PcmInfo {
        schema_version: SCHEMA_VERSION,
        complete: true,
        pcm_file: "pcm.f32le".into(),
        encoding: "f32le".into(),
        interleaved: true,
        sample_rate: info.format.sample_rate,
        channels,
        layout: if decoder.stream_context().is_some()
            || decoder
                .hoa_context()
                .is_some_and(|c| c.shared_configuration_enabled())
        {
            Property::known(decoder.channel_layout().clone())
        } else {
            info.layout
        },
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
                "numeric_profile":crate::synthesis::NUMERIC_PROFILE,
                "cac_numeric_profile":crate::frame::CAC_NUMERIC_PROFILE,
                "cac_tables_sha256":crate::frame::cac_math_sha256(),
                "tns_numeric_profile":crate::frame::TNS_NUMERIC_PROFILE,
                "tns_tables_sha256":crate::frame::tns_math_sha256(),
                "bwe2_numeric_profile":crate::frame::BWE2_NUMERIC_PROFILE,
                "bwe2_format_sha256":crate::bwe2_math::format_sha256(),
                "bwe2_tables_sha256":crate::bwe2_math::math_sha256(),
                "qualification":crate::synthesis::QUALIFICATION,
                "compiler":env!("APAC_BUILD_RUSTC"),
                "debug_assertions":cfg!(debug_assertions),
                "tables_sha256":crate::numeric::tables_sha256()
            })),
        )]),
        all_finite: true,
    };
    if let Some(profile) = decoder.channel_layout_profile() {
        pcm.decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap()["channel_layout_profile"] = json!(profile);
    }
    if let Some(profile) = decoder.hoa_numeric_profile() {
        pcm.decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap()["hoa_numeric_profile"] = json!(profile);
    }
    if let Some(components) = decoder.components() {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_multiple_asc_profile"] = json!(crate::frame::stream::PROFILE);
        value["components"] = json!(components);
        value["hoa_shared_config_format_sha256"] =
            json!(crate::frame::hoa_shared_config_format_sha256());
        if let Some(context) = decoder.stream_context()
            && !context.additional_components().is_empty()
        {
            value["additional_components"] = json!(context.additional_components());
        }
    }
    if decoder.shared_drc_syntax_used() {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["shared_drc_syntax_profile"] = json!(crate::frame::HOA_SHARED_DRC_PROFILE);
        value["shared_drc_format_sha256"] = json!(crate::frame::hoa_shared_drc_format_sha256());
    }
    if let Some(context) = decoder.hoa_context() {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        if context.shared_configuration_enabled() {
            value["hoa_shared_config_profile"] = json!(crate::frame::HOA_SHARED_CONFIG_PROFILE);
            value["hoa_shared_config_format_sha256"] =
                json!(crate::frame::hoa_shared_config_format_sha256());
            value["hoa_sfb_sample_rate_hz"] = json!(context.sfb_sample_rate_hz());
        }
        if let Some(mapping) = context.static_remapping() {
            value["hoa_static_remapping"] = json!(mapping);
        }
        if context.source_layout_enabled() {
            value["hoa_source_layout_profile"] = json!(crate::frame::HOA_SOURCE_LAYOUT_PROFILE);
            value["hoa_source_layout_format_sha256"] =
                json!(crate::frame::hoa_source_layout_format_sha256());
            value["hoa_source_layout"] = json!(context.channel_layout());
            value["hoa_source_channel_count"] = json!(context.channel_count());
            if let Some(normalization) = context.source_normalization() {
                value["hoa_source_normalization"] = json!(normalization);
            }
        }
        if context.controls_extended() {
            value["hoa_spatial_controls_profile"] =
                json!(crate::frame::HOA_SPATIAL_CONTROLS_PROFILE);
            value["hoa_spatial_controls_format_sha256"] =
                json!(crate::frame::hoa_spatial_controls_format_sha256());
            value["hoa_spatial_controls"] = json!(context.spatial_controls());
            if context.spatial_controls().flag_b {
                value["hoa_frame_configuration_state_sha256"] =
                    json!(crate::frame::hoa_frame_configuration_state_sha256());
            }
        }
        if context.dynamic_domains_extended() {
            value["hoa_dynamic_domains_profile"] = json!(crate::frame::HOA_DYNAMIC_DOMAINS_PROFILE);
            value["hoa_dynamic_domains_format_sha256"] =
                json!(crate::frame::hoa_dynamic_domains_format_sha256());
        }
        if !context.full_order() {
            value["hoa_partial_domain_profile"] = json!(crate::frame::HOA_PARTIAL_PROFILE);
            value["hoa_full_order"] = json!(false);
            value["hoa_recovery_slot_count"] = json!(context.recovery_slot_count());
            value["hoa_output_coefficient_count"] = json!(context.channel_count());
        }
        if context.expanded_orders() {
            value["hoa_expanded_orders_profile"] = json!(crate::frame::HOA_EXPANDED_ORDERS_PROFILE);
            value["hoa_expanded_math_sha256"] = json!(crate::frame::hoa_expanded_math_sha256());
        }
        if context.transport_extended() {
            value["hoa_transport_profile"] = json!(crate::frame::HOA_TRANSPORT_PROFILE);
            value["hoa_transport_format_sha256"] =
                json!(crate::frame::hoa_transport_format_sha256());
            value["hoa_transport_channels"] = json!(context.transport_channels());
            value["hoa_core_channels"] = json!(context.core_channels());
            value["hoa_recovery_slot_count"] = json!(context.recovery_slot_count());
            value["hoa_output_coefficient_count"] = json!(context.channel_count());
            value["hoa_transport_elements"] = json!(context.transport_elements());
        }
        if context.profile_id() != 5 || context.level_id() != 0 {
            value["hoa_profile_id"] = json!(context.profile_id());
            value["hoa_level_id"] = json!(context.level_id());
        }
    }
    if let Some(context) = decoder.hoa_context()
        && context.ambient_count_extended()
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_ambient_component_count"] = json!(context.ambient_components());
        value["hoa_ambient_count_profile"] = json!("apac-hoa-ambient-counts-v1");
    }
    if decoder
        .hoa_context()
        .is_some_and(|c| c.salient_components() != 0)
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        let context = decoder.hoa_context().unwrap();
        if context.quantization_extended() {
            value["hoa_salient_quantization_bits"] = json!(context.quantization_bits());
            value["hoa_salient_quantization_profile"] = json!("apac-hoa-salient-quantization-v1");
        }
        if context.component_orders_extended() {
            value["hoa_salient_component_orders"] = json!(
                context
                    .salient_component_configurations()
                    .iter()
                    .map(|c| c.order)
                    .collect::<Vec<_>>()
            );
            if context.salient_components() != 5 {
                value["hoa_salient_component_count"] = json!(context.salient_components());
                value["hoa_salient_count_profile"] = json!("apac-hoa-salient-counts-v1");
            }
            value["hoa_salient_components"] = json!(context.component_order_info());
            value["hoa_descriptor_numeric_profile"] = json!(context.descriptor_numeric_profile());
            if context
                .salient_component_configurations()
                .iter()
                .any(|c| c.order == 1)
            {
                value["hoa_salient_order1_profile"] =
                    json!(crate::frame::HOA_SALIENT_ORDER1_PROFILE);
            }
        } else {
            value["hoa_format_sha256"] = json!(crate::frame::hoa_salient_format_sha256(
                context.recovery_slot_count()
            ));
        }
        value["hoa_tables_sha256"] = json!(crate::frame::hoa_salient_math_sha256());
        if let Some(context) = decoder.hoa_context()
            && (context.ambient_components() != 0 || context.dynamic_selection_enabled())
        {
            value["hoa_descriptor_numeric_profile"] = json!(context.descriptor_numeric_profile());
        }
    }
    if decoder
        .hoa_context()
        .is_some_and(|c| c.static_ambient_enabled())
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_ambient_format_sha256"] = json!(crate::frame::hoa_ambient_format_sha256());
        value["hoa_ambient_tables_sha256"] = json!(crate::frame::hoa_ambient_math_sha256());
    }
    if let Some(context) = decoder.hoa_context()
        && context.dynamic_selection_enabled()
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_recovery_numeric_profile"] = json!(context.recovery_numeric_profile());
        value["hoa_dynamic_format_sha256"] = json!(if context.dynamic_domains_extended() {
            crate::frame::hoa_dynamic_domains_format_sha256()
        } else if context.spatial_controls().flag_f {
            crate::frame::hoa_dynamic_format_sha256(
                context.dynamic_subband_count().expect("dynamic bands"),
            )
        } else {
            crate::frame::hoa_spatial_controls_format_sha256()
        });
        value["hoa_internal_order"] = json!(context.order());
        if let Some(order) = context.channel_layout().ambisonic_order {
            value["hoa_output_order"] = json!(order);
        } else if !context.source_layout_enabled() {
            value["hoa_output_containing_order"] = json!(context.output_order());
        }
        if context.dynamic_domains_extended() {
            value["hoa_output_coefficient_count"] = json!(context.channel_count());
        }
        value["hoa_recovery_slot_count"] = json!(context.recovery_slot_count());
        if let Some(count) = context.dynamic_subband_count().filter(|&n| n < 8) {
            value["hoa_dynamic_subband_count"] = json!(count);
            value["hoa_dynamic_subband_profile"] = json!(crate::frame::HOA_DYNAMIC_SUBBAND_PROFILE);
        }
    }
    if let Some(context) = decoder.hoa_context()
        && context.ambient_combination() == crate::frame::AmbientCombination::Add
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_ambient_combination"] = json!(context.ambient_combination());
        value["hoa_recovery_numeric_profile"] = json!(context.recovery_numeric_profile());
    }
    if let Some((counts, method)) = decoder
        .hoa_context()
        .and_then(|c| {
            Some((
                c.salient_component_configurations()
                    .iter()
                    .map(|s| s.subband_count)
                    .collect::<Vec<_>>(),
                c.salient_partition_method()?,
            ))
        })
        .filter(|(counts, method)| {
            *counts != [4; 5]
                || *method != 0
                || !decoder.hoa_context().unwrap().spatial_controls().flag_f
        })
    {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        value["hoa_salient_subband_counts"] = json!(counts);
        value["hoa_salient_subband_profile"] = json!(crate::frame::HOA_SALIENT_SUBBAND_PROFILE);
        value["hoa_salient_subband_format_sha256"] =
            json!(
                if decoder.hoa_context().unwrap().spatial_controls().flag_f {
                    crate::frame::hoa_salient_subbands_format_sha256(method)
                } else {
                    crate::frame::hoa_spatial_controls_format_sha256()
                }
            );
        if method != 0 {
            value["hoa_salient_partition_method"] = json!(method);
            value["hoa_salient_partition_profile"] =
                json!(crate::frame::HOA_SALIENT_PARTITION_PROFILE);
        }
    }
    out.json("pcm.json", &pcm)?;
    let mut report = json!({"schema_version":SCHEMA_VERSION,"complete":true,"experimental":true,"numeric_profile":crate::synthesis::NUMERIC_PROFILE,"cac_numeric_profile":crate::frame::CAC_NUMERIC_PROFILE,"tns_numeric_profile":crate::frame::TNS_NUMERIC_PROFILE,"tns_tables_sha256":crate::frame::tns_math_sha256(),"bwe2_numeric_profile":crate::frame::BWE2_NUMERIC_PROFILE,"bwe2_format_sha256":crate::bwe2_math::format_sha256(),"bwe2_tables_sha256":crate::bwe2_math::math_sha256(),"numerical_qualification":crate::synthesis::QUALIFICATION,"backend":backend,"native_apis_used":false,
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
    if let Some(profile) = decoder.channel_layout_profile() {
        report["channel_layout_profile"] = json!(profile);
    }
    if channels != 2 || decoder.stream_context().is_some() {
        report["channel_count"] = json!(channels);
        report["channel_layout"] = json!(decoder.channel_layout());
        report["absent_elements"] = json!(absent_elements);
        report["embedded_absent_elements"] = json!(embedded_absent_elements);
    }
    if let Some(profile) = decoder.hoa_numeric_profile() {
        report["hoa_numeric_profile"] = json!(profile);
    }
    if let Some(mode) = access {
        let profile = if decoder.hoa_numeric_profile().is_some() {
            crate::synthesis::HOA_ACCESS_PROFILE
        } else {
            crate::synthesis::ACCESS_PROFILE
        };
        report["access"] = json!({"profile":profile,"mode":mode,
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

#[cfg(test)]
#[path = "hoa_media_tests.rs"]
mod hoa_media_tests;
