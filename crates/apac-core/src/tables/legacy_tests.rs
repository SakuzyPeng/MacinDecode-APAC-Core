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
        inverse_quantizer_f64: Vec<u64>,
        gains_f64: Vec<u64>,
        transforms: BTreeMap<String, RawTransform>,
    }
    let mut raw: RawTables = serde_json::from_str(&data("sq-math-v2.json")).unwrap();
    assert_eq!(raw.numeric_profile, crate::numeric::PROFILE);
    let tables = crate::numeric::tables();
    assert_eq!(tables.sha256, raw.tables_sha256);
    assert_eq!(bits64(tables.inverse), raw.inverse_quantizer_f64);
    assert_eq!(bits64(tables.gains), raw.gains_f64);
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
fn cac_codebooks_tries_and_math_identity_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Books {
        gain: LegacyCodebook,
        repeat: LegacyCodebook,
    }
    #[derive(Deserialize)]
    struct Math {
        numeric_profile: String,
        tables_sha256: String,
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
    // The rotations themselves are checked in `apac-cac`.
    assert_eq!(crate::frame::cac_math_sha256(), math.tables_sha256);
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

fn file_sha256(name: &str) -> String {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../data")
        .join(name);
    crate::model::sha256(&std::fs::read(path).unwrap())
}

#[test]
fn shared_configuration_rates_offsets_and_profiles_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Rate {
        sample_rate: u64,
        sfb_rate: u64,
        long: String,
        short: String,
        tns_long_limit: usize,
        tns_short_limit: usize,
    }
    #[derive(Deserialize)]
    struct Limit {
        r#type: u8,
        maximum_channels: u64,
    }
    #[derive(Deserialize)]
    struct Profile {
        profile: u8,
        levels: Vec<Vec<Limit>>,
        layout_tags: Vec<u32>,
    }
    #[derive(Deserialize)]
    struct Format {
        format_profile: String,
        format_sha256: String,
        rates: Vec<Rate>,
        offset_arrays: BTreeMap<String, Vec<usize>>,
        profiles: Vec<Profile>,
    }
    let format: Format = serde_json::from_str(&data("hoa-shared-config-format-v1.json")).unwrap();
    assert_eq!(
        format.format_profile,
        crate::frame::HOA_SHARED_CONFIG_PROFILE
    );
    assert_eq!(super::SFB_FORMAT_SHA256, format.format_sha256);
    // The former offsets() lookup: two legacy keys name the SQ tables.
    let offsets = |key: &str| -> &[usize] {
        match key {
            "legacy-long" => &super::SQ_LONG_OFFSETS,
            "legacy-short" => &super::SQ_SHORT_OFFSETS,
            _ => &format.offset_arrays[key],
        }
    };
    assert_eq!(super::SFB_RATES.len(), format.rates.len());
    for (actual, expected) in super::SFB_RATES.iter().zip(&format.rates) {
        assert_eq!(
            (actual.sample_rate, actual.sfb_rate),
            (expected.sample_rate, expected.sfb_rate)
        );
        assert_eq!(
            (actual.tns_long_limit, actual.tns_short_limit),
            (expected.tns_long_limit, expected.tns_short_limit)
        );
        assert_eq!(actual.long, offsets(&expected.long));
        assert_eq!(actual.short, offsets(&expected.short));
    }
    assert_eq!(super::SFB_PROFILES.len(), format.profiles.len());
    for (actual, expected) in super::SFB_PROFILES.iter().zip(&format.profiles) {
        assert_eq!(actual.profile, expected.profile);
        assert_eq!(actual.layout_tags, expected.layout_tags.as_slice());
        assert_eq!(actual.levels.len(), expected.levels.len());
        for (a, e) in actual.levels.iter().zip(&expected.levels) {
            let a: Vec<_> = a.iter().map(|l| (l.kind, l.maximum_channels)).collect();
            let e: Vec<_> = e.iter().map(|l| (l.r#type, l.maximum_channels)).collect();
            assert_eq!(a, e);
        }
    }
}

#[test]
fn shared_drc_codes_and_profile_limits_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Code {
        width: usize,
        code: u16,
        value: i32,
    }
    #[derive(Deserialize)]
    struct Tables {
        format_profile: String,
        format_sha256: String,
        clipping: Vec<Code>,
        slopes: Vec<Code>,
    }
    let tables: Tables = serde_json::from_str(&data("hoa-shared-drc-format-v1.json")).unwrap();
    assert_eq!(tables.format_profile, crate::frame::HOA_SHARED_DRC_PROFILE);
    assert_eq!(
        crate::frame::hoa_shared_drc_format_sha256(),
        tables.format_sha256
    );
    let codes = |c: &[super::DrcCode]| {
        c.iter()
            .map(|c| (c.width, c.code, c.value))
            .collect::<Vec<_>>()
    };
    let legacy = |c: &[Code]| {
        c.iter()
            .map(|c| (c.width, c.code, c.value))
            .collect::<Vec<_>>()
    };
    assert_eq!(codes(&super::DRC_CLIPPING), legacy(&tables.clipping));
    assert_eq!(codes(&super::DRC_SLOPES), legacy(&tables.slopes));

    #[derive(Deserialize)]
    struct Entry {
        profile_id: u8,
        maximum_output_channels_by_level: Vec<u64>,
    }
    #[derive(Deserialize)]
    struct Table {
        profiles: Vec<Entry>,
    }
    let table: Table = serde_json::from_str(&data("hoa-profile-levels-v1.json")).unwrap();
    let actual: Vec<_> = super::HOA_PROFILE_LIMITS
        .iter()
        .map(|e| (e.profile_id, e.maximum_output_channels_by_level.to_vec()))
        .collect();
    let expected: Vec<_> = table
        .profiles
        .into_iter()
        .map(|e| (e.profile_id, e.maximum_output_channels_by_level))
        .collect();
    assert_eq!(actual, expected);
}

#[test]
fn hoa_ambient_and_control_tables_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Ambient {
        format_sha256: String,
        tables_sha256: String,
        decoder_matrices_f64: [[u64; 16]; 3],
    }
    let ambient: Ambient =
        serde_json::from_str(&data("hoa-static-ambient-tables-v1.json")).unwrap();
    assert_eq!(
        crate::frame::hoa_ambient_format_sha256(),
        ambient.format_sha256
    );
    assert_eq!(
        crate::frame::hoa_ambient_math_sha256(),
        ambient.tables_sha256
    );
    assert_eq!(super::HOA_AMBIENT_MATRICES, ambient.decoder_matrices_f64);

    #[derive(Deserialize)]
    struct Grid {
        long_ends: Vec<usize>,
    }
    #[derive(Deserialize)]
    struct Controls {
        format_sha256: String,
        mean_coefficients_f32: Vec<u32>,
        tables: Vec<Grid>,
    }
    let controls: Controls =
        serde_json::from_str(&data("hoa-spatial-controls-format-v1.json")).unwrap();
    assert_eq!(
        crate::frame::hoa_spatial_controls_format_sha256(),
        controls.format_sha256
    );
    assert_eq!(
        super::HOA_CONTROL_MEANS,
        controls.mean_coefficients_f32.as_slice()
    );
    assert_eq!(super::HOA_CONTROL_GRIDS.len(), controls.tables.len());
    for (actual, expected) in super::HOA_CONTROL_GRIDS.iter().zip(&controls.tables) {
        assert_eq!(actual.long_ends, expected.long_ends.as_slice());
    }
    assert_eq!(
        crate::frame::hoa_frame_configuration_state_sha256(),
        file_sha256("hoa-frame-configuration-state-v2.json")
    );
}

#[test]
fn hoa_dynamic_and_salient_grids_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Dynamic {
        format_sha256: String,
        long_ends: [[usize; 8]; 3],
        short_ends: [[usize; 8]; 3],
    }
    #[derive(Deserialize)]
    struct SubbandTable {
        long_ends: [Vec<usize>; 3],
        short_ends: [Vec<usize>; 3],
    }
    #[derive(Deserialize)]
    struct Extended {
        format_sha256: String,
        tables: Vec<SubbandTable>,
    }
    let dynamic: Dynamic = serde_json::from_str(&data("hoa-dynamic-format-v1.json")).unwrap();
    let extended: Extended = serde_json::from_str(&data("hoa-dynamic-format-v2.json")).unwrap();
    assert_eq!(
        crate::frame::hoa_dynamic_format_sha256(8),
        dynamic.format_sha256
    );
    assert_eq!(
        crate::frame::hoa_dynamic_format_sha256(1),
        extended.format_sha256
    );
    assert_eq!(super::HOA_DYNAMIC_LONG_ENDS, dynamic.long_ends);
    assert_eq!(super::HOA_DYNAMIC_SHORT_ENDS, dynamic.short_ends);
    assert_eq!(super::HOA_DYNAMIC_SUBBANDS.len(), extended.tables.len());
    for (actual, expected) in super::HOA_DYNAMIC_SUBBANDS.iter().zip(&extended.tables) {
        for method in 0..3 {
            assert_eq!(
                actual.long_ends[method],
                expected.long_ends[method].as_slice()
            );
            assert_eq!(
                actual.short_ends[method],
                expected.short_ends[method].as_slice()
            );
        }
    }
    assert_eq!(
        crate::frame::hoa_dynamic_domains_format_sha256(),
        file_sha256("hoa-dynamic-domains-format-v1.json")
    );

    #[derive(Deserialize)]
    struct Grid {
        long_ends: Vec<usize>,
        short_ends: Vec<usize>,
    }
    #[derive(Deserialize)]
    struct Subbands {
        format_sha256: String,
        tables: Vec<Grid>,
    }
    #[derive(Deserialize)]
    struct MethodGrid {
        #[serde(flatten)]
        grid: Grid,
    }
    #[derive(Deserialize)]
    struct Partition {
        format_sha256: String,
        tables: Vec<MethodGrid>,
    }
    let subbands: Subbands =
        serde_json::from_str(&data("hoa-salient-subbands-format-v1.json")).unwrap();
    let partition: Partition =
        serde_json::from_str(&data("hoa-salient-subbands-format-v2.json")).unwrap();
    assert_eq!(
        crate::frame::hoa_salient_subbands_format_sha256(0),
        subbands.format_sha256
    );
    assert_eq!(
        crate::frame::hoa_salient_subbands_format_sha256(1),
        partition.format_sha256
    );
    let pairs = |grids: &[super::HoaSubbandGrid]| {
        grids
            .iter()
            .map(|g| (g.long_ends.to_vec(), g.short_ends.to_vec()))
            .collect::<Vec<_>>()
    };
    let legacy = |grids: Vec<&Grid>| {
        grids
            .into_iter()
            .map(|g| (g.long_ends.clone(), g.short_ends.clone()))
            .collect::<Vec<_>>()
    };
    assert_eq!(
        pairs(&super::HOA_SALIENT_SUBBANDS),
        legacy(subbands.tables.iter().collect())
    );
    assert_eq!(
        pairs(&super::HOA_SALIENT_PARTITIONS),
        legacy(partition.tables.iter().map(|t| &t.grid).collect())
    );
}

#[test]
fn hoa_source_layouts_and_file_identities_match_the_json_loader() {
    #[derive(Deserialize)]
    struct LayoutEntry {
        tag: u32,
        channel_labels: Vec<u32>,
        lfe_indices: Vec<usize>,
        matrix_id: String,
        matrix_columns: usize,
        matrix_available: bool,
    }
    #[derive(Deserialize)]
    struct Format {
        format_profile: String,
        format_sha256: String,
        layouts: Vec<LayoutEntry>,
        matrices: BTreeMap<String, Vec<u32>>,
        accepted_layout_tags: Vec<u32>,
    }
    let format: Format = serde_json::from_str(&data("hoa-source-layout-format-v1.json")).unwrap();
    assert_eq!(
        format.format_profile,
        crate::frame::HOA_SOURCE_LAYOUT_PROFILE
    );
    assert_eq!(
        crate::frame::hoa_source_layout_format_sha256(),
        format.format_sha256
    );
    assert_eq!(
        super::HOA_SOURCE_ACCEPTED_TAGS,
        format.accepted_layout_tags.as_slice()
    );
    assert_eq!(super::HOA_SOURCE_LAYOUTS.len(), format.layouts.len());
    for (actual, expected) in super::HOA_SOURCE_LAYOUTS.iter().zip(&format.layouts) {
        assert_eq!(actual.tag, expected.tag);
        assert_eq!(actual.channel_labels, expected.channel_labels.as_slice());
        assert_eq!(actual.lfe_indices, expected.lfe_indices.as_slice());
        assert_eq!(actual.matrix_id, expected.matrix_id);
        assert_eq!(
            actual.matrix,
            format.matrices[&expected.matrix_id].as_slice()
        );
        assert_eq!(actual.matrix_columns, expected.matrix_columns);
        assert_eq!(actual.matrix_available, expected.matrix_available);
    }
    assert_eq!(
        crate::frame::hoa_transport_format_sha256(),
        file_sha256("hoa-transports-format-v1.json")
    );
    assert_eq!(
        crate::frame::hoa_static_remapping_format_sha256(),
        file_sha256("hoa-static-remapping-format-v1.json")
    );
}

#[test]
fn hoa_salient_dictionaries_match_the_packed_json_loader() {
    #[derive(Deserialize)]
    struct SharedMode {
        group_indices: Vec<usize>,
        signs: bool,
        matrix_indices: Vec<usize>,
    }
    #[derive(Deserialize)]
    struct Shared {
        modes: Vec<SharedMode>,
        groups: Vec<Vec<usize>>,
        matrix_encoding: String,
        matrices_f32: Vec<String>,
    }
    #[derive(Deserialize)]
    struct StoredMode {
        codebooks: Vec<String>,
    }
    #[derive(Deserialize)]
    struct Stored {
        format_profile: String,
        tables_sha256: String,
        codebook_encoding: String,
        modes: Vec<StoredMode>,
    }
    let mut tries = 0;
    for order in 1usize..=10 {
        let coefficients = (order + 1).pow(2);
        let shared: Shared =
            serde_json::from_str(&data(&format!("hoa-salient-order{order}-shared-v1.json")))
                .unwrap();
        assert_eq!(shared.matrix_encoding, super::packed::MATRIX_ENCODING);
        let matrices: Vec<Vec<u32>> = shared
            .matrices_f32
            .iter()
            .map(|hex| super::packed::matrix(hex, coefficients * coefficients).unwrap())
            .collect();
        for precision in 6u8..=9 {
            let (file, profile) = match (order, precision) {
                (3, 6) => (
                    "hoa-salient-format-v1.json".to_owned(),
                    "apac-hoa-salient-format-v1".to_owned(),
                ),
                (_, 6) => (
                    format!("hoa-salient-order{order}-format-v1.json"),
                    format!("apac-hoa-salient-order{order}-format-v1"),
                ),
                _ => (
                    format!("hoa-salient-order{order}-q{precision}-format-v1.json"),
                    format!("apac-hoa-salient-order{order}-q{precision}-format-v1"),
                ),
            };
            let stored: Stored = serde_json::from_str(&data(&file)).unwrap();
            assert_eq!(stored.format_profile, profile);
            assert_eq!(stored.codebook_encoding, super::packed::CODEBOOK_ENCODING);
            let constants = &super::SALIENT_CONSTANTS[(order - 1) * 4 + usize::from(precision - 6)];
            assert_eq!(
                (constants.coefficients, constants.precision),
                (coefficients, precision)
            );
            assert_eq!(constants.format.tables_sha256, stored.tables_sha256);
            assert_eq!(constants.format.modes.len(), 6);
            for (mode, (actual, common)) in
                constants.format.modes.iter().zip(&shared.modes).enumerate()
            {
                assert_eq!(actual.signs, common.signs);
                let groups: Vec<&[usize]> = common
                    .group_indices
                    .iter()
                    .map(|&i| shared.groups[i].as_slice())
                    .collect();
                assert_eq!(actual.groups, groups.as_slice());
                let expected: Vec<&[u32]> = common
                    .matrix_indices
                    .iter()
                    .map(|&i| matrices[i].as_slice())
                    .collect();
                assert_eq!(actual.matrices_f32, expected.as_slice());
                let books = &stored.modes[mode].codebooks;
                assert_eq!(constants.tries[mode].len(), books.len());
                for (trie, hex) in constants.tries[mode].iter().zip(books) {
                    let book = super::packed::codebook(hex, precision).unwrap();
                    let codes: Vec<u32> = book.iter().map(|v| v.1).collect();
                    let bits: Vec<usize> = book.iter().map(|v| v.0).collect();
                    assert_trie(trie, &codes, &bits);
                    assert_eq!(
                        trie.nodes(),
                        super::trie_build::build(&codes, &bits).as_slice()
                    );
                    tries += 1;
                }
            }
        }
    }
    assert_eq!(tries, 320);
}

#[test]
fn hoa_salient_math_and_normalizations_match_the_json_loader() {
    #[derive(Deserialize)]
    struct Math {
        numeric_profile: String,
        tables_sha256: String,
        azimuth_f64: Vec<[u64; 2]>,
        elevation_f64: Vec<[u64; 2]>,
        roots_f64: [u64; 7],
    }
    #[derive(Deserialize)]
    struct Expanded {
        numeric_profile: String,
        tables_sha256: String,
        normalizations_f64: Vec<Vec<u64>>,
    }
    let math: Math = serde_json::from_str(&data("hoa-salient-math-v1.json")).unwrap();
    assert_eq!(math.numeric_profile, "apac-hoa-salient-math-v1");
    assert_eq!(crate::frame::hoa_salient_math_sha256(), math.tables_sha256);
    let actual = &super::SALIENT_MATH;
    assert_eq!(actual.math_sha, math.tables_sha256);
    let pairs = |v: &[[f64; 2]]| v.iter().map(|p| p.map(f64::to_bits)).collect::<Vec<_>>();
    assert_eq!(pairs(actual.azimuth), math.azimuth_f64);
    assert_eq!(pairs(actual.elevation), math.elevation_f64);
    assert_eq!(actual.roots.map(f64::to_bits), math.roots_f64);
    let expanded: Expanded =
        serde_json::from_str(&data("hoa-expanded-orders-math-v1.json")).unwrap();
    assert_eq!(expanded.numeric_profile, "apac-hoa-expanded-orders-math-v1");
    assert_eq!(super::SALIENT_EXPANDED_MATH_SHA256, expanded.tables_sha256);
    assert_eq!(
        crate::frame::hoa_expanded_math_sha256(),
        expanded.tables_sha256
    );
    let rows: Vec<Vec<u64>> = super::SALIENT_EXPANDED_NORMALIZATIONS
        .iter()
        .map(|r| r.to_vec())
        .collect();
    assert_eq!(rows, expanded.normalizations_f64);
}
