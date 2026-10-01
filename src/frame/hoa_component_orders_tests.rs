use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!(
        "../../data/hoa-component-orders-state-v1.json"
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
fn component_dimensions_select_each_interval_and_zero_only_outside_their_domains() {
    for row in data()["restoration_cases"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
        assert_eq!(
            (
                context.order(),
                context.output_order(),
                context.channel_count(),
                context.recovery_slot_count()
            ),
            (3, 3, 16, 16)
        );
        let orders = context.salient_component_orders().unwrap();
        let dimensions = context.configuration.salient_dimensions();
        let mut packet = parse_hoa_packet(&context, &bytes(&row["packet"]))
            .unwrap()
            .packet;
        for (s, e) in packet.elements.iter_mut().take(5).enumerate() {
            e.channels_after_bwe2[0].scaled.fill(32. * (s + 1) as f32);
        }
        let mut side = packet
            .hoa
            .as_ref()
            .unwrap()
            .spatial
            .as_ref()
            .unwrap()
            .salient
            .clone()
            .unwrap();
        let mut state =
            SalientState::with_dimensions(&dimensions, context.salient_subband_counts().unwrap());
        let output = restore(&packet, &mut side, &mut state, 16, None, false).unwrap();
        for d in &side.descriptors {
            assert_eq!(d.restored.len(), dimensions[d.component_index]);
            assert_eq!(
                state.history[d.component_index][d.subband_index].len(),
                d.restored.len()
            );
        }
        for line in 0..1024 {
            let short = row["block"] == 2;
            let frequency = if short { line % 128 } else { line };
            for (k, channel) in output.iter().enumerate() {
                let mut expected = 0usize;
                if [0, 8, 9, 15].contains(&k) {
                    for (s, grid) in row["grids"].as_array().unwrap().iter().enumerate() {
                        if k >= (usize::from(orders[s]) + 1).pow(2) {
                            continue;
                        }
                        let b = grid
                            .as_array()
                            .unwrap()
                            .iter()
                            .position(|end| {
                                frequency
                                    < end.as_u64().unwrap() as usize / if short { 8 } else { 1 }
                            })
                            .unwrap();
                        expected += (s + b) * (s + 1);
                    }
                }
                assert_eq!(
                    channel.scaled[line].to_bits(),
                    (expected as f32).to_bits(),
                    "ACN{k} line {line}"
                );
            }
        }
    }
}

#[test]
fn variable_width_descriptors_reject_every_bit_truncation_and_preserve_following_marker() {
    let data = data();
    let fixture = &data["fixtures"][0];
    let context = HoaFrameContext::from_cookie(&bytes(&fixture["cookie"])).unwrap();
    let template = parse_hoa_packet(&context, &bytes(&fixture["first"]))
        .unwrap()
        .packet
        .frame;
    for row in data["spatial_cases"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
        let raw = bytes(&row["bytes"]);
        let end = row["bits"].as_u64().unwrap() as usize;
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
                &context.configuration,
                0,
            );
            if limit < end {
                let e = result.unwrap_err();
                assert_eq!(e.kind, "truncated");
                assert!(e.bit_offset <= limit);
            } else {
                let actual = serde_json::to_value(result.unwrap()).unwrap();
                assert_eq!(actual["end_bit_offset"], end);
                for (a, expected) in actual["salient"]["descriptors"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(row["truth"]["salient"]["descriptors"].as_array().unwrap())
                {
                    for (key, value) in expected.as_object().unwrap() {
                        assert_eq!(&a[key], value, "{key}");
                    }
                }
                assert_eq!(
                    actual["salient"]["component_orders"],
                    row["truth"]["salient"]["component_orders"]
                );
                parser.bits.set_end(raw.len() * 8).unwrap();
                assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
            }
        }
    }
}

#[test]
fn lower_order_numeric_failure_precedes_tail_and_rolls_back_actual_history() {
    for f in data()["fixtures"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let dimensions = context.configuration.salient_dimensions();
        let mut drc = context.initial_drc_state();
        let mut state = HoaState {
            salient: Some(Box::new(SalientState::with_dimensions(
                &dimensions,
                context.salient_subband_counts().unwrap(),
            ))),
            ..Default::default()
        };
        state.salient.as_mut().unwrap().history[0][0][dimensions[0] - 2] = f64::MAX;
        let before = state.clone();
        let before_drc =
            serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes)).unwrap();
        let error = parse_hoa_packet_with_state(
            &context,
            &bytes(&f["errors"]["tail_error"]),
            &mut drc,
            &mut state,
        )
        .unwrap_err();
        assert_eq!(
            error.kind,
            if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                "hoa-additive-numeric"
            } else {
                "hoa-numeric"
            }
        );
        assert_eq!(
            error.bit_offset,
            f["internal_end_bit"].as_u64().unwrap() as usize
        );
        assert_eq!(state, before);
        assert_eq!(
            serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes)).unwrap(),
            before_drc
        );
    }
}
