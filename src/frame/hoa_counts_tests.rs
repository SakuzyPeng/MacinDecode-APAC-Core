use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
    synthesis::SqDecoder,
};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!("../../data/hoa-salient-counts-state-v1.json")).unwrap()
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
        assert_eq!(ctx.salient_subband_counts().unwrap().len(), count);
        assert_eq!(ctx.salient_component_orders().unwrap().len(), count);
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
