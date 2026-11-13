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
    serde_json::from_str(include_str!("../../../../data/hoa-ambient-state-v1.json")).unwrap()
}

#[test]
fn source_layout_dimensions_transactions_and_reset_follow_the_declared_output() {
    let data: Value = serde_json::from_str(include_str!(
        "../../../../data/hoa-source-layout-state-v1.json"
    ))
    .unwrap();
    for fixture in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&fixture["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(
            context.is_supported(),
            "{}: {:?}",
            fixture["name"],
            context.rejection()
        );
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let n = fixture["options"]["output_coefficients"].as_u64().unwrap() as usize;
        assert_eq!(decoder.info().channel_count as usize, n);
        let initial = snapshot(&decoder);
        let first = decoder.decode_vec(&bytes(&fixture["first"])).unwrap();
        assert_eq!(first.len(), n * 1024);
        assert!(first.iter().all(|v| v.is_finite()));
        let checkpoint = snapshot(&decoder);
        assert!(decoder.decode_vec(&bytes(&fixture["bad"])).is_err());
        assert_eq!(snapshot(&decoder), checkpoint);
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        clean.decode_vec(&bytes(&fixture["first"])).unwrap();
        assert_eq!(
            decoder.decode_vec(&bytes(&fixture["good"])).unwrap(),
            clean.decode_vec(&bytes(&fixture["good"])).unwrap()
        );
        decoder.reset();
        assert_eq!(snapshot(&decoder), initial);
        assert_eq!(
            decoder.decode_vec(&bytes(&fixture["first"])).unwrap(),
            first
        );
        if fixture["name"] == "n3d-labels" {
            assert_eq!(context.source_normalization(), Some("N3D"));
        }
    }
}

#[test]
fn static_remapping_keeps_fixed_core_maps_and_atomic_history() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-remapping-state-v1.json")).unwrap();
    for fixture in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&fixture["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(
            context.is_supported(),
            "{}: {:?}",
            fixture["name"],
            context.rejection()
        );
        assert_eq!(
            serde_json::to_value(context.static_remapping().unwrap()).unwrap(),
            fixture["mapping"]
        );
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let initial = snapshot(&decoder);
        let first = decoder.decode_vec(&bytes(&fixture["first"])).unwrap();
        assert!(first.iter().all(|v| v.is_finite()));
        let saved = snapshot(&decoder);
        assert!(decoder.decode_vec(&bytes(&fixture["bad"])).is_err());
        assert_eq!(snapshot(&decoder), saved);
        let good = decoder.decode_vec(&bytes(&fixture["good"])).unwrap();
        decoder.reset();
        assert_eq!(snapshot(&decoder), initial);
        assert_eq!(
            decoder.decode_vec(&bytes(&fixture["first"])).unwrap(),
            first
        );
        assert_eq!(decoder.decode_vec(&bytes(&fixture["good"])).unwrap(), good);
    }
}
fn snapshot(d: &Decoder) -> (Vec<Vec<u64>>, String) {
    (
        d.channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
        d.metadata_sha256(),
    )
}
#[test]
fn hoa_transactions_and_reset() {
    let f = fixture();
    let mut d = Decoder::from_cookie(&bytes(&f["cookie"])).unwrap();
    assert_eq!(d.info().channel_count, 16);
    assert_eq!(d.info().layout.ambisonic_order, Some(3));
    d.decode_vec(&bytes(&f["first"])).unwrap();
    let state = snapshot(&d);
    for key in [
        "last_element_error",
        "late_spatial_error",
        "embedded_error",
        "outer_after_embedded_error",
    ] {
        assert!(d.decode_vec(&bytes(&f[key])).is_err());
        assert_eq!(state, snapshot(&d));
    }
    let first = bytes(&f["first"]);
    for end in 0..first.len() {
        assert!(d.decode_vec(&first[..end]).is_err());
        assert_eq!(state, snapshot(&d));
    }
    let mut fresh = Decoder::from_cookie(&bytes(&f["cookie"])).unwrap();
    fresh.decode_vec(&first).unwrap();
    assert_eq!(
        d.decode_vec(&bytes(&f["next"])).unwrap(),
        fresh.decode_vec(&bytes(&f["next"])).unwrap()
    );
    assert_eq!(snapshot(&d), snapshot(&fresh));
    d.reset();
    assert_eq!(
        snapshot(&d),
        snapshot(&Decoder::from_cookie(&bytes(&f["cookie"])).unwrap())
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
}

#[test]
fn salient_history_and_all_coefficient_overlaps_roll_back_and_reset() {
    let f: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-salient-state-v1.json")).unwrap();
    let cookie = bytes(&f["cookie"]);
    let mut decoder = Decoder::from_cookie(&cookie).unwrap();
    let first = bytes(&f["first"]);
    decoder.decode_vec(&first).unwrap();
    let before = snapshot(&decoder);
    for key in [
        "last_element_error",
        "late_spatial_error",
        "late_tail_error",
        "embedded_error",
        "outer_after_embedded_error",
    ] {
        assert!(decoder.decode_vec(&bytes(&f[key])).is_err(), "{key}");
        assert_eq!(before, snapshot(&decoder));
    }
    for end in 0..first.len() {
        assert!(decoder.decode_vec(&first[..end]).is_err(), "{end}");
        assert_eq!(before, snapshot(&decoder));
    }
    let mut clean = Decoder::from_cookie(&cookie).unwrap();
    clean.decode_vec(&first).unwrap();
    assert_eq!(
        decoder.decode_vec(&bytes(&f["next"])).unwrap(),
        clean.decode_vec(&bytes(&f["next"])).unwrap()
    );
    assert_eq!(snapshot(&decoder), snapshot(&clean));
    decoder.reset();
    assert_eq!(
        snapshot(&decoder),
        snapshot(&Decoder::from_cookie(&cookie).unwrap())
    );
}

#[test]
fn hoa_order_dimensions_rates_and_transactions_are_qualified() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-orders-state-v1.json")).unwrap();
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
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        assert_eq!(decoder.decode_vec(&first).unwrap().len(), 1024 * n);
        let before = snapshot(&decoder);
        for key in [
            "last_element_error",
            "late_spatial_error",
            "late_tail_error",
            "embedded_error",
            "outer_after_embedded_error",
        ] {
            assert!(decoder.decode_vec(&bytes(&f[key])).is_err(), "{n} {key}");
            assert_eq!(before, snapshot(&decoder));
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err(), "{n} {end}");
            assert_eq!(before, snapshot(&decoder));
        }
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&first).unwrap();
        assert_eq!(
            decoder.decode_vec(&bytes(&f["next"])).unwrap(),
            fresh.decode_vec(&bytes(&f["next"])).unwrap()
        );
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
    }
}

#[test]
fn mixed_hoa_rolls_back_all_output_history_and_drc_and_resets() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-mixed-state-v1.json")).unwrap();
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
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        assert_eq!(
            decoder.decode_vec(&first).unwrap().len(),
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
            assert!(decoder.decode_vec(&bytes(&f[key])).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        clean.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_vec(&bytes(&f[key])).unwrap(),
                clean.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&clean));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
    }
}

#[test]
fn old_hoa_json_does_not_gain_mixed_fields() {
    for source in [
        include_str!("../../../../data/hoa-salient-state-v1.json"),
        include_str!("../../../../data/hoa-ambient-state-v1.json"),
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
        assert!(report.hoa().mixed.is_none());
    }
}

#[test]
fn static_ambient_selector_descriptor_overlap_and_drc_commit_atomically() {
    let data: Value = serde_json::from_str(include_str!(
        "../../../../data/hoa-static-ambient-state-v1.json"
    ))
    .unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        decoder.decode_vec(&first).unwrap();
        let before = snapshot(&decoder);
        for key in [
            "last_element_error",
            "late_spatial_error",
            "late_tail_error",
            "embedded_error",
            "outer_after_embedded_error",
        ] {
            assert!(decoder.decode_vec(&bytes(&f[key])).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        clean.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_vec(&bytes(&f[key])).unwrap(),
                clean.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&clean));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
    }
}

#[test]
fn dynamic_hoa_dimensions_mapping_state_and_all_outputs_are_atomic() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-dynamic-state-v1.json")).unwrap();
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
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        assert_eq!(decoder.decode_vec(&first).unwrap().len(), 16384);
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
            assert!(decoder.decode_vec(&bytes(&f[key])).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err());
            assert_eq!(snapshot(&decoder), before);
        }
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_vec(&bytes(&f[key])).unwrap(),
                fresh.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&fresh));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
    }
}

#[test]
fn additive_history_transform_mapping_and_output_overlap_are_atomic() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-additive-state-v1.json")).unwrap();
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
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        assert_eq!(
            decoder.decode_vec(&first).unwrap().len(),
            1024 * context.channel_count() as usize
        );
        let before = snapshot(&decoder);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(decoder.decode_vec(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err(), "{end}");
            assert_eq!(snapshot(&decoder), before);
        }
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_vec(&bytes(&f[key])).unwrap(),
                fresh.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&fresh));
        }
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
        let restored = crate::frame::parse_hoa_packet(&context, &first).unwrap();
        assert_eq!(
            restored.hoa().additive.as_ref().unwrap().combination,
            crate::frame::AmbientCombination::Add
        );
    }
}

#[test]
fn additive_last_output_synthesis_failure_preserves_all_packet_state() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-additive-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let mut decoder = Decoder::from_cookie(&bytes(&f["cookie"])).unwrap();
        decoder.decode_vec(&bytes(&f["first"])).unwrap();
        // Fail after spatial/DRC parsing and the preceding ACN channels have rendered.
        decoder.channels.last_mut().unwrap().overlap[0] = f64::MAX;
        let before = snapshot(&decoder);
        let error = decoder.decode_vec(&bytes(&f["embedded_good"])).unwrap_err();
        assert_eq!(error.operation, "SQ synthesis");
        assert_eq!(snapshot(&decoder), before);
    }
}

#[test]
fn effective_subbands_commit_all_eight_maps_and_rollback_unused_row_failures() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-subbands-state-v1.json")).unwrap();
    for f in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        assert_eq!(
            context.dynamic_subband_count(),
            Some(f["options"]["subbands"].as_u64().unwrap() as usize)
        );
        assert_eq!(context.maximum_preroll_bytes(), 32768);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        decoder.decode_vec(&bytes(&f["first"])).unwrap();
        let before = snapshot(&decoder);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(decoder.decode_vec(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        let mut alternate = Decoder::from_cookie(&cookie).unwrap();
        alternate.decode_vec(&bytes(&f["first"])).unwrap();
        assert_eq!(
            decoder.decode_vec(&bytes(&f["next"])).unwrap(),
            alternate.decode_vec(&bytes(&f["alternate"])).unwrap()
        );
        assert_ne!(
            decoder
                .metadata_state()
                .hoa
                .unwrap()
                .last_dynamic_mapping
                .as_ref()
                .unwrap()[7],
            alternate
                .metadata_state()
                .hoa
                .unwrap()
                .last_dynamic_mapping
                .as_ref()
                .unwrap()[7]
        );
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&bytes(&f["first"])).unwrap();
        fresh.decode_vec(&bytes(&f["next"])).unwrap();
        assert_eq!(
            decoder.decode_vec(&bytes(&f["embedded_good"])).unwrap(),
            fresh.decode_vec(&bytes(&f["embedded_good"])).unwrap()
        );
        assert_eq!(snapshot(&decoder), snapshot(&fresh));
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
        let restored = crate::frame::parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
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
    }
    let old: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-ambient-state-v1.json")).unwrap();
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
        "../../../../data/hoa-spatial-subbands-state-v1.json"
    ))
    .unwrap();
    let partition: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-partition-state-v1.json")).unwrap();
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
        let mut d = Decoder::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        d.decode_vec(&first).unwrap();
        let before = snapshot(&d);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(d.decode_vec(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&d), before, "{key}");
        }
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                d.decode_vec(&bytes(&f[key])).unwrap(),
                fresh.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&d), snapshot(&fresh));
        }
        d.channels.last_mut().unwrap().overlap[0] = f64::MAX;
        let before = snapshot(&d);
        assert_eq!(d.decode_vec(&first).unwrap_err().operation, "SQ synthesis");
        assert_eq!(snapshot(&d), before);
        d.reset();
        assert_eq!(
            snapshot(&d),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
        let restored = crate::frame::parse_hoa_packet(&ctx, &first).unwrap();
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

#[test]
fn component_orders_keep_sixteen_outputs_and_commit_all_history_atomically() {
    let data: Value = serde_json::from_str(include_str!(
        "../../../../data/hoa-component-orders-state-v1.json"
    ))
    .unwrap();
    let first_order: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-order1-state-v1.json")).unwrap();
    for f in data["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .chain(first_order["fixtures"].as_array().unwrap())
    {
        let cookie = bytes(&f["cookie"]);
        let context = crate::frame::HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported());
        let order = f["options"]["order"].as_u64().unwrap() as u8;
        let slots = (usize::from(order) + 1).pow(2);
        let output_order = if f["options"]["dynamic"] == true {
            3
        } else {
            order
        };
        let channels = (u32::from(output_order) + 1).pow(2);
        assert_eq!(
            (
                context.order(),
                context.output_order(),
                context.channel_count(),
                context.recovery_slot_count()
            ),
            (order, output_order, channels, slots)
        );
        assert_eq!(
            serde_json::to_value(context.salient_component_orders()).unwrap(),
            f["options"]["component_orders"]
        );
        assert_eq!(
            context.descriptor_numeric_profile(),
            Some("apac-hoa-component-orders-math-v1")
        );
        // This frozen fixture rejected order 1 before that extension. Keep the
        // archived input unchanged and use still-unsupported order 0 here.
        let mut invalid = bytes(&f["bad_order_cookie"]);
        let at = f["cookie_orders"][0]["bit_offset"].as_u64().unwrap() as usize;
        for bit in at..at + 2 {
            invalid[bit / 8] &= !(1 << (7 - bit % 8));
        }
        assert!(
            !crate::frame::HoaFrameContext::from_cookie(&invalid)
                .unwrap()
                .is_supported()
        );
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        assert_eq!(
            decoder.info().layout.ambisonic_order,
            Some(u32::from(output_order))
        );
        let first = bytes(&f["first"]);
        assert_eq!(
            decoder.decode_vec(&first).unwrap().len(),
            channels as usize * 1024
        );
        let before = snapshot(&decoder);
        for (key, value) in f["errors"].as_object().unwrap() {
            assert!(decoder.decode_vec(&bytes(value)).is_err(), "{key}");
            assert_eq!(snapshot(&decoder), before, "{key}");
        }
        for end in 0..first.len() {
            assert!(decoder.decode_vec(&first[..end]).is_err());
            assert_eq!(snapshot(&decoder), before);
        }
        let mut fresh = Decoder::from_cookie(&cookie).unwrap();
        fresh.decode_vec(&first).unwrap();
        for key in ["embedded_good", "next"] {
            assert_eq!(
                decoder.decode_vec(&bytes(&f[key])).unwrap(),
                fresh.decode_vec(&bytes(&f[key])).unwrap()
            );
            assert_eq!(snapshot(&decoder), snapshot(&fresh));
        }
        decoder.channels.last_mut().unwrap().overlap[0] = f64::MAX;
        let before = snapshot(&decoder);
        assert_eq!(
            decoder.decode_vec(&first).unwrap_err().operation,
            "SQ synthesis"
        );
        assert_eq!(snapshot(&decoder), before);
        decoder.reset();
        assert_eq!(
            snapshot(&decoder),
            snapshot(&Decoder::from_cookie(&cookie).unwrap())
        );
        let restored = crate::frame::parse_hoa_packet(&context, &first).unwrap();
        assert!(
            restored
                .hoa()
                .spatial
                .as_ref()
                .unwrap()
                .salient
                .as_ref()
                .unwrap()
                .component_orders
                .is_some()
        );
    }
    let ambient = fixture();
    assert_eq!(
        crate::frame::HoaFrameContext::from_cookie(&bytes(&ambient["cookie"]))
            .unwrap()
            .salient_component_orders(),
        None
    );
}
