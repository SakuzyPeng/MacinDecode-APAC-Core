use super::*;
use crate::{config::bits::BitReader, frame::HoaFrameContext, synthesis::SqDecoder};
use serde_json::Value;

fn data() -> Value {
    serde_json::from_str(include_str!(
        "../../../../data/hoa-quantization-state-v1.json"
    ))
    .unwrap()
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

pub(super) fn check_words(counts: &[usize], precisions: std::ops::RangeInclusive<u8>) -> usize {
    let mut words = 0;
    for &count in counts {
        for precision in precisions.clone() {
            let c = constants_for_bits(count, precision);
            for (mode, format) in c.format.modes.iter().enumerate() {
                for (book, entries) in format.codebooks.iter().enumerate() {
                    for (value, &(length, code)) in entries.iter().enumerate() {
                        let bit_count = length + 5;
                        let size = bit_count.div_ceil(8);
                        let encoded = (((u64::from(code) << 5) | 0b10101)
                            << (size * 8 - bit_count))
                            .to_be_bytes();
                        let raw = &encoded[8 - size..];
                        for limit in 0..length {
                            let mut bits = BitReader::new(raw);
                            bits.set_end(limit).unwrap();
                            assert!(c.tries[mode][book].read(&mut bits).is_err());
                        }
                        let mut bits = BitReader::new(raw);
                        assert_eq!(c.tries[mode][book].read(&mut bits).unwrap(), value);
                        assert_eq!(bits.position(), length);
                        assert_eq!(bits.read(5).unwrap(), 0b10101);
                        words += 1;
                    }
                }
            }
        }
    }
    words
}

#[test]
fn every_wide_dictionary_symbol_preserves_its_value_and_following_bits() {
    assert_eq!(check_words(&[4, 9, 16], 7..=9), 3 * 8 * (128 + 256 + 512));
}

#[test]
fn wide_quantized_reports_preserve_511_and_atomic_history() {
    let mut wide = false;
    for f in data()["fixtures"].as_array().unwrap() {
        let cookie = bytes(&f["cookie"]);
        let first = bytes(&f["first"]);
        let good = bytes(&f["good"]);
        let bad = bytes(&f["bad"]);
        let context = HoaFrameContext::from_cookie(&cookie).unwrap();
        assert!(context.is_supported(), "{:?}", context.rejection());
        assert_eq!(
            u64::from(context.quantization_bits()),
            f["options"]["quantization_bits"].as_u64().unwrap()
        );
        let report = crate::frame::parse_hoa_packet(&context, &first).unwrap();
        let side = report
            .hoa()
            .spatial
            .as_ref()
            .unwrap()
            .salient
            .as_ref()
            .unwrap();
        assert_eq!(side.quantization_bits, Some(context.quantization_bits()));
        for (actual, truth) in side
            .descriptors
            .iter()
            .zip(f["truth"]["descriptors"].as_array().unwrap())
        {
            assert_eq!(
                serde_json::to_value(&actual.quantized).unwrap(),
                truth["quantized"]
            );
            wide |= actual.quantized.contains(&511);
        }
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        let pcm = decoder.decode_frame(&first).unwrap();
        clean.decode_frame(&first).unwrap();
        assert!(decoder.decode_frame(&bad).is_err());
        assert_eq!(
            decoder.decode_frame(&good).unwrap(),
            clean.decode_frame(&good).unwrap()
        );
        decoder.reset();
        assert_eq!(decoder.decode_frame(&first).unwrap(), pcm);
    }
    assert!(wide);
}
