//! Fixed IEEE constants for the independent SQ mathematical profile.
//! Generated offline from formulas; no runtime transcendental functions.
/// The SQ arithmetic profile: fixed IEEE constants, Float64 synthesis.
pub const PROFILE: &str = "apac-sq-math-v1";

pub struct Transform {
    pub window: &'static [f64],
    pub modulation: &'static [[f64; 2]],
    pub twiddles: &'static [[f64; 2]],
}
pub struct Tables {
    pub sha256: &'static str,
    pub inverse: &'static [f32],
    pub gains: &'static [f32],
    pub(crate) short: Transform,
    pub(crate) long: Transform,
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
/// Generated from `data/sq-math-v1.json` by the build script.
pub fn tables() -> &'static Tables {
    &crate::tables::SQ_MATH
}
/// Identity of the SQ constants, as embedded in `data/sq-math-v1.json`.
pub fn tables_sha256() -> &'static str {
    tables().sha256
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
        assert!(t.inverse.iter().chain(t.gains).all(|x| x.is_finite()));
        for sf in -256..=251 {
            assert_eq!(
                t.gains[(sf + 256) as usize] * 2.,
                t.gains[(sf + 260) as usize]
            );
        }
        assert_eq!(t.gains[356], 1.);
    }
}
