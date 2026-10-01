//! Per-component spatial grids; lower counts share the verified dynamic tables.
use serde::Deserialize;
use std::sync::OnceLock;

pub const SUBBAND_PROFILE: &str = "apac-hoa-salient-subbands-v1";
pub const PARTITION_PROFILE: &str = "apac-hoa-salient-partition-v1";
#[derive(Deserialize)]
struct Table {
    subbands: usize,
    long_ends: Vec<usize>,
    short_ends: Vec<usize>,
}
#[derive(Deserialize)]
struct Format {
    format_profile: String,
    format_sha256: String,
    method: usize,
    components: usize,
    maximum_subbands: usize,
    dynamic_format_v1_sha256: String,
    dynamic_format_v2_sha256: String,
    tables: Vec<Table>,
}
#[derive(Deserialize)]
struct MethodTable {
    method: usize,
    #[serde(flatten)]
    grid: Table,
}
#[derive(Deserialize)]
struct PartitionFormat {
    format_profile: String,
    format_sha256: String,
    methods: Vec<usize>,
    components: usize,
    maximum_subbands: usize,
    dynamic_format_v1_sha256: String,
    dynamic_format_v2_sha256: String,
    salient_format_v1_sha256: String,
    tables: Vec<MethodTable>,
}
fn partition_format() -> &'static PartitionFormat {
    static DATA: OnceLock<PartitionFormat> = OnceLock::new();
    DATA.get_or_init(|| {
        let value: PartitionFormat = serde_json::from_str(include_str!(
            "../../data/hoa-salient-subbands-format-v2.json"
        ))
        .expect("built-in spatial partition grids");
        assert_eq!(value.format_profile, "apac-hoa-salient-subbands-format-v2");
        assert_eq!(value.methods, [1, 2]);
        assert_eq!(
            (value.components, value.maximum_subbands, value.tables.len()),
            (5, 16, 16)
        );
        assert_eq!(
            value.dynamic_format_v1_sha256,
            super::hoa_dynamic::format_sha256(8)
        );
        assert_eq!(
            value.dynamic_format_v2_sha256,
            super::hoa_dynamic::format_sha256(1)
        );
        assert_eq!(value.salient_format_v1_sha256, format().format_sha256);
        for (i, row) in value.tables.iter().enumerate() {
            let grid = &row.grid;
            assert_eq!((row.method, grid.subbands), (i / 8 + 1, i % 8 + 9));
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
        value
    })
}
fn format() -> &'static Format {
    static DATA: OnceLock<Format> = OnceLock::new();
    DATA.get_or_init(|| {
        let value: Format = serde_json::from_str(include_str!(
            "../../data/hoa-salient-subbands-format-v1.json"
        ))
        .expect("built-in spatial subband grids");
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
        assert_eq!(
            value.dynamic_format_v1_sha256,
            super::hoa_dynamic::format_sha256(8)
        );
        assert_eq!(
            value.dynamic_format_v2_sha256,
            super::hoa_dynamic::format_sha256(1)
        );
        for (i, t) in value.tables.iter().enumerate() {
            assert_eq!(
                (t.subbands, t.long_ends.len(), t.short_ends.len()),
                (i + 9, i + 9, i + 9)
            );
            assert!(t.long_ends[0] > 0 && t.long_ends.windows(2).all(|w| w[0] < w[1]));
            assert_eq!(t.long_ends.last(), Some(&1024));
            assert!(
                t.long_ends
                    .iter()
                    .zip(&t.short_ends)
                    .all(|(&a, &b)| a % 8 == 0 && a / 8 == b)
            );
        }
        value
    })
}
pub(crate) fn format_sha256(method: u8) -> &'static str {
    assert!(method <= 2);
    if method == 0 {
        &format().format_sha256
    } else {
        &partition_format().format_sha256
    }
}
pub(super) fn boundaries(count: usize, method: u8, short: bool) -> &'static [usize] {
    assert!((1..=16).contains(&count));
    assert!(method <= 2);
    if count <= 8 {
        return super::hoa_dynamic::boundaries(count, usize::from(method), short);
    }
    let table = if method == 0 {
        &format().tables[count - 9]
    } else {
        &partition_format().tables[(usize::from(method) - 1) * 8 + count - 9].grid
    };
    if short {
        &table.short_ends
    } else {
        &table.long_ends
    }
}
