//! Production HOA salient dictionaries (orders 1..10, widths 6..9), their
//! Huffman tries and the shared angle/normalization constants.
use crate::emit::{self, Output};
use crate::{data_json, packed, trie_build};
use serde::Deserialize;
use sha2::{Digest, Sha256};

// One production bound controls both emitted dictionaries and runtime admission.
const MAX_SUPPORTED_ORDER: usize = 10;

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
    order: usize,
    mode: usize,
    book: usize,
    file: &'static str,
    book_sha256: &'static str,
    group_sha256: &'static str,
    group_users: &'static [(usize, usize)],
}

const DIRECT_BOOKS: [DirectBook; 3] = [
    DirectBook {
        order: 3,
        mode: 2,
        book: 0,
        file: "hoa-salient-order3-q6-mode2-book0-measured-v1.json",
        book_sha256: "b6454d2e72fa0d4379ed35a0654117a6d0d642e7120aa361e743215a668e8e51",
        group_sha256: "fa1a8d0500bded3fe41cff7249eb8a9ac45940f271b94677f7f2989fc322aad6",
        group_users: &[(2, 0)],
    },
    DirectBook {
        order: 3,
        mode: 2,
        book: 1,
        file: "hoa-salient-order3-q6-mode2-book1-measured-v1.json",
        book_sha256: "65c66abc4fee011b32b4bc05d03c7fb94de4cf3ece3de4308e82a0cac8be86af",
        group_sha256: "302afc300ad5263c92900f3042be1833fa550b67b29d135125db0012227327a2",
        group_users: &[(2, 1)],
    },
    DirectBook {
        order: 3,
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
        (source.order, 6, source.mode, source.book)
    );
    let group = data.coefficient_group.expect("measured coefficient group");
    assert!(!group.is_empty());
    assert!(group.iter().all(|&i| i < (source.order + 1).pow(2)));
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
    order: usize,
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
        (order, precision, mode, book_index)
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
    source: MeasuredMatrixSource,
}

#[derive(Deserialize)]
struct MeasuredMatrixSource {
    zero_sign_qualification: Option<ZeroQualification>,
}

#[derive(Deserialize)]
struct ZeroQualification {
    profile: String,
    consumer_function_sha256: String,
    coordinates: Vec<[usize; 2]>,
    candidate_words: [u32; 2],
    canonical_word: u32,
    original_zero_signs_identified: bool,
    strict_original_bits_qualified: bool,
}

fn verify_zero_qualification(qualification: &ZeroQualification, words: &[u32], n: usize) {
    assert_eq!(qualification.profile, "apac-hoa-mode4-zero-equivalence-v1");
    assert_eq!(qualification.candidate_words, [0, 0x80000000]);
    assert_eq!(qualification.canonical_word, 0);
    assert!(!qualification.original_zero_signs_identified);
    assert!(!qualification.strict_original_bits_qualified);
    assert!(!qualification.coordinates.is_empty());
    for &[row, column] in &qualification.coordinates {
        assert!(row < n && column < n);
        assert_eq!(words[row * n + column], 0);
    }
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/frame/hoa_salient.rs");
    println!("cargo:rerun-if-changed={}", path.display());
    let source = std::fs::read_to_string(path).expect("reviewed HOA zero consumer");
    let start = source
        .find("pub(super) fn restore_descriptors(")
        .expect("reviewed HOA descriptor function");
    let end = start
        + source[start..]
            .find("\npub(super) fn restore(")
            .expect("reviewed HOA descriptor function end");
    assert_eq!(
        format!("{:x}", Sha256::digest(&source.as_bytes()[start..end])),
        qualification.consumer_function_sha256,
        "HOA zero-equivalence consumer changed and needs qualification"
    );
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

// Frozen lower-order sources from public PCM reconstruction.
const LOWER_CODEBOOKS: [(usize, u8, usize, usize, &str, &str); 64] = [
    (
        1,
        6,
        1,
        0,
        "hoa-salient-order1-q6-mode1-measured-v1.json",
        "0ac031a3830de81423492cec006a2b0efaac7c7dccad6071361f3881cf99e734",
    ),
    (
        1,
        6,
        2,
        0,
        "hoa-salient-order1-q6-mode2-book0-measured-v1.json",
        "1b66164a2d661fd9855480ce26dab690b6123a48f0cb64833c27314d7a811c6d",
    ),
    (
        1,
        6,
        2,
        1,
        "hoa-salient-order1-q6-mode2-book1-measured-v1.json",
        "e015a2b9dd9dee6974aeba48b76560c560953e2d5b37cd806605029f0f8a535d",
    ),
    (
        1,
        6,
        3,
        0,
        "hoa-salient-order1-q6-mode3-measured-v1.json",
        "1bcb349f595f8de7f146c60f0b2885125e3c06b1c37ec19efd65a7809ee242e9",
    ),
    (
        1,
        6,
        4,
        0,
        "hoa-salient-order1-q6-mode4-cluster0-measured-v1.json",
        "b4f354a98d2cda979c5bbd565d224f92500d827389dea742deba9d5ce0ecfcf2",
    ),
    (
        1,
        6,
        4,
        1,
        "hoa-salient-order1-q6-mode4-cluster1-measured-v1.json",
        "96bcfd440974a2e1577fe04ad58c6be62fad8d5e9ccc9f16f470eeb5318575b5",
    ),
    (
        1,
        6,
        4,
        2,
        "hoa-salient-order1-q6-mode4-cluster2-measured-v1.json",
        "a42ee77a3c75bf6d21998d0f1085ed7c26c010023301e270fe04a7f88517cd1f",
    ),
    (
        1,
        6,
        4,
        3,
        "hoa-salient-order1-q6-mode4-cluster3-measured-v1.json",
        "73a5375f0ffc1920e5c961e2d707b0f8fcef9eb8292bd4a8bc3c576a2f9bde1b",
    ),
    (
        1,
        7,
        1,
        0,
        "hoa-salient-order1-q7-mode1-measured-v1.json",
        "c6dad7d1aef2cb75650db6253ee88c984bdb6dec250a3d9b853df52e10f662b7",
    ),
    (
        1,
        7,
        2,
        0,
        "hoa-salient-order1-q7-mode2-book0-measured-v1.json",
        "8aa5381daf6f6921833792d6fc43811e4a2c77618cf73f69726ef553b6cece17",
    ),
    (
        1,
        7,
        2,
        1,
        "hoa-salient-order1-q7-mode2-book1-measured-v1.json",
        "67bd1e6e8427b59613d693168253490fefa3ae882cbf3c83c81f4a71599a3843",
    ),
    (
        1,
        7,
        3,
        0,
        "hoa-salient-order1-q7-mode3-measured-v1.json",
        "c3873c288b31b0c77f87c4db7479ef53830eade58b09903bf911cc5a23eedd7c",
    ),
    (
        1,
        7,
        4,
        0,
        "hoa-salient-order1-q7-mode4-cluster0-measured-v1.json",
        "8b633a81bc512da9fde9ccb4580a450bf3e6a596a6de28eca25fcb2afb4a697e",
    ),
    (
        1,
        7,
        4,
        1,
        "hoa-salient-order1-q7-mode4-cluster1-measured-v1.json",
        "3a131275cb5543d20ac9d4baebcce1500465fa14d9f2997d3ce36e21a2c4315c",
    ),
    (
        1,
        7,
        4,
        2,
        "hoa-salient-order1-q7-mode4-cluster2-measured-v1.json",
        "ad29871886d129221cf7d07d3b06a8677eaf3b64fff1a65eff12eca37f445868",
    ),
    (
        1,
        7,
        4,
        3,
        "hoa-salient-order1-q7-mode4-cluster3-measured-v1.json",
        "cb47d41153fc0270f62fefca81d5a6f6a88dce983b091f62e4175d5aa61d73c2",
    ),
    (
        1,
        8,
        1,
        0,
        "hoa-salient-order1-q8-mode1-measured-v1.json",
        "a955572b33dde47f02187e6f90937107f7ffc5dec92782028bd3d3ae92ebd5de",
    ),
    (
        1,
        8,
        2,
        0,
        "hoa-salient-order1-q8-mode2-book0-measured-v1.json",
        "619acbdfbd1c7304f176d0bfb00e95877a6fa7a8a6f255ade85b503c4b7ed5a3",
    ),
    (
        1,
        8,
        2,
        1,
        "hoa-salient-order1-q8-mode2-book1-measured-v1.json",
        "ed81f9cfced9a6fb38b45e51a9ce906002c4f73d26462d50fd9648191e7b27a4",
    ),
    (
        1,
        8,
        3,
        0,
        "hoa-salient-order1-q8-mode3-measured-v1.json",
        "d459ec014b6e80d580f98b3f21e39ed511ba0c00caf75a8216546ae8d84e521f",
    ),
    (
        1,
        8,
        4,
        0,
        "hoa-salient-order1-q8-mode4-cluster0-measured-v1.json",
        "386f0a365f2b126c21cb5cb65249516ea0ca41c7f0edb1b8343dd06bd07da0ea",
    ),
    (
        1,
        8,
        4,
        1,
        "hoa-salient-order1-q8-mode4-cluster1-measured-v1.json",
        "ffc342833298a000534526b6dd1bdd919396cc24e67b6c6032ffbb443633b0e1",
    ),
    (
        1,
        8,
        4,
        2,
        "hoa-salient-order1-q8-mode4-cluster2-measured-v1.json",
        "03b71373c86f717def45d7324f248addd70cf2ec2e7085a176cc7ef37fa4898a",
    ),
    (
        1,
        8,
        4,
        3,
        "hoa-salient-order1-q8-mode4-cluster3-measured-v1.json",
        "2cafaf96594c338f28a1a8987e7bdce12002d391aa327ab3ffeabf4977f7e238",
    ),
    (
        1,
        9,
        1,
        0,
        "hoa-salient-order1-q9-mode1-measured-v1.json",
        "290cba1881d2d20262fb744c2dd58591e15cce79de44e165e97d744a5e5aa95f",
    ),
    (
        1,
        9,
        2,
        0,
        "hoa-salient-order1-q9-mode2-book0-measured-v1.json",
        "574dc21fe395748ae1aaa9c05e0df5d0f51c6c1e679ca373925b169539b26792",
    ),
    (
        1,
        9,
        2,
        1,
        "hoa-salient-order1-q9-mode2-book1-measured-v1.json",
        "43d6fd01a0aa14960b01c1546a183186797b1877f7bc25def955aeb96fb3d662",
    ),
    (
        1,
        9,
        3,
        0,
        "hoa-salient-order1-q9-mode3-measured-v1.json",
        "d6d7c232a479d09285dc3c39fbe52da118a6bdb1518942b52e19341e5b585bd1",
    ),
    (
        1,
        9,
        4,
        0,
        "hoa-salient-order1-q9-mode4-cluster0-measured-v1.json",
        "89cf9a0994c9e59e867edfc2445382cd0af5d5ba4d9545e0fed66c658404ed25",
    ),
    (
        1,
        9,
        4,
        1,
        "hoa-salient-order1-q9-mode4-cluster1-measured-v1.json",
        "b4d3389d0d77618d85a71e7d908c4e77c3a78e41b24255d95e99c8d872b40ece",
    ),
    (
        1,
        9,
        4,
        2,
        "hoa-salient-order1-q9-mode4-cluster2-measured-v1.json",
        "3a4ce49b7eb0c77bc8a34dd003c0ecca4b1a3b0dd8096aca4949891331740fe3",
    ),
    (
        1,
        9,
        4,
        3,
        "hoa-salient-order1-q9-mode4-cluster3-measured-v1.json",
        "e0f6cfb678c3b8d161296d270b017912d9a8341a946863ba74e1c694fdbb1752",
    ),
    (
        2,
        6,
        1,
        0,
        "hoa-salient-order2-q6-mode1-measured-v1.json",
        "61ac022e728bf42bb0fe5be3cef03d8e5df478a6845e51ba516404ba5784a7c4",
    ),
    (
        2,
        6,
        2,
        0,
        "hoa-salient-order2-q6-mode2-book0-measured-v1.json",
        "0085766d04a612d52b287b2ea4e2c3fb68a80cd931786392add2ef66d4385d2c",
    ),
    (
        2,
        6,
        2,
        1,
        "hoa-salient-order2-q6-mode2-book1-measured-v1.json",
        "f875fe9494d9ee82d186411bea569270003856b4299ad31d5815182205fa5cbb",
    ),
    (
        2,
        6,
        3,
        0,
        "hoa-salient-order2-q6-mode3-measured-v1.json",
        "ffbac9eeb00db40476c5f92d80fce4132dc19b4d9273a51c15d8a3bde5a764ac",
    ),
    (
        2,
        6,
        4,
        0,
        "hoa-salient-order2-q6-mode4-cluster0-measured-v1.json",
        "e571aae15614927a3f417820ec13395889a68d8911648ada7b6848848651ed88",
    ),
    (
        2,
        6,
        4,
        1,
        "hoa-salient-order2-q6-mode4-cluster1-measured-v1.json",
        "2bd4b7a790090886142d43964dbc6aa8d32a0992c4aa5429177d58a560c87e52",
    ),
    (
        2,
        6,
        4,
        2,
        "hoa-salient-order2-q6-mode4-cluster2-measured-v1.json",
        "34c6397a48e22876ee32f331f86c696f5b7ef41a717d20ef5312c93bea2bbea5",
    ),
    (
        2,
        6,
        4,
        3,
        "hoa-salient-order2-q6-mode4-cluster3-measured-v1.json",
        "a3553af20156c67c5445f9a139707f8e4874ac26f506308198068687836ba6e7",
    ),
    (
        2,
        7,
        1,
        0,
        "hoa-salient-order2-q7-mode1-measured-v1.json",
        "dc83a39fd7303ba78a02aaa7fca394452771afe64bde9c94c70f2ed257231a9b",
    ),
    (
        2,
        7,
        2,
        0,
        "hoa-salient-order2-q7-mode2-book0-measured-v1.json",
        "07cad56888088201fc57c0d276f716413b90caf9c421b88aca3220f97b5c771c",
    ),
    (
        2,
        7,
        2,
        1,
        "hoa-salient-order2-q7-mode2-book1-measured-v1.json",
        "de8bca84622030b977de2300aa3f586d4792c65c1483eb66345072e082310d10",
    ),
    (
        2,
        7,
        3,
        0,
        "hoa-salient-order2-q7-mode3-measured-v1.json",
        "cb3baee3cdec8298abd8f60b702cc32789efc28d9f2acdd4af69bce0f04f596d",
    ),
    (
        2,
        7,
        4,
        0,
        "hoa-salient-order2-q7-mode4-cluster0-measured-v1.json",
        "e15962d8626d5a5a1184f2f389795d65e857b674cb9f17b551a01cc3c6069086",
    ),
    (
        2,
        7,
        4,
        1,
        "hoa-salient-order2-q7-mode4-cluster1-measured-v1.json",
        "9bed0200bed2b18906aec8ac9ebcbab8a422ee9024a56fab906723cb9bb7cf39",
    ),
    (
        2,
        7,
        4,
        2,
        "hoa-salient-order2-q7-mode4-cluster2-measured-v1.json",
        "19b5e1c80034013f7efff6e7c98a7966fc3381260b92be6837a97a362d2a1244",
    ),
    (
        2,
        7,
        4,
        3,
        "hoa-salient-order2-q7-mode4-cluster3-measured-v1.json",
        "306c0f3f7026cd28d5a9a5f4e349720776eb85875c468fe3f981a3e6dc1db439",
    ),
    (
        2,
        8,
        1,
        0,
        "hoa-salient-order2-q8-mode1-measured-v1.json",
        "64af5b2598a624793f4bc4f33448f8b0f700eb3c6645382e9307bde031af28f2",
    ),
    (
        2,
        8,
        2,
        0,
        "hoa-salient-order2-q8-mode2-book0-measured-v1.json",
        "c0ac71a3209d2c11fec50b9ce6b3d32d10d58823ecb27978ce42466966012220",
    ),
    (
        2,
        8,
        2,
        1,
        "hoa-salient-order2-q8-mode2-book1-measured-v1.json",
        "96ef473c65477f0abd25b6dc724d2f5d48e46cf9cc4f681cf4e794bcf7acfd1d",
    ),
    (
        2,
        8,
        3,
        0,
        "hoa-salient-order2-q8-mode3-measured-v1.json",
        "50a86d4ac99c012c1155fae5f7e7c129635b183a4733380581254f68ab862386",
    ),
    (
        2,
        8,
        4,
        0,
        "hoa-salient-order2-q8-mode4-cluster0-measured-v1.json",
        "7511940f55ddf69a8fd41abeb1fecfcce59bcd114ff3b24b95f798bb861f37d7",
    ),
    (
        2,
        8,
        4,
        1,
        "hoa-salient-order2-q8-mode4-cluster1-measured-v1.json",
        "da330e108606ae57414782545940304d3d7cdcd40bf61c5281b28d7a11f75dd3",
    ),
    (
        2,
        8,
        4,
        2,
        "hoa-salient-order2-q8-mode4-cluster2-measured-v1.json",
        "924130045265da3b1d2ae542f7468f87e187d2b3db92941a46efaaf05b511b34",
    ),
    (
        2,
        8,
        4,
        3,
        "hoa-salient-order2-q8-mode4-cluster3-measured-v1.json",
        "a65b440dbb8cc88497cc6f0076fe9bd0d82f5132e269df2557d8be8157c189ea",
    ),
    (
        2,
        9,
        1,
        0,
        "hoa-salient-order2-q9-mode1-measured-v1.json",
        "9695777eeb5a8ebb57a8c4cda538ddc5a2320b613c7dbae5a12240a2814314b2",
    ),
    (
        2,
        9,
        2,
        0,
        "hoa-salient-order2-q9-mode2-book0-measured-v1.json",
        "e7488096c0d8e2434c36acaa45ee27f8e9e52f2d225a0d961d512c0c0319f284",
    ),
    (
        2,
        9,
        2,
        1,
        "hoa-salient-order2-q9-mode2-book1-measured-v1.json",
        "3bcb2439a0ad4cfb70ec55efd1b11d6ecffd16dcbf0d735be2b196916c9fa52a",
    ),
    (
        2,
        9,
        3,
        0,
        "hoa-salient-order2-q9-mode3-measured-v1.json",
        "a43174c2ff45a38843858175c8faf657b60e8de1441550b86929873b71d0898f",
    ),
    (
        2,
        9,
        4,
        0,
        "hoa-salient-order2-q9-mode4-cluster0-measured-v1.json",
        "c1ec5994596fab0f9abf79831f4917b2629ce85e5926f9ae82b4b88fab29fc3b",
    ),
    (
        2,
        9,
        4,
        1,
        "hoa-salient-order2-q9-mode4-cluster1-measured-v1.json",
        "73495d7a43f9ef900746b40cad44cfc65a1bf9793e662d72bac5c92b96a6b085",
    ),
    (
        2,
        9,
        4,
        2,
        "hoa-salient-order2-q9-mode4-cluster2-measured-v1.json",
        "e1b1809763b35b6a76633ba8231301e1cfe3c944d7e7cf86c534dcbb7f305dbd",
    ),
    (
        2,
        9,
        4,
        3,
        "hoa-salient-order2-q9-mode4-cluster3-measured-v1.json",
        "90db4692d19c3ddf4ef0c93a3b6a4c06a18e7221e467534e37fe4a82f2206984",
    ),
];
const LOWER_MATRIX_SHA256: [(usize, usize, &str); 8] = [
    (
        1,
        0,
        "9e206890d7fba6f91292d7de1e1e90f5a48eb4313d06ab187cfeb504987c09cd",
    ),
    (
        1,
        1,
        "037b8c69a464f6214b4c12162de2c96a6a594cad73894d2f8db2f190d0099357",
    ),
    (
        1,
        2,
        "fa20cf83c2a7adda7c473d3e7c3c404ed016778489e138705a7b4a064b921ce1",
    ),
    (
        1,
        3,
        "465e963ebbf3fe865fb7a24fb196ede70f4cdfa8a8abb584b3de1005b1dbf17a",
    ),
    (
        2,
        0,
        "6badf9183a38882b5cf270f33e0eb62c0a84d56319f4eb3b2b1f2a373e533f7c",
    ),
    (
        2,
        1,
        "1afe1cfff0caede3a61c81606172b705984ed84f087667386d578c52b672527a",
    ),
    (
        2,
        2,
        "971f3b565dde01552f09a19c4ce14e15e6c3ef094e9a2ab4d76a0639d3ac5867",
    ),
    (
        2,
        3,
        "6d2e918ee22adcb88c36b13462638f80be319bcafeb1caf5b25c33632790d473",
    ),
];
const LOWER_DIRECT_BOOKS: [DirectBook; 6] = [
    DirectBook {
        order: 1,
        mode: 2,
        book: 0,
        file: "hoa-salient-order1-q6-mode2-book0-measured-v1.json",
        book_sha256: "1b66164a2d661fd9855480ce26dab690b6123a48f0cb64833c27314d7a811c6d",
        group_sha256: "038966de9f6b9a901b20b4c6ca8b2a46009feebe031babc842d43690c0bc222b",
        group_users: &[(2, 0)],
    },
    DirectBook {
        order: 1,
        mode: 2,
        book: 1,
        file: "hoa-salient-order1-q6-mode2-book1-measured-v1.json",
        book_sha256: "e015a2b9dd9dee6974aeba48b76560c560953e2d5b37cd806605029f0f8a535d",
        group_sha256: "17f66f2f133c5b3a473a32750ffcef944a00b0581ef1750d412c6f0e26a852ee",
        group_users: &[(2, 1)],
    },
    DirectBook {
        order: 1,
        mode: 3,
        book: 0,
        file: "hoa-salient-order1-q6-mode3-measured-v1.json",
        book_sha256: "1bcb349f595f8de7f146c60f0b2885125e3c06b1c37ec19efd65a7809ee242e9",
        group_sha256: "f2f991d74dfbb2edf7f4475dcb7d82989de00a71e44bb11d4a6fbb49442577b6",
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
    DirectBook {
        order: 2,
        mode: 2,
        book: 0,
        file: "hoa-salient-order2-q6-mode2-book0-measured-v1.json",
        book_sha256: "0085766d04a612d52b287b2ea4e2c3fb68a80cd931786392add2ef66d4385d2c",
        group_sha256: "4b55519b2d83828d2a2d87816db3fbdc9eba1585e2f81ed985e24854ee1dab20",
        group_users: &[(2, 0)],
    },
    DirectBook {
        order: 2,
        mode: 2,
        book: 1,
        file: "hoa-salient-order2-q6-mode2-book1-measured-v1.json",
        book_sha256: "f875fe9494d9ee82d186411bea569270003856b4299ad31d5815182205fa5cbb",
        group_sha256: "4618e992c6554acdfa809f80c21c4a5981b9a2fa07851c89ec4c1249212df290",
        group_users: &[(2, 1)],
    },
    DirectBook {
        order: 2,
        mode: 3,
        book: 0,
        file: "hoa-salient-order2-q6-mode3-measured-v1.json",
        book_sha256: "ffbac9eeb00db40476c5f92d80fce4132dc19b4d9273a51c15d8a3bde5a764ac",
        group_sha256: "5cfc4211e4918b07c75c1ec51a90d9f5423607c3fb588a14a3bd8d0d415f8886",
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

// BEGIN HIGHER MEASURED SOURCES
const HIGHER_CODEBOOKS: &[(usize, u8, usize, usize, &str, &str)] = &[
    (4, 6, 1, 0, "hoa-salient-order4-q6-mode1-measured-v1.json", "6acf97aa88679666bfafdd7c4cca0e08de7846be4073673fc61bf49c0742deae"),
    (4, 6, 2, 0, "hoa-salient-order4-q6-mode2-book0-measured-v1.json", "0b13ef0be6fab6c03e9391e3bffcec96cd5edb5887482686e1f5e98d073d6329"),
    (4, 6, 2, 1, "hoa-salient-order4-q6-mode2-book1-measured-v1.json", "992cafa4debf0acbb68f38d0b7da7fda3a886c2f332a5134ee6062aadf723d44"),
    (4, 6, 3, 0, "hoa-salient-order4-q6-mode3-measured-v1.json", "a3be3a67c9ecfda79b992fdb47a510395b32c3d0f4a6268b32a106ae3a7efa0a"),
    (4, 6, 4, 0, "hoa-salient-order4-q6-mode4-cluster0-measured-v1.json", "a1028d5c66ce22866270192536399c5e722d778db49fb62abab6b15a11347cff"),
    (4, 6, 4, 1, "hoa-salient-order4-q6-mode4-cluster1-measured-v1.json", "aa22969629fa86d0cb55532e19c733220fe94b8b33ccd25f84f8bbfc4d1b3d56"),
    (4, 6, 4, 2, "hoa-salient-order4-q6-mode4-cluster2-measured-v1.json", "96e9f08db2a9df8bb8bf4037cfa71d1fc06aa66a385a6194a518418a99dd887f"),
    (4, 6, 4, 3, "hoa-salient-order4-q6-mode4-cluster3-measured-v1.json", "94bd3d97b2579b17a75865c9dc7b58377e5dc06196f723a1057e84b4c1773568"),
    (4, 7, 1, 0, "hoa-salient-order4-q7-mode1-measured-v1.json", "5d9a94374f620f0d1d95a14ecb98dcf8ff10a8a7ea4bfaf540aba6d2ff279991"),
    (4, 7, 2, 0, "hoa-salient-order4-q7-mode2-book0-measured-v1.json", "99e1512b8ac771fba85b5866b601df4ca07faf8b05c38c598f21ff2296dab829"),
    (4, 7, 2, 1, "hoa-salient-order4-q7-mode2-book1-measured-v1.json", "c780e77eeba00cdb1e9d3ffe6dae86c10a022f024b32d00c788e9dd6753bf85e"),
    (4, 7, 3, 0, "hoa-salient-order4-q7-mode3-measured-v1.json", "990b859522d73357500c045080627bd65ac02c3d5abf1862730c431e7e6b9373"),
    (4, 7, 4, 0, "hoa-salient-order4-q7-mode4-cluster0-measured-v1.json", "1df9f6294936f9a72ecd8fc1c63e80b9ce15d9d509eba128002d7f6144f621e2"),
    (4, 7, 4, 1, "hoa-salient-order4-q7-mode4-cluster1-measured-v1.json", "00d9e3b73d49f4025384016cd10721202e6e6534bbcd856f26b12846dc4e174a"),
    (4, 7, 4, 2, "hoa-salient-order4-q7-mode4-cluster2-measured-v1.json", "3b608ad7f80a8de6ef387814ce786f3e9aa950cafec59e353c667ac532db1bb7"),
    (4, 7, 4, 3, "hoa-salient-order4-q7-mode4-cluster3-measured-v1.json", "5eb696a3ef7fa9f31a694567d7b3d1c1dc00a19a3492abfd328f5cb2f5c257b0"),
    (4, 8, 1, 0, "hoa-salient-order4-q8-mode1-measured-v1.json", "e9dc30f3a398c17e6f50458551fff689c06b4f264502b5882370dc64a7fd612f"),
    (4, 8, 2, 0, "hoa-salient-order4-q8-mode2-book0-measured-v1.json", "9aecc902ead259634fdeb3ff6b7fb00c77a0ca4c840393b17156979b877642f5"),
    (4, 8, 2, 1, "hoa-salient-order4-q8-mode2-book1-measured-v1.json", "877e2630fb60775b08e67064f5601a6335c86d9dc7f364049a6fd2ea751a7d7a"),
    (4, 8, 3, 0, "hoa-salient-order4-q8-mode3-measured-v1.json", "e50c3d71385ce6380f5b29b84462929da273cc4a5dc9d1da77b0fe77662dc842"),
    (4, 8, 4, 0, "hoa-salient-order4-q8-mode4-cluster0-measured-v1.json", "351691c7613eaa8d1111502eb27dcf878b838bc0c33d5a3774570bd16c366d54"),
    (4, 8, 4, 1, "hoa-salient-order4-q8-mode4-cluster1-measured-v1.json", "ab3ac79a4957ae6fe51536f6b49a3d29ac75570b4c76f38e0de1192bee1603f9"),
    (4, 8, 4, 2, "hoa-salient-order4-q8-mode4-cluster2-measured-v1.json", "5e7296174ff52b21365513d3fe3d98a7ed4c7bb97873f14f9c346f8fe7d282db"),
    (4, 8, 4, 3, "hoa-salient-order4-q8-mode4-cluster3-measured-v1.json", "590edd477366c7e1fce5afada49b90a8f2284e1a075567ba2330b87c5d1950d9"),
    (4, 9, 1, 0, "hoa-salient-order4-q9-mode1-measured-v1.json", "3ef8b8e897d89294f64e17d352574141ec428a5dd6a4761ca2d4f02b35806d5b"),
    (4, 9, 2, 0, "hoa-salient-order4-q9-mode2-book0-measured-v1.json", "44428c4fd27d5d265428d36ffd2937d95769dba6d239755f0993a8aadaffe5a0"),
    (4, 9, 2, 1, "hoa-salient-order4-q9-mode2-book1-measured-v1.json", "a0d3a8b4cfaa2ba4bf53a37720295d46ff31f9022737947c37f2f0c55ed26c43"),
    (4, 9, 3, 0, "hoa-salient-order4-q9-mode3-measured-v1.json", "617ce9eb30572bd19271d67ca1372ef1b41ce096a3ca3eaa1ad1816a8a3a85aa"),
    (4, 9, 4, 0, "hoa-salient-order4-q9-mode4-cluster0-measured-v1.json", "22417f1e8a55aee665ea447a2b74f257f4cd24ceaa11a5a6591132f0df5504f8"),
    (4, 9, 4, 1, "hoa-salient-order4-q9-mode4-cluster1-measured-v1.json", "11d81598e17306c5595817fa055b7a39a6630ad01ec4ddc0c238d5e9cf7813c1"),
    (4, 9, 4, 2, "hoa-salient-order4-q9-mode4-cluster2-measured-v1.json", "3a1ae764c3956ec4a8ab387f1fe855e1528f9f419366bdd53e6b6ba36888f792"),
    (4, 9, 4, 3, "hoa-salient-order4-q9-mode4-cluster3-measured-v1.json", "37e218e2ed1ebd84e980612ddfe94dd27ce651a70463251a62c6cc5aca35f668"),
    (5, 6, 1, 0, "hoa-salient-order5-q6-mode1-measured-v1.json", "ba0ff347733dd762e9421703bf683b6d5875edeb8ec10c8e9fdf92ae9cb3b29d"),
    (5, 6, 2, 0, "hoa-salient-order5-q6-mode2-book0-measured-v1.json", "8df98847c8ae8590472d56e0a67c41112a8593651e44e72cb1b3852b33c9fde4"),
    (5, 6, 2, 1, "hoa-salient-order5-q6-mode2-book1-measured-v1.json", "5c59ce2fb6568997ec0d26bd27e44853dc52eeeb17755d9419a4d3fd0e72b665"),
    (5, 6, 3, 0, "hoa-salient-order5-q6-mode3-measured-v1.json", "af637ffafdae1024ff291695a88ba6549810382f0edeae9eb53cd298f463c4e5"),
    (5, 6, 4, 0, "hoa-salient-order5-q6-mode4-cluster0-measured-v1.json", "a4442691006b85bc27bfe73f263979d872633570884760137ddda92c86b02f81"),
    (5, 6, 4, 1, "hoa-salient-order5-q6-mode4-cluster1-measured-v1.json", "d9c26dfe3be6356ca978560ffab684b617e769283d2d74a12b70491623f286cb"),
    (5, 6, 4, 2, "hoa-salient-order5-q6-mode4-cluster2-measured-v1.json", "3dade8cc5d891158e1b7fb7230ddd1b56c86856aeec0f083f7dc5dd243ba9752"),
    (5, 6, 4, 3, "hoa-salient-order5-q6-mode4-cluster3-measured-v1.json", "c2dbbb0292a64956d4b24d7b61274285bc80bda7e7ccf3434890a834d1eb8d47"),
    (5, 7, 1, 0, "hoa-salient-order5-q7-mode1-measured-v1.json", "0e8ab538a93e7d95630f27a3baa7b9f656142b359d98447e6a9c72b1175d9ec5"),
    (5, 7, 2, 0, "hoa-salient-order5-q7-mode2-book0-measured-v1.json", "6d88e91b265d9f72d10e35ac1e33a11aa2bc041442971ceff438651fbd1743ef"),
    (5, 7, 2, 1, "hoa-salient-order5-q7-mode2-book1-measured-v1.json", "47a86bec4c6f2ce2efd1ab901fdab161814fe0c437d91ee9e4403ee4f7c64537"),
    (5, 7, 3, 0, "hoa-salient-order5-q7-mode3-measured-v1.json", "4ea06a74774d9b39df12bd82c3b45422c86b5e83cad32a75827a43e82eacbeef"),
    (5, 7, 4, 0, "hoa-salient-order5-q7-mode4-cluster0-measured-v1.json", "dc2962e90dcc70c5f40a19ee9135e331bab654f44f39a93968bcf4a7c08b1f7d"),
    (5, 7, 4, 1, "hoa-salient-order5-q7-mode4-cluster1-measured-v1.json", "321d5945213b0918e6f96b6e9e52c821e0514e64e61288246ce90ac20cbd1983"),
    (5, 7, 4, 2, "hoa-salient-order5-q7-mode4-cluster2-measured-v1.json", "0845460a8b2e8f50fc4d42a7abc4ac323a111ede648294210ba875a5e24f3dd3"),
    (5, 7, 4, 3, "hoa-salient-order5-q7-mode4-cluster3-measured-v1.json", "f8f1bda5da3f2b99790ba789f759b5a1f8eaef76d4dacfe991dcbfa05890f74b"),
    (5, 8, 1, 0, "hoa-salient-order5-q8-mode1-measured-v1.json", "36e950858c9cdd606f10f0e1007eb05373ccd2cb191291b528002fd93c1e913b"),
    (5, 8, 2, 0, "hoa-salient-order5-q8-mode2-book0-measured-v1.json", "dc2a5d2578ba5a7aa17bafb0fa5760128ef399b206f1a01a77bd3db39dbf5643"),
    (5, 8, 2, 1, "hoa-salient-order5-q8-mode2-book1-measured-v1.json", "c39a0953dd2ef637dc84228a102cd37ebfca0e72d94ca3bedde6c244d536c735"),
    (5, 8, 3, 0, "hoa-salient-order5-q8-mode3-measured-v1.json", "b14ce949618e1312541269dab88f022738786aa8188df6aa253bcf4a82da66ac"),
    (5, 8, 4, 0, "hoa-salient-order5-q8-mode4-cluster0-measured-v1.json", "0d39ef2511d95c48509f19959d69150a6b011ae372cccc0427b05a6f77dff4ed"),
    (5, 8, 4, 1, "hoa-salient-order5-q8-mode4-cluster1-measured-v1.json", "efc85fe6fcbed6add8e73d6297825a9a6d44f5c158324a061be041e14b3a03cc"),
    (5, 8, 4, 2, "hoa-salient-order5-q8-mode4-cluster2-measured-v1.json", "f30a47e244e12a7c7f1dbb0091013bbaa899ab127e4c95b839d89518fb4ee897"),
    (5, 8, 4, 3, "hoa-salient-order5-q8-mode4-cluster3-measured-v1.json", "c1c63b82d4a25264fb081a241f111d36fd78c957a1648a3b39c37668c1977090"),
    (5, 9, 1, 0, "hoa-salient-order5-q9-mode1-measured-v1.json", "18d130fdaa54dcb77b4cb6ccf452e82be6eda118fab77a05a535549106d1f4c8"),
    (5, 9, 2, 0, "hoa-salient-order5-q9-mode2-book0-measured-v1.json", "6882c5745ca35ef219a8e8d3947014fa70374184fca7ddefb1894c159d0c6386"),
    (5, 9, 2, 1, "hoa-salient-order5-q9-mode2-book1-measured-v1.json", "d1a059b1d5566bd62b61105d0ab4819d09dfd3a3eab23e3193c5b05eaab5d8a2"),
    (5, 9, 3, 0, "hoa-salient-order5-q9-mode3-measured-v1.json", "734600aa34908c6591b8f9d4e1b7432365cf85376fe58304bd8bfb2f0cc77be0"),
    (5, 9, 4, 0, "hoa-salient-order5-q9-mode4-cluster0-measured-v1.json", "28b8589cf4ffca4b4a2abe3351b5e781112eec3706adda66bf45e7d1a6b2b46a"),
    (5, 9, 4, 1, "hoa-salient-order5-q9-mode4-cluster1-measured-v1.json", "17957bf6b5d9ffca2f2e8c217002b112df6c687cabb4652fcb6ed928c718599e"),
    (5, 9, 4, 2, "hoa-salient-order5-q9-mode4-cluster2-measured-v1.json", "84225e3a1c8a0030c2ad1df317c6f69bf08ef5bded857aee349df7e387ad44ee"),
    (5, 9, 4, 3, "hoa-salient-order5-q9-mode4-cluster3-measured-v1.json", "244cded4b3037732da9dcd92540ff93c6c25c8cfceb045b82b99e5a926ee9338"),
    (6, 6, 1, 0, "hoa-salient-order6-q6-mode1-measured-v1.json", "251a7e22a2888ead3c1128a7a397bb21e0bbef93d455fc2f35cd21bde9eff220"),
    (6, 6, 2, 0, "hoa-salient-order6-q6-mode2-book0-measured-v1.json", "8368f6899762a3b9757c318cde78289bc103d3cbbc531415d458ac6e74d5cabc"),
    (6, 6, 2, 1, "hoa-salient-order6-q6-mode2-book1-measured-v1.json", "3e24b64d3b29a14a6efa319610b42fa8c350a0a136abaca6085c25d66c5b2d9e"),
    (6, 6, 3, 0, "hoa-salient-order6-q6-mode3-measured-v1.json", "a3ad43eefd7a8b6bc94ab96a4dbd6241c0a55b1a72286149877d79db4bfe4560"),
    (6, 6, 4, 0, "hoa-salient-order6-q6-mode4-cluster0-measured-v1.json", "e9a3e5df51d90dde73172aec0d3416aa062098af64b82b9837ae9ef87cb4944a"),
    (6, 6, 4, 1, "hoa-salient-order6-q6-mode4-cluster1-measured-v1.json", "b62dbe92c6ff0887313ad179487483ca5aa120403116e617fce0085e4ba6c886"),
    (6, 6, 4, 2, "hoa-salient-order6-q6-mode4-cluster2-measured-v1.json", "abc015cbed87b3dd888856bae7a594e47d901eb07ee3cf255000b301ab9348a6"),
    (6, 6, 4, 3, "hoa-salient-order6-q6-mode4-cluster3-measured-v1.json", "4a71afca1d57c4b38eb75ad4e6b392385d1ba453bcf6419e4533d2396d0c32b7"),
    (6, 7, 1, 0, "hoa-salient-order6-q7-mode1-measured-v1.json", "d6158eb6402545d9d85048f38158a52844a52a27fe52aa9b9fcd772c68865221"),
    (6, 7, 2, 0, "hoa-salient-order6-q7-mode2-book0-measured-v1.json", "936184bbcd0177f872d89cac03e9f79cb8aab40f9106d5b803bde6b6585b51ea"),
    (6, 7, 2, 1, "hoa-salient-order6-q7-mode2-book1-measured-v1.json", "ad329a082ad9a1fdd3be455219a61dad8a67531173c76fea47e39ec4dff97550"),
    (6, 7, 3, 0, "hoa-salient-order6-q7-mode3-measured-v1.json", "26f6fc94e2bb3420c246b401de55852d894043be71c13e39663ac9a84252c665"),
    (6, 7, 4, 0, "hoa-salient-order6-q7-mode4-cluster0-measured-v1.json", "68d1ac8b3034856218716eb09d9598873922c2078549964ef2d631642f87d5c2"),
    (6, 7, 4, 1, "hoa-salient-order6-q7-mode4-cluster1-measured-v1.json", "876686604c7ecdad4b70aeaef57404f3460fa4137d38b10c5d0dc3caea91bbd5"),
    (6, 7, 4, 2, "hoa-salient-order6-q7-mode4-cluster2-measured-v1.json", "da423e427e4ae0c72be3422209bc0e7934f7fece84106a789bc2bdb9df56d2f8"),
    (6, 7, 4, 3, "hoa-salient-order6-q7-mode4-cluster3-measured-v1.json", "20b5ff35c5e829b83ab5df09f843732c6ba86894566727797813565e46a5ace6"),
    (6, 8, 1, 0, "hoa-salient-order6-q8-mode1-measured-v1.json", "44958214014e127b268013b39682d229ca574196c4f7ed8eb3ea0f3185f22a90"),
    (6, 8, 2, 0, "hoa-salient-order6-q8-mode2-book0-measured-v1.json", "6e0e7c415aeb16be9237603cf8ca2bb1ebc35c1efaed85f1a6a2a14f526ef662"),
    (6, 8, 2, 1, "hoa-salient-order6-q8-mode2-book1-measured-v1.json", "95248cb7db296aff51e255185577224ff25fb9469596b4612a671a62ec9d9b41"),
    (6, 8, 3, 0, "hoa-salient-order6-q8-mode3-measured-v1.json", "ce4e91f973f2cb4fbe52d1a7addcc4979c46418c86207aac7ab858bea5394be3"),
    (6, 8, 4, 0, "hoa-salient-order6-q8-mode4-cluster0-measured-v1.json", "bb528f133ec82b7b2355869af2033086ab77c1ecc3be82d257bc9e38fa076388"),
    (6, 8, 4, 1, "hoa-salient-order6-q8-mode4-cluster1-measured-v1.json", "98b54621b3b037d8b6cd77f6b0d77021ce7a35acc9305789d77074e769aa9b43"),
    (6, 8, 4, 2, "hoa-salient-order6-q8-mode4-cluster2-measured-v1.json", "f3ff173fb2fa724d6c67fe63e711c006169345d45fac65f5caaec7b0283e16a6"),
    (6, 8, 4, 3, "hoa-salient-order6-q8-mode4-cluster3-measured-v1.json", "4e8e7cfed6260febf7d5d5f7d2d846441940997233f187a1fb48fbb6dca129bf"),
    (6, 9, 1, 0, "hoa-salient-order6-q9-mode1-measured-v1.json", "e4f06161d4c0fc356a9b95cdae3758bddf4b865223ab74b47003c34425ef930f"),
    (6, 9, 2, 0, "hoa-salient-order6-q9-mode2-book0-measured-v1.json", "498df2a5e1e0f1141002b18ae258404c4516ecff650217676f02d79ac7c041d9"),
    (6, 9, 2, 1, "hoa-salient-order6-q9-mode2-book1-measured-v1.json", "c096d2a45660ef5c87463d5cf6560e7fe1cda2beb1715a41be596de1ce1af81f"),
    (6, 9, 3, 0, "hoa-salient-order6-q9-mode3-measured-v1.json", "6d466d2a54ba690ce2c55bb8fe830f891cee2f513e7f354ce51ff19dab9a144c"),
    (6, 9, 4, 0, "hoa-salient-order6-q9-mode4-cluster0-measured-v1.json", "03dabbb5e21ce398123b57a118ec8b8a6f4bf0fec4f7fac937b375d8036858c5"),
    (6, 9, 4, 1, "hoa-salient-order6-q9-mode4-cluster1-measured-v1.json", "4eb8de57d19f8b1cc9172faa7f131d8b6ad15b84b907a3b32a6d5dfc2f749d0a"),
    (6, 9, 4, 2, "hoa-salient-order6-q9-mode4-cluster2-measured-v1.json", "80d71dfaa1e5a2fdd8906e40fb6b861d6563013cdcb65ba4e86b79ec0bd9baa5"),
    (6, 9, 4, 3, "hoa-salient-order6-q9-mode4-cluster3-measured-v1.json", "67d99cddb59f643c86ec0cd1bec3f505ed01f1e2a321dbf2cfe2e243f8dbb439"),
    (7, 6, 1, 0, "hoa-salient-order7-q6-mode1-measured-v1.json", "8f280c368eab9d52be22f251fab44042b72310c9deef55b647cb79a4380351e7"),
    (7, 6, 2, 0, "hoa-salient-order7-q6-mode2-book0-measured-v1.json", "bbeb2f90832229f67ad0c99d565d816b8f062c6c47996b474871c8a22c6fd6c0"),
    (7, 6, 2, 1, "hoa-salient-order7-q6-mode2-book1-measured-v1.json", "3abc1f8d239592bfe1f25389eac331c82ce6e3f9aea37672115ac76a181d17b5"),
    (7, 6, 3, 0, "hoa-salient-order7-q6-mode3-measured-v1.json", "5cf38407c9245975d959ba4216319fb3c6c581857a4a24f54f3308b9388be5f0"),
    (7, 6, 4, 0, "hoa-salient-order7-q6-mode4-cluster0-measured-v1.json", "39daad344999615deb3f16ab87ccb081b5557215c7442c16e88573f726ed0795"),
    (7, 6, 4, 1, "hoa-salient-order7-q6-mode4-cluster1-measured-v1.json", "ded623d694a1074e6c4f85ff1d7ab5cb79f5d5cb5cb53942cd197b16197448ac"),
    (7, 6, 4, 2, "hoa-salient-order7-q6-mode4-cluster2-measured-v1.json", "08e688d97f2bb40e931c4d59f722e9ef2f7aa699b17ed775fed1daa082e1d7aa"),
    (7, 6, 4, 3, "hoa-salient-order7-q6-mode4-cluster3-measured-v1.json", "fbd71206c4a29f22b5a2d00257399cabeca470d76f9c3c343ea0fe5799a4cf3b"),
    (7, 7, 1, 0, "hoa-salient-order7-q7-mode1-measured-v1.json", "3337644df6a6ded1787dbac50f4f9d4764151820622d4f7e2103d710af60df0b"),
    (7, 7, 2, 0, "hoa-salient-order7-q7-mode2-book0-measured-v1.json", "1522dc9cfc296c6cf0d466e9fcc05fa6338079c328c555d7c86a9e04fe093850"),
    (7, 7, 2, 1, "hoa-salient-order7-q7-mode2-book1-measured-v1.json", "c5aca58806b47bd22b150e6a8194c528b8e8cc0d37ed796aff2248720ac91df2"),
    (7, 7, 3, 0, "hoa-salient-order7-q7-mode3-measured-v1.json", "7d19b8d2edc1462ae7f101de2fb2ab9beb5489278dcea203b8e99c45c61c3eac"),
    (7, 7, 4, 0, "hoa-salient-order7-q7-mode4-cluster0-measured-v1.json", "878f0671d32572de0f9f73904760378fac982dbf235ab2fba9f3d388ce932db3"),
    (7, 7, 4, 1, "hoa-salient-order7-q7-mode4-cluster1-measured-v1.json", "55896ae1585324879ae4ff02c92592384abd720ce366e8226556d0fc5e2b5be1"),
    (7, 7, 4, 2, "hoa-salient-order7-q7-mode4-cluster2-measured-v1.json", "dffa5a2066fba91f341acc1e6ce106c4e355021e9e150848b3b8fedf6c4c6afe"),
    (7, 7, 4, 3, "hoa-salient-order7-q7-mode4-cluster3-measured-v1.json", "b62168d62c5603146ec969ddd554b2c45a9cef050f493cf26fd9c160ca583264"),
    (7, 8, 1, 0, "hoa-salient-order7-q8-mode1-measured-v1.json", "397fd8221ab4359a7a8cb9308f2493f35b10f919e80fec1522b89f901527a487"),
    (7, 8, 2, 0, "hoa-salient-order7-q8-mode2-book0-measured-v1.json", "8ff89604ebb472c19182f7c61662cf2b69f577242ec6f2e7751f79427b187f18"),
    (7, 8, 2, 1, "hoa-salient-order7-q8-mode2-book1-measured-v1.json", "6854727b01d18a46b08d6026e662823c662d5a27a66bf7ab238936c224e4b6ad"),
    (7, 8, 3, 0, "hoa-salient-order7-q8-mode3-measured-v1.json", "09824212a2a8b77d02ee4087f49eaf5d805b2e98add90ba782eb2f8c9ce7af70"),
    (7, 8, 4, 0, "hoa-salient-order7-q8-mode4-cluster0-measured-v1.json", "c2dac35343a40cca229de14a6b02db69ad1a7f6f1e8722967e631aa30f95b7a9"),
    (7, 8, 4, 1, "hoa-salient-order7-q8-mode4-cluster1-measured-v1.json", "84d5b09411f434826d14f9216c38bb1994b57d7330924fe08ee673f2916c4ebf"),
    (7, 8, 4, 2, "hoa-salient-order7-q8-mode4-cluster2-measured-v1.json", "342c43b5fd2ec7010b0a8227520f1495ca457952b62cbdfb5d7b690e07d463a6"),
    (7, 8, 4, 3, "hoa-salient-order7-q8-mode4-cluster3-measured-v1.json", "462654ef09b69e22372a205bc506c3e7f5d72b224cdb9fb6be389a865dfe15d6"),
    (7, 9, 1, 0, "hoa-salient-order7-q9-mode1-measured-v1.json", "a4f3bdb06d4bd4d62e8064b2e384984ccafd7b78ac20d0b2636a5a326bff0b09"),
    (7, 9, 2, 0, "hoa-salient-order7-q9-mode2-book0-measured-v1.json", "004a4b209fad955bc8ef24eb4dcaf631bd6d510343de1bbff2dca642bc1f90d9"),
    (7, 9, 2, 1, "hoa-salient-order7-q9-mode2-book1-measured-v1.json", "f34a2dc97dfdd6cc7dba9a8a6c991b58373826e26e45356e14f6e45f459e8a5b"),
    (7, 9, 3, 0, "hoa-salient-order7-q9-mode3-measured-v1.json", "9623d81b226d921299642f07f01156f45af9a5e3780034fb2642c8ca405c59dc"),
    (7, 9, 4, 0, "hoa-salient-order7-q9-mode4-cluster0-measured-v1.json", "7bf0cfd15d0fce8fc9f5bb4913dce75238357301d9824e7c3329f97031df180c"),
    (7, 9, 4, 1, "hoa-salient-order7-q9-mode4-cluster1-measured-v1.json", "9ce2ebbe90b673a0fba8e2f34b57e92fad64107868e1a3b67682e77468844afb"),
    (7, 9, 4, 2, "hoa-salient-order7-q9-mode4-cluster2-measured-v1.json", "84e81b6aadcd1ff8670bf7bd7afbe0e73ce6cc64b844c118d644259f46ed0d0f"),
    (7, 9, 4, 3, "hoa-salient-order7-q9-mode4-cluster3-measured-v1.json", "8d1579930e35f6bfb60d8a3d08c4501296db7449f004fab4a5dace70b69e90ac"),
    (8, 6, 1, 0, "hoa-salient-order8-q6-mode1-measured-v1.json", "6e54ca0376f72f2c40678a53a1f7f73f8e5f65a7fb2653f4ab2cb5612de4c949"),
    (8, 6, 2, 0, "hoa-salient-order8-q6-mode2-book0-measured-v1.json", "ac753039c89cf4df08152ffc7a595bf32de9e8f01a6fb4b4b9fc1ebd5430d0db"),
    (8, 6, 2, 1, "hoa-salient-order8-q6-mode2-book1-measured-v1.json", "76f3064b91f148807bc542bf5424d425f88fe6581c5eae4a03636e344ef9c50d"),
    (8, 6, 3, 0, "hoa-salient-order8-q6-mode3-measured-v1.json", "833126eef565daee0659bcf55bc2582128ee1db31763cd00fff6899088562f2a"),
    (8, 6, 4, 0, "hoa-salient-order8-q6-mode4-cluster0-measured-v1.json", "b3c69399eb04c49a3c10b3fe73853303327d233f3a14a8957a6747cbd1ff6622"),
    (8, 6, 4, 1, "hoa-salient-order8-q6-mode4-cluster1-measured-v1.json", "ee45f8f41f99b09eb92c5df925b29ec4ca1d72c06f61b665dce9d1182aea5ee9"),
    (8, 6, 4, 2, "hoa-salient-order8-q6-mode4-cluster2-measured-v1.json", "1206763271e1ba57e65d628870c139650dbf967ae7c86654a621e9c7c34c6829"),
    (8, 6, 4, 3, "hoa-salient-order8-q6-mode4-cluster3-measured-v1.json", "67ffcd4bd6723dfb1f708d819b9689b8c0425186f905e5d9ce2388e83137af93"),
    (8, 7, 1, 0, "hoa-salient-order8-q7-mode1-measured-v1.json", "a0a44e97471dd5afc4763427ea65d7c180c3d1d66c454ca3d7df6fbc3debe3af"),
    (8, 7, 2, 0, "hoa-salient-order8-q7-mode2-book0-measured-v1.json", "db464fab4438859f895fa25148420c1cdd6082bba10d6a13d55d270db58d285b"),
    (8, 7, 2, 1, "hoa-salient-order8-q7-mode2-book1-measured-v1.json", "6ade7baf8e9e946f4bb1c66f4434eedcfcab0df34a67b12e779d55d7598e494f"),
    (8, 7, 3, 0, "hoa-salient-order8-q7-mode3-measured-v1.json", "bd05f19cda0e57a5713ff0fc2d866df4a0072fe7ea13d40b0c710adaf0c464c6"),
    (8, 7, 4, 0, "hoa-salient-order8-q7-mode4-cluster0-measured-v1.json", "090a8390ccfc87b0d9dc04c8bc41a4b0e4aaf6a7ed6b768321ad25a1f12f95ca"),
    (8, 7, 4, 1, "hoa-salient-order8-q7-mode4-cluster1-measured-v1.json", "4e0b4fced466ab930a9265d34f9de20a11dae251a92bab666957966ff863df7c"),
    (8, 7, 4, 2, "hoa-salient-order8-q7-mode4-cluster2-measured-v1.json", "8a6f6f4bacc90b8ff828e4e77100549de5077fd896892e2bf6e623bfce8a5cc0"),
    (8, 7, 4, 3, "hoa-salient-order8-q7-mode4-cluster3-measured-v1.json", "62830af7cbcb51cd25bbdcc41374fb8e557c70d4bcea7add2aa99bc441c77731"),
    (8, 8, 1, 0, "hoa-salient-order8-q8-mode1-measured-v1.json", "e316462c22399a4c990a9f9872c5dfa63c0dcb8a960c53e6ddbc2f7bdc4036e2"),
    (8, 8, 2, 0, "hoa-salient-order8-q8-mode2-book0-measured-v1.json", "3acba1a8b3a18af0b4cfb10fc9c875dcd44dd212c1a60c5b69db9163430acdc4"),
    (8, 8, 2, 1, "hoa-salient-order8-q8-mode2-book1-measured-v1.json", "3898713ffb8d0a46ce48cdf566189d264a339555c7427206771f84400708bbb6"),
    (8, 8, 3, 0, "hoa-salient-order8-q8-mode3-measured-v1.json", "461352c66df2e99d5f8cc2ad59ec80b6a4b82b663e7acd7aade7f18dd8ff4274"),
    (8, 8, 4, 0, "hoa-salient-order8-q8-mode4-cluster0-measured-v1.json", "7a1c4492c069ecf30ade04a9e742c744bcb7ca1fab2aa92f9ca0aa41ecd62d54"),
    (8, 8, 4, 1, "hoa-salient-order8-q8-mode4-cluster1-measured-v1.json", "381b663212f79a5cad568afd302beb2993f0637a997a030ac41cc972d4f80402"),
    (8, 8, 4, 2, "hoa-salient-order8-q8-mode4-cluster2-measured-v1.json", "811425ef127e938a83bdf300e1d3f07990beb2ed011d4ac8a4d39c2d14f7a1a4"),
    (8, 8, 4, 3, "hoa-salient-order8-q8-mode4-cluster3-measured-v1.json", "322a81197ae855dda59644373261c6c882a5ca571c6fb038f21a0e1ec2f75b81"),
    (8, 9, 1, 0, "hoa-salient-order8-q9-mode1-measured-v1.json", "d3258540f03708db671318f8d6a379ebc6885adf6eff0a4cb4279dabac1e68f2"),
    (8, 9, 2, 0, "hoa-salient-order8-q9-mode2-book0-measured-v1.json", "589c4962f5b8cd92f13769f41dd4f22c2a18467dd7bf5e8c1374598aeb29b2f6"),
    (8, 9, 2, 1, "hoa-salient-order8-q9-mode2-book1-measured-v1.json", "a583aab7255b48a11cf53a61def20de7f2d76185a57c4d9644abdeddce584d3e"),
    (8, 9, 3, 0, "hoa-salient-order8-q9-mode3-measured-v1.json", "72aa4a79ccc7c6761bc4d92864b110295b704d8512ecacb836bf7a70ca74b85f"),
    (8, 9, 4, 0, "hoa-salient-order8-q9-mode4-cluster0-measured-v1.json", "b74fd2415e23901caebd2a77ae7a7cfd4efc76cf69269d3aab50a114f6922f78"),
    (8, 9, 4, 1, "hoa-salient-order8-q9-mode4-cluster1-measured-v1.json", "a4ff312a786fa57f41706729948d8e814712c13d32ce329a27776c33ef0a3402"),
    (8, 9, 4, 2, "hoa-salient-order8-q9-mode4-cluster2-measured-v1.json", "b439e9e59b72e36d7262ca80f75efc05f93ff244ac1a69da646c9d28eb325175"),
    (8, 9, 4, 3, "hoa-salient-order8-q9-mode4-cluster3-measured-v1.json", "c4347491460ee7f8f0523846f237d81eb886db2427853606c8dfe4ef7245ebe7"),
    (9, 6, 1, 0, "hoa-salient-order9-q6-mode1-measured-v1.json", "2e7b6c8f47b83576eecdbabe963d59369abc1933cc6cda000f0c59e20c16a89b"),
    (9, 6, 2, 0, "hoa-salient-order9-q6-mode2-book0-measured-v1.json", "1d6b254d864591243cd61b08021ab37e587b6cd4ae5b9f8fe051b8a2a5bbdf3e"),
    (9, 6, 2, 1, "hoa-salient-order9-q6-mode2-book1-measured-v1.json", "6bb97ddf56359161eae2065765fc89fd2bc8bbbe770a08a2bb2a1718cde281c9"),
    (9, 6, 3, 0, "hoa-salient-order9-q6-mode3-measured-v1.json", "206fa6a602e5d85bbaaca85c63162745aaa598f093857212d4f8f377c0851222"),
    (9, 6, 4, 0, "hoa-salient-order9-q6-mode4-cluster0-measured-v1.json", "157485a07e92614aa6e0d2d2adfeb95a76aa71cbd2de0d1b178beb21b2dd2e52"),
    (9, 6, 4, 1, "hoa-salient-order9-q6-mode4-cluster1-measured-v1.json", "b67fb83dab300167d1e9b3b7a300e3d81e4d9dd1c6930d29fe79d6e8facde53e"),
    (9, 6, 4, 2, "hoa-salient-order9-q6-mode4-cluster2-measured-v1.json", "eb7c2ed56d0d25af5f04eed5d60e8cb72d70c37cb950ee35ffdfb94d4aefb735"),
    (9, 6, 4, 3, "hoa-salient-order9-q6-mode4-cluster3-measured-v1.json", "393c025b79986ed514e79975783fccdd2d45984fb76e5b4a493e858f0cb576d6"),
    (9, 7, 1, 0, "hoa-salient-order9-q7-mode1-measured-v1.json", "58a540e6352d7bf17f43a605302d94831ce9e2f5955afb7fcb8bbf6e1cdb4c77"),
    (9, 7, 2, 0, "hoa-salient-order9-q7-mode2-book0-measured-v1.json", "233587d0797468c78939cb6894f33371d3de329f4bf86e9d2417952c8de4ac98"),
    (9, 7, 2, 1, "hoa-salient-order9-q7-mode2-book1-measured-v1.json", "93ab331d9a611727159fdc50cf9ab17aa12ab1562ce436d645489d5eddf02f39"),
    (9, 7, 3, 0, "hoa-salient-order9-q7-mode3-measured-v1.json", "9496987c4e55e286ba7e1e7acb2712444428e9b5f23729a14a1b42c8f993866f"),
    (9, 7, 4, 0, "hoa-salient-order9-q7-mode4-cluster0-measured-v1.json", "512b7eb9d79af22106428faa6914de57f127aefa6a6c6776ba52011b4b4c98f4"),
    (9, 7, 4, 1, "hoa-salient-order9-q7-mode4-cluster1-measured-v1.json", "02a83e8943d3dfa586ff058d73348d5d790a6a58194fdabf95a0007c2a6728fa"),
    (9, 7, 4, 2, "hoa-salient-order9-q7-mode4-cluster2-measured-v1.json", "4f371142d13e89a1c3ddfa0cc211782cef5de7f150f5c38a162dc0840a99e681"),
    (9, 7, 4, 3, "hoa-salient-order9-q7-mode4-cluster3-measured-v1.json", "6c67ace8697bab7ac4106946d5ed0ab6ab40ab9ac8e7375f2b28d0bd2da7a93a"),
    (9, 8, 1, 0, "hoa-salient-order9-q8-mode1-measured-v1.json", "670ff67f707a3f0f3eb21cfb9500d8f2b9279d574aa40ab3b095c6fe240c407e"),
    (9, 8, 2, 0, "hoa-salient-order9-q8-mode2-book0-measured-v1.json", "99fc0d19e2648568ecfeb89b528036f5e2189d7d822e7ba95db67d040f3288b6"),
    (9, 8, 2, 1, "hoa-salient-order9-q8-mode2-book1-measured-v1.json", "fda405a78fabf76880e952c61b2e8da70c648bcb68953dad982e680f00a6b1ed"),
    (9, 8, 3, 0, "hoa-salient-order9-q8-mode3-measured-v1.json", "46891e8b27a95be531860668d366ecb4f78b79f6adbd762fda9902791d407a2e"),
    (9, 8, 4, 0, "hoa-salient-order9-q8-mode4-cluster0-measured-v1.json", "fd21093f4179de3dd14f4a313fa999232600a91d339b746ac82d24c0e64504e2"),
    (9, 8, 4, 1, "hoa-salient-order9-q8-mode4-cluster1-measured-v1.json", "55aaf93259175a6bf3f3305253c7ea6bda4d2139b3230404b9c6b0a6e123abfe"),
    (9, 8, 4, 2, "hoa-salient-order9-q8-mode4-cluster2-measured-v1.json", "7c2826e79af1773b278fa2e05d4f0935d3fce949b49f6e339d1d08b1477126b9"),
    (9, 8, 4, 3, "hoa-salient-order9-q8-mode4-cluster3-measured-v1.json", "e6e79bb0501988dffb31e36a51f64081918bf4b9f4f660865d100470631463ea"),
    (9, 9, 1, 0, "hoa-salient-order9-q9-mode1-measured-v1.json", "13248d9cbd740150f5accdf6e8cec3de954b7901b2a9420e59ff1e727737fe35"),
    (9, 9, 2, 0, "hoa-salient-order9-q9-mode2-book0-measured-v1.json", "f087daae2b64378f3271ecf30be44de067c5b69213adee1c60e3f37f12350c6f"),
    (9, 9, 2, 1, "hoa-salient-order9-q9-mode2-book1-measured-v1.json", "a073b78ac87e73794a4f33edbe3c5f6d3b07ef9b06a7ca3a7a09986259d7b7d0"),
    (9, 9, 3, 0, "hoa-salient-order9-q9-mode3-measured-v1.json", "f662da821e8708c2dd70ff47d270943f8b45dc97e38c78b1bec2bee1c594e137"),
    (9, 9, 4, 0, "hoa-salient-order9-q9-mode4-cluster0-measured-v1.json", "67b562565691d8a31dcac3c9e03bd4055927e3aa5a09f01aba431e063378ae80"),
    (9, 9, 4, 1, "hoa-salient-order9-q9-mode4-cluster1-measured-v1.json", "8f5fed5985951c8b51cfbd0fd40ff074b25c3a099b1b91b766bbda21d1bb1499"),
    (9, 9, 4, 2, "hoa-salient-order9-q9-mode4-cluster2-measured-v1.json", "1ffb87a39fbb0b5232a3903ad55466ed018304b3b127bb19a6f048b7b1c3dcd4"),
    (9, 9, 4, 3, "hoa-salient-order9-q9-mode4-cluster3-measured-v1.json", "f5c949f19a4ac0c920f677662a2b6fa64fee5bcc553791021fab0d32ff92070a"),
    (10, 6, 1, 0, "hoa-salient-order10-q6-mode1-measured-v1.json", "7d857413038cc61b632ea627c050b3fc20e227f498f2161f8a313d3db0e94b07"),
    (10, 6, 2, 0, "hoa-salient-order10-q6-mode2-book0-measured-v1.json", "5f346398815675b0cd9bd741dd60421b0febc5d41e06449c199c3f65e7ec8182"),
    (10, 6, 2, 1, "hoa-salient-order10-q6-mode2-book1-measured-v1.json", "167f797c1e7ac12c9ffe60d3a3034909e097c2a353814502d02a23f1c741626c"),
    (10, 6, 3, 0, "hoa-salient-order10-q6-mode3-measured-v1.json", "b52d178df42f9236b75532af2934f99dcefffaed03f18d28fd9758ab08c8689d"),
    (10, 6, 4, 0, "hoa-salient-order10-q6-mode4-cluster0-measured-v1.json", "fe59133975172a73435d388101298c2ac9e55d60f8939160072006e4a1768117"),
    (10, 6, 4, 1, "hoa-salient-order10-q6-mode4-cluster1-measured-v1.json", "b6c6d7a7f079d9ed851c44a5bb08e0d33c328a59f868d1fcb7790762f5df43f2"),
    (10, 6, 4, 2, "hoa-salient-order10-q6-mode4-cluster2-measured-v1.json", "7a4c35b20b30f397a40980f5787a285b6a65879eb3b001bc0c975499033c31c2"),
    (10, 6, 4, 3, "hoa-salient-order10-q6-mode4-cluster3-measured-v1.json", "d26d1c4b3979a985a9a2443c0e694e5cf6fcfb8c7c5f53a94c502beaa9be98b8"),
    (10, 7, 1, 0, "hoa-salient-order10-q7-mode1-measured-v1.json", "414670c6f02830a3ab420f4ad8b4dca688876e8e2b3de089baea475c737e7517"),
    (10, 7, 2, 0, "hoa-salient-order10-q7-mode2-book0-measured-v1.json", "f6466b3258bbdd17e894844b302c88d04574980fed183c7284eabc7839f63530"),
    (10, 7, 2, 1, "hoa-salient-order10-q7-mode2-book1-measured-v1.json", "b84c7f671e7abe5bacdacc28f0f9c64cdd36d2c7acfdbef4230c6e79243c5fd8"),
    (10, 7, 3, 0, "hoa-salient-order10-q7-mode3-measured-v1.json", "d613c9d508418cbd5793830db9734e683abda56c70f0374778ab59b6bad8eacb"),
    (10, 7, 4, 0, "hoa-salient-order10-q7-mode4-cluster0-measured-v1.json", "53b83f49d0fdc8d7ac2ea20c0f0a07686bba58b5799477d59797fbd504332555"),
    (10, 7, 4, 1, "hoa-salient-order10-q7-mode4-cluster1-measured-v1.json", "80be936f0c15d3bf09b85ba1fe051dec951418a108775d45f8244fa508af6ef5"),
    (10, 7, 4, 2, "hoa-salient-order10-q7-mode4-cluster2-measured-v1.json", "6bd9ca2ccd4388f0225da79853e82eaa318cafd1519767f3c30c552fecf28725"),
    (10, 7, 4, 3, "hoa-salient-order10-q7-mode4-cluster3-measured-v1.json", "332f54656985651d6cad114549163053ed6f55700f212e4f2879a2c3f5c4b35d"),
    (10, 8, 1, 0, "hoa-salient-order10-q8-mode1-measured-v1.json", "d44a2f10518cca2e60758d3f957dc8ba4ff05768e05891a3a50b3c701297daf5"),
    (10, 8, 2, 0, "hoa-salient-order10-q8-mode2-book0-measured-v1.json", "b7daf468881c7ec3bebe30daec68e81dbbe241a5914dd551a84aa8fdd9959334"),
    (10, 8, 2, 1, "hoa-salient-order10-q8-mode2-book1-measured-v1.json", "fb83ee090986bafc4452807d408bdc382662f555b832f6cfed051017ccfed1f0"),
    (10, 8, 3, 0, "hoa-salient-order10-q8-mode3-measured-v1.json", "9586952a9f5c012d1b85ad70f6bfc953599baab6baaecb8dcbffbbfc347abd22"),
    (10, 8, 4, 0, "hoa-salient-order10-q8-mode4-cluster0-measured-v1.json", "c2a17e128d95b053527b3b6b86197391662b1a0dfbc1115378d3f37757a42519"),
    (10, 8, 4, 1, "hoa-salient-order10-q8-mode4-cluster1-measured-v1.json", "48fb13d9ff1be8220ccb05e47cdf72a52df629a5fcfc8fe449f198b1242c1b57"),
    (10, 8, 4, 2, "hoa-salient-order10-q8-mode4-cluster2-measured-v1.json", "15393dc0a398dd3701b64ea2abf71d4d757348d37dbf860e8566935229b43170"),
    (10, 8, 4, 3, "hoa-salient-order10-q8-mode4-cluster3-measured-v1.json", "514812e237a38d7e5f036884f8a7a492ef5f741b1795574d0f74741ac4a7086a"),
    (10, 9, 1, 0, "hoa-salient-order10-q9-mode1-measured-v1.json", "c6c8302573b9872e11b0d27f67c42bf3d63f870284ad6c6d581cc6ebc7e7d5f3"),
    (10, 9, 2, 0, "hoa-salient-order10-q9-mode2-book0-measured-v1.json", "cffff98ffe4cf03e2eb2ac9672dac298ca1eb1b240fe259682b2b67a5094e60e"),
    (10, 9, 2, 1, "hoa-salient-order10-q9-mode2-book1-measured-v1.json", "cd0117d5234edb2a0e958f2d17ae1bbd4a86415a7ff9214bbe20af60fb4b60f1"),
    (10, 9, 3, 0, "hoa-salient-order10-q9-mode3-measured-v1.json", "41750b5d9cd60e5db2279efae70ec8aa2c3be4d3551ac6c9910e9bd854bcd60c"),
    (10, 9, 4, 0, "hoa-salient-order10-q9-mode4-cluster0-measured-v1.json", "bcbf929cd5d28c00228929c89a29e309e2ba5005b397fb40536e1f93a9dde0d9"),
    (10, 9, 4, 1, "hoa-salient-order10-q9-mode4-cluster1-measured-v1.json", "035711ef2607b2909eb079511deb60f4c89092c31690976fc24c86188a0d1a8c"),
    (10, 9, 4, 2, "hoa-salient-order10-q9-mode4-cluster2-measured-v1.json", "0bfc881614336c767f96810340d0fddf4d58488631c51b48550f2943cbe6cb3a"),
    (10, 9, 4, 3, "hoa-salient-order10-q9-mode4-cluster3-measured-v1.json", "5252d4ca819e08ff99c6b4ec4c70ec74bab685c372f9fd5d85c89b543325cb87"),
];
const HIGHER_MATRIX_SHA256: &[(usize, usize, &str)] = &[
    (4, 0, "bbc904b9d24c3c20c721da48926c770b9bd754c8eb7dc83188f8770a2d37d5f0"),
    (4, 1, "754f811a7524d2b42157dea412b631a46ee1435bb2b5402aadbbb66331093568"),
    (4, 2, "13afba33d44601f5fa12571bdb7a3aa158fa75e07b82c122b77724071b942c2b"),
    (4, 3, "0e09c4a86143beac4a9e82b5afc00bdd1e6563c7320158e7b1193bae28c35e17"),
    (5, 0, "dd7406d918a22f08130e642456fd0760051a4ad0c7cbacd9d853d8fda534276d"),
    (5, 1, "8185caf4610661cdcbf8891efb497b7c79b058b6e831e042a6aa83d0ad538628"),
    (5, 2, "6683775a64ad80de253275f019f914bc3050e0344c6161570e5cc0bd74d55513"),
    (5, 3, "3bd68f0264323ded45d79e8ba332e67f45738ebae28933ca90c25d8fd7e648ff"),
    (6, 0, "3edb6133a943c5774c8d1c48167bf14bbfe808e3438b4b7b4775b91b0017d40c"),
    (6, 1, "fdc93737e4a26174a6e14696c8814fd0d9033a78b3fa3047d3af00ce3a04d479"),
    (6, 2, "58fcdcd3d16dd621b6fe10224da29f399710604cd89111d95d1001cccc1191d6"),
    (6, 3, "d6a1c3ff7192f266fd1469927934e9eee5d2482b45b1c33140207bed92d71bd5"),
    (7, 0, "7962de51e9e1228338775354d115750a1151a1708b9e4caac3499eb28cd29644"),
    (7, 1, "fc810cc3acdd249ed3ad1e6c1829fd45dcc674598e7d28d00b81a01975815003"),
    (7, 2, "a1163695f8127d92c706feed73e7f0cec9a318c28150494c9dc04147e3a2be3f"),
    (7, 3, "4b813fe64f903a39bb88b160bf4f5e2a8d26e5319ca3c6778257f1c20cb53487"),
    (8, 0, "727f6f8f0b16a96bd95beec781bba8437c716558d06f987f0a43f89772cca1ab"),
    (8, 1, "d90f49bd37c70436e92dd081d4b4cc50f982252ac940ceb5e2e1c80cbd225bf7"),
    (8, 2, "c5023c8b3d1dea8052ea4b54cbf9783cd16058b191f0cb53f6a9ea966235f2c3"),
    (8, 3, "e8e062d9cfda1d14255f23ae24d8f69fc0ff1dda675a3966f360d29fcc45bfbd"),
    (9, 0, "af08b2b678475354c493d1a53174c1e97e0aa9c0ba1a7403c00b8b5deaa64013"),
    (9, 1, "05426ac91dbec5e993c8d0e40b7ea5dc7c0eefd0d75c9058c592e4c03c277d82"),
    (9, 2, "af4b792170b1348d83b52c08edf622831a1d78b0266aec4d0ac525aecc3723c4"),
    (9, 3, "e86b2dadbed8e7d16570a38cec35fd3c14a6e5a7b2fe60735430008329cae75f"),
    (10, 0, "310c1e854eb37c56ad7eabad1a1ddf56c1cd44fc63b214489c2fa2701546cfb7"),
    (10, 1, "a3f0e43b63b1aa1d242f6928a274eaf1dd0123807b0dd222f960d943678c5bba"),
    (10, 2, "49e3711c859dc2aadafb010b7eea88528eab254dbd2b5edc22f040488f13e6c5"),
    (10, 3, "16a5d3ef7df17b3453d74e1da42e5815d156e1d391838242730e8d43838a6392"),
];
const HIGHER_DIRECT_BOOKS: &[DirectBook] = &[
    DirectBook { order: 4, mode: 2, book: 0, file: "hoa-salient-order4-q6-mode2-book0-measured-v1.json", book_sha256: "0b13ef0be6fab6c03e9391e3bffcec96cd5edb5887482686e1f5e98d073d6329", group_sha256: "a5b0a5193bb733a12b657d994c77f86568e4453c93de9f20fa67bff48384614c", group_users: &[(2, 0)] },
    DirectBook { order: 4, mode: 2, book: 1, file: "hoa-salient-order4-q6-mode2-book1-measured-v1.json", book_sha256: "992cafa4debf0acbb68f38d0b7da7fda3a886c2f332a5134ee6062aadf723d44", group_sha256: "15a47a8c4309223c692a581a7bb5edad4ad81e336b351d0fc6031299da1a53da", group_users: &[(2, 1)] },
    DirectBook { order: 4, mode: 3, book: 0, file: "hoa-salient-order4-q6-mode3-measured-v1.json", book_sha256: "a3be3a67c9ecfda79b992fdb47a510395b32c3d0f4a6268b32a106ae3a7efa0a", group_sha256: "2ae8d7e0bcd941cfbb7ad7af8e1fe2c80b0f205d3f43d346984e492ee1a6e60c", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 5, mode: 2, book: 0, file: "hoa-salient-order5-q6-mode2-book0-measured-v1.json", book_sha256: "8df98847c8ae8590472d56e0a67c41112a8593651e44e72cb1b3852b33c9fde4", group_sha256: "008c4dbb7f3d70a677a3d709e4a9e5bcf040a6da84e07fdb3a08fa8a6d95a1ab", group_users: &[(2, 0)] },
    DirectBook { order: 5, mode: 2, book: 1, file: "hoa-salient-order5-q6-mode2-book1-measured-v1.json", book_sha256: "5c59ce2fb6568997ec0d26bd27e44853dc52eeeb17755d9419a4d3fd0e72b665", group_sha256: "3652dbc1c3db8f3bf38e92a2f96a4ba99ba88e86845d6e6bf09ba97170034e93", group_users: &[(2, 1)] },
    DirectBook { order: 5, mode: 3, book: 0, file: "hoa-salient-order5-q6-mode3-measured-v1.json", book_sha256: "af637ffafdae1024ff291695a88ba6549810382f0edeae9eb53cd298f463c4e5", group_sha256: "7b1c1da9d372f688387109d78cb96a80333b1fe30715411b1dbe47fb66e61194", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 6, mode: 2, book: 0, file: "hoa-salient-order6-q6-mode2-book0-measured-v1.json", book_sha256: "8368f6899762a3b9757c318cde78289bc103d3cbbc531415d458ac6e74d5cabc", group_sha256: "1f91b48b52eee441608b297a63661230661a4d73f8401f69051702fc354bfd1a", group_users: &[(2, 0)] },
    DirectBook { order: 6, mode: 2, book: 1, file: "hoa-salient-order6-q6-mode2-book1-measured-v1.json", book_sha256: "3e24b64d3b29a14a6efa319610b42fa8c350a0a136abaca6085c25d66c5b2d9e", group_sha256: "8256b298852a2a457e4b716a8c758622612c64b328292db1a0b1ee40ba5757c0", group_users: &[(2, 1)] },
    DirectBook { order: 6, mode: 3, book: 0, file: "hoa-salient-order6-q6-mode3-measured-v1.json", book_sha256: "a3ad43eefd7a8b6bc94ab96a4dbd6241c0a55b1a72286149877d79db4bfe4560", group_sha256: "35c0bcf8dffb11e076af92d97af74c8f2211e0ee8cffe9944a2f5f7c60c5c3ba", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 7, mode: 2, book: 0, file: "hoa-salient-order7-q6-mode2-book0-measured-v1.json", book_sha256: "bbeb2f90832229f67ad0c99d565d816b8f062c6c47996b474871c8a22c6fd6c0", group_sha256: "74836ba1803574b11038affeefa81a0e801afa6f4f326030edd4cc35cc3f3862", group_users: &[(2, 0)] },
    DirectBook { order: 7, mode: 2, book: 1, file: "hoa-salient-order7-q6-mode2-book1-measured-v1.json", book_sha256: "3abc1f8d239592bfe1f25389eac331c82ce6e3f9aea37672115ac76a181d17b5", group_sha256: "2622fc1ab7d6075948d7c8c0bae6933137d0b99969f1fc513829df40e8e844a4", group_users: &[(2, 1)] },
    DirectBook { order: 7, mode: 3, book: 0, file: "hoa-salient-order7-q6-mode3-measured-v1.json", book_sha256: "5cf38407c9245975d959ba4216319fb3c6c581857a4a24f54f3308b9388be5f0", group_sha256: "a49d88051b2d2e5d1255d4f806a7569cda9b33a650a908566197031349ad8db4", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 8, mode: 2, book: 0, file: "hoa-salient-order8-q6-mode2-book0-measured-v1.json", book_sha256: "ac753039c89cf4df08152ffc7a595bf32de9e8f01a6fb4b4b9fc1ebd5430d0db", group_sha256: "fff34b9726c633d9e3f3ec590fe14ffeeb6aff988ec7428146f746674de9e3c6", group_users: &[(2, 0)] },
    DirectBook { order: 8, mode: 2, book: 1, file: "hoa-salient-order8-q6-mode2-book1-measured-v1.json", book_sha256: "76f3064b91f148807bc542bf5424d425f88fe6581c5eae4a03636e344ef9c50d", group_sha256: "28abc340beb8749ce76bcbef501efa9d5a1a65cfad95b698e5a909ce1e38d075", group_users: &[(2, 1)] },
    DirectBook { order: 8, mode: 3, book: 0, file: "hoa-salient-order8-q6-mode3-measured-v1.json", book_sha256: "833126eef565daee0659bcf55bc2582128ee1db31763cd00fff6899088562f2a", group_sha256: "99033b219b18b929e312186395696c828c9d8baba69b648c46ae7b69291ca162", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 9, mode: 2, book: 0, file: "hoa-salient-order9-q6-mode2-book0-measured-v1.json", book_sha256: "1d6b254d864591243cd61b08021ab37e587b6cd4ae5b9f8fe051b8a2a5bbdf3e", group_sha256: "811474247063148c36936dceacdf758acdcc4fa81269481cd69f2125fa8e2f91", group_users: &[(2, 0)] },
    DirectBook { order: 9, mode: 2, book: 1, file: "hoa-salient-order9-q6-mode2-book1-measured-v1.json", book_sha256: "6bb97ddf56359161eae2065765fc89fd2bc8bbbe770a08a2bb2a1718cde281c9", group_sha256: "a06bdd063d160292bc2e6ff45556b1249b82c777d82026ac29f23029995cd685", group_users: &[(2, 1)] },
    DirectBook { order: 9, mode: 3, book: 0, file: "hoa-salient-order9-q6-mode3-measured-v1.json", book_sha256: "206fa6a602e5d85bbaaca85c63162745aaa598f093857212d4f8f377c0851222", group_sha256: "9dd50adbd73bc58bb391efca85d1436fcc4c19cb0bc2495ec0eeb4c1e42f6952", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
    DirectBook { order: 10, mode: 2, book: 0, file: "hoa-salient-order10-q6-mode2-book0-measured-v1.json", book_sha256: "5f346398815675b0cd9bd741dd60421b0febc5d41e06449c199c3f65e7ec8182", group_sha256: "74b778962c5dca20770b558dfd57b8fcbc469d447eb2dab2b82588ab40ce98d4", group_users: &[(2, 0)] },
    DirectBook { order: 10, mode: 2, book: 1, file: "hoa-salient-order10-q6-mode2-book1-measured-v1.json", book_sha256: "167f797c1e7ac12c9ffe60d3a3034909e097c2a353814502d02a23f1c741626c", group_sha256: "3b32138b90307a5b5fb71f10e4e7ce140066377031579456cf7ac44ed13be132", group_users: &[(2, 1)] },
    DirectBook { order: 10, mode: 3, book: 0, file: "hoa-salient-order10-q6-mode3-measured-v1.json", book_sha256: "b52d178df42f9236b75532af2934f99dcefffaed03f18d28fd9758ab08c8689d", group_sha256: "252dce1bf40f9cf596d131f6962a29ef0fed11c6e6de9872754fdab48956c66e", group_users: &[(0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)] },
];
// END HIGHER MEASURED SOURCES

fn is_measured_order(order: usize) -> bool {
    (1..=3).contains(&order) || HIGHER_CODEBOOKS.iter().any(|source| source.0 == order)
}

/// Each order shares its matrices across all four quantization widths.
fn measured_matrix(order: usize, cluster: usize) -> Vec<u32> {
    let data: MeasuredMatrix = data_json(&format!(
        "hoa-salient-order{order}-mode4-cluster{cluster}-matrix-measured-v1.json"
    ));
    assert_eq!(data.schema_version, 1);
    assert_eq!(data.profile, "apac-hoa-salient-measured-matrix-v1");
    assert_eq!(
        (data.order, data.mode, data.cluster, data.rows, data.columns),
        (order, 4, cluster, (order + 1).pow(2), (order + 1).pow(2))
    );
    assert_eq!(data.storage, "row-major");
    assert_eq!(data.matrix_f32.len(), (order + 1).pow(4));
    assert!(data
        .matrix_f32
        .iter()
        .all(|&word| f32::from_bits(word).is_finite()));
    let digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&data.matrix_f32).expect("measured matrix JSON"))
    );
    assert_eq!(digest, data.matrix_sha256);
    let expected = if order == 3 {
        MODE4_MATRIX_SHA256[cluster]
    } else {
        LOWER_MATRIX_SHA256
            .iter()
            .chain(HIGHER_MATRIX_SHA256.iter())
            .find_map(|&(o, c, sha)| (o == order && c == cluster).then_some(sha))
            .expect("measured matrix source")
    };
    assert_eq!(digest, expected);
    if let Some(qualification) = &data.source.zero_sign_qualification {
        verify_zero_qualification(qualification, &data.matrix_f32, (order + 1).pow(2));
    }
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
    let measured: Vec<_> = if is_measured_order(order) {
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
                (index, measured_matrix(order, cluster))
            })
            .collect()
    } else {
        Vec::new()
    };
    if is_measured_order(order) {
        let sources: Vec<_> = DIRECT_BOOKS
            .iter()
            .chain(LOWER_DIRECT_BOOKS.iter())
            .chain(HIGHER_DIRECT_BOOKS.iter())
            .filter(|source| source.order == order)
            .collect();
        assert_eq!(sources.len(), 3, "measured coefficient group sources");
        for source in sources {
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
    out.raw(&format!(
        "pub const HOA_MAX_SUPPORTED_ORDER: usize = {MAX_SUPPORTED_ORDER};\n\
         pub const HOA_MAX_SUPPORTED_COEFFICIENTS: usize = {};",
        (MAX_SUPPORTED_ORDER + 1).pow(2)
    ));
    let measured_direct: Vec<_> = DIRECT_BOOKS
        .iter()
        .map(|source| {
            measured_codebook(
                source.file,
                source.order,
                6,
                source.mode,
                source.book,
                source.book_sha256,
            )
        })
        .collect();
    let measured_mode1 = measured_codebook(
        "hoa-salient-order3-q6-mode1-measured-v1.json",
        3,
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
                3,
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
                measured_codebook(file, 3, precision, mode, book, sha),
            )
        })
        .collect();
    let measured_lower: Vec<_> = LOWER_CODEBOOKS
        .iter()
        .chain(HIGHER_CODEBOOKS.iter())
        .filter(|source| source.0 <= MAX_SUPPORTED_ORDER)
        .map(|&(order, precision, mode, book, file, sha)| {
            (
                order,
                precision,
                mode,
                book,
                measured_codebook(file, order, precision, mode, book, sha),
            )
        })
        .collect();
    let mut constants = Vec::new();
    for order in 1usize..=MAX_SUPPORTED_ORDER {
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
            if is_measured_order(order) {
                for (mode, count) in stored.modes.iter().zip([0, 1, 2, 1, 4, 0]) {
                    assert_eq!(mode.codebooks.len(), count);
                }
            }
            let mut modes = Vec::new();
            let mut tries = Vec::new();
            for (mode_index, (mode, common)) in stored.modes.iter().zip(&shared.modes).enumerate() {
                assert_eq!(mode.mode, mode_index);
                assert_eq!(common.mode, mode_index);
                assert!(common
                    .group_indices
                    .iter()
                    .flat_map(|&g| &shared.groups[g])
                    .all(|&i| i < coefficients));
                assert!(common
                    .matrix_indices
                    .iter()
                    .all(|&m| shared.matrix_lengths[m] == coefficients * coefficients));
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
                        (other_order, precision, mode, book)
                            if other_order != 3 && is_measured_order(other_order) =>
                        {
                            Some(
                                measured_lower
                                    .iter()
                                    .find_map(|(o, p, m, b, words)| {
                                        (*o == order && *p == precision && *m == mode && *b == book)
                                            .then_some(words)
                                    })
                                    .expect("measured codebook source"),
                            )
                        }
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
