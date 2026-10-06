//! Repository storage shares order-dependent tables across quantization widths.
//! The expanded format and its digest retain their original wire identities.
//!
//! The dictionaries are unpacked into static tables by the build script; this
//! test reconstructs each expanded format, including the codebooks that are
//! only kept as Huffman tries at run time, and checks its published digest.
use super::constants_for_bits;
use crate::prelude::*;
use crate::tables::packed;
use serde_json::{Value, json};

/// The dictionary codebooks as stored, unpacked like the build script does.
pub(super) fn stored_codebooks(order: usize, precision: u8) -> Vec<Vec<Vec<(usize, u32)>>> {
    let name = match (order, precision) {
        (3, 6) => "hoa-salient-format-v1.json".to_owned(),
        (_, 6) => format!("hoa-salient-order{order}-format-v1.json"),
        _ => format!("hoa-salient-order{order}-q{precision}-format-v1.json"),
    };
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../data")
        .join(name);
    let stored: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    stored["modes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|mode| {
            mode["codebooks"]
                .as_array()
                .unwrap()
                .iter()
                .map(|hex| packed::codebook(hex.as_str().unwrap(), precision).unwrap())
                .collect()
        })
        .collect()
}

#[test]
fn supported_digests_match_and_quantization_widths_share_table_storage() {
    assert_eq!(crate::tables::SALIENT_CONSTANTS.len(), 12);
    assert!(
        crate::tables::SALIENT_CONSTANTS
            .iter()
            .all(|c| c.coefficients <= 16)
    );
    for order in 1usize..=crate::tables::HOA_MAX_SUPPORTED_ORDER {
        let coefficients = (order + 1).pow(2);
        let base = &constants_for_bits(coefficients, 6).format;
        for precision in 6..=9 {
            let format = &constants_for_bits(coefficients, precision).format;
            let codebooks = stored_codebooks(order, precision);
            let modes: Vec<Value> = format
                .modes
                .iter()
                .zip(&codebooks)
                .enumerate()
                .map(|(index, (mode, books))| {
                    json!({
                        "mode": index,
                        "groups": mode.groups,
                        "codebooks": books,
                        "signs": mode.signs,
                        "matrices_f32": mode.matrices_f32,
                    })
                })
                .collect();
            let expanded = json!({
                "order": order,
                "quantization_bits": precision,
                "modes": modes,
            });
            assert_eq!(
                crate::model::sha256(&serde_json::to_vec(&expanded).unwrap()),
                format.tables_sha256,
                "order {order}, precision {precision}"
            );
            for (mode, original) in format.modes.iter().zip(base.modes) {
                for (&group, &expected) in mode.groups.iter().zip(original.groups) {
                    assert!(core::ptr::eq(group, expected));
                }
                for (&matrix, &expected) in mode.matrices_f32.iter().zip(original.matrices_f32) {
                    assert!(core::ptr::eq(matrix, expected));
                }
            }
        }
    }
}
