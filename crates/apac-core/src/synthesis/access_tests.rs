//! Access-mode convergence and transaction tests independent of container seeking.
use super::*;
use serde_json::Value;
fn bytes(v: &Value) -> Vec<u8> {
    let s = v.as_str().unwrap();
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn fixtures() -> Vec<Value> {
    [
        include_str!("../../../../data/channel-state-fixtures-v1.json"),
        include_str!("../../../../data/layout-state-fixtures-v1.json"),
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
fn pcm(v: Vec<f32>) -> Vec<u32> {
    v.into_iter().map(f32::to_bits).collect()
}
#[test]
fn scanning_preserves_metadata_and_one_predecessor_converges() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        let next = bytes(&row["next"]);
        let mut sequential = Decoder::from_cookie(&cookie).unwrap();
        let mut fast = Decoder::from_cookie(&cookie).unwrap();
        for _ in 0..7 {
            sequential.decode_vec(&first).unwrap();
            let counts = fast.advance(&first).unwrap();
            assert_eq!(counts.numeric_elements, 0);
            assert_eq!(fast.metadata_sha256(), sequential.metadata_sha256());
            assert!(
                fast.channels
                    .iter()
                    .all(|c| c.overlap.iter().all(|x| *x == 0.))
            );
        }
        sequential.decode_vec(&next).unwrap();
        fast.decode_vec(&next).unwrap();
        assert_eq!(
            pcm(sequential.decode_vec(&next).unwrap()),
            pcm(fast.decode_vec(&next).unwrap())
        );
        assert_eq!(fast.metadata_sha256(), sequential.metadata_sha256());
        fast.reset();
        let fresh = Decoder::from_cookie(&cookie).unwrap();
        assert_eq!(fast.metadata_sha256(), fresh.metadata_sha256());
        assert!(
            fast.channels
                .iter()
                .all(|c| c.overlap.iter().all(|x| *x == 0.))
        );
    }
}
#[test]
fn failed_scans_preserve_all_state_and_exact_decoder_error() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        let mut sequential = Decoder::from_cookie(&cookie).unwrap();
        let mut fast = Decoder::from_cookie(&cookie).unwrap();
        sequential.decode_vec(&first).unwrap();
        fast.advance(&first).unwrap();
        let before = fast.metadata_sha256();
        for key in ["last_element_error", "late_drc_error"] {
            let packet = bytes(&row[key]);
            let expected = sequential.decode_vec(&packet).unwrap_err();
            let actual = fast.advance(&packet).err().unwrap();
            assert_eq!(actual, expected);
            assert_eq!(before, fast.metadata_sha256());
            assert!(
                fast.channels
                    .iter()
                    .all(|c| c.overlap.iter().all(|x| *x == 0.))
            );
        }
        for end in 0..first.len() {
            let expected = sequential.decode_vec(&first[..end]).unwrap_err();
            let actual = fast.advance(&first[..end]).err().unwrap();
            assert_eq!(actual, expected);
            assert_eq!(before, fast.metadata_sha256());
        }
    }
}
#[test]
fn bounded_constants_and_extreme_finite_spectra_cannot_overflow_synthesis() {
    let tables = crate::numeric::tables();
    // All inverse/gain entries are nonnegative and monotone (numeric unit test).
    let maximum = tables.inverse[8191] * tables.gains[511];
    assert!(maximum.is_finite());
    assert!(f64::from(maximum) < 2f64.powi(58));
    for n in [128, 1024] {
        let t = tables.transform(n);
        assert!(t.window.iter().all(|v| v.is_finite() && v.abs() <= 1.));
        assert!(
            t.twiddles
                .iter()
                .chain(t.modulation)
                .flatten()
                .all(|v| v.is_finite() && v.abs() <= 1.)
        );
    }
    // A conservative complex-component bound grows by at most 3 per FFT stage.
    // Long: 4*3^9/(1024*32768); short: 2*4*3^6/(128*32768).
    // Include the previous frame and generous rounding slack; both are < 1/128.
    for (n, stages, overlap) in [(1024., 9, 1.), (128., 6, 2.)] {
        assert!(2. * overlap * 4. * 3f64.powi(stages) / (n * 32768.) * 1.0001 < 1. / 128.);
    }
    let mut state = ChannelState::new();
    for block in 0..4 {
        for signs in [false, true] {
            let input: Vec<f32> = (0..1024)
                .map(|i| {
                    if signs && i % 2 == 0 {
                        -f32::MAX
                    } else {
                        f32::MAX
                    }
                })
                .collect();
            let result = state.render(&input, block).unwrap();
            assert!(
                result
                    .iter()
                    .all(|v| v.is_finite() && v.abs() <= f32::MAX / 128.)
            );
            assert!(
                state
                    .overlap
                    .iter()
                    .all(|v| v.is_finite() && v.abs() <= f64::from(f32::MAX) / 128.)
            );
        }
    }
}
#[test]
fn decoding_into_a_short_buffer_leaves_the_state_unchanged() {
    for row in fixtures() {
        let cookie = bytes(&row["cookie"]);
        let first = bytes(&row["first"]);
        let next = bytes(&row["next"]);
        let mut decoder = Decoder::from_cookie(&cookie).unwrap();
        let mut reference = Decoder::from_cookie(&cookie).unwrap();
        decoder.decode_vec(&first).unwrap();
        reference.decode_vec(&first).unwrap();
        let samples = 1024 * decoder.info().channel_count as usize;
        let before = decoder.metadata_sha256();
        let mut short = vec![0f32; samples - 1];
        assert!(decoder.decode(&next, &mut short).is_err());
        assert_eq!(decoder.metadata_sha256(), before);
        let mut out = vec![f32::NAN; samples + 3];
        let info = decoder.decode(&next, &mut out).unwrap();
        let expected = pcm(reference.decode_vec(&next).unwrap());
        assert_eq!(pcm(out[..samples].to_vec()), expected);
        assert!(out[samples..].iter().all(|v| v.is_nan()));
        assert_eq!(info.embedded_preroll_frames, 0);
    }
}
#[test]
fn stream_info_matches_the_configuration() {
    let mut decoders = 0usize;
    for cookie in crate::config::model_tests::corpus() {
        let Ok(config) = crate::config::Config::parse(&cookie) else {
            continue;
        };
        let Ok(decoder) = Decoder::new(&config) else {
            continue;
        };
        let info = decoder.info();
        assert_eq!(Some(info.sample_rate_hz), config.sample_rate_hz());
        assert_eq!(info.frame_samples, 1024);
        assert_eq!(
            info.kind == StreamKind::Composite,
            decoder.composite().is_some()
        );
        assert_eq!(info.kind == StreamKind::Hoa, decoder.hoa().is_some());
        decoders += 1;
    }
    assert!(decoders > 50, "{decoders} decoders");
}
