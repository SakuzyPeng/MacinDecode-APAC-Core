//! Independent Python-authored wire fixtures exercise public decoder transactions.
use super::*;
use serde_json::Value;
fn bytes(value: &Value) -> Vec<u8> {
    value
        .as_str()
        .unwrap()
        .as_bytes()
        .chunks(2)
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect()
}
fn fixtures() -> Vec<Value> {
    [
        include_str!("../../../../data/channel-state-fixtures-v1.json"),
        include_str!("../../../../data/layout-state-fixtures-v1.json"),
        include_str!("../../../../data/surround916-state-fixtures-v1.json"),
    ]
    .into_iter()
    .flat_map(|raw| {
        serde_json::from_str::<Value>(raw).unwrap()["fixtures"]
            .as_array()
            .unwrap()
            .clone()
    })
    .collect()
}
fn bits(values: Vec<f32>) -> Vec<u32> {
    values.into_iter().map(f32::to_bits).collect()
}
fn state(decoder: &Decoder) -> (Vec<Vec<u64>>, Value) {
    (
        decoder
            .channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
        serde_json::json!({"configuration":decoder.drc.configuration,"nodes":decoder.drc.previous_nodes}),
    )
}
#[test]
fn all_layouts_preserve_every_channel_and_metadata_after_late_errors() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let n = row["channels"].as_u64().unwrap() as u32;
        assert_eq!(decoder.info().channel_count, n);
        assert_eq!(decoder.info().layout.tag & 0xffff, n);
        assert_eq!(
            decoder.decode_vec(&bytes(&row["first"])).unwrap().len(),
            n as usize * 1024
        );
        let before = state(&decoder);
        for key in ["last_element_error", "late_drc_error"] {
            assert!(decoder.decode_vec(&bytes(&row[key])).is_err(), "{n} {key}");
            assert_eq!(state(&decoder), before, "{n} {key}");
        }
        let actual = bits(decoder.decode_vec(&bytes(&row["next"])).unwrap());
        let mut reference = Decoder::from_cookie(&cookie).unwrap();
        reference.decode_vec(&bytes(&row["first"])).unwrap();
        assert_eq!(
            actual,
            bits(reference.decode_vec(&bytes(&row["next"])).unwrap())
        );
    }
}
#[test]
fn reset_rebuilds_all_channels_and_initial_drc_configuration() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let fresh = Decoder::from_cookie(&cookie).unwrap();
        decoder.decode_vec(&bytes(&row["first"])).unwrap();
        decoder.reset();
        assert_eq!(state(&decoder), state(&fresh));
        let actual = bits(decoder.decode_vec(&bytes(&row["first"])).unwrap());
        let mut fresh = fresh;
        assert_eq!(
            actual,
            bits(fresh.decode_vec(&bytes(&row["first"])).unwrap())
        );
    }
}
#[test]
fn channel_reports_cover_declared_slots_and_reject_lrvq_at_the_last_element() {
    for row in fixtures() {
        let context =
            crate::frame::ChannelFrameContext::from_cookie(&bytes(&row["cookie"])).unwrap();
        let report = crate::frame::parse_channel_packet(&context, &bytes(&row["first"])).unwrap();
        assert!(report.packet_complete);
        let indices: Vec<u8> = report
            .elements
            .iter()
            .flat_map(|e| e.configuration.output_channels.iter().copied())
            .collect();
        assert_eq!(
            indices,
            (0..row["channels"].as_u64().unwrap() as u8).collect::<Vec<_>>()
        );
        let report =
            crate::frame::parse_channel_packet(&context, &bytes(&row["last_element_error"]))
                .unwrap();
        assert!(!report.packet_complete);
        assert!(report.frame.stop_reason.starts_with("lrvq_element_"));
        assert!(
            report.elements[..report.elements.len() - 1]
                .iter()
                .all(|e| e.element_complete)
        );
    }
}
