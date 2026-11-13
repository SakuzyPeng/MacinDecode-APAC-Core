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
    source: SharedConfigSource,
}

#[derive(Deserialize)]
struct SharedConfigSource {
    inputs: Vec<SharedConfigInput>,
}

#[derive(Deserialize)]
struct SharedConfigInput {
    file: String,
    sha256: String,
}

#[derive(Deserialize)]
struct LayoutProfile {
    profile_id: u8,
    layout_tags: Vec<u32>,
}

#[derive(Deserialize)]
struct MeasuredLayouts {
    schema_version: u32,
    profile: String,
    profiles: Vec<LayoutProfile>,
}

#[derive(Deserialize)]
struct MeasuredLayoutLevels {
    profile: String,
    profile_id: u8,
    component_type: u8,
    accepted_layout_tags_by_level: Vec<Vec<u32>>,
}

/// Rate buckets and band offsets; the two legacy keys name the SQ tables.
pub fn shared_config(out: &mut Output) {
    let raw = data_bytes("hoa-shared-config-format-v2.json");
    assert_eq!(
        format!("{:x}", Sha256::digest(&raw)),
        "57a892784b33d99072047f44b46853ce6d6e455c6b4cb5aa13e7e58440ed0ed7",
        "shared configuration or provenance differs"
    );
    let format: SharedConfig = serde_json::from_slice(&raw).expect("shared configuration");
    for source in &format.source.inputs {
        assert_eq!(
            file_sha256(&source.file),
            source.sha256,
            "shared configuration source differs for {}",
            source.file
        );
    }
    let measured = measured_hoa_profiles();
    let channels: MeasuredProfiles = data_json("shared-channel-profile-levels-measured-v1.json");
    assert_eq!(channels.schema_version, 1);
    assert_eq!(
        channels.profile,
        "apac-shared-channel-profile-levels-measured-v1"
    );
    assert_eq!(channels.component_type, 0);
    let layouts: MeasuredLayouts = data_json("shared-layout-tags-measured-v1.json");
    assert_eq!(layouts.schema_version, 1);
    assert_eq!(layouts.profile, "apac-shared-layout-tags-measured-v1");
    let levels: MeasuredLayoutLevels = data_json("shared-profile31-layout-levels-measured-v1.json");
    assert_eq!(
        levels.profile,
        "apac-shared-profile31-layout-levels-measured-v1"
    );
    assert_eq!((levels.profile_id, levels.component_type), (31, 0));
    assert_eq!(layouts.profiles.len(), format.profiles.len());
    for profile in &format.profiles {
        let qualified = layouts
            .profiles
            .iter()
            .find(|row| row.profile_id == profile.profile)
            .expect("shared layout profile missing");
        assert_eq!(profile.layout_tags, qualified.layout_tags);
        for (level, limits) in profile.levels.iter().enumerate() {
            for limit in limits {
                let maximum = match limit.r#type {
                    0 if profile.profile == 31 => levels.accepted_layout_tags_by_level[level]
                        .iter()
                        .map(|tag| u64::from(tag & 65535))
                        .max()
                        .expect("nonempty measured layout domain"),
                    0 | 2 => {
                        let table = if limit.r#type == 0 {
                            &channels.table
                        } else {
                            &measured
                        };
                        table
                            .profiles
                            .iter()
                            .find(|row| row.profile_id == profile.profile)
                            .and_then(|row| row.maximum_output_channels_by_level.get(level))
                            .copied()
                            .expect("shared profile measurement missing")
                    }
                    _ => panic!("unsupported component in shared runtime tables"),
                };
                assert_eq!(
                    limit.maximum_channels, maximum,
                    "shared profile copy differs"
                );
            }
        }
    }
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

#[derive(Debug, Deserialize, PartialEq, Eq)]
struct Code {
    width: usize,
    code: u16,
    value: i32,
}
#[derive(Deserialize)]
struct SharedDrc {
    format_profile: String,
    format_sha256: String,
    normal: Vec<Code>,
    clipping: Vec<Code>,
    slopes: Vec<Code>,
    source: MeasurementCopySource,
}
#[derive(Deserialize)]
struct MeasurementCopySource {
    measurement_file: String,
    measurement_sha256: String,
}
#[derive(Deserialize)]
struct MeasuredDrc {
    profile: String,
    normal: Vec<Code>,
    clipping: Vec<Code>,
    slopes: Vec<Code>,
}

pub fn shared_drc(out: &mut Output) {
    let name = "hoa-shared-drc-codebooks-measured-v1.json";
    let mut measured: MeasuredDrc = data_json(name);
    assert_eq!(
        measured.profile,
        "apac-hoa-shared-drc-codebooks-measured-v1"
    );
    let mut copy: serde_json::Value = data_json("hoa-shared-drc-format-v1.json");
    let tables: SharedDrc = serde_json::from_value(copy.clone()).expect("shared DRC copy schema");
    assert_eq!(tables.format_profile, "apac-hoa-shared-drc-syntax-v1");
    assert_eq!(tables.source.measurement_file, name);
    assert_eq!(tables.source.measurement_sha256, file_sha256(name));
    assert_eq!(tables.normal, measured.normal);
    assert_eq!(tables.clipping, measured.clipping);
    assert_eq!(tables.slopes, measured.slopes);
    copy.as_object_mut().unwrap().remove("format_sha256");
    assert_eq!(
        tables.format_sha256,
        format!("{:x}", Sha256::digest(serde_json::to_vec(&copy).unwrap())),
        "shared DRC format digest differs"
    );
    measured.normal.sort_by_key(|row| row.value);
    assert_eq!(
        measured
            .normal
            .iter()
            .map(|row| row.value)
            .collect::<Vec<_>>(),
        (-16..=8).collect::<Vec<_>>()
    );
    out.array(
        true,
        "DRC_NORMAL_GAIN_CODES",
        "(u16, usize)",
        measured
            .normal
            .iter()
            .map(|row| format!("({}, {})", row.code, row.width)),
    );
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
        codes(&measured.clipping),
    );
    out.array(
        true,
        "DRC_SLOPES",
        "crate::tables::DrcCode",
        codes(&measured.slopes),
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

#[derive(Deserialize)]
struct MeasuredProfiles {
    schema_version: u32,
    profile: String,
    component_type: u8,
    #[serde(flatten)]
    table: ProfileTable,
}

fn measured_hoa_profiles() -> ProfileTable {
    let measured: MeasuredProfiles = data_json("hoa-profile-levels-measured-v1.json");
    assert_eq!(measured.schema_version, 1);
    assert_eq!(measured.profile, "apac-hoa-profile-levels-measured-v1");
    assert_eq!(measured.component_type, 2);
    measured.table
}

pub fn hoa_profiles(out: &mut Output) {
    let table = measured_hoa_profiles();
    let copy: ProfileTable = data_json("hoa-profile-levels-v1.json");
    assert_eq!(table.profiles.len(), copy.profiles.len());
    for (measured, original) in table.profiles.iter().zip(&copy.profiles) {
        assert_eq!(measured.profile_id, original.profile_id);
        assert_eq!(
            measured.maximum_output_channels_by_level,
            original.maximum_output_channels_by_level
        );
    }
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

#[derive(Deserialize)]
struct AmbientRows {
    schema_version: u32,
    profile: String,
    row_orders: [[u8; 4]; 3],
    source: AmbientRowSource,
}

#[derive(Deserialize)]
struct AmbientRowSource {
    qualification: AmbientRowQualification,
}

#[derive(Deserialize)]
struct AmbientRowQualification {
    passed: bool,
    basis_checks: usize,
    heldout_checks: usize,
    nonzero_pcm_words_bit_identical: bool,
    zero_equivalence: AmbientZeroQualification,
}

#[derive(Deserialize)]
struct AmbientZeroQualification {
    profile: String,
    consumers_sha256: BTreeMap<String, String>,
}

pub fn hoa_ambient(out: &mut Output) {
    let measured: AmbientRows = data_json("hoa-ambient-row-orders-measured-v1.json");
    assert_eq!(measured.schema_version, 1);
    assert_eq!(measured.profile, "apac-hoa-ambient-row-orders-measured-v1");
    let qualification = measured.source.qualification;
    assert!(qualification.passed && qualification.nonzero_pcm_words_bit_identical);
    assert_eq!(
        (qualification.basis_checks, qualification.heldout_checks),
        (48, 36)
    );
    assert_eq!(
        qualification.zero_equivalence.profile,
        "apac-ambient-pcm-zero-equivalence-v1"
    );
    assert_eq!(qualification.zero_equivalence.consumers_sha256.len(), 2);
    for name in ["src/frame/hoa_ambient.rs", "src/synthesis/mod.rs"] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(name);
        println!("cargo:rerun-if-changed={}", path.display());
        let raw = std::fs::read(path).expect("reviewed ambient zero consumer");
        assert_eq!(
            format!("{:x}", Sha256::digest(raw)),
            qualification.zero_equivalence.consumers_sha256[name],
            "ambient zero-equivalence consumer changed and needs qualification"
        );
    }
    let matrices = measured.row_orders.map(|order| {
        let mut sorted = order;
        sorted.sort_unstable();
        assert_eq!(sorted, [0, 1, 2, 3]);
        core::array::from_fn::<_, 16, _>(|index| {
            let parity = (order[index % 4] & (index / 4) as u8).count_ones() % 2;
            (if parity == 0 { 0.5_f64 } else { -0.5_f64 }).to_bits()
        })
    });
    let value: Ambient = data_json("hoa-static-ambient-tables-v1.json");
    assert_eq!(value.numeric_profile, "apac-hoa-static-ambient-math-v1");
    assert_eq!(value.format_profile, "apac-hoa-ambient-transform-format-v1");
    assert_eq!(
        value.decoder_matrices_f64, matrices,
        "ambient format copy differs from measured row order"
    );
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
        matrices.iter().map(|row| {
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

#[derive(Deserialize)]
struct MeasuredPartitions {
    schema_version: u32,
    profile: String,
    tables: Vec<MeasuredPartition>,
}

#[derive(Deserialize)]
struct MeasuredPartition {
    method: usize,
    subbands: usize,
    rounded: bool,
    long_ends: Vec<usize>,
    short_ends: Option<Vec<usize>>,
    short_frequency_major_ends: Option<Vec<usize>>,
}

fn measured_partitions() -> MeasuredPartitions {
    let value: MeasuredPartitions = data_json("hoa-spatial-partitions-measured-v1.json");
    assert_eq!(value.schema_version, 1);
    assert_eq!(value.profile, "apac-hoa-spatial-partitions-measured-v1");
    assert_eq!(value.tables.len(), 96);
    for method in 0..3 {
        for count in 1..=16 {
            for rounded in [false, true] {
                assert_eq!(
                    value
                        .tables
                        .iter()
                        .filter(|row| (row.method, row.subbands, row.rounded)
                            == (method, count, rounded))
                        .count(),
                    1
                );
                let row = partition_source(&value, method, count, rounded);
                assert_eq!(row.long_ends.len(), count);
                assert_eq!(row.long_ends.last(), Some(&1024));
                assert!(
                    row.long_ends[0] > 0 && row.long_ends.windows(2).all(|pair| pair[0] < pair[1])
                );
                if rounded {
                    let short = row.short_ends.as_ref().expect("measured short-window grid");
                    assert_eq!(short.len(), count);
                    assert!(row.long_ends.iter().zip(short).all(|(&a, &b)| a == b * 8));
                } else {
                    assert_eq!(
                        row.short_frequency_major_ends.as_ref(),
                        Some(&row.long_ends)
                    );
                }
            }
        }
    }
    value
}

fn partition_source(
    value: &MeasuredPartitions,
    method: usize,
    count: usize,
    rounded: bool,
) -> &MeasuredPartition {
    value
        .tables
        .iter()
        .find(|row| (row.method, row.subbands, row.rounded) == (method, count, rounded))
        .expect("qualified spatial partition")
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
    let mut f: Controls = data_json("hoa-spatial-controls-format-v1.json");
    let partitions = measured_partitions();
    for row in &mut f.tables {
        let measured = partition_source(&partitions, row.method, row.subbands, false);
        assert_eq!(
            row.long_ends, measured.long_ends,
            "unrounded spatial grid copy differs"
        );
        row.long_ends.clone_from(&measured.long_ends);
    }
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
        "62abfc59fede7c8a31ef7affa3fdbb8d68616bfba20166c294e972a44002ae21",
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
    let mut value: Dynamic = data_json("hoa-dynamic-format-v1.json");
    let partitions = measured_partitions();
    for method in 0..3 {
        let measured = partition_source(&partitions, method, 8, true);
        let short = measured
            .short_ends
            .as_ref()
            .expect("measured dynamic short grid");
        assert_eq!(value.long_ends[method].as_slice(), measured.long_ends);
        assert_eq!(value.short_ends[method].as_slice(), short);
        value.long_ends[method].copy_from_slice(&measured.long_ends);
        value.short_ends[method].copy_from_slice(short);
    }
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

    let mut extended: DynamicExtended = data_json("hoa-dynamic-format-v2.json");
    for row in &mut extended.tables {
        for method in 0..3 {
            let measured = partition_source(&partitions, method, row.subbands, true);
            let short = measured
                .short_ends
                .as_ref()
                .expect("measured dynamic short grid");
            assert_eq!(row.long_ends[method], measured.long_ends);
            assert_eq!(&row.short_ends[method], short);
            row.long_ends[method].clone_from(&measured.long_ends);
            row.short_ends[method].clone_from(short);
        }
    }
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
        "7cf30ebe6453bf6bfcc5d8bf3d353dc53c58e7adf8fd7b530b31ea0997be760a",
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

fn use_measured_grid(grid: &mut Grid, partitions: &MeasuredPartitions, method: usize) {
    let measured = partition_source(partitions, method, grid.subbands, true);
    let short = measured
        .short_ends
        .as_ref()
        .expect("measured salient short grid");
    assert_eq!(grid.long_ends, measured.long_ends);
    assert_eq!(&grid.short_ends, short);
    grid.long_ends.clone_from(&measured.long_ends);
    grid.short_ends.clone_from(short);
}

pub fn hoa_salient_subbands(out: &mut Output, dynamic: &(String, String)) {
    let mut value: SalientSubbands = data_json("hoa-salient-subbands-format-v1.json");
    let measured = measured_partitions();
    for row in &mut value.tables {
        use_measured_grid(row, &measured, 0);
    }
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

    let mut partition: SalientPartition = data_json("hoa-salient-subbands-format-v2.json");
    for row in &mut partition.tables {
        use_measured_grid(&mut row.grid, &measured, row.method);
    }
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
    source: SourceFormatSource,
}
#[derive(Deserialize)]
struct SourceFormatSource {
    measurement_file: String,
    measurement_sha256: String,
    acceptance_measurement_file: String,
    acceptance_measurement_sha256: String,
    complete_format_qualified: bool,
}
#[derive(Deserialize)]
struct SourceLayoutLabels {
    tag: u32,
    channel_labels: Vec<u32>,
    lfe_indices: Vec<usize>,
}
#[derive(Deserialize)]
struct MeasuredSourceAcceptance {
    profile: String,
    accepted_layout_tags: Vec<u32>,
    public_labels: Vec<SourceLayoutLabels>,
}

#[derive(Deserialize)]
struct MeasuredSourceMatrix {
    tag: u32,
    channel_labels: Vec<u32>,
    lfe_indices: Vec<usize>,
    matrix_rows: usize,
    matrix_columns: usize,
    matrix_f32: Vec<u32>,
}
#[derive(Deserialize)]
struct MeasuredSourceMatrices {
    profile: String,
    layouts: Vec<MeasuredSourceMatrix>,
    source: SourceMatrixSource,
}
#[derive(Deserialize)]
struct SourceMatrixSource {
    qualification: SourceMatrixQualification,
}
#[derive(Deserialize)]
struct SourceMatrixQualification {
    zero_equivalence: SourceMatrixZeroQualification,
}
#[derive(Deserialize)]
struct SourceMatrixZeroQualification {
    consumers_sha256: BTreeMap<String, String>,
}

pub fn hoa_source(out: &mut Output) {
    let name = "hoa-source-matrices-measured-v1.json";
    let measured: MeasuredSourceMatrices = data_json(name);
    assert_eq!(measured.profile, "apac-hoa-source-matrices-measured-v1");
    assert_eq!(measured.layouts.len(), 34);
    let consumers = measured
        .source
        .qualification
        .zero_equivalence
        .consumers_sha256;
    assert_eq!(consumers.len(), 3);
    for name in [
        "src/frame/hoa_source.rs",
        "src/frame/hoa_additive.rs",
        "src/synthesis/channels.rs",
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(name);
        println!("cargo:rerun-if-changed={}", path.display());
        let raw = std::fs::read(path).expect("reviewed source-matrix zero consumer");
        assert_eq!(
            format!("{:x}", Sha256::digest(raw)),
            consumers[name],
            "source-matrix zero-equivalence consumer changed and needs qualification"
        );
    }
    let mut copy: serde_json::Value = data_json("hoa-source-layout-format-v1.json");
    let value: SourceFormat = serde_json::from_value(copy.clone()).expect("source-layout format");
    copy.as_object_mut().unwrap().remove("format_sha256");
    assert_eq!(
        value.format_sha256,
        format!("{:x}", Sha256::digest(serde_json::to_vec(&copy).unwrap())),
        "source-layout format digest differs"
    );
    assert_eq!(value.format_profile, "apac-hoa-source-layout-format-v1");
    assert_eq!(value.layouts.len(), measured.layouts.len() + 2);
    assert_eq!(value.source.measurement_file, name);
    assert_eq!(value.source.measurement_sha256, file_sha256(name));
    let acceptance_name = "hoa-source-layout-acceptance-measured-v1.json";
    let acceptance: MeasuredSourceAcceptance = data_json(acceptance_name);
    assert_eq!(
        acceptance.profile,
        "apac-hoa-source-layout-acceptance-measured-v1"
    );
    assert_eq!(value.source.acceptance_measurement_file, acceptance_name);
    assert_eq!(
        value.source.acceptance_measurement_sha256,
        file_sha256(acceptance_name)
    );
    assert!(value.source.complete_format_qualified);
    assert_eq!(value.accepted_layout_tags, acceptance.accepted_layout_tags);
    assert!(!value.accepted_layout_tags.contains(&12058632));
    let mut tags = std::collections::BTreeSet::new();
    let mut source_words = BTreeMap::new();
    for entry in &value.layouts {
        assert!(tags.insert(entry.tag), "duplicate source-layout tag");
        assert_eq!(entry.channel_labels.len(), (entry.tag & 0xffff) as usize);
        assert_eq!(
            entry.matrix_rows + entry.lfe_indices.len(),
            entry.channel_labels.len()
        );
        let labels = acceptance
            .public_labels
            .iter()
            .find(|row| row.tag == entry.tag)
            .expect("qualified public source-layout labels are required");
        assert_eq!(entry.channel_labels, labels.channel_labels);
        assert_eq!(entry.lfe_indices, labels.lfe_indices);
        let source = measured.layouts.iter().find(|row| row.tag == entry.tag);
        let words = if entry.matrix_available {
            let source = source.expect("qualified source matrix is required");
            assert_eq!(source.channel_labels, entry.channel_labels);
            assert_eq!(source.lfe_indices, entry.lfe_indices);
            assert_eq!(source.matrix_rows, entry.matrix_rows);
            assert_eq!(source.matrix_columns, entry.matrix_columns);
            source.matrix_f32.as_slice()
        } else {
            assert!(source.is_none());
            assert!(matches!(entry.tag, 8060934 | 12058632));
            assert_eq!(entry.matrix_columns, 0);
            &[]
        };
        assert_eq!(value.matrices[&entry.matrix_id], words);
        assert_eq!(words.len(), entry.matrix_rows * entry.matrix_columns);
        assert!(words.iter().all(|&bits| f32::from_bits(bits).is_finite()));
        source_words.insert(&entry.matrix_id, words);
    }
    assert_eq!(source_words.len(), value.matrices.len());
    let mut matrices = BTreeMap::new();
    for (i, (id, words)) in source_words.iter().enumerate() {
        let name = out.array(
            false,
            &format!("HOA_SOURCE_MATRIX_{i}"),
            "u32",
            words.iter(),
        );
        matrices.insert((*id).clone(), name);
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
        &acceptance.accepted_layout_tags,
    );
    out.str_const("HOA_SOURCE_FORMAT_SHA256", &value.format_sha256);
}

/// Frozen compatibility identities. Private research descriptions are not build inputs.
pub fn file_identities(out: &mut Output) {
    out.str_const(
        "HOA_TRANSPORT_FORMAT_SHA256",
        "acf87582080a3b32ad51babe5cbe4bdadae2a925f2e463b4213dbe0f2e8a715f",
    );
    out.str_const(
        "HOA_REMAPPING_FORMAT_SHA256",
        "f6e38b2555edc0e89d2804bcd8287f4868ebd927241969671a3ff8cf2b5c7e72",
    );
}
