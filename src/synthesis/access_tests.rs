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
    serde_json::from_str::<Value>(include_str!("../../data/channel-state-fixtures-v1.json"))
        .unwrap()["fixtures"]
        .as_array()
        .unwrap()
        .clone()
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
        let mut sequential = SqDecoder::from_cookie(&cookie).unwrap();
        let mut fast = SqDecoder::from_cookie(&cookie).unwrap();
        for _ in 0..7 {
            sequential.decode_frame(&first).unwrap();
            let counts = fast.scan_frame(&first).unwrap();
            assert_eq!(counts.numeric_elements, 0);
            assert_eq!(fast.metadata_sha256(), sequential.metadata_sha256());
            assert!(
                fast.channels
                    .iter()
                    .all(|c| c.overlap.iter().all(|x| *x == 0.))
            );
        }
        sequential.decode_frame(&next).unwrap();
        fast.decode_frame(&next).unwrap();
        assert_eq!(
            pcm(sequential.decode_frame(&next).unwrap()),
            pcm(fast.decode_frame(&next).unwrap())
        );
        assert_eq!(fast.metadata_sha256(), sequential.metadata_sha256());
        fast.reset();
        let fresh = SqDecoder::from_cookie(&cookie).unwrap();
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
        let mut sequential = SqDecoder::from_cookie(&cookie).unwrap();
        let mut fast = SqDecoder::from_cookie(&cookie).unwrap();
        sequential.decode_frame(&first).unwrap();
        fast.scan_frame(&first).unwrap();
        let before = fast.metadata_sha256();
        for key in ["last_element_error", "late_drc_error"] {
            let packet = bytes(&row[key]);
            let expected = sequential.decode_frame(&packet).unwrap_err();
            let actual = fast.scan_frame(&packet).err().unwrap();
            assert_eq!(
                serde_json::to_value(actual).unwrap(),
                serde_json::to_value(expected).unwrap()
            );
            assert_eq!(before, fast.metadata_sha256());
            assert!(
                fast.channels
                    .iter()
                    .all(|c| c.overlap.iter().all(|x| *x == 0.))
            );
        }
        for end in 0..first.len() {
            let expected = sequential.decode_frame(&first[..end]).unwrap_err();
            let actual = fast.scan_frame(&first[..end]).err().unwrap();
            assert_eq!(
                serde_json::to_value(actual).unwrap(),
                serde_json::to_value(expected).unwrap()
            );
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
                .chain(&t.modulation)
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
