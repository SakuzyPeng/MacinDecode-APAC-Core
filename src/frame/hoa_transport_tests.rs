use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
    synthesis::SqDecoder,
};
use serde_json::Value;

fn fixtures() -> Value {
    serde_json::from_str(include_str!("../../data/hoa-transports-state-v1.json")).unwrap()
}
fn bytes(value: &Value) -> Vec<u8> {
    value
        .as_str()
        .unwrap()
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
        .collect()
}

#[test]
fn element_counts_carriers_and_output_domains_are_independent() {
    let data = fixtures();
    for f in data["fixtures"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        assert!(context.is_supported(), "{:?}", context.rejection());
        let types = f["options"]["tce_types"].as_array().unwrap();
        assert_eq!(context.transport_elements().len(), types.len());
        let mut next = 0;
        for (index, (element, kind)) in context.transport_elements().iter().zip(types).enumerate() {
            let width = match kind.as_u64().unwrap() {
                0 | 3 => 1,
                1 => 2,
                6 => 0,
                _ => unreachable!(),
            };
            assert_eq!(element.element_index, index);
            assert_eq!(
                element.transport_channels.as_deref().unwrap(),
                &(next..next + width).collect::<Vec<_>>()
            );
            next += width;
        }
        assert_eq!(context.transport_channels(), usize::from(next));
        let packet = parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
        assert!(packet.packet.packet_complete);
        assert_eq!(
            packet.hoa().channels_after_hoa.len(),
            context.channel_count() as usize
        );
        assert_eq!(packet.hoa().transport_element_count, Some(types.len()));
        assert_eq!(
            spectra(&packet.packet).unwrap().len(),
            context.transport_channels()
        );
        for element in &packet.packet.elements {
            if matches!(
                element.configuration.kind,
                crate::frame::ElementKind::Lfe | crate::frame::ElementKind::Extension
            ) {
                assert!(element.tns.is_empty() && element.bwe2.is_none());
            }
        }
        let output = SqDecoder::from_cookie(&bytes(&f["cookie"]))
            .unwrap()
            .decode_frame(&bytes(&f["first"]))
            .unwrap();
        assert_eq!(output.len(), context.channel_count() as usize * 1024);
    }
    for raw in data["invalid"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(raw));
        assert!(context.is_err() || !context.unwrap().is_supported());
    }
}

#[test]
fn new_elements_and_late_errors_preserve_all_packet_state() {
    for f in fixtures()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let context = HoaFrameContext::from_cookie(&cookie).unwrap();
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        let initial = decoder.decode_frame(&bytes(&f["first"])).unwrap();
        clean.decode_frame(&bytes(&f["first"])).unwrap();
        let mut state = HoaState::default();
        let mut drc = context.initial_drc_state();
        parse_hoa_packet_with_state(&context, &bytes(&f["first"]), &mut drc, &mut state).unwrap();
        let saved = state.clone();
        let saved_drc =
            serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes)).unwrap();
        for (name, bad) in f["errors"].as_object().unwrap() {
            let result = parse_hoa_packet_with_state(&context, &bytes(bad), &mut drc, &mut state);
            assert!(
                result.is_err() || !result.unwrap().packet.packet_complete,
                "{name}"
            );
            assert_eq!(state, saved);
            assert_eq!(
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap(),
                saved_drc
            );
            assert!(decoder.decode_frame(&bytes(bad)).is_err(), "{name}");
        }
        assert_eq!(
            decoder.decode_frame(&bytes(&f["good"])).unwrap(),
            clean.decode_frame(&bytes(&f["good"])).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_frame(&bytes(&f["first"])).unwrap(), initial);
    }
}

#[test]
fn extension_lengths_are_bounded_at_every_bit_and_preserve_the_following_payload() {
    let data = fixtures();
    let f = &data["fixtures"][0];
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let template = parse_hoa_packet(&context, &bytes(&f["first"]))
        .unwrap()
        .packet
        .frame;
    for e in data["extensions"].as_array().unwrap() {
        let packet = bytes(&e["packet"]);
        let start = e["start"].as_u64().unwrap() as usize;
        let end = e["end"].as_u64().unwrap() as usize;
        for limit in start..=end {
            let mut parser = Parser {
                capture: true,
                bits: BitReader::new(&packet),
                report: template.clone(),
            };
            parser.bits.set_end(limit).unwrap();
            for _ in 0..start {
                parser.bits.read(1).unwrap();
            }
            let decoded = read(&mut parser, "extension-test");
            if limit < end {
                assert!(
                    decoded.is_err(),
                    "accepted extension ending at {end} with limit {limit}"
                );
            } else {
                assert_eq!(
                    serde_json::to_value(decoded.unwrap()).unwrap(),
                    e["expected"]
                );
                assert_eq!(parser.bits.position(), end);
            }
        }
    }
}

#[test]
fn numeric_failure_still_precedes_a_broken_dynamic_map() {
    let f = &fixtures()["numeric_priority"];
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let components = context.salient_component_configurations();
    let mut state = HoaState::default();
    let salient = super::super::hoa_salient::SalientState::with_dimensions(
        components
            .iter()
            .map(|c| c.coefficient_count)
            .collect::<Vec<_>>(),
        components
            .iter()
            .map(|c| c.subband_count)
            .collect::<Vec<_>>(),
    );
    let mut encoded = serde_json::to_value(salient).unwrap();
    encoded["history"][0][0][0] = serde_json::json!(f64::MAX);
    state.salient = Some(Box::new(serde_json::from_value(encoded).unwrap()));
    let saved = state.clone();
    let error = parse_hoa_packet_with_state(
        &context,
        &bytes(&f["bad"]),
        &mut context.initial_drc_state(),
        &mut state,
    )
    .unwrap_err();
    assert_eq!(error.kind, "hoa-additive-numeric");
    assert_eq!(state, saved);
}
