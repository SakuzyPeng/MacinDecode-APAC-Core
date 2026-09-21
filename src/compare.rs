use crate::{
    error::{Error, Result},
    model::{PcmInfo, SCHEMA_VERSION},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{BufReader, Read},
    path::{Component, Path, PathBuf},
};

#[derive(Debug, Serialize, Deserialize)]
pub struct ChannelMetrics {
    pub channel: u32,
    pub samples: u64,
    pub max_absolute_error: f64,
    pub rms_error: f64,
    pub snr_db: Option<f64>,
    pub snr_kind: String,
    pub samples_outside_tolerance: u64,
}
#[derive(Debug, Serialize, Deserialize)]
pub struct Comparison {
    pub schema_version: u32,
    pub passed: bool,
    pub bit_identical: bool,
    pub layout_verified: bool,
    pub frames: u64,
    pub atol: f64,
    pub rtol: f64,
    pub channels: Vec<ChannelMetrics>,
}

fn load(path: &Path) -> Result<(PcmInfo, PathBuf)> {
    if fs::metadata(path)?.len() > 1024 * 1024 {
        return Err(Error::new("PCM metadata", "metadata exceeds 1 MiB"));
    }
    let info: PcmInfo = serde_json::from_reader(BufReader::new(File::open(path)?))?;
    info.validate()?;
    let root = path.parent().unwrap_or_else(|| Path::new("."));
    if root.join(".incomplete.json").exists() {
        return Err(Error::new(
            "PCM metadata",
            "bundle still has an incomplete marker",
        ));
    }
    let relative = Path::new(&info.pcm_file);
    if relative.as_os_str().is_empty()
        || relative
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(Error::new(
            "PCM metadata",
            "pcm_file must be a relative bundle path without parent traversal",
        ));
    }
    let data = root.join(relative);
    if fs::metadata(&data)?.len() != info.bytes {
        return Err(Error::new(
            "PCM size",
            "file size does not match declared frame count",
        ));
    }
    Ok((info, data))
}

pub fn compare(reference: &Path, candidate: &Path, atol: f64, rtol: f64) -> Result<Comparison> {
    if !atol.is_finite() || !rtol.is_finite() || atol < 0. || rtol < 0. {
        return Err(Error::new(
            "compare",
            "tolerances must be finite and nonnegative",
        ));
    }
    let (a, pa) = load(reference)?;
    let (b, pb) = load(candidate)?;
    // Core Audio's Unknown tag only carries a channel count in its low 16 bits.
    let reference_layout = a
        .layout
        .value
        .as_ref()
        .filter(|layout| layout.tag >> 16 != 0xffff);
    let candidate_layout = b
        .layout
        .value
        .as_ref()
        .filter(|layout| layout.tag >> 16 != 0xffff);
    let same_layout = match (reference_layout, candidate_layout) {
        (Some(a), Some(b)) => a.equivalent(b),
        (None, None) => true,
        _ => false,
    };
    if a.sample_rate != b.sample_rate
        || a.channels != b.channels
        || a.frames != b.frames
        || a.start_frame != b.start_frame
        || !same_layout
    {
        return Err(Error::new(
            "compare format",
            format!(
                "sample rate, channels, layout, frame count or start frame differ (reference: {} Hz, {} ch, {} frames @ {}; candidate: {} Hz, {} ch, {} frames @ {})",
                a.sample_rate,
                a.channels,
                a.frames,
                a.start_frame,
                b.sample_rate,
                b.channels,
                b.frames,
                b.start_frame
            ),
        ));
    }
    let n = a.channels as usize;
    let mut ra = BufReader::new(File::open(pa)?);
    let mut rb = BufReader::new(File::open(pb)?);
    let mut ha = Sha256::new();
    let mut hb = Sha256::new();
    let mut energy = vec![0.; n];
    let mut error = vec![0.; n];
    let mut maximum = vec![0f64; n];
    let mut failures = vec![0u64; n];
    let mut exact = true;
    let mut position = 0;
    let mut ba = vec![0; n * 4 * 4096];
    let mut bb = vec![0; ba.len()];
    while position < a.frames {
        let frames = (a.frames - position).min(4096) as usize;
        let bytes = frames * n * 4;
        ra.read_exact(&mut ba[..bytes])?;
        rb.read_exact(&mut bb[..bytes])?;
        ha.update(&ba[..bytes]);
        hb.update(&bb[..bytes]);
        exact &= ba[..bytes] == bb[..bytes];
        for (i, (x, y)) in ba[..bytes]
            .chunks_exact(4)
            .zip(bb[..bytes].chunks_exact(4))
            .enumerate()
        {
            let x = f64::from(f32::from_le_bytes(x.try_into().unwrap()));
            let y = f64::from(f32::from_le_bytes(y.try_into().unwrap()));
            if !x.is_finite() || !y.is_finite() {
                return Err(Error::new(
                    "compare PCM",
                    format!(
                        "non-finite sample at frame {}, channel {}",
                        position + (i / n) as u64,
                        i % n
                    ),
                ));
            }
            let channel = i % n;
            let delta = (x - y).abs();
            energy[channel] += x * x;
            error[channel] += delta * delta;
            maximum[channel] = maximum[channel].max(delta);
            if delta > atol + rtol * x.abs() {
                failures[channel] += 1;
            }
        }
        position += frames as u64;
    }
    if !format!("{:x}", ha.finalize()).eq_ignore_ascii_case(&a.sha256)
        || !format!("{:x}", hb.finalize()).eq_ignore_ascii_case(&b.sha256)
    {
        return Err(Error::new(
            "PCM integrity",
            "PCM SHA-256 does not match metadata",
        ));
    }
    let channels = (0..n)
        .map(|i| {
            let (snr, kind) = if a.frames == 0 {
                (None, "empty")
            } else if error[i] == 0. {
                (None, "zero-error")
            } else if energy[i] == 0. {
                (None, "zero-reference-energy")
            } else {
                (Some(10. * (energy[i] / error[i]).log10()), "finite")
            };
            ChannelMetrics {
                channel: i as u32,
                samples: a.frames,
                max_absolute_error: maximum[i],
                rms_error: if a.frames == 0 {
                    0.
                } else {
                    (error[i] / a.frames as f64).sqrt()
                },
                snr_db: snr,
                snr_kind: kind.into(),
                samples_outside_tolerance: failures[i],
            }
        })
        .collect();
    Ok(Comparison {
        schema_version: SCHEMA_VERSION,
        passed: failures.iter().all(|&n| n == 0),
        bit_identical: exact,
        layout_verified: reference_layout.is_some(),
        frames: a.frames,
        atol,
        rtol,
        channels,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::*;
    use std::{
        collections::BTreeMap,
        sync::atomic::{AtomicU64, Ordering},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!(
                "apac-compare-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&p).unwrap();
            Self(p)
        }
        fn bundle(&self, name: &str, samples: &[f32], channels: u32) -> PathBuf {
            let bytes: Vec<_> = samples.iter().flat_map(|s| s.to_le_bytes()).collect();
            fs::write(self.0.join(format!("{name}.f32le")), &bytes).unwrap();
            let info = PcmInfo {
                schema_version: 1,
                complete: true,
                pcm_file: format!("{name}.f32le"),
                encoding: "f32le".into(),
                interleaved: true,
                sample_rate: 48000.,
                channels,
                layout: Property::known(ChannelLayout::tagged(
                    (101 << 16) | channels,
                    channels,
                    None,
                )),
                start_frame: 0,
                requested_frames: samples.len() as u64 / u64::from(channels),
                frames: samples.len() as u64 / u64::from(channels),
                bytes: bytes.len() as u64,
                sha256: sha256(&bytes),
                source: None,
                source_cookie_sha256: None,
                source_packet_table: None,
                environment: Environment {
                    tool_version: "test".into(),
                    os: "test".into(),
                    architecture: "test".into(),
                    system_version: "test".into(),
                },
                decoder_settings: BTreeMap::new(),
                all_finite: true,
            };
            let p = self.0.join(format!("{name}.json"));
            fs::write(&p, serde_json::to_vec(&info).unwrap()).unwrap();
            p
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn exact_gain_and_one_channel_errors_are_measured() {
        let t = Temp::new();
        let a = t.bundle("a", &[0.25, 0.5, -0.25, -0.5], 2);
        let b = t.bundle("b", &[0.25, 1., -0.25, -1.], 2);
        let same = compare(&a, &a, 1e-6, 1e-5).unwrap();
        assert!(same.passed && same.bit_identical);
        assert!(same.layout_verified);
        let changed = compare(&a, &b, 1e-6, 1e-5).unwrap();
        assert!(!changed.passed && !changed.bit_identical);
        assert_eq!(changed.channels[0].rms_error, 0.);
        assert_eq!(changed.channels[1].rms_error, 0.5);
        assert_eq!(changed.channels[1].samples_outside_tolerance, 2);
        assert!(compare(&a, &b, 0.5, 0.).unwrap().passed);
    }
    #[test]
    fn unknown_and_missing_layouts_compare_without_verification() {
        let t = Temp::new();
        let known = t.bundle("known", &[0.25, 0.5], 2);
        let mut info: PcmInfo = serde_json::from_slice(&fs::read(&known).unwrap()).unwrap();
        info.layout = Property::known(ChannelLayout::tagged(0xffff0002, 2, None));
        let unknown = t.0.join("unknown.json");
        fs::write(&unknown, serde_json::to_vec(&info).unwrap()).unwrap();
        info.layout =
            Property::from_result(Err(crate::error::Error::new("test layout", "unavailable")));
        let missing = t.0.join("missing.json");
        fs::write(&missing, serde_json::to_vec(&info).unwrap()).unwrap();

        for reference in [&unknown, &missing] {
            for candidate in [&unknown, &missing] {
                let result = compare(reference, candidate, 0., 0.).unwrap();
                assert!(result.passed && result.bit_identical);
                assert!(!result.layout_verified);
            }
            assert!(compare(&known, reference, 0., 0.).is_err());
            assert!(compare(reference, &known, 0., 0.).is_err());
        }
    }
    #[test]
    fn silence_signed_zero_and_nonfinite_are_handled() {
        let t = Temp::new();
        let z = t.bundle("zero", &[0., 0.], 2);
        let nz = t.bundle("negative-zero", &[-0., 0.], 2);
        let r = compare(&z, &nz, 0., 0.).unwrap();
        assert!(r.passed);
        assert!(!r.bit_identical);
        assert_eq!(r.channels[0].snr_kind, "zero-error");
        serde_json::to_string(&r).unwrap();
        let noise = t.bundle("nonzero", &[0.1, 0.], 2);
        assert_eq!(
            compare(&z, &noise, 0., 0.).unwrap().channels[0].snr_kind,
            "zero-reference-energy"
        );
        for (i, value) in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY]
            .into_iter()
            .enumerate()
        {
            let bad = t.bundle(&format!("bad{i}"), &[value, 0.], 2);
            assert!(compare(&z, &bad, 0., 0.).is_err());
        }
    }
    #[test]
    fn mismatched_frames_layout_integrity_and_incomplete_fail() {
        let t = Temp::new();
        let a = t.bundle("a", &[0., 0.], 2);
        let b = t.bundle("b", &[0., 0., 0., 0.], 2);
        assert!(compare(&a, &b, 0., 0.).is_err());
        let mut info: PcmInfo = serde_json::from_slice(&fs::read(&a).unwrap()).unwrap();
        info.layout.value.as_mut().unwrap().tag += 1;
        let wrong = t.0.join("wrong.json");
        fs::write(&wrong, serde_json::to_vec(&info).unwrap()).unwrap();
        assert!(compare(&a, &wrong, 0., 0.).is_err());
        fs::write(t.0.join("a.f32le"), [1u8; 8]).unwrap();
        assert!(compare(&a, &a, 0., 0.).is_err());
        fs::write(t.0.join(".incomplete.json"), b"{}").unwrap();
        assert!(compare(&b, &b, 0., 0.).is_err());
    }
}
