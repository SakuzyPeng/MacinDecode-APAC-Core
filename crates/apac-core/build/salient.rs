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
    coefficient_group: Option<Vec<usize>>,
    group_sha256: Option<String>,
}

struct DirectBook {
    mode: usize,
    book: usize,
    file: &'static str,
    book_sha256: &'static str,
    group_sha256: &'static str,
    group_users: &'static [(usize, usize)],
}

const DIRECT_BOOKS: [DirectBook; 3] = [
    DirectBook {
        mode: 2,
        book: 0,
        file: "hoa-salient-order3-q6-mode2-book0-measured-v1.json",
        book_sha256: "b6454d2e72fa0d4379ed35a0654117a6d0d642e7120aa361e743215a668e8e51",
        group_sha256: "fa1a8d0500bded3fe41cff7249eb8a9ac45940f271b94677f7f2989fc322aad6",
        group_users: &[(2, 0)],
    },
    DirectBook {
        mode: 2,
        book: 1,
        file: "hoa-salient-order3-q6-mode2-book1-measured-v1.json",
        book_sha256: "65c66abc4fee011b32b4bc05d03c7fb94de4cf3ece3de4308e82a0cac8be86af",
        group_sha256: "302afc300ad5263c92900f3042be1833fa550b67b29d135125db0012227327a2",
        group_users: &[(2, 1)],
    },
    DirectBook {
        mode: 3,
        book: 0,
        file: "hoa-salient-order3-q6-mode3-measured-v1.json",
        book_sha256: "a987e10a45914106ab796d31fc7587a3d997310d09aab04a02f66a7c27c899d1",
        group_sha256: "4939a292c2f5164ddcf27a07ddc7ef96928baa3e8967453a7294a9ecebf0a5c3",
        group_users: &[
            (0, 0),
            (1, 0),
            (3, 0),
            (4, 0),
            (4, 1),
            (4, 2),
            (4, 3),
            (5, 0),
        ],
    },
];

fn measured_group(source: &DirectBook) -> Vec<usize> {
    let data: MeasuredCodebook = data_json(source.file);
    assert_eq!(data.schema_version, 1);
    assert_eq!(data.profile, "apac-hoa-salient-measured-v1");
    assert_eq!(
        (data.order, data.quantization_bits, data.mode, data.book),
        (3, 6, source.mode, source.book)
    );
    let group = data.coefficient_group.expect("measured coefficient group");
    assert!(!group.is_empty());
    assert!(group.iter().all(|&i| i < 16));
    let mut unique = group.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), group.len());
    let digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&group).expect("measured group JSON"))
    );
    assert_eq!(Some(digest.as_str()), data.group_sha256.as_deref());
    assert_eq!(digest, source.group_sha256);
    group
}

/// This book's canonical input is the frozen black-box measurement. The
/// original format file keeps a generated packed copy for existing tools.
fn measured_codebook(
    file: &str,
    precision: u8,
    mode: usize,
    book_index: usize,
    expected_sha256: &str,
) -> Vec<(usize, u32)> {
    let data: MeasuredCodebook = data_json(file);
    assert_eq!(data.schema_version, 1);
    assert_eq!(data.profile, "apac-hoa-salient-measured-v1");
    assert_eq!(
        (data.order, data.quantization_bits, data.mode, data.book),
        (3, precision, mode, book_index)
    );
    assert_eq!(data.entries.len(), 1usize << precision);
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
    assert_eq!(digest, expected_sha256);
    book
}

#[derive(Deserialize)]
struct MeasuredMatrix {
    schema_version: u8,
    profile: String,
    order: usize,
    mode: usize,
    cluster: usize,
    rows: usize,
    columns: usize,
    storage: String,
    matrix_sha256: String,
    matrix_f32: Vec<u32>,
}

const MODE4_MATRIX_SHA256: [&str; 4] = [
    "87a5fbe1a977b1312d8d1093425ee3217d87ad4dfc77a7855b0290e8b9861c19",
    "a3cfa1e9321c82986dbf97ed2ba8f2c9b86be9095962f84b3e5d33f36233ab2b",
    "e81d7a49eb0e162933d90c2378ca745012be336b5eb6485af79dca4d512c75d4",
    "ff82131f4cdc56f49559c5bdeb7dbf67dd9c5ffcd09d3f8b16bd0a2899ba95b3",
];

const MODE4_CODEBOOK_SHA256: [&str; 4] = [
    "08fa83f508549126a68be673c7f6065c15e8ea5c2279385e00ac6f180c943322",
    "6e04f7a58d699d3e08666b078afd3e77b8e181ce4a1fd30e01effa5732e58cea",
    "07959b3786362b47eb1380e587c7ab3fba4e0c094b0221652ac30ee798be9d32",
    "6ed393ceffa5d005b34939fe9c0673197c39bcd09ac0619b011ad9d1df8c0327",
];

const WIDE_CODEBOOKS: [(u8, usize, usize, &str, &str); 24] = [
    (
        7,
        1,
        0,
        "hoa-salient-order3-q7-mode1-measured-v1.json",
        "a71142dc81e6729626b0f02747593a555139dc6f91d1636cb0abc4de3c4c20b0",
    ),
    (
        7,
        2,
        0,
        "hoa-salient-order3-q7-mode2-book0-measured-v1.json",
        "38e93984c09b6b68b27ffb8110efcc57ffb881c2af347aaaae99c99a22693c8d",
    ),
    (
        7,
        2,
        1,
        "hoa-salient-order3-q7-mode2-book1-measured-v1.json",
        "c76ea7f0a66503828ce9cb6b0d7bebfeefcf4941d1e66a9a4e965c238052c664",
    ),
    (
        7,
        3,
        0,
        "hoa-salient-order3-q7-mode3-measured-v1.json",
        "7895bb8683079eb2d0c91e2c3494a0b4e04ebfd96e9891c5935a8d83c4bb8a19",
    ),
    (
        7,
        4,
        0,
        "hoa-salient-order3-q7-mode4-cluster0-measured-v1.json",
        "994c707b790a40eb9332fb997b08fe5ae2745b8a386093dae622f1cda14c306a",
    ),
    (
        7,
        4,
        1,
        "hoa-salient-order3-q7-mode4-cluster1-measured-v1.json",
        "59d76118b47881eeb2cda1f0d13296ebd6bfe26b81b9b001973f936417050ab9",
    ),
    (
        7,
        4,
        2,
        "hoa-salient-order3-q7-mode4-cluster2-measured-v1.json",
        "c38a07cc2a7d097c8d3f694bce8e659360f8abf63a28b4eb8d3d46a4b0e02988",
    ),
    (
        7,
        4,
        3,
        "hoa-salient-order3-q7-mode4-cluster3-measured-v1.json",
        "410e970c4326fe62f1ad7aaf2eafd1a34d865b65e89d52e60835f605642c549d",
    ),
    (
        8,
        1,
        0,
        "hoa-salient-order3-q8-mode1-measured-v1.json",
        "8bd37ce28df300eaa7f0169a5ca36dfc45aab90c668dcfa1bc6f55c2e4e14b6a",
    ),
    (
        8,
        2,
        0,
        "hoa-salient-order3-q8-mode2-book0-measured-v1.json",
        "0d0c684387bc0af50a548e9f014e56e605d22369e8ce5fcd314a6663675de041",
    ),
    (
        8,
        2,
        1,
        "hoa-salient-order3-q8-mode2-book1-measured-v1.json",
        "c19f493f6231aefa6b4584f4e730e2b75f0fedeeadb8bb609646b740c12c785c",
    ),
    (
        8,
        3,
        0,
        "hoa-salient-order3-q8-mode3-measured-v1.json",
        "55ade17b5d15c017a39a01267b08d985bb144e163dcef4014b50914d5a37e6d2",
    ),
    (
        8,
        4,
        0,
        "hoa-salient-order3-q8-mode4-cluster0-measured-v1.json",
        "4d088ad904bce6e0651b30bd04fd13832216076eb5faeb40b776e81fc728d574",
    ),
    (
        8,
        4,
        1,
        "hoa-salient-order3-q8-mode4-cluster1-measured-v1.json",
        "950bb4f93c516bdf0334d3a0e0995195e783e1a906bd2e9e701dd60933c63bcf",
    ),
    (
        8,
        4,
        2,
        "hoa-salient-order3-q8-mode4-cluster2-measured-v1.json",
        "108333b9f72498b0e5f4f0db2f9db6de5800cc5b7f7f32250c99a00a90033b79",
    ),
    (
        8,
        4,
        3,
        "hoa-salient-order3-q8-mode4-cluster3-measured-v1.json",
        "c80c2b6e059c40caf9893e7036284e92440dd358b4a2f60fc9413a98def9e29b",
    ),
    (
        9,
        1,
        0,
        "hoa-salient-order3-q9-mode1-measured-v1.json",
        "7b8944ad075dc444c6fe38b392472c1cd261f253d7195f013b14419a3fd56081",
    ),
    (
        9,
        2,
        0,
        "hoa-salient-order3-q9-mode2-book0-measured-v1.json",
        "1c00698469f08e3695a83f462ac790fb8701d618a8a3c7f53ab2a5616916be69",
    ),
    (
        9,
        2,
        1,
        "hoa-salient-order3-q9-mode2-book1-measured-v1.json",
        "281093078f9bbf9b8993d4123dcbd68a97897f42e33d28601253917ec6d15f8d",
    ),
    (
        9,
        3,
        0,
        "hoa-salient-order3-q9-mode3-measured-v1.json",
        "8ce05c719252922d6099be573bad6f3e59d976ac1142a45d5a7bf3754e8b0a43",
    ),
    (
        9,
        4,
        0,
        "hoa-salient-order3-q9-mode4-cluster0-measured-v1.json",
        "3eda12c8bd891aa6b3f15ce780bf486ceec977949d5e5a8a79a4e88c76c832ea",
    ),
    (
        9,
        4,
        1,
        "hoa-salient-order3-q9-mode4-cluster1-measured-v1.json",
        "2c64768c1ea958f6f6f5fec9aab9e9fac877c9f1cd1dc31194b990cdc606402a",
    ),
    (
        9,
        4,
        2,
        "hoa-salient-order3-q9-mode4-cluster2-measured-v1.json",
        "c44b293e084da28c1236532fa07b5f39bf8d9f3dd6878fef382f1f619af2c085",
    ),
    (
        9,
        4,
        3,
        "hoa-salient-order3-q9-mode4-cluster3-measured-v1.json",
        "100c17110ad55b062eae941500670410c8da1111e3012c84f1f7cd6c61bd9b2c",
    ),
];

/// Order-3 dictionaries share each matrix across all four quantization widths.
fn measured_matrix(cluster: usize) -> Vec<u32> {
    let data: MeasuredMatrix = data_json(&format!(
        "hoa-salient-order3-mode4-cluster{cluster}-matrix-measured-v1.json"
    ));
    assert_eq!(data.schema_version, 1);
    assert_eq!(data.profile, "apac-hoa-salient-measured-matrix-v1");
    assert_eq!(
        (data.order, data.mode, data.cluster, data.rows, data.columns),
        (3, 4, cluster, 16, 16)
    );
    assert_eq!(data.storage, "row-major");
    assert_eq!(data.matrix_f32.len(), 256);
    assert!(
        data.matrix_f32
            .iter()
            .all(|&word| f32::from_bits(word).is_finite())
    );
    let digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&data.matrix_f32).expect("measured matrix JSON"))
    );
    assert_eq!(digest, data.matrix_sha256);
    assert_eq!(digest, MODE4_MATRIX_SHA256[cluster]);
    data.matrix_f32
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
    let mut data: StoredSharedFormat =
        data_json(&format!("hoa-salient-order{order}-shared-v1.json"));
    assert_eq!(data.schema_version, 2);
    assert_eq!(data.format_profile, "apac-hoa-salient-shared-v1");
    assert_eq!(data.matrix_encoding, packed::MATRIX_ENCODING);
    assert_eq!(data.order, order);
    assert_eq!(data.modes.len(), 6);
    let measured: Vec<_> = if order == 3 {
        assert_eq!(data.modes[4].mode, 4);
        assert_eq!(data.modes[4].matrix_indices.len(), 4);
        data.modes[4]
            .matrix_indices
            .iter()
            .enumerate()
            .map(|(cluster, &index)| {
                assert!(index < data.matrices_f32.len());
                let users: Vec<_> = data
                    .modes
                    .iter()
                    .flat_map(|mode| {
                        mode.matrix_indices
                            .iter()
                            .enumerate()
                            .filter_map(move |(c, &i)| (i == index).then_some((mode.mode, c)))
                    })
                    .collect();
                assert_eq!(users, [(4, cluster)]);
                (index, measured_matrix(cluster))
            })
            .collect()
    } else {
        Vec::new()
    };
    if order == 3 {
        for source in &DIRECT_BOOKS {
            let index = data.modes[source.mode].group_indices[source.book];
            let users: Vec<_> = data
                .modes
                .iter()
                .flat_map(|mode| {
                    mode.group_indices
                        .iter()
                        .enumerate()
                        .filter_map(move |(group, &i)| (i == index).then_some((mode.mode, group)))
                })
                .collect();
            assert_eq!(users, source.group_users, "measured group aliases differ");
            let group = measured_group(source);
            assert_eq!(
                data.groups[index], group,
                "shared copy of measured group differs"
            );
            data.groups[index] = group;
        }
    }
    let group_names = data
        .groups
        .iter()
        .enumerate()
        .map(|(i, g)| out.array(false, &format!("SALIENT_O{order}_GROUP_{i}"), "usize", g))
        .collect();
    let mut matrix_lengths = Vec::new();
    let mut matrix_names = Vec::new();
    for (i, hex) in data.matrices_f32.iter().enumerate() {
        let packed_words =
            packed::matrix(hex, (order + 1).pow(4)).expect("built-in packed HOA matrix");
        let words = if let Some((_, words)) = measured.iter().find(|(index, _)| *index == i) {
            assert_eq!(
                &packed_words, words,
                "packed copy of measured matrix differs"
            );
            words.clone()
        } else {
            packed_words
        };
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
    let measured_direct: Vec<_> = DIRECT_BOOKS
        .iter()
        .map(|source| {
            measured_codebook(source.file, 6, source.mode, source.book, source.book_sha256)
        })
        .collect();
    let measured_mode1 = measured_codebook(
        "hoa-salient-order3-q6-mode1-measured-v1.json",
        6,
        1,
        0,
        "296d730714d97de653c45cc487fa4fa94aebce9a49559da78e81215591e600ee",
    );
    let measured_mode4: Vec<_> = MODE4_CODEBOOK_SHA256
        .iter()
        .enumerate()
        .map(|(cluster, digest)| {
            measured_codebook(
                &format!("hoa-salient-order3-q6-mode4-cluster{cluster}-measured-v1.json"),
                6,
                4,
                cluster,
                digest,
            )
        })
        .collect();
    let measured_wide: Vec<_> = WIDE_CODEBOOKS
        .iter()
        .map(|&(precision, mode, book, file, sha)| {
            (
                precision,
                mode,
                book,
                measured_codebook(file, precision, mode, book, sha),
            )
        })
        .collect();
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
            if order == 3 {
                for (mode, count) in stored.modes.iter().zip([0, 1, 2, 1, 4, 0]) {
                    assert_eq!(mode.codebooks.len(), count);
                }
            }
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
                    let measured = match (order, precision, mode_index, book_index) {
                        (3, 6, 1, 0) => Some(&measured_mode1),
                        (3, 6, 2, book) => Some(&measured_direct[book]),
                        (3, 6, 3, 0) => Some(&measured_direct[2]),
                        (3, 6, 4, cluster) => Some(&measured_mode4[cluster]),
                        (3, 7..=9, mode, book) => Some(
                            measured_wide
                                .iter()
                                .find_map(|(p, m, b, words)| {
                                    (*p == precision && *m == mode && *b == book).then_some(words)
                                })
                                .expect("measured third-order codebook source"),
                        ),
                        _ => None,
                    };
                    let book = if let Some(measured) = measured {
                        assert_eq!(
                            &packed_book, measured,
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
