//! Per-component spatial grids; lower counts share the verified dynamic tables.

/// Profile of salient components with their own band counts.
pub const SUBBAND_PROFILE: &str = "apac-hoa-salient-subbands-v1";
/// Profile of the alternative salient band partitions.
pub const PARTITION_PROFILE: &str = "apac-hoa-salient-partition-v1";
/// Generated from `data/hoa-salient-subbands-format-v1.json` (method 0) and
/// `data/hoa-salient-subbands-format-v2.json` (methods 1 and 2) by the build script.
pub fn format_sha256(method: u8) -> &'static str {
    assert!(method <= 2);
    if method == 0 {
        crate::tables::HOA_SALIENT_SUBBANDS_FORMAT_SHA256
    } else {
        crate::tables::HOA_SALIENT_PARTITION_FORMAT_SHA256
    }
}
pub(super) fn boundaries(count: usize, method: u8, short: bool) -> &'static [usize] {
    assert!((1..=16).contains(&count));
    assert!(method <= 2);
    if count <= 8 {
        return super::hoa_dynamic::boundaries(count, usize::from(method), short);
    }
    let table = if method == 0 {
        &crate::tables::HOA_SALIENT_SUBBANDS[count - 9]
    } else {
        &crate::tables::HOA_SALIENT_PARTITIONS[(usize::from(method) - 1) * 8 + count - 9]
    };
    if short {
        table.short_ends
    } else {
        table.long_ends
    }
}
