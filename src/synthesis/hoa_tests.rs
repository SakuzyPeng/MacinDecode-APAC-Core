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

#[test]
fn hoa_order_dimensions_rates_and_transactions_are_qualified() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-orders-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        let n = f["channels"].as_u64().unwrap() as usize;
        assert_eq!(context.channel_count() as usize, n);
        assert_eq!(context.order() as u64, f["order"].as_u64().unwrap());
        assert_eq!(context.sample_rate_hz(), f["rate"].as_u64().unwrap());
        assert_eq!(
            context.maximum_preroll_bytes(),
            if n == 4 { 8192 } else { 18432 }
        );
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        assert_eq!(decoder.decode_frame(&first).unwrap().len(), 1024 * n);
        let before = snapshot(&decoder);
        for key in [
            "last_element_error",
            "late_spatial_error",
            "late_tail_error",
            "embedded_error",
            "outer_after_embedded_error",
        ] {
            assert!(decoder.decode_frame(&bytes(&f[key])).is_err(), "{n} {key}");
            assert_eq!(before, snapshot(&decoder));
        }
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err(), "{n} {end}");
            assert_eq!(before, snapshot(&decoder));
        }
        let mut fresh = SqDecoder::from_cookie(&cookie).unwrap();
        fresh.decode_frame(&first).unwrap();
        assert_eq!(
            decoder.decode_frame(&bytes(&f["next"])).unwrap(),
            fresh.decode_frame(&bytes(&f["next"])).unwrap()
        );
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        assert!(decoder.scan_frame(&first).is_err());
    }
}

#[test]
fn mixed_hoa_rolls_back_all_output_history_and_drc_and_resets() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-mixed-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        assert_eq!(
            (
                context.salient_components(),
                context.ambient_components(),
                context.core_channels()
            ),
            (5, 4, 9)
        );
        assert_eq!(
            context.transport_channels(),
            context.channel_count() as usize
        );
        assert_eq!(context.state_profile(), "apac-hoa-mixed-state-v1");
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        assert_eq!(decoder.backend(), "rust_hoa_mixed_sq_drc_off_f64_fft_v1");
        let first = bytes(&f["first"]);
        assert_eq!(
            decoder.decode_frame(&first).unwrap().len(),
            1024 * context.channel_count() as usize
        );
        let before = snapshot(&decoder);
        for key in [
            "last_element_error",
            "late_spatial_error",
            "late_tail_error",
            "embedded_error",
            "outer_after_embedded_error",
        ] {
            assert!(decoder.decode_frame(&bytes(&f[key])).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        clean.decode_frame(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_frame(&bytes(&f[key])).unwrap(),
                clean.decode_frame(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&clean));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        assert!(decoder.scan_frame(&first).is_err());
    }
}

#[test]
fn old_hoa_json_does_not_gain_mixed_fields() {
    for source in [
        include_str!("../../data/hoa-salient-state-v1.json"),
        include_str!("../../data/hoa-ambient-state-v1.json"),
    ] {
        let f: Value = serde_json::from_str(source).unwrap();
        let context = crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let report = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
        let value = serde_json::to_value(&report).unwrap();
        assert!(value["hoa"].get("mixed").is_none());
        if let Some(descriptors) = value["hoa"]["spatial"]["salient"]["descriptors"].as_array() {
            for d in descriptors {
                assert!(d.get("coded_coefficient_indices").is_none());
                assert!(d.get("ambient_omitted_coefficients").is_none());
            }
        }
        let restored: crate::frame::HoaPacketReport = serde_json::from_value(value).unwrap();
        assert!(restored.hoa().mixed.is_none());
    }
}

#[test]
fn static_ambient_selector_descriptor_overlap_and_drc_commit_atomically() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-static-ambient-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        assert_eq!(
            decoder.hoa_numeric_profile(),
            Some("apac-hoa-static-ambient-math-v1")
        );
        assert_eq!(
            decoder.backend(),
            "rust_hoa_static_ambient_sq_drc_off_f64_fft_v1"
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
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        clean.decode_frame(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_frame(&bytes(&f[key])).unwrap(),
                clean.decode_frame(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&clean));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        assert!(decoder.scan_frame(&first).is_err());
    }
}

#[test]
fn dynamic_hoa_dimensions_mapping_state_and_all_outputs_are_atomic() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-dynamic-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported() && context.dynamic_selection_enabled());
        assert_eq!(
            (
                context.order(),
                context.output_order(),
                context.recovery_slot_count(),
                context.channel_count()
            ),
            (2, 3, 9, 16)
        );
        assert_eq!(context.maximum_preroll_bytes(), 32768);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        assert_eq!(
            decoder.backend(),
            "rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v1"
        );
        let first = bytes(&f["first"]);
        assert_eq!(decoder.decode_frame(&first).unwrap().len(), 16384);
        let before = snapshot(&decoder);
        for key in [
            "last_element_error",
            "late_spatial_error",
            "dynamic_error",
            "bitmap_too_few",
            "bitmap_too_many",
            "late_tail_error",
            "embedded_error",
            "outer_after_embedded_error",
        ] {
            assert!(decoder.decode_frame(&bytes(&f[key])).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err());
            assert_eq!(snapshot(&decoder), before);
        }
        let mut fresh = SqDecoder::from_cookie(&cookie).unwrap();
        fresh.decode_frame(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_frame(&bytes(&f[key])).unwrap(),
                fresh.decode_frame(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&fresh));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        assert!(decoder.scan_frame(&first).is_err());
    }
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

#[test]
fn additive_history_transform_mapping_and_output_overlap_are_atomic() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-additive-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        assert_eq!(
            context.ambient_combination(),
            crate::frame::AmbientCombination::Add
        );
        assert_eq!(context.core_channels(), 9);
        assert_eq!(
            context.maximum_preroll_bytes(),
            if context.channel_count() == 9 {
                18432
            } else {
                32768
            }
        );
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        assert_eq!(decoder.backend(), "rust_hoa_additive_sq_drc_off_f64_fft_v1");
        assert_eq!(decoder.state_profile(), "apac-hoa-additive-state-v1");
        let first = bytes(&f["first"]);
        assert_eq!(
            decoder.decode_frame(&first).unwrap().len(),
            1024 * context.channel_count() as usize
        );
        let before = snapshot(&decoder);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(decoder.decode_frame(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut fresh = SqDecoder::from_cookie(&cookie).unwrap();
        fresh.decode_frame(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_frame(&bytes(&f[key])).unwrap(),
                fresh.decode_frame(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&fresh));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        let report = crate::frame::parse_hoa_packet(&context, &first).unwrap();
        let value = serde_json::to_value(&report).unwrap();
        let restored: crate::frame::HoaPacketReport = serde_json::from_value(value).unwrap();
        assert_eq!(
            restored.hoa().additive.as_ref().unwrap().combination,
            crate::frame::AmbientCombination::Add
        );
        assert!(decoder.scan_frame(&first).is_err());
    }
}

#[test]
fn additive_last_output_synthesis_failure_preserves_all_packet_state() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-additive-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let mut decoder = SqDecoder::from_cookie(&bytes(&f["cookie"])).unwrap();
        decoder.decode_frame(&bytes(&f["first"])).unwrap();
        // Fail after spatial/DRC parsing and the preceding ACN channels have rendered.
        decoder.channels.last_mut().unwrap().overlap[0] = f64::MAX;
        let before = snapshot(&decoder);
        let error = decoder
            .decode_frame(&bytes(&f["embedded_good"]))
            .unwrap_err();
        assert_eq!(error.operation, "SQ synthesis");
        assert_eq!(snapshot(&decoder), before);
    }
}

#[test]
fn effective_subbands_commit_all_eight_maps_and_rollback_unused_row_failures() {
    let data: Value =
        serde_json::from_str(include_str!("../../data/hoa-subbands-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        assert_eq!(
            context.dynamic_subband_count(),
            Some(f["options"]["subbands"].as_u64().unwrap() as usize)
        );
        assert_eq!(context.maximum_preroll_bytes(), 32768);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        decoder.decode_frame(&bytes(&f["first"])).unwrap();
        let before = snapshot(&decoder);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(decoder.decode_frame(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        let mut alternate = SqDecoder::from_cookie(&cookie).unwrap();
        alternate.decode_frame(&bytes(&f["first"])).unwrap();
        assert_eq!(
            decoder.decode_frame(&bytes(&f["next"])).unwrap(),
            alternate.decode_frame(&bytes(&f["alternate"])).unwrap()
        );
        assert_ne!(
            decoder.hoa_state.last_dynamic_mapping.unwrap()[7],
            alternate.hoa_state.last_dynamic_mapping.unwrap()[7]
        );
        let mut fresh = SqDecoder::from_cookie(&cookie).unwrap();
        fresh.decode_frame(&bytes(&f["first"])).unwrap();
        fresh.decode_frame(&bytes(&f["next"])).unwrap();
        assert_eq!(
            decoder.decode_frame(&bytes(&f["embedded_good"])).unwrap(),
            fresh.decode_frame(&bytes(&f["embedded_good"])).unwrap()
        );
        assert_eq!(snapshot(&decoder), snapshot(&fresh));
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        let report = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
        let value = serde_json::to_value(&report).unwrap();
        let restored: crate::frame::HoaPacketReport = serde_json::from_value(value).unwrap();
        assert_eq!(
            restored
                .hoa()
                .dynamic_selection
                .as_ref()
                .unwrap()
                .mappings
                .len(),
            8
        );
        assert!(decoder.scan_frame(&bytes(&f["first"])).is_err());
    }
    let old: Value =
        serde_json::from_str(include_str!("../../data/hoa-ambient-state-v1.json")).unwrap();
    assert_eq!(
        crate::frame::HoaFrameContext::from_cookie(&bytes(&old["cookie"]))
            .unwrap()
            .dynamic_subband_count(),
        None
    );
}

#[test]
fn spatial_subband_history_overlap_drc_and_late_failures_are_atomic() {
    let data: Value = serde_json::from_str(include_str!(
        "../../data/hoa-spatial-subbands-state-v1.json"
    ))
    .unwrap();
    let partition: Value =
        serde_json::from_str(include_str!("../../data/hoa-partition-state-v1.json")).unwrap();
    for f in data["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .chain(partition["fixtures"].as_array().unwrap())
    {
        let cookie = bytes(&f["cookie"]);
        let ctx = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(ctx.is_supported());
        assert_eq!(
            ctx.salient_partition_method(),
            Some(f["options"]["spatial_method"].as_u64().unwrap_or(0) as u8)
        );
        let counts: Vec<_> = f["options"]["counts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap() as usize)
            .collect();
        assert_eq!(ctx.salient_subband_counts().unwrap().as_slice(), counts);
        let bad =
            crate::frame::HoaFrameContext::from_cookie(&bytes(&f["bad_count_cookie"])).unwrap_err();
        assert_eq!(bad.kind, "hoa-subband-count");
        let mut d = SqDecoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        d.decode_frame(&first).unwrap();
        let before = snapshot(&d);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(d.decode_frame(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&d), before, "{key}");
        }
        let mut fresh = SqDecoder::from_cookie(&cookie).unwrap();
        fresh.decode_frame(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                d.decode_frame(&bytes(&f[key])).unwrap(),
                fresh.decode_frame(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&d), snapshot(&fresh));
        }
        d.channels.last_mut().unwrap().overlap[0] = f64::MAX;
        let before = snapshot(&d);
        assert_eq!(
            d.decode_frame(&first).unwrap_err().operation,
            "SQ synthesis"
        );
        assert_eq!(snapshot(&d), before);
        d.reset();
        assert_eq!(
            snapshot(&d),
            snapshot(&SqDecoder::from_cookie(&cookie).unwrap())
        );
        assert!(d.scan_frame(&first).is_err());
        let parsed = crate::frame::parse_hoa_packet(&ctx, &first).unwrap();
        let value = serde_json::to_value(parsed).unwrap();
        let restored: crate::frame::HoaPacketReport = serde_json::from_value(value).unwrap();
        assert_eq!(
            restored
                .hoa()
                .spatial
                .as_ref()
                .unwrap()
                .salient
                .as_ref()
                .unwrap()
                .descriptors
                .len(),
            counts.iter().sum::<usize>()
        );
    }
    let f = fixture();
    assert_eq!(
        crate::frame::HoaFrameContext::from_cookie(&bytes(&f["cookie"]))
            .unwrap()
            .salient_subband_counts(),
        None
    );
}
