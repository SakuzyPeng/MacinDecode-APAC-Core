use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
    synthesis::SqDecoder,
};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!(
        "../../../../data/hoa-salient-counts-state-v1.json"
    ))
    .unwrap()
}
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

#[test]
fn every_qualified_count_recovers_the_last_carrier_and_rejects_excess_core() {
    let data = data();
    for f in data["counts"].as_array().unwrap() {
        let ctx = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        assert!(ctx.is_supported(), "{:?}", ctx.rejection());
        let count = f["options"]["counts"].as_array().unwrap().len();
        assert_eq!(ctx.salient_components(), count);
        let configurations = ctx.salient_component_configurations();
        assert_eq!(configurations.len(), count);
        assert!(configurations.iter().all(|c| c.subband_count == 1));
        assert!(
            configurations
                .iter()
                .all(|c| c.coefficient_count == (usize::from(c.order) + 1).pow(2))
        );
        let legacy_counts: Option<[usize; 5]> = ctx.salient_subband_counts();
        let legacy_orders: Option<[u8; 5]> = ctx.salient_component_orders();
        assert_eq!(legacy_counts.is_some(), count == 5);
        assert_eq!(legacy_orders.is_some(), count == 5);
        if count == 5 {
            assert_eq!(
                legacy_counts.unwrap().as_slice(),
                configurations
                    .iter()
                    .map(|c| c.subband_count)
                    .collect::<Vec<_>>()
            );
            assert_eq!(
                legacy_orders.unwrap().as_slice(),
                configurations.iter().map(|c| c.order).collect::<Vec<_>>()
            );
        }
        let report = parse_hoa_packet(&ctx, &bytes(&f["packet"])).unwrap();
        assert!(report.packet.packet_complete && report.hoa().hoa_complete);
        let side = report
            .hoa()
            .spatial
            .as_ref()
            .unwrap()
            .salient
            .as_ref()
            .unwrap();
        assert_eq!(side.descriptors.len(), count);
        assert_eq!(side.component_count, (count != 5).then_some(count));
        let target = if f["options"]["path"] == "replace" {
            4
        } else {
            3
        };
        assert!(
            report.hoa().channels_after_hoa[target]
                .scaled
                .iter()
                .any(|&v| v != 0.)
        );
    }
    for raw in data["invalid"].as_array().unwrap() {
        if let Ok(ctx) = HoaFrameContext::from_cookie(&bytes(raw)) {
            assert!(!ctx.is_supported());
        }
    }
}

#[test]
fn actual_component_query_preserves_cookie_shapes_and_legacy_five_item_views() {
    for f in data()["fixtures"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let components = context.salient_component_configurations();
        assert_eq!(components.len(), context.salient_components());
        let parsed = crate::config::parse_cookie(&bytes(&f["cookie"])).unwrap();
        for (index, component) in components.iter().enumerate() {
            let field = |suffix: &str| {
                parsed
                    .fields
                    .iter()
                    .find(|f| f.name == format!("components[0].hoa.salient[{index}].{suffix}"))
                    .unwrap()
                    .value
                    .as_u64()
                    .unwrap()
            };
            assert_eq!(u64::from(component.order), field("order"));
            assert_eq!(
                component.subband_count as u64,
                field("subbands_minus_one") + 1
            );
            assert_eq!(
                component.coefficient_count,
                (usize::from(component.order) + 1).pow(2)
            );
        }
        assert_eq!(
            context.salient_component_orders().is_some(),
            components.len() == 5
        );
        assert_eq!(
            context.salient_subband_counts().is_some(),
            components.len() == 5
        );
    }
    let ambient: Value = serde_json::from_str(include_str!(
        "../../../../data/hoa-ambient-counts-state-v1.json"
    ))
    .unwrap();
    for f in ambient["fixtures"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        if context.salient_components() == 0 {
            assert!(context.salient_component_configurations().is_empty());
            assert_eq!(context.salient_subband_counts(), None);
            assert_eq!(context.salient_component_orders(), None);
        }
    }
}

#[test]
fn count_histories_pcm_and_drc_roll_back_together_and_reset() {
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let ctx = HoaFrameContext::from_cookie(&cookie).unwrap();
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let first_pcm = decoder.decode_frame(&first).unwrap();
        let mut state = HoaState::default();
        let mut drc = ctx.initial_drc_state();
        parse_hoa_packet_with_state(&ctx, &first, &mut drc, &mut state).unwrap();
        assert_eq!(
            state.salient.as_ref().unwrap().history.len(),
            ctx.salient_components()
        );
        let saved = state.clone();
        let drc_before =
            serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes)).unwrap();
        for raw in f["errors"].as_object().unwrap().values() {
            let bad = bytes(raw);
            assert!(decoder.decode_frame(&bad).is_err());
            let failed = parse_hoa_packet_with_state(&ctx, &bad, &mut drc, &mut state);
            assert!(failed.is_err() || !failed.unwrap().packet.packet_complete);
            assert_eq!(state, saved);
            assert_eq!(
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap(),
                drc_before
            );
        }
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        clean.decode_frame(&first).unwrap();
        assert_eq!(
            decoder.decode_frame(&good).unwrap(),
            clean.decode_frame(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_frame(&first).unwrap(), first_pcm);
    }
}

#[test]
fn count_dependent_spatial_payloads_reject_each_bit_truncation() {
    let data = data();
    let first = &data["fixtures"][0];
    let ctx = HoaFrameContext::from_cookie(&bytes(&first["cookie"])).unwrap();
    let template = parse_hoa_packet(&ctx, &bytes(&first["first"]))
        .unwrap()
        .packet
        .frame;
    for f in data["spatial_cases"].as_array().unwrap() {
        let ctx = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let raw = bytes(&f["bytes"]);
        let end = f["bits"].as_u64().unwrap() as usize;
        for limit in 0..=end {
            let mut parser = Parser {
                capture: false,
                bits: BitReader::new(&raw),
                report: template.clone(),
            };
            parser.bits.set_end(limit).unwrap();
            let result = crate::frame::hoa::spatial(
                &mut parser,
                &mut HoaState::default(),
                &ctx.configuration,
                2,
            );
            if limit < end {
                assert_eq!(result.unwrap_err().kind, "truncated");
            } else {
                assert_eq!(result.unwrap().end_bit_offset, end);
                parser.bits.set_end(raw.len() * 8).unwrap();
                assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
            }
        }
    }
}
