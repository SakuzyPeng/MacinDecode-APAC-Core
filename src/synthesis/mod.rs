//! Independent SQ mathematics: fixed IEEE constants, Float64 synthesis and overlap.
//! Ordinary products and sums round separately; only final PCM is cast to Float32.
#[cfg(test)]
mod access_tests;
mod bundle;
#[cfg(test)]
mod channel_tests;
mod channels;
#[cfg(test)]
mod drc_tests;
mod hoa;
#[cfg(test)]
mod hoa_tests;
pub(crate) mod input;
use crate::{
    error::{Error, Result},
    frame::{DrcState, FrameContext, PacketReport, parse_packet_with_state},
};
pub use bundle::{
    SqAccessMode, SqDecodeOptions, decode_sq, decode_sq_with_access, decode_sq_with_options,
};
pub const ACCESS_PROFILE: &str = "apac-sq-access-v1";
pub const NUMERIC_PROFILE: &str = crate::numeric::PROFILE;
pub const BACKEND: &str = "rust_sq_cac_tns_bwe2_drc_off_f64_fft_v10";
pub const QUALIFICATION: &str = "independent_math_reference";

#[derive(Clone, Copy, Default)]
struct Complex {
    re: f64,
    im: f64,
}
fn fft(data: &mut [Complex]) {
    let n = data.len();
    let twiddles = &crate::numeric::tables().transform(2 * n).twiddles;
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
                let [cos, sin] = twiddles[k * n / size];
                let a = chunk[k];
                let b = chunk[k + size / 2];
                let re = b.re * cos - b.im * sin;
                let im = b.re * sin + b.im * cos;
                chunk[k] = Complex {
                    re: a.re + re,
                    im: a.im + im,
                };
                chunk[k + size / 2] = Complex {
                    re: a.re - re,
                    im: a.im - im,
                };
            }
        }
        size *= 2;
    }
}
fn modulation(n: usize) -> &'static [[f64; 2]] {
    &crate::numeric::tables().transform(n).modulation
}
fn imdct(input: &[f32]) -> Vec<f64> {
    let n = input.len();
    let mut data = vec![Complex::default(); n / 2];
    let normalization = 1. / (n as f64 * 32768.);
    for (k, z) in data.iter_mut().enumerate() {
        let [sin, cos] = modulation(n)[k];
        let a = f64::from(input[2 * k]) * normalization;
        let b = f64::from(input[n - 1 - 2 * k]) * normalization;
        *z = Complex {
            re: a * cos + b * sin,
            im: b * cos - a * sin,
        };
    }
    fft(&mut data);
    let mut dct = vec![0f64; n];
    for (k, z) in data.iter().enumerate() {
        let [sin, cos] = modulation(n)[k];
        dct[2 * k] = z.re * cos + z.im * sin;
        dct[n - 1 - 2 * k] = z.re * sin - z.im * cos;
    }
    (0..2 * n)
        .map(|i| {
            let m = i + n / 2;
            if m < n {
                dct[m]
            } else if m < 2 * n {
                -dct[2 * n - 1 - m]
            } else {
                -dct[m - 2 * n]
            }
        })
        .collect()
}
fn window(n: usize) -> &'static [f64] {
    &crate::numeric::tables().transform(n).window
}
#[derive(Clone)]
struct ChannelState {
    overlap: Vec<f64>,
}
impl ChannelState {
    fn new() -> Self {
        Self {
            overlap: vec![0.; 1024],
        }
    }
    fn render(&mut self, spectrum: &[f32], block: u8) -> Result<Vec<f32>> {
        if spectrum.len() != 1024 || spectrum.iter().any(|v| !v.is_finite()) {
            return Err(Error::new(
                "SQ synthesis",
                "requires 1024 finite coefficients",
            ));
        }
        if block > 3 {
            return Err(Error::new("SQ synthesis", "unsupported window type"));
        }
        let mut time = vec![0.; 2048];
        if block == 2 {
            for w in 0..8 {
                let samples = imdct(&spectrum[w * 128..(w + 1) * 128]);
                for (i, &sample) in samples.iter().enumerate() {
                    let index = 448 + w * 128 + i;
                    let product = sample * window(128)[i];
                    time[index] += product;
                }
            }
        } else {
            let samples = imdct(spectrum);
            for i in 0..2048 {
                let gain = match block {
                    1 if i >= 1024 => {
                        if i < 1472 {
                            1.
                        } else if i < 1600 {
                            window(128)[128 + i - 1472]
                        } else {
                            0.
                        }
                    }
                    3 if i < 1024 => {
                        if i < 448 {
                            0.
                        } else if i < 576 {
                            window(128)[i - 448]
                        } else {
                            1.
                        }
                    }
                    _ => window(1024)[i],
                };
                time[i] = samples[i] * gain;
            }
        }
        let output: Vec<f32> = (0..1024)
            .map(|i| {
                let value = (time[i] + self.overlap[i]) as f32;
                if value == 0. { 0. } else { value }
            })
            .collect();
        if output.iter().any(|v| !v.is_finite()) || time.iter().any(|v| !v.is_finite()) {
            return Err(Error::new("SQ synthesis", "nonfinite PCM"));
        }
        self.overlap.copy_from_slice(&time[1024..]);
        Ok(output)
    }
}

/// Qualified SQ, neutral scene metadata and fixed DRC-off policy.
/// Each packet produces 1024 * channel_count() interleaved samples. Errors do not advance state.
pub struct SqDecoder {
    drc: DrcState,
    hoa_context: Option<crate::frame::HoaFrameContext>,
    hoa_state: crate::frame::HoaState,
    access_context: crate::frame::ChannelFrameContext,
    scan_workspace: crate::frame::ScanWorkspace,
    context: FrameContext,
    channels: Vec<ChannelState>,
    channel_context: Option<crate::frame::ChannelFrameContext>,
    layout: crate::model::ChannelLayout,
}
impl SqDecoder {
    pub fn from_cookie(cookie: &[u8]) -> Result<Self> {
        let context = FrameContext::from_cookie(cookie)?;
        let (channel_context, hoa_context) =
            match crate::frame::DecodedFrameContext::from_cookie(cookie)? {
                crate::frame::DecodedFrameContext::Channels(c) => (c, None),
                crate::frame::DecodedFrameContext::Hoa(h) => (h.transport.clone(), Some(h)),
            };
        let multichannel = channel_context.channel_count != 2;
        if let Some(reason) = if multichannel {
            channel_context.rejection.as_deref()
        } else {
            context.packet_rejection()
        } {
            return Err(Error::new(
                "SQ decoder",
                format!("unsupported configuration: {reason}"),
            ));
        }
        Ok(Self {
            hoa_state: crate::frame::HoaState::default(),
            access_context: channel_context.clone(),
            scan_workspace: crate::frame::ScanWorkspace::default(),
            drc: if multichannel {
                channel_context.initial_state()
            } else {
                DrcState::new(&context)
            },
            context,
            channels: vec![ChannelState::new(); usize::from(channel_context.channel_count)],
            layout: channel_context.layout.clone().expect("qualified layout"),
            channel_context: (multichannel && hoa_context.is_none()).then_some(channel_context),
            hoa_context,
        })
    }
    pub fn reset(&mut self) {
        self.drc = self
            .channel_context
            .as_ref()
            .map_or_else(|| DrcState::new(&self.context), |c| c.initial_state());
        if let Some(context) = &self.hoa_context {
            self.drc = context.initial_drc_state();
        }
        self.hoa_state = crate::frame::HoaState::default();
        self.channels.fill(ChannelState::new());
        self.scan_workspace.numeric_elements = 0;
    }
    fn metadata_sha256(&self) -> String {
        if self.hoa_context.is_some() {
            return crate::model::sha256(&serde_json::to_vec(&serde_json::json!({"channels":self.drc.channels,"configuration":self.drc.configuration,"previous_nodes":self.drc.previous_nodes,"hoa":self.hoa_state})).expect("finite HOA state"));
        }
        crate::model::sha256(
            &serde_json::to_vec(&serde_json::json!({
                "channels":self.drc.channels,"configuration":self.drc.configuration,
                "previous_nodes":self.drc.previous_nodes,
            }))
            .expect("finite metadata"),
        )
    }
    /// Private state-only advancement; callers must synthesize the predecessor
    /// before exporting PCM. No public decoder method exposes stale overlap.
    fn scan_frame(&mut self, packet: &[u8]) -> Result<PrefixCounts> {
        if self.hoa_context.is_some() {
            return Err(Error::new(
                "SQ access",
                "HOA fast access is not supported; use sequential",
            ));
        }
        let mut next = self.drc.clone();
        let scanned = crate::frame::scan_channel_packet(
            &self.access_context,
            packet,
            &mut next,
            &mut self.scan_workspace,
        );
        match scanned {
            Ok(report) if report.packet_complete => {
                let mut counts = PrefixCounts::from_report(&report);
                counts.numeric_elements = self.scan_workspace.numeric_elements;
                self.drc = next;
                Ok(counts)
            }
            _ => {
                // The legacy stereo wrapper validates current spectra before
                // embedded spectra. Re-run only failed scans through that exact
                // path to preserve its first-error ordering and public errors.
                let mut validation = Self {
                    hoa_context: self.hoa_context.clone(),
                    hoa_state: self.hoa_state.clone(),
                    context: self.context.clone(),
                    drc: self.drc.clone(),
                    channels: self.channels.clone(),
                    layout: self.layout.clone(),
                    channel_context: self.channel_context.clone(),
                    access_context: self.access_context.clone(),
                    scan_workspace: crate::frame::ScanWorkspace::default(),
                };
                match validation.decode_frame_report(packet) {
                    Err(error) => Err(error),
                    Ok(_) => Err(Error::new(
                        "SQ access",
                        "state scan disagreed with the complete decoder",
                    )),
                }
            }
        }
    }
    pub fn channel_count(&self) -> u32 {
        self.channels.len() as u32
    }
    pub fn channel_layout(&self) -> &crate::model::ChannelLayout {
        &self.layout
    }
    pub fn backend(&self) -> &'static str {
        if let Some(context) = &self.hoa_context {
            return if context.salient_components() != 0 && context.salient_components() != 5 {
                hoa::COUNTS_BACKEND
            } else if context.component_orders_extended() {
                hoa::COMPONENT_ORDERS_BACKEND
            } else if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                hoa::ADDITIVE_BACKEND
            } else if context.dynamic_selection_enabled() {
                hoa::DYNAMIC_BACKEND
            } else if context.static_ambient_enabled() {
                hoa::STATIC_AMBIENT_BACKEND
            } else if context.salient_components() != 0 && context.ambient_components() != 0 {
                hoa::MIXED_BACKEND
            } else if context.salient_components() != 0 {
                hoa::SALIENT_BACKEND
            } else {
                hoa::BACKEND
            };
        }
        if self.channel_context.is_some() {
            channels::BACKEND
        } else {
            BACKEND
        }
    }
    pub fn state_profile(&self) -> &'static str {
        if let Some(context) = &self.hoa_context {
            return context.state_profile();
        }
        if self.channel_context.is_some() {
            crate::frame::CHANNEL_STATE_PROFILE
        } else {
            crate::frame::STATE_PROFILE
        }
    }
    pub fn support_scope(&self) -> &'static str {
        if let Some(context) = &self.hoa_context {
            if context.salient_components() != 0 && context.salient_components() != 5 {
                return "hoa_variable_salient_counts_sq_drc_off";
            }
            if context.ambient_combination() == crate::frame::AmbientCombination::Add {
                return if context.dynamic_selection_enabled() {
                    "hoa_dynamic9_to16_additive_sq_drc_off"
                } else if context.order() == 2 {
                    "hoa2_additive_sq_drc_off"
                } else {
                    "hoa3_additive_sq_drc_off"
                };
            }
            if context.dynamic_selection_enabled() {
                return if context.ambient_components() == 0 {
                    "hoa_dynamic9_to16_salient_sq_drc_off"
                } else {
                    "hoa_dynamic9_to16_mixed_sq_drc_off"
                };
            }
            if context.static_ambient_enabled() {
                return match (context.order(), context.salient_components()) {
                    (1, 0) => "hoa1_static_ambient_sq_drc_off",
                    (2, 5) => "hoa2_mixed_static_ambient_sq_drc_off",
                    (3, 5) => "hoa3_mixed_static_ambient_sq_drc_off",
                    _ => "hoa3_static_ambient_sq_drc_off",
                };
            }
            return match (
                context.order(),
                context.salient_components(),
                context.ambient_components(),
            ) {
                (1, 0, _) => "hoa1_ambient4_sq_drc_off",
                (2, 5, 4) => "hoa2_salient5_ambient4_sq_drc_off",
                (3, 5, 4) => "hoa3_salient5_ambient4_sq_drc_off",
                (2, 5, 0) => "hoa2_salient5_sq_drc_off",
                (3, 5, 0) => "hoa3_salient5_sq_drc_off",
                _ => "hoa3_ambient16_sq_drc_off",
            };
        }
        if matches!(self.channel_count(), 12 | 24) {
            "single_asc_714_222_sq_drc_off"
        } else if self.channel_context.is_some() {
            "single_asc_mono_51_71_sq_drc_off"
        } else {
            "stereo_sq_drc_off_neutral_scene_asp"
        }
    }
    pub fn hoa_numeric_profile(&self) -> Option<&'static str> {
        self.hoa_context.as_ref().map(|c| c.numeric_profile())
    }
    pub fn decode_frame(&mut self, packet: &[u8]) -> Result<Vec<f32>> {
        self.decode_frame_report(packet).map(|(samples, _)| samples)
    }
    pub(crate) fn decode_frame_report(
        &mut self,
        packet: &[u8],
    ) -> Result<(Vec<f32>, FrameStateCounts)> {
        if let Some(context) = &self.hoa_context {
            return hoa::decode(
                context,
                &mut self.drc,
                &mut self.hoa_state,
                &mut self.channels,
                packet,
            );
        }
        if let Some(context) = &self.channel_context {
            return channels::decode(context, &mut self.drc, &mut self.channels, packet);
        }
        let parse_timer = std::time::Instant::now();
        let mut next_drc = self.drc.clone();
        let decoded =
            parse_packet_with_state(&self.context, packet, &mut next_drc).map_err(|e| {
                let mut error = Error::new("SQ spectrum", e.to_string());
                error.bit_offset = Some(e.bit_offset);
                error
            })?;
        if !decoded.packet_complete {
            let frame = decoded.frame();
            let mut error = Error::new(
                "SQ decoder",
                format!("unsupported frame: {}", frame.stop_reason),
            );
            error.bit_offset = Some(
                frame
                    .diagnostics
                    .first()
                    .map_or(frame.stop_bit_offset, |d| d.bit_offset),
            );
            return Err(error);
        }
        let parse_seconds = parse_timer.elapsed().as_secs_f64();
        let synthesis_timer = std::time::Instant::now();
        let mut next = self.channels.clone();
        let mut output = render_packet(&mut next, &decoded)?;
        output.1.parse_seconds = parse_seconds;
        output.1.synthesis_seconds = synthesis_timer.elapsed().as_secs_f64();
        self.channels = next;
        self.drc = next_drc;
        Ok(output)
    }
}

#[derive(Default)]
pub(crate) struct FrameStateCounts {
    pub cpe_absent: bool,
    pub absent_elements: u64,
    pub embedded_absent_elements: u64,
    pub embedded_preroll_frames: u64,
    pub embedded_cpe_absent: u64,
    pub drc_payload_frames: u64,
    pub drc_missing_history_frames: u64,
    pub parse_seconds: f64,
    pub synthesis_seconds: f64,
}

#[derive(Default)]
struct PrefixCounts {
    frames: u64,
    present_elements: u64,
    numeric_elements: u64,
    drc_payload_frames: u64,
    drc_missing_history_frames: u64,
}
impl PrefixCounts {
    fn from_report(report: &crate::frame::ChannelPacketReport) -> Self {
        let mut out = report
            .embedded_preroll
            .as_ref()
            .map_or_else(Self::default, |p| Self::from_report(&p.report));
        out.frames += 1;
        out.present_elements += report.elements.iter().filter(|e| e.present).count() as u64;
        out.drc_payload_frames += u64::from(report.drc_complete == Some(true));
        out.drc_missing_history_frames += u64::from(report.drc_history_sufficient == Some(false));
        out
    }
}

fn render_packet(
    channels: &mut [ChannelState],
    decoded: &PacketReport,
) -> Result<(Vec<f32>, FrameStateCounts)> {
    let mut counts = FrameStateCounts::default();
    if let Some(preroll) = &decoded.embedded_preroll {
        // The internal frame replaces the overlap used by the current frame.
        // Its PCM is discarded; the entire outer packet commits atomically.
        let (_, inner) = render_packet(channels, &preroll.report)?;
        counts.drc_payload_frames = inner.drc_payload_frames;
        counts.drc_missing_history_frames = inner.drc_missing_history_frames;
        counts.embedded_preroll_frames = 1 + inner.embedded_preroll_frames;
        counts.embedded_cpe_absent = u64::from(inner.cpe_absent) + inner.embedded_cpe_absent;
    }
    counts.drc_payload_frames += u64::from(decoded.drc_complete == Some(true));
    counts.drc_missing_history_frames += u64::from(decoded.drc_history_sufficient == Some(false));
    counts.cpe_absent = decoded.cpe_absent();
    let mut sides = Vec::with_capacity(2);
    for (index, state) in channels.iter_mut().enumerate() {
        let samples = if counts.cpe_absent {
            // Exact zero current spectrum: retain the previous tail for this
            // output interval, then clear it. No fabricated encoded channels.
            let samples: Vec<f32> = state
                .overlap
                .iter()
                .map(|&v| {
                    let value = v as f32;
                    if value == 0. { 0. } else { value }
                })
                .collect();
            if samples.iter().any(|v| !v.is_finite()) {
                return Err(Error::new("SQ synthesis", "nonfinite PCM"));
            }
            state.overlap.fill(0.);
            samples
        } else {
            state.render(
                &decoded.bwe2.channels_after_bwe2[index].scaled,
                decoded.bwe2.tns.cac.spectrum.channels[index].ics.block_type,
            )?
        };
        sides.push(samples);
    }
    let mut output = Vec::with_capacity(2048);
    for (&left, &right) in sides[0].iter().zip(&sides[1]) {
        output.extend([left, right]);
    }
    Ok((output, counts))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;
    #[test]
    fn mathematical_windows_are_symmetric_and_power_complementary() {
        for n in [128, 1024] {
            let values = window(n);
            assert!(values[..n].windows(2).all(|w| w[0] < w[1]));
            for i in 0..n {
                assert_eq!(values[i].to_bits(), values[2 * n - 1 - i].to_bits());
                let a = values[i];
                let b = values[n - 1 - i];
                assert!((a * a + b * b - 1.).abs() < 3e-16);
            }
        }
    }
    #[test]
    fn transform_matches_direct_definition() {
        for n in [128, 1024] {
            let mut input = vec![0.; n];
            input[0] = 1.;
            input[3] = -2.;
            input[n - 1] = 0.5;
            let result = imdct(&input);
            for (i, &x) in result.iter().enumerate() {
                let expected: [f64; 3] = [0., 3., (n - 1) as f64];
                let sum = expected
                    .into_iter()
                    .zip([1., -2., 0.5])
                    .map(|(k, v)| {
                        v * (PI / n as f64 * (i as f64 + 0.5 + n as f64 / 2.) * (k + 0.5)).cos()
                    })
                    .sum::<f64>()
                    / (n as f64 * 32768.);
                assert!((x - sum).abs() < 1e-12, "{n} {i} {x} {sum}");
            }
        }
    }
    #[test]
    fn every_long_and_short_frequency_has_the_correct_basis() {
        for n in [128, 1024] {
            let mut input = vec![0.; n];
            for k in 0..n {
                input[k] = 1.;
                for (i, actual) in imdct(&input).into_iter().enumerate() {
                    let expected =
                        (PI / n as f64 * (i as f64 + 0.5 + n as f64 / 2.) * (k as f64 + 0.5)).cos()
                            / (n as f64 * 32768.);
                    assert!((actual - expected).abs() < 1e-18, "{n} {k} {i}");
                }
                input[k] = 0.;
            }
        }
    }
    #[test]
    fn window_transitions_and_reset_state() {
        let mut state = ChannelState::new();
        let zero = vec![0.; 1024];
        for block in [0, 1, 2, 2, 3, 0] {
            assert!(
                state
                    .render(&zero, block)
                    .unwrap()
                    .iter()
                    .all(|v| v.to_bits() == 0)
            );
        }
        for first in 0..4 {
            for second in 0..4 {
                state.render(&zero, first).unwrap();
                state.render(&zero, second).unwrap();
            }
        }
        assert!(state.render(&zero, 4).is_err());
        assert!(state.render(&[f32::NAN; 1024], 0).is_err());
    }
}
