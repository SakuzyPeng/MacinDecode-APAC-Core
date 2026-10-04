use crate::{
    error::{Error, Result},
    model::*,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
};
use crate::{implementation, input::Input, packets::ReplayRange, synthesis::Decoder};
use apac_container::{Access, PacketSource, Reader};
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

/// `metadata_after_processing_sha256`: the SHA-256 of the committed DRC, scene
/// graph and composite/HOA state as a key-sorted JSON object.
pub(crate) fn metadata_sha256(decoder: &Decoder) -> String {
    let state = decoder.metadata_state();
    let drc = state.drc;
    let mut value = json!({"channels":drc.channels,"configuration":drc.configuration,"previous_nodes":drc.previous_nodes});
    if !drc.previous_sequences.is_empty() {
        value["previous_sequences"] = json!(drc.previous_sequences);
    }
    if drc.shared_syntax_used {
        value["shared_drc_syntax_profile"] = json!(apac_core::frame::HOA_SHARED_DRC_PROFILE);
    }
    if let Some(graph) = &drc.scene_graph {
        value["scene_graph"] = json!(graph);
    }
    if let Some(components) = state.components {
        value["components"] = json!(components);
    } else if let Some(hoa) = state.hoa {
        value["hoa"] = json!(hoa);
    }
    sha256(&serde_json::to_vec(&value).expect("finite metadata"))
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
    let bundle = Input::open(input)?;
    let preparation_seconds = total_timer.elapsed().as_secs_f64();
    let info = bundle.info().clone();
    let mut reader = Reader::open(
        bundle,
        options.start_frame,
        options.frames,
        if fast {
            Access::Fast
        } else {
            Access::Sequential
        },
    )?;
    let table = info
        .packet_table
        .value
        .clone()
        .expect("checked by the reader");
    let range = ReplayRange::from(*reader.range());
    let channels = reader.decoder().info().channel_count;
    let backend = implementation::backend(reader.decoder());
    let state_profile = implementation::state_profile(reader.decoder());
    let support_scope = implementation::support_scope(reader.decoder());
    let out = OutputDir::create(destination, Budget::new(limit))?;
    out.budget.ensure(
        pcm_bytes(range.frames, channels)?
            .checked_add(65536)
            .ok_or_else(|| Error::new("SQ decoder", "output size overflow"))?,
    )?;
    let mut writer = out.writer("pcm.f32le")?;
    let mut hash = Sha256::new();
    let mut state_before_output = None;
    let mut samples = vec![0f32; 1024 * channels as usize];
    loop {
        let frames = reader.read_with(&mut samples, |decoder| {
            if access.is_some() {
                state_before_output = Some(metadata_sha256(decoder));
            }
        })?;
        if frames == 0 {
            break;
        }
        let bytes = pcm_to_le(&samples[..frames * channels as usize])?;
        writer.write_all(&bytes)?;
        hash.update(&bytes);
    }
    let (bundle, decoder, stats) = reader.finish()?;
    writer.finish()?;
    let saved = stats.saved_frames;
    if saved != range.frames {
        return Err(Error::new("SQ decoder", "incomplete PCM frame range"));
    }
    let seconds = |d: std::time::Duration| d.as_secs_f64();
    let timings = stats.timings;
    let (read_seconds, scan_seconds, synthesis_seconds) = (
        seconds(timings.read),
        seconds(timings.scan),
        seconds(timings.packet),
    );
    let (full_parse_seconds, render_seconds) =
        (seconds(timings.parse), seconds(timings.synthesize));
    let (drc_frames, drc_missing_history) =
        (stats.drc_payload_frames, stats.drc_missing_history_frames);
    let (decoded_packets, warmup_packets, absent_packets, embedded_frames, embedded_absent) = (
        stats.decoded_packets,
        stats.warmup_packets,
        stats.cpe_absent_packets,
        stats.embedded_preroll_frames,
        stats.embedded_cpe_absent_frames,
    );
    let (absent_elements, embedded_absent_elements) =
        (stats.absent_elements, stats.embedded_absent_elements);
    let (prefix_packets, prefix_frames) = (stats.prefix_packets, stats.prefix_frames);
    let (numeric_prefix_packets, numeric_prefix_elements, bounded_prefix_elements) = (
        stats.prefix_numeric_packets,
        stats.prefix_numeric_elements,
        stats.prefix_bounded_elements,
    );
    let first_synthesis_packet = stats.first_synthesis_packet;
    let mut pcm = PcmInfo {
        schema_version: SCHEMA_VERSION,
        complete: true,
        pcm_file: "pcm.f32le".into(),
        encoding: "f32le".into(),
        interleaved: true,
        sample_rate: info.format.sample_rate,
        channels,
        layout: if decoder.composite().is_some()
            || decoder
                .hoa()
                .is_some_and(|c| c.shared_configuration_enabled())
        {
            Property::known(decoder.info().layout.clone())
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
                "qualification":implementation::QUALIFICATION,
                "compiler":env!("APAC_BUILD_RUSTC"),
                "debug_assertions":cfg!(debug_assertions),
                "tables_sha256":crate::numeric::tables_sha256()
            })),
        )]),
        all_finite: true,
    };
    if let Some(profile) = implementation::channel_layout_profile(&decoder) {
        pcm.decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap()["channel_layout_profile"] = json!(profile);
    }
    if let Some(profile) = implementation::hoa_numeric_profile(&decoder) {
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
        if let Some(context) = decoder.composite()
            && !context.additional_components().is_empty()
        {
            value["additional_components"] = json!(context.additional_components());
        }
    }
    if decoder.metadata_state().drc.shared_syntax_used {
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
    if let Some(context) = decoder.hoa() {
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
    if let Some(context) = decoder.hoa()
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
    if decoder.hoa().is_some_and(|c| c.salient_components() != 0) {
        let value = pcm
            .decoder_settings
            .get_mut("implementation")
            .unwrap()
            .value
            .as_mut()
            .unwrap();
        let context = decoder.hoa().unwrap();
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
        if let Some(context) = decoder.hoa()
            && (context.ambient_components() != 0 || context.dynamic_selection_enabled())
        {
            value["hoa_descriptor_numeric_profile"] = json!(context.descriptor_numeric_profile());
        }
    }
    if decoder.hoa().is_some_and(|c| c.static_ambient_enabled()) {
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
    if let Some(context) = decoder.hoa()
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
    if let Some(context) = decoder.hoa()
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
        .hoa()
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
            *counts != [4; 5] || *method != 0 || !decoder.hoa().unwrap().spatial_controls().flag_f
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
            json!(if decoder.hoa().unwrap().spatial_controls().flag_f {
                crate::frame::hoa_salient_subbands_format_sha256(method)
            } else {
                crate::frame::hoa_spatial_controls_format_sha256()
            });
        if method != 0 {
            value["hoa_salient_partition_method"] = json!(method);
            value["hoa_salient_partition_profile"] =
                json!(crate::frame::HOA_SALIENT_PARTITION_PROFILE);
        }
    }
    out.json("pcm.json", &pcm)?;
    let mut report = json!({"schema_version":SCHEMA_VERSION,"complete":true,"experimental":true,"numeric_profile":crate::synthesis::NUMERIC_PROFILE,"cac_numeric_profile":crate::frame::CAC_NUMERIC_PROFILE,"tns_numeric_profile":crate::frame::TNS_NUMERIC_PROFILE,"tns_tables_sha256":crate::frame::tns_math_sha256(),"bwe2_numeric_profile":crate::frame::BWE2_NUMERIC_PROFILE,"bwe2_format_sha256":crate::bwe2_math::format_sha256(),"bwe2_tables_sha256":crate::bwe2_math::math_sha256(),"numerical_qualification":implementation::QUALIFICATION,"backend":backend,"native_apis_used":false,
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
    if let Some(profile) = implementation::channel_layout_profile(&decoder) {
        report["channel_layout_profile"] = json!(profile);
    }
    if channels != 2 || decoder.composite().is_some() {
        report["channel_count"] = json!(channels);
        report["channel_layout"] = json!(decoder.info().layout);
        report["absent_elements"] = json!(absent_elements);
        report["embedded_absent_elements"] = json!(embedded_absent_elements);
    }
    if let Some(profile) = implementation::hoa_numeric_profile(&decoder) {
        report["hoa_numeric_profile"] = json!(profile);
    }
    if let Some(mode) = access {
        let profile = if implementation::hoa_numeric_profile(&decoder).is_some() {
            implementation::HOA_ACCESS_PROFILE
        } else {
            implementation::ACCESS_PROFILE
        };
        report["access"] = json!({"profile":profile,"mode":mode,
            "verification_scope":"full_input","prefix_scanned_packets":prefix_packets,
            "prefix_scanned_frames":prefix_frames,"prefix_numeric_packets":numeric_prefix_packets,
            "prefix_numeric_elements":numeric_prefix_elements,"prefix_bounded_elements":bounded_prefix_elements,
            "synthesized_packets":decoded_packets,"synthesis_start_packet":first_synthesis_packet,
            "external_warmup_packets":warmup_packets,"metadata_before_output_sha256":state_before_output,
            "metadata_after_processing_sha256":metadata_sha256(&decoder),
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
