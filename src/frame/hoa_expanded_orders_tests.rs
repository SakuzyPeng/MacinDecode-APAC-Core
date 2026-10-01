use super::*;
use crate::{frame::HoaFrameContext, synthesis::SqDecoder};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!("../../data/hoa-expanded-orders-state-v1.json")).unwrap()
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
fn full_order_dimensions_profiles_and_maximum_component_count_are_bounded() {
    let d = data();
    for f in d["boundaries"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        assert!(context.is_supported(), "{:?}", context.rejection());
        let order = f["order"].as_u64().unwrap() as u8;
        let n = (usize::from(order) + 1).pow(2);
        assert_eq!(context.order(), order);
        assert_eq!(context.channel_count(), n as u32);
        assert_eq!(context.maximum_preroll_bytes(), n as u64 * 2048);
        let r = crate::frame::parse_hoa_packet(&context, &bytes(&f["packet"])).unwrap();
        assert!(r.packet.packet_complete && r.hoa().hoa_complete);
        let active = f["active_coefficient"].as_u64().unwrap() as usize;
        for (k, c) in r.hoa().channels_after_hoa.iter().enumerate() {
            assert_eq!(
                c.scaled[0],
                if k == active {
                    if order == 0 { 1. } else { 0.25 }
                } else {
                    0.
                }
            );
            assert!(c.scaled[1..].iter().all(|&v| v == 0.));
        }
    }
    for f in d["invalid"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(f)).unwrap();
        assert!(!context.is_supported());
    }
}

#[test]
fn higher_order_directions_have_unit_energy_and_periodic_azimuth() {
    for order in 4usize..=10 {
        for (azimuth, elevation) in [(0, 0), (37, 121), (359, 90), (511, 255)] {
            let v = direction(azimuth, elevation, (order + 1).pow(2));
            assert!((v.iter().map(|x| x * x).sum::<f64>() - 1.).abs() < 2e-13);
            assert_eq!(v, direction(azimuth % 360, elevation, (order + 1).pow(2)));
        }
    }
}

#[test]
fn expanded_dimensions_preserve_atomic_overlaps_and_compensated_residuals() {
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let bad = bytes(&f["bad"]);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        let initial = decoder.decode_frame(&first).unwrap();
        clean.decode_frame(&first).unwrap();
        assert!(decoder.decode_frame(&bad).is_err());
        assert_eq!(
            decoder.decode_frame(&good).unwrap(),
            clean.decode_frame(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_frame(&first).unwrap(), initial);
        if f["name"] == "compensated-cancellation" {
            let context = HoaFrameContext::from_cookie(&cookie).unwrap();
            let report = crate::frame::parse_hoa_packet(&context, &first).unwrap();
            assert_eq!(report.hoa().channels_after_hoa[4].scaled[0], 1.);
        }
    }
}

#[test]
fn every_high_order_dictionary_symbol_and_truncation_is_checked() {
    assert_eq!(
        super::quantization_tests::check_words(&[25, 36, 49, 64, 81, 100, 121], 6..=9),
        7 * 8 * (64 + 128 + 256 + 512)
    );
}
