use crate::{
    frame::{HoaFrameContext, parse_hoa_packet},
    synthesis::SqDecoder,
};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!("../../../../data/hoa-partial-state-v1.json")).unwrap()
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
fn explicit_dimensions_retain_last_coefficient_without_square_padding() {
    for f in data()["boundaries"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let n = f["coefficients"].as_u64().unwrap() as usize;
        let context = HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported(), "{:?}", context.rejection());
        assert!(!context.full_order());
        assert_eq!(context.order(), f["order"].as_u64().unwrap() as u8);
        assert_eq!(context.recovery_slot_count(), n);
        assert_eq!(context.channel_count(), n as u32);
        assert_eq!(
            context.channel_layout().ambisonic_order,
            (n.isqrt().pow(2) == n).then_some(n.isqrt() as u32 - 1)
        );
        assert!(
            context
                .salient_component_configurations()
                .iter()
                .all(|c| c.coefficient_count == n)
        );
        let packet = bytes(&f["packet"]);
        let report = parse_hoa_packet(&context, &packet).unwrap();
        assert!(report.packet.packet_complete && report.hoa().hoa_complete);
        assert_eq!(report.hoa().full_order, Some(false));
        assert_eq!(report.hoa().channels_after_hoa.len(), n);
        for (k, c) in report.hoa().channels_after_hoa.iter().enumerate() {
            let expected = if k != n - 1 {
                0.
            } else if n == 1 {
                1.
            } else if n % 4 == 3 {
                -0.03125
            } else {
                0.25
            };
            assert_eq!(c.scaled[0], expected, "dimension {n}, coefficient {k}");
            assert!(c.scaled[1..].iter().all(|&x| x == 0.));
        }
        let pcm = SqDecoder::from_cookie(&cookie)
            .unwrap()
            .decode_frame(&packet)
            .unwrap();
        assert_eq!(pcm.len(), 1024 * n);
    }
}

#[test]
fn explicit_dimensions_preserve_packet_atomicity_and_reset() {
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let bad = bytes(&f["bad"]);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let mut reference = SqDecoder::from_cookie(&cookie).unwrap();
        let initial = decoder.decode_frame(&first).unwrap();
        reference.decode_frame(&first).unwrap();
        for end in 0..first.len() {
            assert!(decoder.decode_frame(&first[..end]).is_err());
        }
        assert!(decoder.decode_frame(&bad).is_err());
        assert_eq!(
            decoder.decode_frame(&good).unwrap(),
            reference.decode_frame(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_frame(&first).unwrap(), initial);
    }
}

#[test]
fn explicit_domain_matrix_and_direction_modes_are_rejected() {
    for f in data()["invalid_modes"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let packet = bytes(&f["packet"]);
        let context = HoaFrameContext::from_cookie(&cookie).unwrap();
        let error = parse_hoa_packet(&context, &packet).unwrap_err();
        assert_eq!(error.kind, "hoa-coding-mode");
        assert!(
            SqDecoder::from_cookie(&cookie)
                .unwrap()
                .decode_frame(&packet)
                .is_err()
        );
    }
}
