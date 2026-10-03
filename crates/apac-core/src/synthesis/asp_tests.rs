//! ASP alias semantics and atomic failure boundaries, including component state.
use super::*;
use serde_json::Value;
fn bytes(value: &Value) -> Vec<u8> {
    value
        .as_str()
        .unwrap()
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
fn state(decoder: &SqDecoder) -> (String, Vec<Vec<u64>>) {
    (
        decoder.metadata_sha256(),
        decoder
            .channels
            .iter()
            .map(|c| c.overlap.iter().map(|v| v.to_bits()).collect())
            .collect(),
    )
}
fn pcm(samples: Vec<f32>) -> Vec<u32> {
    samples.into_iter().map(f32::to_bits).collect()
}
#[test]
fn asp_type_aliases_and_ignored_padding_preserve_pcm() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-asp-vectors-v1.json")).unwrap();
    for row in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        for (baseline, variant) in [
            ("first", "no_preroll"),
            ("zero", "three"),
            ("embedded", "inner_three"),
            ("embedded", "padded"),
        ] {
            let mut a = SqDecoder::from_cookie(&cookie).unwrap();
            let mut b = SqDecoder::from_cookie(&cookie).unwrap();
            a.decode_frame(&first).unwrap();
            b.decode_frame(&first).unwrap();
            assert_eq!(
                pcm(a.decode_frame(&bytes(&row[baseline])).unwrap()),
                pcm(b.decode_frame(&bytes(&row[variant])).unwrap()),
                "{} {variant}",
                row["name"]
            );
            assert_eq!(
                pcm(a.decode_frame(&bytes(&row["zero"])).unwrap()),
                pcm(b.decode_frame(&bytes(&row["zero"])).unwrap())
            );
        }
    }
}

#[test]
fn global_frame_length_rejections_are_reference_implementation_boundaries() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-asp-vectors-v1.json")).unwrap();
    let original = bytes(&data["fixtures"][0]["cookie"]);
    assert!(SqDecoder::from_cookie(&original).is_ok());
    for index in 1..64u8 {
        let mut cookie = original.clone();
        let start = 96 + 16 + 6 + 4 + 1 + 6;
        for bit in 0..6 {
            let at = start + bit;
            cookie[at / 8] = (cookie[at / 8] & !(1 << (7 - at % 8)))
                | (((index >> (5 - bit)) & 1) << (7 - at % 8));
        }
        let (_, report) = crate::config::parse_recorded(&cookie).unwrap();
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.message.contains("only implements frame-size index 0"))
        );
        assert!(SqDecoder::from_cookie(&cookie).is_err());
    }
}
#[test]
fn asp_rejections_do_not_commit_any_history_and_retry_reset_match() {
    let data: Value =
        serde_json::from_str(include_str!("../../../../data/hoa-asp-vectors-v1.json")).unwrap();
    for row in data["fixtures"].as_array().unwrap() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        let mut decoder = SqDecoder::from_cookie(&cookie).unwrap();
        let initial = state(&decoder);
        let first_pcm = pcm(decoder.decode_frame(&first).unwrap());
        let before = state(&decoder);
        for (name, packet) in row["bad"].as_object().unwrap() {
            assert!(
                decoder.decode_frame(&bytes(packet)).is_err(),
                "{} {name}",
                row["name"]
            );
            assert_eq!(before, state(&decoder), "{} {name}", row["name"]);
        }
        let mut clean = SqDecoder::from_cookie(&cookie).unwrap();
        clean.decode_frame(&first).unwrap();
        assert_eq!(
            pcm(decoder.decode_frame(&bytes(&row["embedded"])).unwrap()),
            pcm(clean.decode_frame(&bytes(&row["embedded"])).unwrap())
        );
        assert_eq!(state(&decoder), state(&clean));
        decoder.reset();
        assert_eq!(initial, state(&decoder));
        assert_eq!(first_pcm, pcm(decoder.decode_frame(&first).unwrap()));
    }
}
