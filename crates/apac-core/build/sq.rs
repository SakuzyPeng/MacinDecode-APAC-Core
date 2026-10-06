//! SQ numeric tables, SQ/CAC Huffman codebooks, TNS and BWE2 constants. The CAC
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
pub fn cac(out: &mut Output) {
    let books: CacBooks = data_json("cac-codebooks.json");
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

#[derive(Deserialize)]
struct Bwe2Format {
    schema_version: u32,
    format_profile: String,
    tables_sha256: String,
    lsf_codebooks_f32: Vec<Vec<[u32; 16]>>,
    excitation_gains_f32: Vec<u32>,
    source: Bwe2Source,
}
#[derive(Deserialize)]
struct Bwe2Source {
    gain_replacement: Bwe2GainReplacement,
    remaining_tables: BTreeMap<String, String>,
}
#[derive(Deserialize)]
struct Bwe2GainReplacement {
    source_file: String,
    source_sha256: String,
    gains_sha256: String,
}
#[derive(Deserialize)]
struct MeasuredBwe2Gains {
    schema_version: u32,
    profile: String,
    count: usize,
    storage: String,
    gains_sha256: String,
    excitation_gains_f32: Vec<u32>,
}
#[derive(Deserialize)]
struct Bwe2Math {
    numeric_profile: String,
    tables_sha256: String,
    twiddles_f64: BTreeMap<String, Vec<[u64; 2]>>,
    cosine_f64: [u64; 13],
    sine_f64: [u64; 13],
    lsf_angle_scale_f64: u64,
    autocorrelation_loading_f64: u64,
}

/// Float32 codebook and gain values widen to Float64 exactly.
fn widened(bits: u32) -> String {
    emit::f64_bits(f64::from(f32::from_bits(bits)).to_bits())
}

pub fn bwe2(out: &mut Output) {
    const SOURCE_FILE: &str = "bwe2-gains-measured-v1.json";
    const SOURCE_SHA256: &str = "f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865";
    const WORDS_SHA256: &str = "b4089e46f670df5bb84923eba409db45b7b117f5f910c816b48de197cdc5fe5d";
    const TABLES_SHA256: &str = "651850263d6adf1c2e5c2285910dc1fd6f0921293b6c0e7578a0cb780384a661";
    let raw = data_bytes(SOURCE_FILE);
    assert_eq!(format!("{:x}", Sha256::digest(&raw)), SOURCE_SHA256);
    let measured: MeasuredBwe2Gains =
        serde_json::from_slice(&raw).expect("measured BWE2 excitation gains");
    assert_eq!(measured.schema_version, 1);
    assert_eq!(measured.profile, "apac-bwe2-gains-measured-v1");
    assert_eq!(measured.storage, "index-order-float32-bits");
    assert_eq!(measured.count, 64);
    assert_eq!(measured.excitation_gains_f32.len(), measured.count);
    assert!(measured.excitation_gains_f32.iter().all(|&word| {
        let value = f32::from_bits(word);
        value.is_finite() && value > 0.
    }));
    assert!(
        measured
            .excitation_gains_f32
            .windows(2)
            .all(|pair| { f32::from_bits(pair[0]) < f32::from_bits(pair[1]) })
    );
    let mut digest = Sha256::new();
    for word in &measured.excitation_gains_f32 {
        digest.update(word.to_le_bytes());
    }
    assert_eq!(format!("{:x}", digest.finalize()), WORDS_SHA256);
    assert_eq!(measured.gains_sha256, WORDS_SHA256);
    let format: Bwe2Format = data_json("bwe2-format-v1.json");
    let math: Bwe2Math = data_json("bwe2-math-v2.json");
    assert_eq!(format.schema_version, 1);
    assert_eq!(format.format_profile, "apac-bwe2-format-v1");
    assert_eq!(math.numeric_profile, "apac-bwe2-math-v2");
    assert_eq!(format.lsf_codebooks_f32.len(), 2);
    assert!(format.lsf_codebooks_f32.iter().all(|b| b.len() == 512));
    assert_eq!(format.excitation_gains_f32.len(), 64);
    assert_eq!(format.excitation_gains_f32, measured.excitation_gains_f32);
    assert_eq!(format.source.gain_replacement.source_file, SOURCE_FILE);
    assert_eq!(format.source.gain_replacement.source_sha256, SOURCE_SHA256);
    assert_eq!(format.source.gain_replacement.gains_sha256, WORDS_SHA256);
    assert_eq!(format.source.remaining_tables.len(), 1);
    assert_eq!(
        format
            .source
            .remaining_tables
            .get("lsf_codebooks_f32")
            .map(String::as_str),
        Some("original_observation")
    );
    let semantic = serde_json::json!({
        "lsf_codebooks_f32": format.lsf_codebooks_f32,
        "excitation_gains_f32": format.excitation_gains_f32,
    });
    assert_eq!(
        format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&semantic).expect("BWE2 semantic JSON"))
        ),
        TABLES_SHA256
    );
    assert_eq!(format.tables_sha256, TABLES_SHA256);
    let mut books = Vec::new();
    for (i, b) in format.lsf_codebooks_f32.iter().enumerate() {
        books.push(out.array(
            false,
            &format!("BWE2_BOOK_{i}"),
            "[f64; 16]",
            b.iter().map(|row| {
                let values: Vec<String> = row.iter().map(|&x| widened(x)).collect();
                format!("[{}]", values.join(", "))
            }),
        ));
    }
    let gains = out.array(
        false,
        "BWE2_GAINS",
        "f64",
        measured.excitation_gains_f32.iter().map(|&x| widened(x)),
    );
    let mut sizes: Vec<(usize, &Vec<[u64; 2]>)> = math
        .twiddles_f64
        .iter()
        .map(|(n, v)| (n.parse().expect("transform size"), v))
        .collect();
    sizes.sort_by_key(|(n, _)| *n);
    let mut twiddles = Vec::new();
    for (n, values) in sizes {
        let name = out.array(
            false,
            &format!("BWE2_TWIDDLES_{n}"),
            "crate::bwe2_math::Complex",
            values.iter().map(|[re, im]| {
                format!(
                    "crate::bwe2_math::Complex {{ re: {}, im: {} }}",
                    emit::f64_bits(*re),
                    emit::f64_bits(*im)
                )
            }),
        );
        twiddles.push(format!("({n}, &{name})"));
    }
    let twiddles = out.array(
        false,
        "BWE2_TWIDDLES",
        "(usize, &[crate::bwe2_math::Complex])",
        twiddles,
    );
    let list = |values: &[u64; 13]| {
        let values: Vec<String> = values.iter().map(|&b| emit::f64_bits(b)).collect();
        format!("[{}]", values.join(", "))
    };
    out.raw(&format!(
        "pub static BWE2: crate::bwe2_math::Constants = crate::bwe2_math::Constants {{ format_sha: {:?}, math_sha: {:?}, books: [&{}, &{}], gains: &{gains}, twiddles: &{twiddles}, cosine: {}, sine: {}, angle_scale: {}, loading: {} }};",
        format.tables_sha256,
        math.tables_sha256,
        books[0],
        books[1],
        list(&math.cosine_f64),
        list(&math.sine_f64),
        emit::f64_bits(math.lsf_angle_scale_f64),
        emit::f64_bits(math.autocorrelation_loading_f64),
    ));
}
