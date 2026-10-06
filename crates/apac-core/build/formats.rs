//! Shared-configuration, DRC and HOA format tables other than the salient
//! dictionaries, plus the file-byte identities of format descriptions.
use crate::emit::Output;
use crate::{data_bytes, data_json};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

fn file_sha256(name: &str) -> String {
    format!("{:x}", Sha256::digest(data_bytes(name)))
}

fn usize_list(values: &[usize]) -> String {
    let items: Vec<String> = values.iter().map(|v| v.to_string()).collect();
    format!("&[{}]", items.join(", "))
}

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
struct SharedConfig {
    format_profile: String,
    format_sha256: String,
    rates: Vec<Rate>,
    offset_arrays: BTreeMap<String, Vec<usize>>,
    profiles: Vec<Profile>,
}

/// Rate buckets and band offsets; the two legacy keys name the SQ tables.
pub fn shared_config(out: &mut Output) {
    let format: SharedConfig = data_json("hoa-shared-config-format-v1.json");
    assert_eq!(format.format_profile, "apac-hoa-shared-configuration-v1");
    let rates: Vec<u64> = format.rates.iter().map(|r| r.sample_rate).collect();
    assert_eq!(
        rates,
        [
            96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350
        ]
    );
    let mut arrays = BTreeMap::new();
    for (i, (key, values)) in format.offset_arrays.iter().enumerate() {
        assert_eq!(values.first(), Some(&0));
        assert!(matches!(values.last(), Some(128 | 1024)));
        assert!(values.windows(2).all(|p| p[0] < p[1]));
        let name = out.array(false, &format!("SFB_OFFSETS_{i}"), "usize", values);
        arrays.insert(key.clone(), name);
    }
    let offsets = |key: &str| match key {
        "legacy-long" => "SQ_LONG_OFFSETS".to_owned(),
        "legacy-short" => "SQ_SHORT_OFFSETS".to_owned(),
        _ => arrays.get(key).expect("declared offset array").clone(),
    };
    out.array(
        true,
        "SFB_RATES",
        "crate::tables::SfbRate",
        format.rates.iter().map(|r| {
            format!(
                "crate::tables::SfbRate {{ sample_rate: {}, sfb_rate: {}, long: &{}, short: &{}, tns_long_limit: {}, tns_short_limit: {} }}",
                r.sample_rate,
                r.sfb_rate,
                offsets(&r.long),
                offsets(&r.short),
                r.tns_long_limit,
                r.tns_short_limit
            )
        }),
    );
    out.array(
        true,
        "SFB_PROFILES",
        "crate::tables::SfbProfile",
        format.profiles.iter().map(|p| {
            let levels: Vec<String> = p
                .levels
                .iter()
                .map(|level| {
                    let limits: Vec<String> = level
                        .iter()
                        .map(|l| {
                            format!(
                                "crate::tables::SfbLimit {{ kind: {}, maximum_channels: {} }}",
                                l.r#type, l.maximum_channels
                            )
                        })
                        .collect();
                    format!("&[{}]", limits.join(", "))
                })
                .collect();
            let tags: Vec<String> = p.layout_tags.iter().map(|t| t.to_string()).collect();
            format!(
                "crate::tables::SfbProfile {{ profile: {}, levels: &[{}], layout_tags: &[{}] }}",
                p.profile,
                levels.join(", "),
                tags.join(", ")
            )
        }),
    );
    out.str_const("SFB_FORMAT_SHA256", &format.format_sha256);
}

#[derive(Deserialize)]
struct Code {
    width: usize,
    code: u16,
    value: i32,
}
#[derive(Deserialize)]
struct SharedDrc {
    format_profile: String,
    format_sha256: String,
    clipping: Vec<Code>,
    slopes: Vec<Code>,
}

pub fn shared_drc(out: &mut Output) {
    let tables: SharedDrc = data_json("hoa-shared-drc-format-v1.json");
    assert_eq!(tables.format_profile, "apac-hoa-shared-drc-syntax-v1");
    let codes = |codes: &[Code]| {
        codes
            .iter()
            .map(|c| {
                format!(
                    "crate::tables::DrcCode {{ width: {}, code: {}, value: {} }}",
                    c.width, c.code, c.value
                )
            })
            .collect::<Vec<_>>()
    };
    out.array(
        true,
        "DRC_CLIPPING",
        "crate::tables::DrcCode",
        codes(&tables.clipping),
    );
    out.array(
        true,
        "DRC_SLOPES",
        "crate::tables::DrcCode",
        codes(&tables.slopes),
    );
    out.str_const("DRC_SHARED_FORMAT_SHA256", &tables.format_sha256);
}

#[derive(Deserialize)]
struct ProfileEntry {
    profile_id: u8,
    maximum_output_channels_by_level: Vec<u64>,
}
#[derive(Deserialize)]
struct ProfileTable {
    profiles: Vec<ProfileEntry>,
}

pub fn hoa_profiles(out: &mut Output) {
    let table: ProfileTable = data_json("hoa-profile-levels-v1.json");
    out.array(
        true,
        "HOA_PROFILE_LIMITS",
        "crate::tables::HoaProfileLimit",
        table.profiles.iter().map(|p| {
            let limits: Vec<String> = p
                .maximum_output_channels_by_level
                .iter()
                .map(|v| v.to_string())
                .collect();
            format!(
                "crate::tables::HoaProfileLimit {{ profile_id: {}, maximum_output_channels_by_level: &[{}] }}",
                p.profile_id,
                limits.join(", ")
            )
        }),
    );
}

#[derive(Deserialize)]
struct Ambient {
    numeric_profile: String,
    format_profile: String,
    format_sha256: String,
    tables_sha256: String,
    decoder_matrices_f64: [[u64; 16]; 3],
}

pub fn hoa_ambient(out: &mut Output) {
    let value: Ambient = data_json("hoa-static-ambient-tables-v1.json");
    assert_eq!(value.numeric_profile, "apac-hoa-static-ambient-math-v1");
    assert_eq!(value.format_profile, "apac-hoa-ambient-transform-format-v1");
    assert!(
        value
            .decoder_matrices_f64
            .iter()
            .flatten()
            .all(|&word| f64::from_bits(word).abs() == 0.5)
    );
    out.array(
        true,
        "HOA_AMBIENT_MATRICES",
        "[u64; 16]",
        value.decoder_matrices_f64.iter().map(|row| {
            let words: Vec<String> = row.iter().map(|w| format!("0x{w:016x}")).collect();
            format!("[{}]", words.join(", "))
        }),
    );
    out.str_const("HOA_AMBIENT_FORMAT_SHA256", &value.format_sha256);
    out.str_const("HOA_AMBIENT_MATH_SHA256", &value.tables_sha256);
}

#[derive(Deserialize)]
struct ControlGrid {
    method: usize,
    subbands: usize,
    long_ends: Vec<usize>,
}
#[derive(Deserialize)]
struct Controls {
    format_profile: String,
    format_sha256: String,
    mean_coefficients_f32: Vec<u32>,
    mean_sha256: String,
    source: ControlSource,
    tables: Vec<ControlGrid>,
}

#[derive(Deserialize)]
struct ControlSource {
    mean_replacement: MeanReplacement,
}

#[derive(Deserialize)]
struct MeanReplacement {
    source_file: String,
    source_sha256: String,
    mean_sha256: String,
}

#[derive(Deserialize)]
struct MeasuredMeans {
    schema_version: u32,
    profile: String,
    count: usize,
    storage: String,
    mean_sha256: String,
    mean_coefficients_f32: Vec<u32>,
}

pub fn hoa_controls(out: &mut Output) {
    const SOURCE_FILE: &str = "hoa-spatial-means-measured-v1.json";
    const SOURCE_SHA256: &str = "e5fb483ca3eb0e109155e2f7282b4bd3a51371489e5bfa55a9f8e6ea7e938483";
    const WORDS_SHA256: &str = "299576ce0a06ba6165b2a7ee1485d7211f9bed94b7e5936ce7ec51c5b4f30c42";
    let raw = data_bytes(SOURCE_FILE);
    assert_eq!(format!("{:x}", Sha256::digest(&raw)), SOURCE_SHA256);
    let means: MeasuredMeans = serde_json::from_slice(&raw).expect("measured HOA spatial means");
    assert_eq!(means.schema_version, 1);
    assert_eq!(means.profile, "apac-hoa-spatial-means-measured-v1");
    assert_eq!(means.storage, "acn-order-float32-bits");
    assert_eq!(means.count, 121);
    assert_eq!(means.mean_coefficients_f32.len(), means.count);
    assert!(
        means
            .mean_coefficients_f32
            .iter()
            .all(|&w| f32::from_bits(w).is_finite())
    );
    let mut digest = Sha256::new();
    for word in &means.mean_coefficients_f32 {
        digest.update(word.to_le_bytes());
    }
    assert_eq!(format!("{:x}", digest.finalize()), WORDS_SHA256);
    assert_eq!(means.mean_sha256, WORDS_SHA256);
    let f: Controls = data_json("hoa-spatial-controls-format-v1.json");
    assert_eq!(f.format_profile, "apac-hoa-spatial-controls-format-v1");
    assert_eq!(f.mean_coefficients_f32, means.mean_coefficients_f32);
    assert_eq!(f.mean_sha256, WORDS_SHA256);
    assert_eq!(f.source.mean_replacement.source_file, SOURCE_FILE);
    assert_eq!(f.source.mean_replacement.source_sha256, SOURCE_SHA256);
    assert_eq!(f.source.mean_replacement.mean_sha256, WORDS_SHA256);
    assert_eq!(f.tables.len(), 48);
    for (i, g) in f.tables.iter().enumerate() {
        assert_eq!((g.method, g.subbands), (i / 16, i % 16 + 1));
        assert_eq!(g.long_ends.len(), g.subbands);
        assert_eq!(g.long_ends.last(), Some(&1024));
        assert!(g.long_ends[0] > 0 && g.long_ends.windows(2).all(|w| w[0] < w[1]));
    }
    out.array(
        true,
        "HOA_CONTROL_MEANS",
        "u32",
        &means.mean_coefficients_f32,
    );
    out.array(
        true,
        "HOA_CONTROL_GRIDS",
        "crate::tables::HoaControlGrid",
        f.tables.iter().map(|g| {
            format!(
                "crate::tables::HoaControlGrid {{ long_ends: {} }}",
                usize_list(&g.long_ends)
            )
        }),
    );
    out.str_const("HOA_CONTROLS_FORMAT_SHA256", &f.format_sha256);
    out.str_const(
        "HOA_FRAME_CONFIGURATION_STATE_SHA256",
        &file_sha256("hoa-frame-configuration-state-v2.json"),
    );
}

#[derive(Deserialize)]
struct Dynamic {
    format_profile: String,
    format_sha256: String,
    internal_slots: usize,
    output_coefficients: usize,
    subbands: usize,
    long_ends: [[usize; 8]; 3],
    short_ends: [[usize; 8]; 3],
}
#[derive(Deserialize)]
struct DynamicSubbands {
    subbands: usize,
    long_ends: [Vec<usize>; 3],
    short_ends: [Vec<usize>; 3],
}
#[derive(Deserialize)]
struct DynamicExtended {
    format_profile: String,
    format_sha256: String,
    base_format_sha256: String,
    internal_slots: usize,
    output_coefficients: usize,
    wire_mapping_groups: usize,
    tables: Vec<DynamicSubbands>,
}

/// Returns the v1 and v2 format digests for the salient-subband cross-checks.
pub fn hoa_dynamic(out: &mut Output) -> (String, String) {
    let value: Dynamic = data_json("hoa-dynamic-format-v1.json");
    assert_eq!(value.format_profile, "apac-hoa-dynamic-selection-format-v1");
    assert_eq!(
        (
            value.internal_slots,
            value.output_coefficients,
            value.subbands
        ),
        (9, 16, 8)
    );
    for (long, short) in value.long_ends.iter().zip(value.short_ends.iter()) {
        assert_eq!(long[7], 1024);
        assert!(long.windows(2).all(|w| w[0] < w[1]));
        assert!(
            long.iter()
                .zip(short)
                .all(|(&a, &b)| a % 8 == 0 && a / 8 == b)
        );
    }
    let rows = |rows: &[[usize; 8]; 3]| rows.iter().map(|r| format!("{r:?}")).collect::<Vec<_>>();
    out.array(
        true,
        "HOA_DYNAMIC_LONG_ENDS",
        "[usize; 8]",
        rows(&value.long_ends),
    );
    out.array(
        true,
        "HOA_DYNAMIC_SHORT_ENDS",
        "[usize; 8]",
        rows(&value.short_ends),
    );
    out.str_const("HOA_DYNAMIC_FORMAT_SHA256", &value.format_sha256);

    let extended: DynamicExtended = data_json("hoa-dynamic-format-v2.json");
    assert_eq!(
        extended.format_profile,
        "apac-hoa-dynamic-selection-format-v2"
    );
    assert_eq!(extended.base_format_sha256, value.format_sha256);
    assert_eq!(
        (
            extended.internal_slots,
            extended.output_coefficients,
            extended.wire_mapping_groups,
            extended.tables.len()
        ),
        (9, 16, 8, 7)
    );
    for (i, table) in extended.tables.iter().enumerate() {
        assert_eq!(table.subbands, i + 1);
        for (long, short) in table.long_ends.iter().zip(&table.short_ends) {
            assert_eq!((long.len(), short.len()), (i + 1, i + 1));
            assert_eq!(long.last(), Some(&1024));
            assert!(long[0] > 0 && long.windows(2).all(|w| w[0] < w[1]));
            assert!(
                long.iter()
                    .zip(short)
                    .all(|(&a, &b)| a % 8 == 0 && a / 8 == b)
            );
        }
    }
    out.array(
        true,
        "HOA_DYNAMIC_SUBBANDS",
        "crate::tables::HoaDynamicSubbands",
        extended.tables.iter().map(|t| {
            let lists = |ends: &[Vec<usize>; 3]| {
                let items: Vec<String> = ends.iter().map(|e| usize_list(e)).collect();
                format!("[{}]", items.join(", "))
            };
            format!(
                "crate::tables::HoaDynamicSubbands {{ long_ends: {}, short_ends: {} }}",
                lists(&t.long_ends),
                lists(&t.short_ends)
            )
        }),
    );
    out.str_const(
        "HOA_DYNAMIC_EXTENDED_FORMAT_SHA256",
        &extended.format_sha256,
    );
    out.str_const(
        "HOA_DYNAMIC_DOMAINS_FORMAT_SHA256",
        &file_sha256("hoa-dynamic-domains-format-v1.json"),
    );
    (value.format_sha256, extended.format_sha256)
}

#[derive(Deserialize)]
struct Grid {
    subbands: usize,
    long_ends: Vec<usize>,
    short_ends: Vec<usize>,
}
#[derive(Deserialize)]
struct SalientSubbands {
    format_profile: String,
    format_sha256: String,
    method: usize,
    components: usize,
    maximum_subbands: usize,
    dynamic_format_v1_sha256: String,
    dynamic_format_v2_sha256: String,
    tables: Vec<Grid>,
}
#[derive(Deserialize)]
struct MethodGrid {
    method: usize,
    #[serde(flatten)]
    grid: Grid,
}
#[derive(Deserialize)]
struct SalientPartition {
    format_profile: String,
    format_sha256: String,
    methods: Vec<usize>,
    components: usize,
    maximum_subbands: usize,
    dynamic_format_v1_sha256: String,
    dynamic_format_v2_sha256: String,
    salient_format_v1_sha256: String,
    tables: Vec<MethodGrid>,
}

fn check_grid(grid: &Grid) {
    assert_eq!(grid.long_ends.len(), grid.subbands);
    assert_eq!(grid.short_ends.len(), grid.subbands);
    assert!(grid.long_ends[0] > 0 && grid.long_ends.windows(2).all(|w| w[0] < w[1]));
    assert_eq!(grid.long_ends.last(), Some(&1024));
    assert!(
        grid.long_ends
            .iter()
            .zip(&grid.short_ends)
            .all(|(&a, &b)| a % 8 == 0 && a / 8 == b)
    );
}

fn grid(grid: &Grid) -> String {
    format!(
        "crate::tables::HoaSubbandGrid {{ long_ends: {}, short_ends: {} }}",
        usize_list(&grid.long_ends),
        usize_list(&grid.short_ends)
    )
}

pub fn hoa_salient_subbands(out: &mut Output, dynamic: &(String, String)) {
    let value: SalientSubbands = data_json("hoa-salient-subbands-format-v1.json");
    assert_eq!(value.format_profile, "apac-hoa-salient-subbands-format-v1");
    assert_eq!(
        (
            value.method,
            value.components,
            value.maximum_subbands,
            value.tables.len()
        ),
        (0, 5, 16, 8)
    );
    assert_eq!(value.dynamic_format_v1_sha256, dynamic.0);
    assert_eq!(value.dynamic_format_v2_sha256, dynamic.1);
    for (i, t) in value.tables.iter().enumerate() {
        assert_eq!(t.subbands, i + 9);
        check_grid(t);
    }
    out.array(
        true,
        "HOA_SALIENT_SUBBANDS",
        "crate::tables::HoaSubbandGrid",
        value.tables.iter().map(grid),
    );
    out.str_const("HOA_SALIENT_SUBBANDS_FORMAT_SHA256", &value.format_sha256);

    let partition: SalientPartition = data_json("hoa-salient-subbands-format-v2.json");
    assert_eq!(
        partition.format_profile,
        "apac-hoa-salient-subbands-format-v2"
    );
    assert_eq!(partition.methods, [1, 2]);
    assert_eq!(
        (
            partition.components,
            partition.maximum_subbands,
            partition.tables.len()
        ),
        (5, 16, 16)
    );
    assert_eq!(partition.dynamic_format_v1_sha256, dynamic.0);
    assert_eq!(partition.dynamic_format_v2_sha256, dynamic.1);
    assert_eq!(partition.salient_format_v1_sha256, value.format_sha256);
    for (i, row) in partition.tables.iter().enumerate() {
        assert_eq!((row.method, row.grid.subbands), (i / 8 + 1, i % 8 + 9));
        check_grid(&row.grid);
    }
    out.array(
        true,
        "HOA_SALIENT_PARTITIONS",
        "crate::tables::HoaSubbandGrid",
        partition.tables.iter().map(|row| grid(&row.grid)),
    );
    out.str_const(
        "HOA_SALIENT_PARTITION_FORMAT_SHA256",
        &partition.format_sha256,
    );
}

#[derive(Deserialize)]
struct LayoutEntry {
    tag: u32,
    channel_labels: Vec<u32>,
    lfe_indices: Vec<usize>,
    matrix_id: String,
    matrix_rows: usize,
    matrix_columns: usize,
    matrix_available: bool,
}
#[derive(Deserialize)]
struct SourceFormat {
    format_profile: String,
    format_sha256: String,
    layouts: Vec<LayoutEntry>,
    matrices: BTreeMap<String, Vec<u32>>,
    accepted_layout_tags: Vec<u32>,
}

pub fn hoa_source(out: &mut Output) {
    let value: SourceFormat = data_json("hoa-source-layout-format-v1.json");
    assert_eq!(value.format_profile, "apac-hoa-source-layout-format-v1");
    let mut matrices = BTreeMap::new();
    for (i, (id, words)) in value.matrices.iter().enumerate() {
        let name = out.array(false, &format!("HOA_SOURCE_MATRIX_{i}"), "u32", words);
        matrices.insert(id.clone(), name);
    }
    for entry in &value.layouts {
        assert_eq!(entry.channel_labels.len(), (entry.tag & 0xffff) as usize);
        assert_eq!(
            entry.matrix_rows + entry.lfe_indices.len(),
            entry.channel_labels.len()
        );
        assert_eq!(
            value.matrices[&entry.matrix_id].len(),
            entry.matrix_rows * entry.matrix_columns
        );
        assert!(
            value.matrices[&entry.matrix_id]
                .iter()
                .all(|&bits| f32::from_bits(bits).is_finite())
        );
    }
    out.array(
        true,
        "HOA_SOURCE_LAYOUTS",
        "crate::tables::HoaSourceLayout",
        value.layouts.iter().map(|e| {
            let labels: Vec<String> = e.channel_labels.iter().map(|v| v.to_string()).collect();
            format!(
                "crate::tables::HoaSourceLayout {{ tag: {}, channel_labels: &[{}], lfe_indices: {}, matrix_id: {:?}, matrix: &{}, matrix_columns: {}, matrix_available: {} }}",
                e.tag,
                labels.join(", "),
                usize_list(&e.lfe_indices),
                e.matrix_id,
                matrices[&e.matrix_id],
                e.matrix_columns,
                e.matrix_available
            )
        }),
    );
    out.array(
        true,
        "HOA_SOURCE_ACCEPTED_TAGS",
        "u32",
        &value.accepted_layout_tags,
    );
    out.str_const("HOA_SOURCE_FORMAT_SHA256", &value.format_sha256);
}

/// Descriptions whose identity is the SHA-256 of the file bytes.
pub fn file_identities(out: &mut Output) {
    out.str_const(
        "HOA_TRANSPORT_FORMAT_SHA256",
        &file_sha256("hoa-transports-format-v1.json"),
    );
    out.str_const(
        "HOA_REMAPPING_FORMAT_SHA256",
        &file_sha256("hoa-static-remapping-format-v1.json"),
    );
}
