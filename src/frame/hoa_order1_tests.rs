use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
};
use serde_json::Value;
fn data() -> Value {
    serde_json::from_str(include_str!("../../data/hoa-order1-state-v1.json")).unwrap()
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
fn first_order_direction_uses_half_normalization_before_explicit_replacement() {
    let v = direction(0, 90, 4);
    assert_eq!(v.len(), 4);
    assert_eq!(v[0].to_bits(), 0.5f64.to_bits());
    assert_eq!(v[3].to_bits(), 0.866_025_403_784_438_6f64.to_bits());
    assert_eq!(v[1], 0.);
    assert_eq!(v[2], 0.);
    assert_eq!(
        direction(0, 0, 4)[2].to_bits(),
        (-0.866_025_403_784_438_6f64).to_bits()
    );
    assert_eq!(direction(0, 90, 4), direction(360, 90, 4));
}

#[test]
fn first_order_payloads_and_zero_width_descriptions_preserve_following_bits() {
    let data = data();
    let f = &data["fixtures"][0];
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let template = parse_hoa_packet(&context, &bytes(&f["first"]))
        .unwrap()
        .packet
        .frame;
    let mut empty = 0;
    for row in data["spatial_cases"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
        let raw = bytes(&row["bytes"]);
        let end = row["bits"].as_u64().unwrap() as usize;
        for limit in 0..=end {
            let mut p = Parser {
                capture: false,
                bits: BitReader::new(&raw),
                report: template.clone(),
            };
            p.bits.set_end(limit).unwrap();
            let result = crate::frame::hoa::spatial(
                &mut p,
                &mut HoaState::default(),
                context.configuration,
                0,
            );
            if limit < end {
                let e = result.unwrap_err();
                assert_eq!(e.kind, "truncated");
                assert!(e.bit_offset <= limit);
            } else {
                let result = result.unwrap();
                assert_eq!(result.end_bit_offset, end);
                let actual = result.salient.unwrap();
                assert_eq!(actual.order1_profile.as_deref(), Some(ORDER1_PROFILE));
                for (d, t) in actual
                    .descriptors
                    .iter()
                    .zip(row["truth"]["salient"]["descriptors"].as_array().unwrap())
                {
                    let value = serde_json::to_value(d).unwrap();
                    for (k, v) in t.as_object().unwrap() {
                        assert_eq!(&value[k], v, "{k}");
                    }
                    if d.start_bit_offset == d.end_bit_offset {
                        empty += 1;
                        assert!(d.quantized.is_empty());
                        assert!(d.signs_positive.is_empty());
                        assert_eq!(d.coded_coefficient_indices, Some(vec![]));
                    }
                }
                p.bits.set_end(raw.len() * 8).unwrap();
                assert_eq!(p.bits.read(5).unwrap(), 0b10101);
            }
        }
    }
    assert_eq!(empty, 10);
}

#[test]
fn first_order_history_rolls_back_before_corrupt_inactive_mapping_and_tail() {
    for f in data()["fixtures"].as_array().unwrap() {
        // Full replacement omissions deliberately clear history. Inject the
        // numerical fault only where the coefficient is actually retained.
        if f["options"]["path"] == "replace" {
            continue;
        }
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        for key in ["inactive_mapping_error", "tail_error"] {
            if f["errors"][key].is_null() {
                continue;
            }
            let mut drc = context.initial_drc_state();
            let mut state = HoaState {
                salient: Some(Box::new(SalientState::with_dimensions(
                    context.configuration.salient_dimensions(),
                    context.salient_subband_counts().unwrap(),
                ))),
                ..Default::default()
            };
            state.salient.as_mut().unwrap().history[0][0][3] = f64::MAX;
            let before = state.clone();
            let drc_before =
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap();
            let e = parse_hoa_packet_with_state(
                &context,
                &bytes(&f["errors"][key]),
                &mut drc,
                &mut state,
            )
            .unwrap_err();
            assert_eq!(
                e.kind,
                if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                    "hoa-additive-numeric"
                } else {
                    "hoa-numeric"
                }
            );
            assert_eq!(
                e.bit_offset,
                f["internal_end_bit"].as_u64().unwrap() as usize
            );
            assert_eq!(state, before);
            assert_eq!(
                serde_json::to_value((&drc.channels, &drc.configuration, &drc.previous_nodes))
                    .unwrap(),
                drc_before
            );
        }
    }
}
