use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, HoaState, parse_hoa_packet, parse_hoa_packet_with_state},
    synthesis::Decoder,
};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!("../../../../data/hoa-controls-state-v2.json")).unwrap()
}
fn bytes(v: &Value) -> Vec<u8> {
    v.as_str()
        .unwrap()
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect()
}

#[test]
fn control_histories_overlaps_and_failed_packets_commit_atomically() {
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let bad = bytes(&f["bad"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        let initial = decoder.decode_vec(&first).unwrap();
        clean.decode_vec(&first).unwrap();
        assert!(decoder.decode_vec(&bad).is_err());
        assert_eq!(
            decoder.decode_vec(&good).unwrap(),
            clean.decode_vec(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_vec(&first).unwrap(), initial);
        let mut sequence = Decoder::from_cookie(&cookie).unwrap();
        for p in f["packets"].as_array().unwrap() {
            assert_eq!(
                sequence.decode_vec(&bytes(p)).unwrap().len(),
                1024 * sequence.info().channel_count as usize
            );
        }
    }
}

#[test]
fn inactive_subbands_and_components_are_cleared_using_the_encoder_stride() {
    let d = data();
    let f = d["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["name"] == "frame-variable")
        .unwrap();
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let mut state = HoaState::default();
    let mut drc = context.initial_drc_state();
    for (i, p) in f["packets"].as_array().unwrap().iter().enumerate() {
        let r = parse_hoa_packet_with_state(&context, &bytes(p), &mut drc, &mut state).unwrap();
        assert!(r.packet.packet_complete);
        if i == 2 || i == 4 {
            let descriptors = &r
                .hoa()
                .spatial
                .as_ref()
                .unwrap()
                .salient
                .as_ref()
                .unwrap()
                .descriptors;
            let expected = [-0.03125, 0.0625, -0.09375];
            for (d, v) in descriptors.iter().take(3).zip(expected) {
                assert_eq!(d.restored[3], v);
            }
        }
    }
}

#[test]
fn frame_configuration_rejects_every_bit_truncation_without_partial_state() {
    let d = data();
    for f in d["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|f| !f["frame_configuration"].is_null())
    {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let raw = bytes(&f["first"]);
        let template = parse_hoa_packet(&context, &raw).unwrap().packet.frame;
        let start = f["frame_configuration"]["start_bit_offset"]
            .as_u64()
            .unwrap() as usize;
        let end = f["frame_configuration"]["end_bit_offset"].as_u64().unwrap() as usize;
        for cut in start..end {
            let mut parser = Parser {
                capture: true,
                bits: BitReader::new(&raw),
                report: template.clone(),
            };
            parser.bits.set_end(cut).unwrap();
            parser.bits.skip(start).unwrap();
            let mut state = HoaState::default();
            let before = state.clone();
            let error = effective_configuration(&mut parser, &mut state, &context.configuration, 1)
                .unwrap_err();
            assert_eq!(error.kind, "truncated");
            assert_eq!(state, before);
        }
    }
}

#[test]
fn shrinking_shapes_clear_inactive_history_without_reviving_higher_coefficients() {
    let d = data();
    let f = &d["coefficient_history"];
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let mut state = HoaState::default();
    let mut drc = context.initial_drc_state();
    for (i, p) in f["packets"].as_array().unwrap().iter().enumerate() {
        let r = parse_hoa_packet_with_state(&context, &bytes(p), &mut drc, &mut state).unwrap();
        assert!(r.packet.packet_complete);
        if i == 1 {
            assert!(
                r.hoa().channels_after_hoa[8]
                    .scaled
                    .iter()
                    .all(|&v| v == 0.)
            );
        }
        if i == 2 || i == 4 {
            let values = &r
                .hoa()
                .spatial
                .as_ref()
                .unwrap()
                .salient
                .as_ref()
                .unwrap()
                .descriptors;
            let expected = [-0.03125, 0.0625, -0.09375];
            for (row, value) in values.iter().take(3).zip(expected) {
                assert_eq!(row.restored[8], value);
            }
        }
    }
}

#[test]
fn direction_without_explicit_coefficients_retains_the_whole_unit_vector() {
    let d = data();
    let f = d["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["name"] == "direction")
        .unwrap();
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let r = parse_hoa_packet(&context, &bytes(&f["first"])).unwrap();
    for d in &r
        .hoa()
        .spatial
        .as_ref()
        .unwrap()
        .salient
        .as_ref()
        .unwrap()
        .descriptors
    {
        assert!(d.quantized.is_empty());
        assert_eq!(d.end_bit_offset - d.start_bit_offset, 20);
        assert!((d.restored.iter().map(|v| v * v).sum::<f64>() - 1.).abs() < 1e-13);
    }
    for cookie in data()["invalid"].as_array().unwrap() {
        assert!(
            !HoaFrameContext::from_cookie(&bytes(cookie))
                .unwrap()
                .is_supported()
        );
    }
    for f in data()["invalid_frames"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        assert_eq!(
            parse_hoa_packet(&context, &bytes(&f["packet"]))
                .unwrap_err()
                .kind,
            "hoa-frame-configuration"
        );
    }
}

#[test]
fn encoder_history_stride_does_not_alias_components_when_the_active_maximum_changes() {
    let d = data();
    let f = &d["stride_history"];
    let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
    let mut state = HoaState::default();
    let mut drc = context.initial_drc_state();
    for (i, p) in f["packets"].as_array().unwrap().iter().enumerate() {
        let r = parse_hoa_packet_with_state(&context, &bytes(p), &mut drc, &mut state).unwrap();
        assert!(r.packet.packet_complete);
        let values = &r
            .hoa()
            .spatial
            .as_ref()
            .unwrap()
            .salient
            .as_ref()
            .unwrap()
            .descriptors;
        let expected: &[f64] = match i {
            0 => &[0.25, 0.375, 0.5],
            1 => &[0.28125, 0.3125],
            _ => &[0.3125, 0.25, 0.09375],
        };
        for (row, &value) in values
            .iter()
            .filter(|v| v.component_index == 1)
            .zip(expected)
        {
            assert_eq!(row.restored[8], value);
        }
    }
}
