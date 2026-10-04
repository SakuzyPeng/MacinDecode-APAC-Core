//! CAC inverse mixing for `apac-core`.
//!
//! APAC codes a shared-ICS channel pair with channel-aligned coding (CAC): per scale-factor
//! band, a gain index selects a 2×2 inverse mixing matrix that turns the coded pair back into
//! the left and right spectra. `apac-core` reads the CAC syntax itself but carries no inverse
//! mixing: it takes [`rotate`] from this crate when built with its `cac` feature, and without
//! it rejects every frame that uses a nonzero gain index.
//!
//! The rotations follow the `apac-cac-math-v1` profile, generated from formulas into
//! `data/cac-math-v1.json` (`scripts/generate_cac_math.py`); the arithmetic order is frozen.
#![no_std]
#![warn(missing_docs)]

#[cfg(test)]
extern crate std;

/// The CAC arithmetic profile.
pub const NUMERIC_PROFILE: &str = "apac-cac-math-v1";

/// One gain index's rotation: Float64 bit patterns and the channel swap.
struct Rotation {
    a_f64: u64,
    b_f64: u64,
    swap: bool,
}

include!(concat!(env!("OUT_DIR"), "/rotations.rs"));

/// Inverse mixing of one spectral line pair for a CAC gain index (0..=34).
///
/// Index 0 returns the pair unchanged. The two products and their sum each round
/// separately to Float32, and a zero result is returned as +0.
///
/// # Panics
///
/// If `gain` is above 34; the CAC gain codebook has no larger index.
pub fn rotate(x: f32, y: f32, gain: u8) -> (f32, f32) {
    if gain == 0 {
        return (x, y);
    }
    let entry = &ROTATIONS[usize::from(gain)];
    let (a, b) = (f64::from_bits(entry.a_f64), f64::from_bits(entry.b_f64));
    let (x, y) = (f64::from(x), f64::from(y));
    // The products and sum each round separately. Do not fuse or reassociate.
    let sum = (a * x + b * y) as f32;
    let difference = (b * x - a * y) as f32;
    let canonical = |value: f32| if value == 0. { 0. } else { value };
    if entry.swap {
        (canonical(difference), canonical(sum))
    } else {
        (canonical(sum), canonical(difference))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::string::String;
    use std::vec::Vec;

    #[test]
    fn rotations_match_the_json_table() {
        #[derive(serde::Deserialize)]
        struct Entry {
            a_f64: u64,
            b_f64: u64,
            swap: bool,
        }
        #[derive(serde::Deserialize)]
        struct Math {
            numeric_profile: String,
            tables_sha256: String,
            rotations: Vec<Entry>,
        }
        let math: Math =
            serde_json::from_str(include_str!("../../../data/cac-math-v1.json")).unwrap();
        assert_eq!(math.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(math.tables_sha256, MATH_SHA256);
        assert_eq!(ROTATIONS.len(), math.rotations.len());
        for (actual, expected) in ROTATIONS.iter().zip(&math.rotations) {
            assert_eq!(
                (actual.a_f64, actual.b_f64, actual.swap),
                (expected.a_f64, expected.b_f64, expected.swap)
            );
        }
    }

    #[test]
    fn rotations_fit_the_skipped_prefix_finite_value_bound() {
        for rotation in &ROTATIONS {
            let a = f64::from_bits(rotation.a_f64);
            let b = f64::from_bits(rotation.b_f64);
            assert!(a.is_finite() && b.is_finite() && a.abs() <= 1. && b.abs() <= 1.);
            // SQ < 2^58, each sum uses two bounded products, with ample
            // separate-rounding headroom inside the conservative 2^60 bound.
            assert!((a.abs() + b.abs()) * 2f64.powi(58) * 1.0001 < 2f64.powi(60));
        }
    }

    #[test]
    fn rotations_preserve_identity_energy_and_polarity() {
        assert_eq!(rotate(2., -3., 0), (2., -3.));
        for gain in 1..=34 {
            let (x, y) = rotate(1., 0., gain);
            assert!((f64::from(x) * f64::from(x) + f64::from(y) * f64::from(y) - 1.).abs() < 1e-7);
            let zeros = rotate(0., 0., gain);
            assert_eq!((zeros.0.to_bits(), zeros.1.to_bits()), (0, 0));
        }
        let (left, right) = rotate(1., 1., 9);
        assert_eq!(left.to_bits(), 0);
        assert!(right > 1.4);
        let (left, right) = rotate(1., 1., 26);
        assert!(left < -1.4);
        assert_eq!(right.to_bits(), 0);
    }
}
