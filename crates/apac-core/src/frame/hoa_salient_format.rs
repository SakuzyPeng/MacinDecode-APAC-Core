//! Repository storage shares order-dependent tables across quantization widths.
//! The expanded format and its digest retain their original wire identities.
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

#[path = "hoa_packed_tables.rs"]
mod packed;

#[derive(Serialize)]
pub(super) struct Mode {
    pub mode: usize,
    pub groups: Vec<&'static [usize]>,
    pub codebooks: Vec<Vec<(usize, u32)>>,
    pub signs: bool,
    pub matrices_f32: Vec<&'static [u32]>,
}

pub(super) struct Format {
    pub format_profile: String,
    pub tables_sha256: String,
    pub modes: Vec<Mode>,
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

struct SharedFormat {
    modes: Vec<SharedMode>,
    groups: Vec<Vec<usize>>,
    matrices_f32: Vec<Vec<u32>>,
}

fn shared_format(order: usize) -> &'static SharedFormat {
    static DATA: [OnceLock<SharedFormat>; 10] = [const { OnceLock::new() }; 10];
    const SOURCES: [&str; 10] = [
        include_str!("../../../../data/hoa-salient-order1-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order2-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order3-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order4-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order5-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order6-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order7-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order8-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order9-shared-v1.json"),
        include_str!("../../../../data/hoa-salient-order10-shared-v1.json"),
    ];
    DATA[order - 1].get_or_init(|| {
        let data: StoredSharedFormat =
            serde_json::from_str(SOURCES[order - 1]).expect("built-in shared HOA tables");
        assert_eq!(data.schema_version, 2);
        assert_eq!(data.format_profile, "apac-hoa-salient-shared-v1");
        assert_eq!(data.matrix_encoding, packed::MATRIX_ENCODING);
        assert_eq!(data.order, order);
        assert_eq!(data.modes.len(), 6);
        SharedFormat {
            modes: data.modes,
            groups: data.groups,
            matrices_f32: data
                .matrices_f32
                .iter()
                .map(|hex| {
                    packed::matrix(hex, (order + 1).pow(4)).expect("built-in packed HOA matrix")
                })
                .collect(),
        }
    })
}

impl Format {
    pub(super) fn load(source: &str, coefficients: usize, precision: u8) -> Self {
        let stored: StoredFormat = serde_json::from_str(source).expect("built-in HOA dictionary");
        let order = coefficients.isqrt() - 1;
        assert_eq!(stored.schema_version, 3);
        assert_eq!(stored.codebook_encoding, packed::CODEBOOK_ENCODING);
        assert_eq!(stored.order, order);
        assert_eq!((order + 1).pow(2), coefficients);
        assert_eq!(stored.quantization_bits, precision);
        assert_eq!(
            stored.shared_file,
            format!("hoa-salient-order{order}-shared-v1.json")
        );
        assert_eq!(stored.modes.len(), 6);
        let shared = shared_format(order);
        let modes = stored
            .modes
            .into_iter()
            .zip(&shared.modes)
            .enumerate()
            .map(|(index, (mode, common))| {
                assert_eq!(mode.mode, index);
                assert_eq!(common.mode, index);
                Mode {
                    mode: index,
                    groups: common
                        .group_indices
                        .iter()
                        .map(|&i| shared.groups[i].as_slice())
                        .collect(),
                    codebooks: mode
                        .codebooks
                        .iter()
                        .map(|hex| {
                            packed::codebook(hex, precision).expect("built-in packed HOA codebook")
                        })
                        .collect(),
                    signs: common.signs,
                    matrices_f32: common
                        .matrix_indices
                        .iter()
                        .map(|&i| shared.matrices_f32[i].as_slice())
                        .collect(),
                }
            })
            .collect();
        Self {
            format_profile: stored.format_profile,
            tables_sha256: stored.tables_sha256,
            modes,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::constants_for_bits;
    use serde_json::json;

    #[test]
    fn all_expanded_digests_match_and_quantization_widths_share_table_storage() {
        for order in 1usize..=10 {
            let coefficients = (order + 1).pow(2);
            let base = &constants_for_bits(coefficients, 6).format;
            for precision in 6..=9 {
                let format = &constants_for_bits(coefficients, precision).format;
                let expanded = json!({
                    "order": order,
                    "quantization_bits": precision,
                    "modes": format.modes,
                });
                assert_eq!(
                    crate::model::sha256(&serde_json::to_vec(&expanded).unwrap()),
                    format.tables_sha256,
                    "order {order}, precision {precision}"
                );
                for (mode, original) in format.modes.iter().zip(&base.modes) {
                    for (&group, &expected) in mode.groups.iter().zip(&original.groups) {
                        assert!(std::ptr::eq(group, expected));
                    }
                    for (&matrix, &expected) in mode.matrices_f32.iter().zip(&original.matrices_f32)
                    {
                        assert!(std::ptr::eq(matrix, expected));
                    }
                }
            }
        }
    }
}
