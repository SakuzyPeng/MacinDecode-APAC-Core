//! Equivalence of the generated tables with the former runtime loaders.
//!
//! Each section keeps the loader code that used to run on first use (JSON
//! parsing, assertions and derivations) and compares its result with the
//! static data, bit for bit.
use crate::prelude::*;
use serde::Deserialize;
use std::collections::BTreeMap;

/// The former `Trie::new`, kept verbatim so the shared builder is checked
/// against an independent implementation.
mod legacy_trie {
    use crate::prelude::*;

    #[derive(Default, Clone, PartialEq, Debug)]
    pub struct Node {
        pub children: [Option<usize>; 2],
        pub symbol: Option<usize>,
    }
    pub fn build(codes: &[u32], bits: &[usize]) -> Vec<Node> {
        let mut nodes = vec![Node::default()];
        assert_eq!(codes.len(), bits.len());
        for (symbol, (&code, &width)) in codes.iter().zip(bits).enumerate() {
            let mut node = 0;
            for shift in (0..width).rev() {
                assert!(nodes[node].symbol.is_none());
                let bit = ((code >> shift) & 1) as usize;
                node = if let Some(next) = nodes[node].children[bit] {
                    next
                } else {
                    let next = nodes.len();
                    nodes.push(Node::default());
                    nodes[node].children[bit] = Some(next);
                    next
                };
            }
            assert!(nodes[node].symbol.is_none() && nodes[node].children == [None; 2]);
            nodes[node].symbol = Some(symbol);
        }
        nodes
    }
}

fn data(name: &str) -> String {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../data")
        .join(name);
    std::fs::read_to_string(path).expect("data file")
}

fn bits32(values: &[f32]) -> Vec<u32> {
    values.iter().map(|v| v.to_bits()).collect()
}
fn bits64(values: &[f64]) -> Vec<u64> {
    values.iter().map(|v| v.to_bits()).collect()
}

/// Static trie nodes must equal the former runtime trie, node by node.
fn assert_trie(trie: &super::Trie, codes: &[u32], bits: &[usize]) {
    let legacy: Vec<[u16; 3]> = legacy_trie::build(codes, bits)
        .into_iter()
        .map(|n| {
            [
                n.children[0].map_or(0, |v| v as u16),
                n.children[1].map_or(0, |v| v as u16),
                n.symbol.map_or(super::trie_build::NO_SYMBOL, |v| v as u16),
            ]
        })
        .collect();
    assert_eq!(trie.nodes(), legacy.as_slice());
}

#[derive(Deserialize)]
struct LegacyCodebook {
    codes: Vec<u32>,
    bits: Vec<usize>,
}
fn assert_codebook(actual: &super::Codebook, expected: &LegacyCodebook) {
    assert_eq!(actual.codes, expected.codes.as_slice());
    assert_eq!(actual.bits, expected.bits.as_slice());
}

#[test]
fn sq_numeric_tables_match_the_json_loader() {
    #[derive(Deserialize)]
    struct RawTransform {
        window_f64: Vec<u64>,
        modulation_f64: Vec<[u64; 2]>,
        twiddles_f64: Vec<[u64; 2]>,
    }
    #[derive(Deserialize)]
    struct RawTables {
        numeric_profile: String,
        tables_sha256: String,
        inverse_quantizer_f32: Vec<u32>,
        gains_f32: Vec<u32>,
        transforms: BTreeMap<String, RawTransform>,
    }
    let mut raw: RawTables = serde_json::from_str(&data("sq-math-v1.json")).unwrap();
    assert_eq!(raw.numeric_profile, crate::numeric::PROFILE);
    let tables = crate::numeric::tables();
    assert_eq!(tables.sha256, raw.tables_sha256);
    assert_eq!(bits32(tables.inverse), raw.inverse_quantizer_f32);
    assert_eq!(bits32(tables.gains), raw.gains_f32);
    for n in [128, 1024] {
        let data = raw.transforms.remove(&n.to_string()).unwrap();
        let mut window: Vec<_> = data.window_f64.into_iter().map(f64::from_bits).collect();
        window.extend(window.clone().into_iter().rev());
        let transform = tables.transform(n);
        assert_eq!(bits64(transform.window), bits64(&window));
        let pairs = |v: &[[f64; 2]]| v.iter().map(|p| p.map(f64::to_bits)).collect::<Vec<_>>();
        assert_eq!(pairs(transform.modulation), data.modulation_f64);
        assert_eq!(pairs(transform.twiddles), data.twiddles_f64);
    }
}

#[test]
fn sq_codebooks_offsets_and_tries_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Tables {
        spectral: Vec<LegacyCodebook>,
        scalefactor: LegacyCodebook,
        long_offsets: Vec<usize>,
        short_offsets: Vec<usize>,
    }
    let legacy: Tables = serde_json::from_str(&data("sq-codebooks.json")).unwrap();
    assert_eq!(super::SQ_SPECTRAL.len(), legacy.spectral.len());
    assert_codebook(&super::SQ_SCALEFACTOR, &legacy.scalefactor);
    for (actual, expected) in super::SQ_SPECTRAL.iter().zip(&legacy.spectral) {
        assert_codebook(actual, expected);
    }
    assert_eq!(super::SQ_LONG_OFFSETS, legacy.long_offsets.as_slice());
    assert_eq!(super::SQ_SHORT_OFFSETS, legacy.short_offsets.as_slice());
    let books: Vec<&LegacyCodebook> = core::iter::once(&legacy.scalefactor)
        .chain(legacy.spectral.iter())
        .collect();
    assert_eq!(super::SQ_TRIES.len(), books.len());
    for (trie, book) in super::SQ_TRIES.iter().zip(books) {
        assert_trie(trie, &book.codes, &book.bits);
    }
}

#[test]
fn cac_codebooks_rotations_and_tries_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Books {
        gain: LegacyCodebook,
        repeat: LegacyCodebook,
    }
    #[derive(Deserialize)]
    struct Rotation {
        a_f64: u64,
        b_f64: u64,
        swap: bool,
    }
    #[derive(Deserialize)]
    struct Math {
        numeric_profile: String,
        tables_sha256: String,
        rotations: Vec<Rotation>,
    }
    let books: Books = serde_json::from_str(&data("cac-codebooks.json")).unwrap();
    assert_codebook(&super::CAC_GAIN, &books.gain);
    assert_codebook(&super::CAC_REPEAT, &books.repeat);
    assert_trie(&super::CAC_TRIES[0], &books.gain.codes, &books.gain.bits);
    assert_trie(
        &super::CAC_TRIES[1],
        &books.repeat.codes,
        &books.repeat.bits,
    );
    let math: Math = serde_json::from_str(&data("cac-math-v1.json")).unwrap();
    assert_eq!(math.numeric_profile, crate::frame::CAC_NUMERIC_PROFILE);
    assert_eq!(super::CAC_MATH_SHA256, math.tables_sha256);
    assert_eq!(crate::frame::cac_math_sha256(), math.tables_sha256);
    assert_eq!(super::CAC_ROTATIONS.len(), math.rotations.len());
    for (actual, expected) in super::CAC_ROTATIONS.iter().zip(&math.rotations) {
        assert_eq!(
            (actual.a_f64, actual.b_f64, actual.swap),
            (expected.a_f64, expected.b_f64, expected.swap)
        );
    }
}

#[test]
fn tns_reflections_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Math {
        numeric_profile: String,
        tables_sha256: String,
        reflection_f64: BTreeMap<String, Vec<u64>>,
    }
    let math: Math = serde_json::from_str(&data("tns-math-v1.json")).unwrap();
    assert_eq!(math.numeric_profile, crate::frame::TNS_NUMERIC_PROFILE);
    assert_eq!(crate::frame::tns_math_sha256(), math.tables_sha256);
    assert_eq!(super::TNS_REFLECTION_3, math.reflection_f64["3"].as_slice());
    assert_eq!(super::TNS_REFLECTION_4, math.reflection_f64["4"].as_slice());
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
    assert_eq!(math.numeric_profile, crate::bwe2_math::PROFILE);
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
