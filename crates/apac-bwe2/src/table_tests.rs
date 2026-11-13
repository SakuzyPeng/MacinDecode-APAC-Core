//! Bit-for-bit comparison against the serialized numerical sources.
use super::*;
use serde::Deserialize;
use std::collections::BTreeMap;
fn data(name: &str) -> String {
    if name == "bwe2-format-v1.json" {
        return std::fs::read_to_string(
            std::env::var_os("APAC_BWE2_TABLE").expect("external LSF input"),
        )
        .expect("external LSF file");
    }
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../data")
        .join(name);
    std::fs::read_to_string(path).expect("data file")
}
fn bits64(values: &[f64]) -> Vec<u64> {
    values.iter().map(|v| v.to_bits()).collect()
}

#[test]
fn bwe2_constants_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Format {
        format_profile: String,
        tables_sha256: String,
        lsf_codebooks_f32: Vec<Vec<[u32; 16]>>,
        excitation_gains_f32: Vec<u32>,
    }
    #[derive(Deserialize)]
    struct Math {
        numeric_profile: String,
        tables_sha256: String,
        twiddles_f64: BTreeMap<String, Vec<[u64; 2]>>,
        cosine_f64: [u64; 13],
        sine_f64: [u64; 13],
        lsf_angle_scale_f64: u64,
        autocorrelation_loading_f64: u64,
    }
    let format: Format = serde_json::from_str(&data("bwe2-format-v1.json")).unwrap();
    let math: Math = serde_json::from_str(&data("bwe2-math-v2.json")).unwrap();
    assert_eq!(format.format_profile, "apac-bwe2-format-v1");
    assert_eq!(math.numeric_profile, crate::PROFILE);
    let constants = &super::BWE2;
    assert_eq!(constants.format_sha, format.tables_sha256);
    assert_eq!(constants.math_sha, math.tables_sha256);
    for (book, legacy) in constants.books.iter().zip(&format.lsf_codebooks_f32) {
        let expected: Vec<[u64; 16]> = legacy
            .iter()
            .map(|row| row.map(|x| f64::from(f32::from_bits(x)).to_bits()))
            .collect();
        let actual: Vec<[u64; 16]> = book.iter().map(|row| row.map(f64::to_bits)).collect();
        assert_eq!(actual, expected);
    }
    let gains: Vec<u64> = format
        .excitation_gains_f32
        .iter()
        .map(|&x| f64::from(f32::from_bits(x)).to_bits())
        .collect();
    assert_eq!(bits64(constants.gains), gains);
    assert_eq!(constants.twiddles.len(), math.twiddles_f64.len());
    for (n, values) in &math.twiddles_f64 {
        let n: usize = n.parse().unwrap();
        let (_, actual) = constants
            .twiddles
            .iter()
            .find(|(size, _)| *size == n)
            .unwrap();
        let actual: Vec<[u64; 2]> = actual
            .iter()
            .map(|c| [c.re.to_bits(), c.im.to_bits()])
            .collect();
        assert_eq!(&actual, values);
    }
    assert_eq!(constants.cosine.map(f64::to_bits), math.cosine_f64);
    assert_eq!(constants.sine.map(f64::to_bits), math.sine_f64);
    assert_eq!(constants.angle_scale.to_bits(), math.lsf_angle_scale_f64);
    assert_eq!(
        constants.loading.to_bits(),
        math.autocorrelation_loading_f64
    );
}
