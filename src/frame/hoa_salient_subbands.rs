//! Per-component perceptual grids; lower counts share the verified dynamic tables.
use serde::Deserialize;
use std::sync::OnceLock;

pub const SUBBAND_PROFILE: &str = "apac-hoa-salient-subbands-v1";
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
pub(crate) fn format_sha256() -> &'static str {
    &format().format_sha256
}
pub(super) fn boundaries(count: usize, short: bool) -> &'static [usize] {
    assert!((1..=16).contains(&count));
    if count <= 8 {
        return super::hoa_dynamic::boundaries(count, 0, short);
    }
    let table = &format().tables[count - 9];
    if short {
        &table.short_ends
    } else {
        &table.long_ends
    }
}
