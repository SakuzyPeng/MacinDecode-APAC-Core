//! Fixed BWE2 mathematics. Ordinary Float64 operations never fuse or reassociate.
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::OnceLock};

pub const PROFILE: &str = "apac-bwe2-math-v1";
#[derive(Deserialize)]
struct Format {
    format_profile: String,
    tables_sha256: String,
    lsf_codebooks_f32: Vec<Vec<[u32; 16]>>,
    excitation_gains_f32: Vec<u32>,
}
#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    twiddles_f64: BTreeMap<String, Vec<[u64; 2]>>,
    cosine_f64: [u64; 13],
    sine_f64: [u64; 13],
    lsf_angle_scale_f64: u64,
    autocorrelation_loading_f64: u64,
}
struct Constants {
    format_sha: String,
    math_sha: String,
    books: Vec<Vec<[f64; 16]>>,
    gains: Vec<f64>,
    twiddles: BTreeMap<usize, Vec<Complex>>,
    cosine: [f64; 13],
    sine: [f64; 13],
    angle_scale: f64,
    loading: f64,
}
fn constants() -> &'static Constants {
    static DATA: OnceLock<Constants> = OnceLock::new();
    DATA.get_or_init(|| {
        let format: Format = serde_json::from_str(include_str!("../data/bwe2-format-v1.json"))
            .expect("built-in BWE2 format constants");
        let math: Math = serde_json::from_str(include_str!("../data/bwe2-math-v1.json"))
            .expect("built-in BWE2 mathematical constants");
        assert_eq!(format.format_profile, "apac-bwe2-format-v1");
        assert_eq!(math.numeric_profile, PROFILE);
        assert_eq!(format.lsf_codebooks_f32.len(), 2);
        assert!(format.lsf_codebooks_f32.iter().all(|b| b.len() == 512));
        assert_eq!(format.excitation_gains_f32.len(), 64);
        Constants {
            format_sha: format.tables_sha256,
            math_sha: math.tables_sha256,
            books: format
                .lsf_codebooks_f32
                .into_iter()
                .map(|b| {
                    b.into_iter()
                        .map(|v| v.map(|x| f64::from(f32::from_bits(x))))
                        .collect()
                })
                .collect(),
            gains: format
                .excitation_gains_f32
                .into_iter()
                .map(|x| f64::from(f32::from_bits(x)))
                .collect(),
            twiddles: math
                .twiddles_f64
                .into_iter()
                .map(|(n, v)| {
                    (
                        n.parse().expect("transform size"),
                        v.into_iter()
                            .map(|[re, im]| Complex {
                                re: f64::from_bits(re),
                                im: f64::from_bits(im),
                            })
                            .collect(),
                    )
                })
                .collect(),
            cosine: math.cosine_f64.map(f64::from_bits),
            sine: math.sine_f64.map(f64::from_bits),
            angle_scale: f64::from_bits(math.lsf_angle_scale_f64),
            loading: f64::from_bits(math.autocorrelation_loading_f64),
        }
    })
}
pub(crate) fn format_sha256() -> &'static str {
    &constants().format_sha
}
pub(crate) fn math_sha256() -> &'static str {
    &constants().math_sha
}

#[derive(Clone, Copy, Default, Debug)]
struct Complex {
    re: f64,
    im: f64,
}
impl Complex {
    fn mul(self, other: Self) -> Self {
        Self {
            re: self.re * other.re - self.im * other.im,
            im: self.re * other.im + self.im * other.re,
        }
    }
    fn add(self, other: Self) -> Self {
        Self {
            re: self.re + other.re,
            im: self.im + other.im,
        }
    }
}
fn radix2(data: &mut [Complex]) {
    let n = data.len();
    assert!(n.is_power_of_two());
    let twiddles = &constants().twiddles[&n];
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            data.swap(i, j);
        }
    }
    let mut size = 2;
    while size <= n {
        for chunk in data.chunks_exact_mut(size) {
            for k in 0..size / 2 {
                let a = chunk[k];
                let b = chunk[k + size / 2].mul(twiddles[k * n / size]);
                chunk[k] = a.add(b);
                chunk[k + size / 2] = Complex {
                    re: a.re - b.re,
                    im: a.im - b.im,
                };
            }
        }
        size *= 2;
    }
}
fn forward(data: &mut [Complex]) {
    let n = data.len();
    if n.is_power_of_two() {
        radix2(data);
        return;
    }
    assert!(matches!(n, 96 | 768));
    let m = n / 3;
    let mut branches: [Vec<Complex>; 3] =
        std::array::from_fn(|r| (0..m).map(|j| data[3 * j + r]).collect());
    for branch in &mut branches {
        radix2(branch);
    }
    let w = &constants().twiddles[&n];
    for k in 0..n {
        // Fixed association: (branch 0 + rotated branch 1) + rotated branch 2.
        data[k] = branches[0][k % m]
            .add(branches[1][k % m].mul(w[k]))
            .add(branches[2][k % m].mul(w[2 * k % n]));
    }
}
fn inverse(data: &mut [Complex]) {
    for z in data.iter_mut() {
        z.im = -z.im;
    }
    forward(data);
    let n = data.len() as f64;
    for z in data {
        z.re /= n;
        z.im = -z.im / n;
    }
}
fn polynomial(coefficients: &[f64; 13], value: f64) -> f64 {
    let mut result = coefficients[12];
    for &c in coefficients[..12].iter().rev() {
        let product = result * value;
        result = product + c;
    }
    result
}
fn lsf_cosine(frequency: f64) -> f64 {
    // Fold in the dyadic frequency domain first; the core angle is <= pi/4.
    let (frequency, sign) = if frequency > 6000. {
        (12000. - frequency, -1.)
    } else {
        (frequency, 1.)
    };
    if frequency == 6000. {
        return 0.;
    }
    let (frequency, sine) = if frequency > 3000. {
        (6000. - frequency, true)
    } else {
        (frequency, false)
    };
    let x = frequency * constants().angle_scale;
    let x2 = x * x;
    let value = if sine {
        x * polynomial(&constants().sine, x2)
    } else {
        polynomial(&constants().cosine, x2)
    };
    sign * value
}
fn conditioned_lsf(indices: [u16; 2]) -> [f64; 16] {
    let mut result = [0.; 16];
    let mut minimum = 50.;
    for (i, value) in result.iter_mut().enumerate() {
        let sum = constants().books[0][indices[0] as usize][i]
            + constants().books[1][indices[1] as usize][i];
        *value = sum.max(minimum);
        minimum = *value + 50.;
    }
    if result[15] > 11950. {
        let mut maximum = 11950.;
        for value in result.iter_mut().rev() {
            *value = value.min(maximum);
            maximum = *value - 50.;
        }
    }
    result
}
fn lsf_lpc(lsf: &[f64; 16]) -> [f64; 17] {
    let mut products = [[0.; 17]; 2];
    for (parity, product) in products.iter_mut().enumerate() {
        product[0] = 1.;
        for (step, &frequency) in lsf.iter().skip(parity).step_by(2).enumerate() {
            let factor = -2. * lsf_cosine(frequency);
            let old = *product;
            *product = [0.; 17];
            for j in 0..=2 * step {
                product[j] += old[j];
                let middle = factor * old[j];
                product[j + 1] += middle;
                product[j + 2] += old[j];
            }
        }
    }
    let (p, q) = (&products[0], &products[1]);
    let mut a = [0.; 17];
    a[0] = 1.;
    for j in 1..=16 {
        a[j] = ((p[j] + q[j]) + p[j - 1] - q[j - 1]) * 0.5;
    }
    a
}
fn autocorrelation(input: &[f32], windows: usize, window_size: usize, cutoff: usize) -> [f64; 17] {
    let n = 2 * cutoff;
    let mut result = [0.; 17];
    for window in 0..windows {
        let mut psd = vec![Complex::default(); n];
        for k in 0..cutoff {
            let x = f64::from(input[window * window_size + k]);
            psd[k].re = x * x;
            if k > 0 {
                psd[n - k].re = psd[k].re;
            }
        }
        // A real inverse DFT with an explicitly zero Nyquist term.
        inverse(&mut psd);
        for (r, z) in result.iter_mut().zip(psd) {
            *r += z.re;
        }
    }
    result[0] *= constants().loading;
    result
}
fn levinson(r: &[f64; 17]) -> Result<[f64; 17], &'static str> {
    let mut a = [0.; 17];
    a[0] = 1.;
    let mut error = r[0];
    if !error.is_finite() || error <= 0. {
        return Err("nonpositive BWE2 source energy");
    }
    for order in 1..=16 {
        let mut residual = r[order];
        for j in 1..order {
            let product = a[j] * r[order - j];
            residual += product;
        }
        let reflection = -residual / error;
        if !reflection.is_finite() || reflection.abs() >= 1. {
            return Err("invalid BWE2 prediction reflection coefficient");
        }
        let old = a;
        for j in 1..order {
            let product = reflection * old[order - j];
            a[j] = old[j] + product;
        }
        a[order] = reflection;
        let square = reflection * reflection;
        error *= 1. - square;
        if !error.is_finite() || error <= 0. {
            return Err("nonpositive BWE2 prediction error");
        }
    }
    Ok(a)
}
fn envelope(a: &[f64; 17], bins: usize) -> Vec<f64> {
    let mut data = vec![Complex::default(); 2 * bins];
    for (z, &a) in data.iter_mut().zip(a) {
        z.re = a;
    }
    forward(&mut data);
    data[..bins]
        .iter()
        .map(|z| {
            let re = z.re * z.re;
            let im = z.im * z.im;
            (re + im).sqrt()
        })
        .collect()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Analysis {
    pub conditioned_lsf: [f64; 16],
    pub source_lpc: [f64; 17],
    pub target_lpc: [f64; 17],
}
pub(crate) fn restore(
    input: &[f32],
    short: bool,
    cutoff: usize,
    groups: &[u32],
    indices: [u16; 2],
    gains: &[u8],
) -> Result<(Vec<f32>, Option<Analysis>), String> {
    let (size, windows, source_start, high_bins) = if short {
        (128, 8, 16, 64)
    } else {
        (1024, 1, 128, 512)
    };
    if (0..windows).all(|w| input[w * size..w * size + cutoff].iter().all(|v| *v == 0.)) {
        return Ok((input.to_vec(), None));
    }
    let source_lpc = levinson(&autocorrelation(input, windows, size, cutoff))?;
    let conditioned_lsf = conditioned_lsf(indices);
    let target_lpc = lsf_lpc(&conditioned_lsf);
    let source_envelope = envelope(&source_lpc, cutoff);
    let target_envelope = envelope(&target_lpc, high_bins);
    let mut ratios = Vec::with_capacity(high_bins);
    let width = cutoff - source_start;
    for (k, denominator) in target_envelope.into_iter().enumerate() {
        let numerator = source_envelope[source_start + k % width];
        if !denominator.is_finite()
            || denominator <= 0.
            || !numerator.is_finite()
            || numerator <= 0.
        {
            return Err("nonpositive or nonfinite BWE2 envelope".into());
        }
        ratios.push(numerator / denominator);
    }
    let mut output = input.to_vec();
    let mut first = 0;
    for (group, &length) in groups.iter().enumerate() {
        let gain = constants().gains[gains[group] as usize];
        for window in first..first + length as usize {
            for (k, &ratio) in ratios.iter().enumerate() {
                let source = f64::from(input[window * size + source_start + k % width]);
                let shaped = source * ratio;
                let value = (shaped * gain) as f32;
                if !value.is_finite() {
                    return Err(format!(
                        "nonfinite BWE2 output at window {window}, frequency line {}",
                        cutoff + k
                    ));
                }
                output[window * size + cutoff + k] = if value == 0. { 0. } else { value };
            }
        }
        first += length as usize;
    }
    Ok((
        output,
        Some(Analysis {
            conditioned_lsf,
            source_lpc,
            target_lpc,
        }),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;
    #[test]
    fn every_lsf_pair_has_valid_spacing_and_gain_zero_is_not_mute() {
        assert!(constants().gains[0] > 0. && constants().gains[0] < 1.);
        assert!(constants().gains.windows(2).all(|v| v[0] < v[1]));
        for a in 0..512 {
            for b in 0..512 {
                let values = conditioned_lsf([a, b]);
                assert!(values[0] >= 50. && values[15] <= 11950.);
                assert!(values.windows(2).all(|v| v[1] - v[0] >= 50.));
            }
        }
    }
    #[test]
    fn nonfinite_output_is_rejected_without_clamping() {
        let error = restore(&[f32::MAX; 1024], false, 256, &[1], [0, 0], &[63]).unwrap_err();
        assert!(error.contains("window 0, frequency line 256"));
    }
    #[test]
    fn bounded_cosine_matches_the_definition_including_quadrants() {
        for i in 0..=24000 {
            let f = f64::from(i) * 0.5;
            assert!(
                (lsf_cosine(f) - (PI * f / 12000.).cos()).abs() < 7e-16,
                "{f}"
            );
        }
        assert_eq!(lsf_cosine(6000.).to_bits(), 0);
    }
    #[test]
    fn every_transform_basis_and_inverse_has_the_prescribed_sign_and_scale() {
        for n in [64, 96, 128, 512, 768, 1024] {
            for bin in 0..n {
                let mut values = vec![Complex::default(); n];
                values[bin].re = 1.;
                forward(&mut values);
                for (k, value) in values.iter().enumerate() {
                    let angle = -2. * PI * ((k * bin) % n) as f64 / n as f64;
                    assert!((value.re - angle.cos()).abs() < 2e-14);
                    assert!((value.im - angle.sin()).abs() < 2e-14);
                }
                inverse(&mut values);
                for (k, value) in values.iter().enumerate() {
                    assert!((value.re - f64::from(k == bin)).abs() < 1e-14);
                    assert!(value.im.abs() < 1e-14);
                }
            }
        }
    }
    fn direct(input: &[Complex]) -> Vec<Complex> {
        let n = input.len();
        let w = &constants().twiddles[&n];
        (0..n)
            .map(|k| {
                input
                    .iter()
                    .enumerate()
                    .fold(Complex::default(), |sum, (j, &x)| {
                        sum.add(x.mul(w[k * j % n]))
                    })
            })
            .collect()
    }
    fn seeded(n: usize) -> Vec<Complex> {
        let mut state = 0x42574532u32;
        (0..n)
            .map(|_| {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                Complex {
                    re: f64::from((state & 0xffff) as i32 - 32768) / 32768.,
                    im: f64::from((state >> 16) as i32 - 32768) / 32768.,
                }
            })
            .collect()
    }
    #[test]
    fn fixed_seed_complex_vectors_match_direct_dft() {
        for n in [64, 96, 128, 512, 768, 1024] {
            let mut actual = seeded(n);
            let expected = direct(&actual);
            forward(&mut actual);
            for (a, b) in actual.iter().zip(expected) {
                assert!((a.re - b.re).abs() < 2e-11);
                assert!((a.im - b.im).abs() < 2e-11);
            }
        }
    }
    #[test]
    #[ignore = "required release benchmark; run explicitly on each native platform"]
    fn optimized_768_is_at_least_twice_as_fast_as_direct_dft() {
        assert!(
            !std::hint::black_box(cfg!(debug_assertions)),
            "benchmark requires --release"
        );
        use std::{hint::black_box, time::Instant};
        let input = seeded(768);
        black_box(direct(&input));
        let mut warm = input.clone();
        forward(&mut warm);
        let mut optimized = Vec::new();
        let mut reference = Vec::new();
        for _ in 0..9 {
            let start = Instant::now();
            for _ in 0..32 {
                let mut values = input.clone();
                forward(black_box(&mut values));
                black_box(values);
            }
            optimized.push(start.elapsed().as_nanos() / 32);
            let start = Instant::now();
            for _ in 0..2 {
                black_box(direct(black_box(&input)));
            }
            reference.push(start.elapsed().as_nanos() / 2);
        }
        optimized.sort_unstable();
        reference.sort_unstable();
        println!(
            "BWE2_BENCH {}",
            serde_json::json!({"size":768,"optimized_ns":optimized[4],"direct_ns":reference[4],
            "speedup":reference[4] as f64/optimized[4] as f64,"numeric_profile":PROFILE,"compiler":env!("APAC_BUILD_RUSTC")})
        );
        assert!(2 * optimized[4] <= reference[4]);
    }
}
