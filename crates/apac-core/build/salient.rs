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
    let expected = if order == 3 {
        MODE4_MATRIX_SHA256[cluster]
    } else {
        LOWER_MATRIX_SHA256
            .iter()
            .find_map(|&(o, c, sha)| (o == order && c == cluster).then_some(sha))
            .expect("measured lower-order matrix source")
    };
    assert_eq!(digest, expected);
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
    let measured: Vec<_> = if (1..=3).contains(&order) {
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
    if (1..=3).contains(&order) {
        let sources: Vec<_> = DIRECT_BOOKS
            .iter()
            .chain(LOWER_DIRECT_BOOKS.iter())
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
            if (1..=3).contains(&order) {
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
                        (1 | 2, precision, mode, book) => Some(
                            measured_lower
                                .iter()
                                .find_map(|(o, p, m, b, words)| {
                                    (*o == order && *p == precision && *m == mode && *b == book)
                                        .then_some(words)
                                })
                                .expect("measured lower-order codebook source"),
                        ),
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
