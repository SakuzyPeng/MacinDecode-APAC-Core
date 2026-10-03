//! Fixed IEEE constants for the independent SQ mathematical profile.
//! Generated offline from formulas; no runtime transcendental functions.
use serde::Deserialize;
use std::{collections::BTreeMap, sync::OnceLock};

pub const PROFILE: &str = "apac-sq-math-v1";

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
    inverse_quantizer_f32: Vec<u32>,
    gains_f32: Vec<u32>,
    transforms: BTreeMap<String, RawTransform>,
}
pub struct Transform {
    pub window: Vec<f64>,
    pub modulation: Vec<[f64; 2]>,
    pub twiddles: Vec<[f64; 2]>,
}
pub struct Tables {
    pub sha256: String,
    pub inverse: Vec<f32>,
    pub gains: Vec<f32>,
    short: Transform,
    long: Transform,
}
impl Tables {
    pub fn transform(&self, n: usize) -> &Transform {
        match n {
            128 => &self.short,
            1024 => &self.long,
            _ => unreachable!("verified SQ transform size"),
        }
    }
}
pub fn tables() -> &'static Tables {
    static TABLES: OnceLock<Tables> = OnceLock::new();
    TABLES.get_or_init(|| {
        let mut raw: RawTables =
            serde_json::from_str(include_str!("../../../data/sq-math-v1.json"))
                .expect("built-in independent SQ constants");
        assert_eq!(raw.numeric_profile, PROFILE);
        assert_eq!(raw.inverse_quantizer_f32.len(), 8192);
        assert_eq!(raw.gains_f32.len(), 512);
        let mut transform = |n: usize| {
            let data = raw.transforms.remove(&n.to_string()).expect("SQ size");
            assert_eq!(data.window_f64.len(), n);
            assert_eq!(data.modulation_f64.len(), n / 2);
            assert_eq!(data.twiddles_f64.len(), n / 4);
            let mut window: Vec<_> = data.window_f64.into_iter().map(f64::from_bits).collect();
            window.extend(window.clone().into_iter().rev());
            Transform {
                window,
                modulation: data
                    .modulation_f64
                    .into_iter()
                    .map(|a| a.map(f64::from_bits))
                    .collect(),
                twiddles: data
                    .twiddles_f64
                    .into_iter()
                    .map(|a| a.map(f64::from_bits))
                    .collect(),
            }
        };
        let short = transform(128);
        let long = transform(1024);
        Tables {
            sha256: raw.tables_sha256,
            inverse: raw
                .inverse_quantizer_f32
                .into_iter()
                .map(f32::from_bits)
                .collect(),
            gains: raw.gains_f32.into_iter().map(f32::from_bits).collect(),
            short,
            long,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn constants_cover_the_verified_domain() {
        let t = tables();
        assert_eq!(t.inverse[0].to_bits(), 0);
        for (q, expected) in [(1, 1.), (8, 16.), (27, 81.), (4096, 65536.)] {
            assert_eq!(t.inverse[q], expected);
        }
        assert!(t.inverse.windows(2).all(|x| x[0] < x[1]));
        assert!(t.gains.windows(2).all(|x| x[0] < x[1]));
        assert!(t.inverse.iter().chain(&t.gains).all(|x| x.is_finite()));
        for sf in -256..=251 {
            assert_eq!(
                t.gains[(sf + 256) as usize] * 2.,
                t.gains[(sf + 260) as usize]
            );
        }
        assert_eq!(t.gains[356], 1.);
    }
}
