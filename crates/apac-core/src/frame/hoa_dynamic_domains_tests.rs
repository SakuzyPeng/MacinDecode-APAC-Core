use super::*;
use crate::{
    config::bits::BitReader,
    frame::{HoaFrameContext, parse_hoa_packet},
    synthesis::Decoder,
};
use serde_json::Value;
fn data() -> Value {
    serde_json::from_str(include_str!(
        "../../../../data/hoa-dynamic-domains-state-v1.json"
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
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect()
}

#[test]
fn actual_domains_preserve_output_dimensions_and_atomic_transactions() {
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let bad = bytes(&f["bad"]);
        let context = HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported(), "{:?}", context.rejection());
        let n = f["options"]["output_coefficients"].as_u64().unwrap() as usize;
        assert_eq!(context.channel_count(), n as u32);
        assert_eq!(context.maximum_preroll_bytes(), n as u64 * 2048);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        let initial = decoder.decode_vec(&first).unwrap();
        assert_eq!(initial.len(), 1024 * n);
        clean.decode_vec(&first).unwrap();
        assert!(decoder.decode_vec(&bad).is_err());
        assert_eq!(
            decoder.decode_vec(&good).unwrap(),
            clean.decode_vec(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_vec(&first).unwrap(), initial);
        let report = parse_hoa_packet(&context, &first).unwrap();
        let selection = report.hoa().dynamic_selection.as_ref().unwrap();
        assert_eq!(
            selection.before_selection.len(),
            context.recovery_slot_count()
        );
        assert_eq!(report.hoa().channels_after_hoa.len(), n);
        assert_eq!(
            report.hoa().output_order,
            (n.isqrt().pow(2) == n).then_some(n.isqrt() as u8 - 1)
        );
    }
}

#[test]
fn general_mapping_payloads_preserve_following_bits_and_zero_unselected_outputs() {
    for f in data()["mapping"].as_array().unwrap() {
        let context = HoaFrameContext::from_cookie(&bytes(&f["cookie"])).unwrap();
        let packet = bytes(&f["packet"]);
        let template = parse_hoa_packet(&context, &packet).unwrap().packet.frame;
        let raw = bytes(&f["mapping_bytes"]);
        let end = f["bits"].as_u64().unwrap() as usize;
        let m = f["internal"].as_u64().unwrap() as usize;
        let n = f["output"].as_u64().unwrap() as usize;
        let input: Vec<_> = (0..m)
            .map(|slot| RecoverySlotSpectrum {
                slot_index: slot as u8,
                scaled: vec![(slot + 1) as f32; 1024],
            })
            .collect();
        for cut in (0..=end).filter(|&cut| {
            end <= 512
                || cut < 9
                || cut + 16 >= end
                || (cut > 0 && (cut - 1) % ((end - 1) / 8) == 0)
        }) {
            let mut parser = Parser {
                mode: crate::frame::ParseMode::Report,
                bits: BitReader::new(&raw),
                report: template.clone(),
            };
            parser.bits.set_end(cut).unwrap();
            let mut state = HoaState::default();
            let result = read_and_apply(
                &mut parser,
                &context.configuration,
                0,
                input.clone(),
                None,
                &mut state,
            );
            if cut < end {
                assert_eq!(result.unwrap_err().kind, "truncated");
                assert!(state.last_dynamic_mapping.is_none());
                continue;
            }
            let (mapping, output) = result.unwrap();
            assert_eq!(mapping.end_bit_offset, end);
            assert_eq!(mapping.mappings.len(), if m < n { 8 } else { 0 });
            for line in 0..1024 {
                let targets: Vec<_> = if m >= n {
                    (0..n).collect()
                } else {
                    let band = f["truth"]["lines_per_window"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .position(|v| line < v.as_u64().unwrap() as usize)
                        .unwrap();
                    f["truth"]["mappings"][band]["target_acn_indices"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_u64().unwrap() as usize)
                        .collect()
                };
                for (acn, c) in output.iter().enumerate() {
                    assert_eq!(
                        c.scaled[line],
                        targets
                            .iter()
                            .position(|&v| v == acn)
                            .map_or(0., |s| (s + 1) as f32)
                    );
                }
            }
            parser.bits.set_end(raw.len() * 8).unwrap();
            assert_eq!(parser.bits.read(5).unwrap(), 0b10101);
            assert_eq!(state.last_dynamic_mapping.is_some(), m < n);
        }
    }
}

#[test]
fn invalid_final_mapping_rows_do_not_commit_description_or_output_state() {
    for f in data()["mapping"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let good = bytes(&f["packet"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let mut clean = Decoder::from_cookie(&cookie).unwrap();
        decoder.decode_vec(&good).unwrap();
        clean.decode_vec(&good).unwrap();
        for invalid in f["errors"].as_array().unwrap() {
            assert!(decoder.decode_vec(&bytes(invalid)).is_err());
        }
        assert_eq!(
            decoder.decode_vec(&good).unwrap(),
            clean.decode_vec(&good).unwrap()
        );
    }
}
