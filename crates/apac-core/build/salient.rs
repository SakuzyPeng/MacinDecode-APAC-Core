//! HOA salient dictionaries (orders 1..10, quantization widths 6..9), their
//! Huffman tries and the shared angle/normalization constants.
use crate::emit::{self, Output};
use crate::{data_json, packed, trie_build};
use serde::Deserialize;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
struct MeasuredWord {
    symbol: usize,
    codeword: String,
    bit_length: usize,
}
#[derive(Deserialize)]
struct MeasuredCodebook {
    schema_version: u8,
    profile: String,
    order: usize,
    quantization_bits: u8,
    mode: usize,
    book: usize,
    book_sha256: String,
    entries: Vec<MeasuredWord>,
}

/// This book's canonical input is the frozen black-box measurement. The
/// original format file keeps a generated packed copy for existing tools.
fn measured_mode1() -> Vec<(usize, u32)> {
    let data: MeasuredCodebook = data_json("hoa-salient-order3-q6-mode1-measured-v1.json");
    assert_eq!(data.schema_version, 1);
    assert_eq!(data.profile, "apac-hoa-salient-measured-v1");
    assert_eq!(
        (data.order, data.quantization_bits, data.mode, data.book),
        (3, 6, 1, 0)
    );
    assert_eq!(data.entries.len(), 64);
    let book: Vec<_> = data
        .entries
        .iter()
        .enumerate()
        .map(|(symbol, entry)| {
            assert_eq!(entry.symbol, symbol);
            assert!((1..=32).contains(&entry.bit_length));
            assert_eq!(entry.codeword.len(), entry.bit_length);
            assert!(entry.codeword.bytes().all(|b| b == b'0' || b == b'1'));
            (
                entry.bit_length,
                u32::from_str_radix(&entry.codeword, 2).expect("measured codeword"),
            )
        })
        .collect();
    let digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&book).expect("measured codebook JSON"))
    );
    assert_eq!(digest, data.book_sha256);
    assert_eq!(
        digest,
        "296d730714d97de653c45cc487fa4fa94aebce9a49559da78e81215591e600ee"
    );
    book
}

#[derive(Deserialize)]
struct SharedMode {
    mode: usize,
    group_indices: Vec<usize>,
    signs: bool,
    matrix_indices: Vec<usize>,
}
#[derive(Deserialize)]
struct StoredSharedFormat {
    schema_version: u8,
    format_profile: String,
    order: usize,
    modes: Vec<SharedMode>,
    groups: Vec<Vec<usize>>,
    matrix_encoding: String,
    matrices_f32: Vec<String>,
}
#[derive(Deserialize)]
struct StoredMode {
    mode: usize,
    codebooks: Vec<String>,
}
#[derive(Deserialize)]
struct StoredFormat {
    schema_version: u8,
    format_profile: String,
    tables_sha256: String,
    order: usize,
    quantization_bits: u8,
    codebook_encoding: String,
    shared_file: String,
    modes: Vec<StoredMode>,
}

/// Order 3 at the original 6-bit width keeps its first-release file name.
fn variant(order: usize, precision: u8) -> (String, String) {
    match (order, precision) {
        (3, 6) => (
            "hoa-salient-format-v1.json".into(),
            "apac-hoa-salient-format-v1".into(),
        ),
        (_, 6) => (
            format!("hoa-salient-order{order}-format-v1.json"),
            format!("apac-hoa-salient-order{order}-format-v1"),
        ),
        _ => (
            format!("hoa-salient-order{order}-q{precision}-format-v1.json"),
            format!("apac-hoa-salient-order{order}-q{precision}-format-v1"),
        ),
    }
}

struct Shared {
    modes: Vec<SharedMode>,
    groups: Vec<Vec<usize>>,
    group_names: Vec<String>,
    matrix_lengths: Vec<usize>,
    matrix_names: Vec<String>,
}

fn shared(out: &mut Output, order: usize) -> Shared {
    let data: StoredSharedFormat = data_json(&format!("hoa-salient-order{order}-shared-v1.json"));
    assert_eq!(data.schema_version, 2);
    assert_eq!(data.format_profile, "apac-hoa-salient-shared-v1");
    assert_eq!(data.matrix_encoding, packed::MATRIX_ENCODING);
    assert_eq!(data.order, order);
    assert_eq!(data.modes.len(), 6);
    let group_names = data
        .groups
        .iter()
        .enumerate()
        .map(|(i, g)| out.array(false, &format!("SALIENT_O{order}_GROUP_{i}"), "usize", g))
        .collect();
    let mut matrix_lengths = Vec::new();
    let mut matrix_names = Vec::new();
    for (i, hex) in data.matrices_f32.iter().enumerate() {
        let words = packed::matrix(hex, (order + 1).pow(4)).expect("built-in packed HOA matrix");
        matrix_lengths.push(words.len());
        matrix_names.push(out.array(
            false,
            &format!("SALIENT_O{order}_MATRIX_{i}"),
            "u32",
            &words,
        ));
    }
    Shared {
        modes: data.modes,
        groups: data.groups,
        group_names,
        matrix_lengths,
        matrix_names,
    }
}

fn refs(names: impl IntoIterator<Item = String>) -> String {
    let items: Vec<String> = names.into_iter().map(|n| format!("&{n}")).collect();
    format!("&[{}]", items.join(", "))
}

pub fn dictionaries(out: &mut Output) {
    let measured = measured_mode1();
    let mut constants = Vec::new();
    for order in 1usize..=10 {
        let coefficients = (order + 1).pow(2);
        let shared = shared(out, order);
        for precision in 6u8..=9 {
            let index = (order - 1) * 4 + usize::from(precision - 6);
            let (file, profile) = variant(order, precision);
            let stored: StoredFormat = data_json(&file);
            assert_eq!(stored.schema_version, 3);
            assert_eq!(stored.codebook_encoding, packed::CODEBOOK_ENCODING);
            assert_eq!(stored.order, order);
            assert_eq!(stored.quantization_bits, precision);
            assert_eq!(
                stored.shared_file,
                format!("hoa-salient-order{order}-shared-v1.json")
            );
            assert_eq!(stored.format_profile, profile);
            assert_eq!(stored.modes.len(), 6);
            let mut modes = Vec::new();
            let mut tries = Vec::new();
            for (mode_index, (mode, common)) in stored.modes.iter().zip(&shared.modes).enumerate() {
                assert_eq!(mode.mode, mode_index);
                assert_eq!(common.mode, mode_index);
                assert!(
                    common
                        .group_indices
                        .iter()
                        .flat_map(|&g| &shared.groups[g])
                        .all(|&i| i < coefficients)
                );
                assert!(
                    common
                        .matrix_indices
                        .iter()
                        .all(|&m| shared.matrix_lengths[m] == coefficients * coefficients)
                );
                let groups: Vec<String> = common
                    .group_indices
                    .iter()
                    .map(|&i| shared.group_names[i].clone())
                    .collect();
                let matrices: Vec<String> = common
                    .matrix_indices
                    .iter()
                    .map(|&i| shared.matrix_names[i].clone())
                    .collect();
                modes.push(format!(
                    "crate::tables::SalientMode {{ groups: {}, signs: {}, matrices_f32: {} }}",
                    refs(groups),
                    common.signs,
                    refs(matrices)
                ));
                let mut mode_tries = Vec::new();
                for (book_index, hex) in mode.codebooks.iter().enumerate() {
                    let packed_book =
                        packed::codebook(hex, precision).expect("built-in packed HOA codebook");
                    let book = if (order, precision, mode_index, book_index) == (3, 6, 1, 0) {
                        assert_eq!(
                            packed_book, measured,
                            "packed copy of measured codebook differs"
                        );
                        measured.clone()
                    } else {
                        packed_book
                    };
                    assert_eq!(book.len(), 1usize << precision);
                    let codes: Vec<u32> = book.iter().map(|v| v.1).collect();
                    let bits: Vec<usize> = book.iter().map(|v| v.0).collect();
                    let nodes = trie_build::build(&codes, &bits);
                    let name = out.array(
                        false,
                        &format!("SALIENT_V{index}_M{mode_index}_B{book_index}_TRIE"),
                        "[u16; 3]",
                        nodes.into_iter().map(emit::node),
                    );
                    mode_tries.push(format!("crate::tables::Trie::from_static(&{name})"));
                }
                tries.push(out.array(
                    false,
                    &format!("SALIENT_V{index}_M{mode_index}_TRIES"),
                    "crate::tables::Trie",
                    mode_tries,
                ));
            }
            let modes = out.array(
                false,
                &format!("SALIENT_V{index}_MODES"),
                "crate::tables::SalientMode",
                modes,
            );
            constants.push(format!(
                "crate::tables::SalientConstants {{ coefficients: {coefficients}, precision: {precision}, format: crate::tables::SalientFormat {{ tables_sha256: {:?}, modes: &{modes} }}, tries: {} }}",
                stored.tables_sha256,
                refs(tries)
            ));
        }
    }
    out.array(
        true,
        "SALIENT_CONSTANTS",
        "crate::tables::SalientConstants",
        constants,
    );
}

#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    azimuth_f64: Vec<[u64; 2]>,
    elevation_f64: Vec<[u64; 2]>,
    roots_f64: [u64; 7],
}
#[derive(Deserialize)]
struct ExpandedMath {
    numeric_profile: String,
    tables_sha256: String,
    normalizations_f64: Vec<Vec<u64>>,
}

pub fn math(out: &mut Output) {
    let math: Math = data_json("hoa-salient-math-v1.json");
    assert_eq!(math.numeric_profile, "apac-hoa-salient-math-v1");
    assert_eq!(math.azimuth_f64.len(), 512);
    assert_eq!(math.elevation_f64.len(), 256);
    let pairs = |values: &[[u64; 2]]| {
        values
            .iter()
            .map(|p| emit::pair(p.map(emit::f64_bits)))
            .collect::<Vec<_>>()
    };
    let azimuth = out.array(
        false,
        "SALIENT_AZIMUTH",
        "[f64; 2]",
        pairs(&math.azimuth_f64),
    );
    let elevation = out.array(
        false,
        "SALIENT_ELEVATION",
        "[f64; 2]",
        pairs(&math.elevation_f64),
    );
    let roots: Vec<String> = math.roots_f64.iter().map(|&b| emit::f64_bits(b)).collect();
    out.raw(&format!(
        "pub static SALIENT_MATH: crate::tables::SalientMath = crate::tables::SalientMath {{ math_sha: {:?}, azimuth: &{azimuth}, elevation: &{elevation}, roots: [{}] }};",
        math.tables_sha256,
        roots.join(", ")
    ));

    let expanded: ExpandedMath = data_json("hoa-expanded-orders-math-v1.json");
    assert_eq!(expanded.numeric_profile, "apac-hoa-expanded-orders-math-v1");
    assert_eq!(expanded.normalizations_f64.len(), 11);
    for (order, row) in expanded.normalizations_f64.iter().enumerate() {
        assert_eq!(row.len(), (order + 1).pow(2));
    }
    let rows: Vec<String> = expanded
        .normalizations_f64
        .iter()
        .enumerate()
        .map(|(order, row)| {
            out.array(
                false,
                &format!("SALIENT_NORMALIZATIONS_{order}"),
                "u64",
                row,
            )
        })
        .collect();
    out.raw(&format!(
        "pub static SALIENT_EXPANDED_NORMALIZATIONS: [&[u64]; 11] = [{}];",
        rows.iter()
            .map(|n| format!("&{n}"))
            .collect::<Vec<_>>()
            .join(", ")
    ));
    out.str_const("SALIENT_EXPANDED_MATH_SHA256", &expanded.tables_sha256);
}
