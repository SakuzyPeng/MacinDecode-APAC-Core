//! SQ numeric tables, SQ/CAC Huffman codebooks, TNS constants. The CAC
//! rotations belong to the `apac-cac` crate.
use crate::emit::{self, Output};
use crate::{data_bytes, data_json, trie_build};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Book {
    codes: Vec<u32>,
    bits: Vec<usize>,
}

/// Emits a codebook's arrays plus its trie; returns (codebook expr, trie expr).
/// Codebooks that only tests read are compiled into test builds only.
fn book(out: &mut Output, name: &str, book: &Book, test_only: bool) -> (String, String) {
    if test_only {
        out.test_only();
    }
    let codes = out.array(false, &format!("{name}_CODES"), "u32", &book.codes);
    if test_only {
        out.test_only();
    }
    let bits = out.array(false, &format!("{name}_BITS"), "usize", &book.bits);
    let nodes = trie_build::build(&book.codes, &book.bits);
    let trie = out.array(
        false,
        &format!("{name}_TRIE"),
        "[u16; 3]",
        nodes.into_iter().map(emit::node),
    );
    (
        format!("crate::tables::Codebook {{ codes: &{codes}, bits: &{bits} }}"),
        format!("crate::tables::Trie::from_static(&{trie})"),
    )
}

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
    inverse_quantizer_f64: Vec<u64>,
    gains_f64: Vec<u64>,
    transforms: BTreeMap<String, RawTransform>,
}

/// `numeric::tables()`: inverse quantizer, gains and both transform sizes.
pub fn numeric(out: &mut Output) {
    let mut raw: RawTables = data_json("sq-math-v2.json");
    assert_eq!(raw.numeric_profile, "apac-sq-math-v2");
    assert_eq!(raw.inverse_quantizer_f64.len(), 8192);
    assert_eq!(raw.gains_f64.len(), 512);
    let inverse = out.array(
        false,
        "SQ_INVERSE",
        "f64",
        raw.inverse_quantizer_f64.iter().map(|&b| emit::f64_bits(b)),
    );
    let gains = out.array(
        false,
        "SQ_GAINS",
        "f64",
        raw.gains_f64.iter().map(|&b| emit::f64_bits(b)),
    );
    let mut transform = |n: usize| {
        let data = raw.transforms.remove(&n.to_string()).expect("SQ size");
        assert_eq!(data.window_f64.len(), n);
        assert_eq!(data.modulation_f64.len(), n / 2);
        assert_eq!(data.twiddles_f64.len(), n / 4);
        // The rising half is stored; the falling half mirrors it bit for bit.
        let mut window = data.window_f64.clone();
        window.extend(data.window_f64.iter().rev());
        let window = out.array(
            false,
            &format!("SQ_WINDOW_{n}"),
            "f64",
            window.into_iter().map(emit::f64_bits),
        );
        let modulation = out.array(
            false,
            &format!("SQ_MODULATION_{n}"),
            "[f64; 2]",
            data.modulation_f64
                .iter()
                .map(|p| emit::pair(p.map(emit::f64_bits))),
        );
        let twiddles = out.array(
            false,
            &format!("SQ_TWIDDLES_{n}"),
            "[f64; 2]",
            data.twiddles_f64
                .iter()
                .map(|p| emit::pair(p.map(emit::f64_bits))),
        );
        format!(
            "crate::numeric::Transform {{ window: &{window}, modulation: &{modulation}, twiddles: &{twiddles} }}"
        )
    };
    let short = transform(128);
    let long = transform(1024);
    out.raw(&format!(
        "pub static SQ_MATH: crate::numeric::Tables = crate::numeric::Tables {{ sha256: {:?}, inverse: &{inverse}, gains: &{gains}, short: {short}, long: {long} }};",
        raw.tables_sha256
    ));
}

#[derive(Deserialize)]
struct SqBooks {
    spectral: Vec<Book>,
    scalefactor: Book,
    long_offsets: Vec<usize>,
    short_offsets: Vec<usize>,
}

/// SQ spectral and scale-factor codebooks; trie 0 is the scale-factor book.
pub fn codebooks(out: &mut Output) {
    let books: SqBooks = data_json("sq-codebooks.json");
    let (scalefactor, scalefactor_trie) = book(out, "SQ_SCALEFACTOR", &books.scalefactor, true);
    let mut spectral = Vec::new();
    let mut tries = vec![scalefactor_trie];
    for (i, b) in books.spectral.iter().enumerate() {
        let (codebook, trie) = book(out, &format!("SQ_SPECTRAL_{}", i + 1), b, true);
        spectral.push(codebook);
        tries.push(trie);
    }
    out.test_only();
    out.raw(&format!(
        "pub static SQ_SCALEFACTOR: crate::tables::Codebook = {scalefactor};"
    ));
    out.test_only();
    out.array(true, "SQ_SPECTRAL", "crate::tables::Codebook", spectral);
    out.array(true, "SQ_TRIES", "crate::tables::Trie", tries);
    out.array(true, "SQ_LONG_OFFSETS", "usize", &books.long_offsets);
    out.array(true, "SQ_SHORT_OFFSETS", "usize", &books.short_offsets);
}

#[derive(Deserialize)]
struct CacBooks {
    gain: Book,
    repeat: Book,
}
#[derive(Deserialize)]
struct MeasuredCacBooks {
    schema_version: u32,
    profile: String,
    #[serde(flatten)]
    books: CacBooks,
}
pub fn cac(out: &mut Output) {
    let raw = data_bytes("cac-codebooks-measured-v1.json");
    assert_eq!(
        format!("{:x}", Sha256::digest(&raw)),
        "87f8b01726741470ed7eddfeacf92fc86d7297dbe5604cfcac4a9cdf994786e6",
        "CAC measurement or qualification differs"
    );
    let measured: MeasuredCacBooks =
        serde_json::from_slice(&raw).expect("qualified measured CAC codebooks");
    assert_eq!(measured.schema_version, 1);
    assert_eq!(measured.profile, "apac-cac-codebooks-measured-v1");
    let books = measured.books;
    let copy: CacBooks = data_json("cac-codebooks.json");
    assert_eq!(copy.gain.codes, books.gain.codes);
    assert_eq!(copy.gain.bits, books.gain.bits);
    assert_eq!(copy.repeat.codes, books.repeat.codes);
    assert_eq!(copy.repeat.bits, books.repeat.bits);
    assert_eq!(books.gain.codes.len(), 35);
    assert_eq!(books.repeat.codes.len(), 44);
    let (gain, gain_trie) = book(out, "CAC_GAIN", &books.gain, false);
    let (repeat, repeat_trie) = book(out, "CAC_REPEAT", &books.repeat, false);
    out.raw(&format!(
        "pub static CAC_GAIN: crate::tables::Codebook = {gain};"
    ));
    out.raw(&format!(
        "pub static CAC_REPEAT: crate::tables::Codebook = {repeat};"
    ));
    out.array(
        true,
        "CAC_TRIES",
        "crate::tables::Trie",
        [gain_trie, repeat_trie],
    );
}

#[derive(Deserialize)]
struct TnsMath {
    numeric_profile: String,
    tables_sha256: String,
    reflection_f64: BTreeMap<String, Vec<u64>>,
}

pub fn tns(out: &mut Output) {
    let math: TnsMath = data_json("tns-math-v1.json");
    assert_eq!(math.numeric_profile, "apac-tns-math-v1");
    assert_eq!(math.reflection_f64["3"].len(), 8);
    assert_eq!(math.reflection_f64["4"].len(), 16);
    out.array(true, "TNS_REFLECTION_3", "u64", &math.reflection_f64["3"]);
    out.array(true, "TNS_REFLECTION_4", "u64", &math.reflection_f64["4"]);
    out.str_const("TNS_MATH_SHA256", &math.tables_sha256);
}
