//! Independent SQ mathematics: fixed IEEE constants, Float64 synthesis and overlap.
//! Ordinary products and sums round separately; only final PCM is cast to Float32.
use crate::prelude::*;
#[cfg(test)]
mod access_tests;
#[cfg(test)]
mod asp_tests;
#[cfg(test)]
mod channel_tests;
mod channels;
#[cfg(test)]
mod drc_tests;
mod hoa;
#[cfg(test)]
mod hoa_access_tests;
#[cfg(test)]
mod hoa_tests;
#[cfg(test)]
mod mode_tests;
#[cfg(test)]
mod shared_tests;
mod stream;
use crate::{
    error::{DecodeError, Result},
    frame::{
        ChannelFrameContext, DrcState, FrameContext, HoaFrameContext, HoaState, PacketReport,
        ScanWorkspace, StreamFrameContext, stream::StreamState,
    },
};

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
    crate::numeric::tables().transform(n).modulation
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
    crate::numeric::tables().transform(n).window
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
            return Err(DecodeError::new(
                "SQ synthesis",
                "requires 1024 finite coefficients",
            ));
        }
        if block > 3 {
            return Err(DecodeError::new("SQ synthesis", "unsupported window type"));
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
            return Err(DecodeError::new("SQ synthesis", "nonfinite PCM"));
        }
        self.overlap.copy_from_slice(&time[1024..]);
        Ok(output)
    }
}

/// Qualified SQ, neutral scene metadata and fixed DRC-off policy.
/// Each packet produces 1024 * channel_count interleaved samples. Errors do not advance state.
pub struct Decoder {
    engine: Engine,
    drc: DrcState,
    channels: Vec<ChannelState>,
    layout: crate::model::ChannelLayout,
    /// The single-ASC transport context state scans and layout profiles read.
    access: ChannelFrameContext,
    scan: ScanWorkspace,
    /// Retained by parsed packets, so moving this decoder preserves ownership
    /// and a replacement cannot reuse the identity of a dropped decoder.
    owner: Arc<()>,
    /// Counts commits and resets; a parsed packet is valid for one generation.
    generation: u64,
    /// Decoding records no syntax; tests switch to report parsing to prove
    /// that recording never changes a decision.
    mode: crate::frame::ParseMode,
}
impl Clone for Decoder {
    fn clone(&self) -> Self {
        Self {
            engine: self.engine.clone(),
            drc: self.drc.clone(),
            channels: self.channels.clone(),
            layout: self.layout.clone(),
            access: self.access.clone(),
            scan: ScanWorkspace::default(),
            owner: Arc::new(()),
            generation: self.generation,
            mode: self.mode,
        }
    }
}
/// The four decoding paths; each owns its configuration and stream state.
/// A decoder holds exactly one, so variant sizes do not multiply.
#[derive(Clone)]
#[allow(clippy::large_enum_variant)]
enum Engine {
    Stereo {
        context: FrameContext,
    },
    Channels {
        context: ChannelFrameContext,
    },
    Hoa {
        context: HoaFrameContext,
        state: HoaState,
    },
    Composite {
        context: Box<StreamFrameContext>,
        state: StreamState,
    },
}

/// Which decoding path a configuration selected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamKind {
    /// Single-ASC stereo with neutral scene metadata and ASP.
    Stereo,
    /// Single-ASC mono or multichannel layouts.
    Channels,
    /// Single-ASC higher-order ambisonics.
    Hoa,
    /// Multiple ASCs, shared configuration or HOA plus SQ components.
    Composite,
}

/// Output description of a qualified stream.
#[derive(Debug, Clone, Copy)]
pub struct StreamInfo<'a> {
    /// Output sample rate in Hz.
    pub sample_rate_hz: u64,
    /// Interleaved output channels.
    pub channel_count: u32,
    /// PCM frames each outer packet produces.
    pub frame_samples: u32,
    /// The decoding path.
    pub kind: StreamKind,
    /// The output channel layout.
    pub layout: &'a crate::model::ChannelLayout,
}

impl Decoder {
    /// Parse `cookie` and build the decoder ([`Config::parse`] then
    /// [`Decoder::new`]).
    ///
    /// [`Config::parse`]: crate::Config::parse
    pub fn from_cookie(cookie: &[u8]) -> Result<Self> {
        Self::new(&crate::config::Config::parse(cookie)?)
    }
    /// Build the decoder from a configuration parsed once by the caller.
    pub fn new(config: &crate::config::Config) -> Result<Self> {
        let context = FrameContext::from_config(config);
        let decoded_context = crate::frame::DecodedFrameContext::from_config(config)?;
        if let crate::frame::DecodedFrameContext::Stream(stream) = decoded_context {
            if let Some(reason) = stream.rejection() {
                return Err(DecodeError::new(
                    "SQ decoder",
                    format!("unsupported configuration: {reason}"),
                ));
            }
            return Ok(Self {
                drc: stream.initial_drc_state(),
                access: stream.first_core().clone(),
                channels: vec![ChannelState::new(); stream.synthesis_channel_count()],
                layout: stream.channel_layout().clone(),
                engine: Engine::Composite {
                    state: stream.initial_state(),
                    context: stream,
                },
                scan: ScanWorkspace::default(),
                owner: Arc::new(()),
                generation: 0,
                mode: crate::frame::ParseMode::Decode,
            });
        }
        let (channel_context, hoa_context) = match decoded_context {
            crate::frame::DecodedFrameContext::Channels(c) => (c, None),
            crate::frame::DecodedFrameContext::Hoa(h) => (h.transport.clone(), Some(h)),
            crate::frame::DecodedFrameContext::Stream(_) => unreachable!("handled composite"),
        };
        let multichannel = hoa_context.is_some() || channel_context.channel_count != 2;
        if let Some(reason) = if multichannel {
            channel_context.rejection.as_deref()
        } else {
            context.packet_rejection()
        } {
            return Err(DecodeError::new(
                "SQ decoder",
                format!("unsupported configuration: {reason}"),
            ));
        }
        let drc = if multichannel {
            channel_context.initial_state()
        } else {
            DrcState::new(&context)
        };
        let channels = vec![ChannelState::new(); usize::from(channel_context.channel_count)];
        let layout = channel_context.layout.clone().expect("qualified layout");
        let engine = match hoa_context {
            Some(context) => Engine::Hoa {
                context,
                state: HoaState::default(),
            },
            None if multichannel => Engine::Channels {
                context: channel_context.clone(),
            },
            None => Engine::Stereo { context },
        };
        Ok(Self {
            engine,
            drc,
            channels,
            layout,
            access: channel_context,
            scan: ScanWorkspace::default(),
            owner: Arc::new(()),
            generation: 0,
            mode: crate::frame::ParseMode::Decode,
        })
    }
    /// The same decoder parsing packets as reports, with recorded syntax.
    #[cfg(test)]
    pub(crate) fn recording(mut self) -> Self {
        self.mode = crate::frame::ParseMode::Report;
        self
    }
    /// The output description.
    pub fn info(&self) -> StreamInfo<'_> {
        let (kind, sample_rate_hz) = match &self.engine {
            Engine::Stereo { .. } => (StreamKind::Stereo, self.access.sample_rate_hz()),
            Engine::Channels { .. } => (StreamKind::Channels, self.access.sample_rate_hz()),
            Engine::Hoa { context, .. } => (StreamKind::Hoa, context.sample_rate_hz()),
            Engine::Composite { context, .. } => (StreamKind::Composite, context.sample_rate_hz()),
        };
        StreamInfo {
            sample_rate_hz,
            channel_count: self.channel_count(),
            frame_samples: 1024,
            kind,
            layout: &self.layout,
        }
    }
    fn channel_count(&self) -> u32 {
        match &self.engine {
            Engine::Composite { context, .. } => context.channel_count(),
            _ => self.channels.len() as u32,
        }
    }
    /// Return to the initial state, as after [`Decoder::new`]; the next packet
    /// must be decodable from a fresh state.
    pub fn reset(&mut self) {
        match &mut self.engine {
            Engine::Stereo { context } => self.drc = DrcState::new(context),
            Engine::Channels { context } => self.drc = context.initial_state(),
            Engine::Hoa { context, state } => {
                self.drc = context.initial_drc_state();
                *state = HoaState::default();
            }
            Engine::Composite { context, state } => {
                self.drc = context.initial_drc_state();
                *state = context.initial_state();
            }
        }
        self.channels.fill(ChannelState::new());
        self.scan.numeric_elements = 0;
        self.generation += 1;
    }
    /// Decode one outer packet into `out` (at least 1024 × channel_count
    /// interleaved samples). On error the state is unchanged.
    pub fn decode(&mut self, packet: &[u8], out: &mut [f32]) -> Result<FrameInfo> {
        self.check_output(out)?;
        let parsed = self.parse(packet)?;
        self.synthesize(parsed, out)
    }
    /// [`Decoder::decode`] into a new buffer.
    pub fn decode_vec(&mut self, packet: &[u8]) -> Result<Vec<f32>> {
        let parsed = self.parse(packet)?;
        self.commit(parsed).map(|(samples, _)| samples)
    }
    fn check_output(&self, out: &[f32]) -> Result<()> {
        let needed = 1024 * self.channel_count() as usize;
        if out.len() < needed {
            return Err(DecodeError::new(
                "SQ decoder",
                format!(
                    "output buffer holds {} samples, {needed} required",
                    out.len()
                ),
            ));
        }
        Ok(())
    }
    /// First stage of [`Decoder::decode`]: parse one packet against copies of
    /// the current state. Nothing is committed until [`Decoder::synthesize`].
    /// The parsed packet belongs to this decoder, not to any of its clones.
    pub fn parse(&self, packet: &[u8]) -> Result<ParsedPacket> {
        let body = match &self.engine {
            Engine::Stereo { context } => {
                let mut drc = self.drc.clone();
                let report =
                    crate::frame::parse_packet_with_mode(context, packet, &mut drc, self.mode)
                        .map_err(|e| {
                            let mut error = DecodeError::new("SQ spectrum", e.to_string());
                            error.bit_offset = Some(e.bit_offset);
                            error
                        })?;
                if !report.packet_complete {
                    let frame = report.frame();
                    let mut error = DecodeError::new(
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
                Parsed::Stereo { report, drc }
            }
            Engine::Channels { context } => {
                let (report, drc) = channels::parse(context, &self.drc, packet, self.mode)?;
                Parsed::Channels { report, drc }
            }
            Engine::Hoa { context, state } => {
                let (report, drc, state) =
                    hoa::parse(context, &self.drc, state, packet, self.mode)?;
                Parsed::Hoa { report, drc, state }
            }
            Engine::Composite { context, state } => {
                let (report, drc, state) =
                    stream::parse(context, &self.drc, state, packet, self.mode)?;
                Parsed::Composite { report, drc, state }
            }
        };
        Ok(ParsedPacket {
            owner: Arc::clone(&self.owner),
            generation: self.generation,
            body,
        })
    }
    /// Second stage of [`Decoder::decode`]: synthesize a packet parsed from
    /// the current state into `out`, then commit the overlap and the parsed
    /// state together. A packet from another decoder (including a clone), or
    /// parsed before any later commit or reset, is rejected without changing
    /// state or output. Moving the original decoder preserves packet ownership.
    pub fn synthesize(&mut self, parsed: ParsedPacket, out: &mut [f32]) -> Result<FrameInfo> {
        self.check_output(out)?;
        let (samples, info) = self.commit(parsed)?;
        out[..samples.len()].copy_from_slice(&samples);
        Ok(info)
    }
    fn commit(&mut self, parsed: ParsedPacket) -> Result<(Vec<f32>, FrameInfo)> {
        if !Arc::ptr_eq(&parsed.owner, &self.owner) {
            return Err(DecodeError::new(
                "SQ decoder",
                "parsed packet belongs to a different decoder",
            ));
        }
        if parsed.generation != self.generation {
            return Err(DecodeError::new(
                "SQ decoder",
                "parsed packet is stale: the decoder state changed after parsing",
            ));
        }
        let mut next = self.channels.clone();
        let (samples, info) = match &parsed.body {
            Parsed::Stereo { report, .. } => render_packet(&mut next, report)?,
            Parsed::Channels { report, .. } => channels::render(&mut next, report)?,
            Parsed::Hoa { report, .. } => channels::render(&mut next, &report.packet)?,
            Parsed::Composite { report, .. } => stream::render(&mut next, report)?,
        };
        self.channels = next;
        match (parsed.body, &mut self.engine) {
            (Parsed::Stereo { drc, .. } | Parsed::Channels { drc, .. }, _) => self.drc = drc,
            (Parsed::Hoa { drc, state, .. }, Engine::Hoa { state: current, .. }) => {
                self.drc = drc;
                *current = state;
            }
            (Parsed::Composite { drc, state, .. }, Engine::Composite { state: current, .. }) => {
                self.drc = drc;
                *current = state;
            }
            _ => unreachable!("a parsed packet matches its engine"),
        }
        self.generation += 1;
        Ok((samples, info))
    }
    /// The committed state the research layer digests as
    /// `metadata_after_processing_sha256`.
    pub fn metadata_state(&self) -> MetadataState<'_> {
        MetadataState {
            drc: &self.drc,
            components: match &self.engine {
                Engine::Composite { state, .. } => Some(state),
                _ => None,
            },
            hoa: match &self.engine {
                Engine::Hoa { state, .. } => Some(state),
                _ => None,
            },
        }
    }
    /// Test fingerprint of the committed state; the same JSON the research
    /// layer digests (`apac_research::decode::metadata_sha256`).
    #[cfg(test)]
    pub(crate) fn metadata_sha256(&self) -> String {
        let state = self.metadata_state();
        let drc = state.drc;
        let mut value = serde_json::json!({"channels":drc.channels,"configuration":drc.configuration,"previous_nodes":drc.previous_nodes});
        if !drc.previous_sequences.is_empty() {
            value["previous_sequences"] = serde_json::json!(drc.previous_sequences);
        }
        if drc.shared_syntax_used {
            value["shared_drc_syntax_profile"] =
                serde_json::json!(crate::frame::HOA_SHARED_DRC_PROFILE);
        }
        if let Some(graph) = &drc.scene_graph {
            value["scene_graph"] = serde_json::json!(graph);
        }
        if let Some(components) = state.components {
            value["components"] = serde_json::json!(components);
        } else if let Some(hoa) = state.hoa {
            value["hoa"] = serde_json::json!(hoa);
        }
        crate::model::sha256(&serde_json::to_vec(&value).expect("finite metadata"))
    }
    /// Advance the stream state over one packet without synthesis (fast
    /// access). The overlap is stale afterwards: callers must decode the
    /// predecessor of the first packet they export before exporting PCM.
    pub fn advance(&mut self, packet: &[u8]) -> Result<AdvanceInfo> {
        let mut next = self.drc.clone();
        let (mut next_hoa, mut next_stream) = (None, None);
        self.scan.numeric_elements = 0;
        let scanned = match &self.engine {
            Engine::Composite { context, state } => {
                let mut state = state.clone();
                let scanned = crate::frame::stream::parse_with_state(
                    context,
                    packet,
                    &mut next,
                    &mut state,
                    crate::frame::ParseMode::Scan,
                    &mut self.scan,
                )
                .map(|r| r.packet_complete.then(|| AdvanceInfo::from_stream(&r)));
                next_stream = Some(state);
                scanned
            }
            Engine::Hoa { state, .. } => {
                let mut state = state.clone();
                let scanned = crate::frame::scan_hoa_packet(
                    &self.access,
                    packet,
                    &mut next,
                    &mut state,
                    &mut self.scan,
                )
                .map(|r| r.packet_complete.then(|| AdvanceInfo::from_report(&r)));
                next_hoa = Some(state);
                scanned
            }
            Engine::Stereo { .. } | Engine::Channels { .. } => {
                crate::frame::scan_channel_packet(&self.access, packet, &mut next, &mut self.scan)
                    .map(|r| r.packet_complete.then(|| AdvanceInfo::from_report(&r)))
            }
        };
        match scanned {
            Ok(Some(mut counts)) => {
                counts.numeric_elements = self.scan.numeric_elements;
                self.drc = next;
                match &mut self.engine {
                    Engine::Hoa { state, .. } => *state = next_hoa.expect("scanned HOA state"),
                    Engine::Composite { state, .. } => {
                        *state = next_stream.expect("scanned stream state");
                    }
                    Engine::Stereo { .. } | Engine::Channels { .. } => {}
                }
                self.generation += 1;
                Ok(counts)
            }
            _ => {
                // The legacy stereo wrapper validates current spectra before
                // embedded spectra. Re-run only failed scans through that exact
                // path to preserve its first-error ordering and public errors.
                match self.clone().decode_vec(packet) {
                    Err(error) => Err(error),
                    Ok(_) => Err(DecodeError::new(
                        "SQ access",
                        "state scan disagreed with the complete decoder",
                    )),
                }
            }
        }
    }
    /// The composite stream context, when several components are combined.
    pub fn composite(&self) -> Option<&StreamFrameContext> {
        match &self.engine {
            Engine::Composite { context, .. } => Some(context),
            _ => None,
        }
    }
    /// The single-ASC HOA context.
    pub fn hoa(&self) -> Option<&HoaFrameContext> {
        match &self.engine {
            Engine::Hoa { context, .. } => Some(context),
            _ => None,
        }
    }
    /// The single-ASC transport context fast access scans with.
    pub fn transport(&self) -> &ChannelFrameContext {
        &self.access
    }
    /// The declared components of a composite stream.
    pub fn components(&self) -> Option<&[crate::frame::StreamComponentConfiguration]> {
        self.composite().map(|c| c.components())
    }
    /// The HOA context of component `index` (of a composite stream, or index 0
    /// of a single-ASC HOA stream).
    pub fn hoa_component(&self, index: usize) -> Option<&HoaFrameContext> {
        match &self.engine {
            Engine::Composite { context, .. } => context.hoa_component(index),
            Engine::Hoa { context, .. } if index == 0 => Some(context),
            _ => None,
        }
    }
}

/// Borrowed decoder state: DRC history plus the composite or HOA state.
pub struct MetadataState<'a> {
    /// DRC syntax history.
    pub drc: &'a DrcState,
    /// Per-component state of a composite stream.
    pub components: Option<&'a crate::frame::stream::StreamState>,
    /// HOA state of a single-ASC HOA stream.
    pub hoa: Option<&'a crate::frame::HoaState>,
}
/// What one decoded outer packet contained.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct FrameInfo {
    /// The current frame's stereo CPE was absent (exact zero spectrum).
    pub cpe_absent: bool,
    /// Absent channel elements in the current frame.
    pub absent_elements: u64,
    /// Absent channel elements in embedded preroll frames.
    pub embedded_absent_elements: u64,
    /// Embedded preroll frames synthesized before the current frame.
    pub embedded_preroll_frames: u64,
    /// Embedded preroll frames whose stereo CPE was absent.
    pub embedded_cpe_absent: u64,
    /// Frames carrying a complete DRC payload (parsed, not applied).
    pub drc_payload_frames: u64,
    /// Frames whose DRC payload had no earlier gain node at or before the frame
    /// start.
    pub drc_missing_history_frames: u64,
}
/// A packet parsed by [`Decoder::parse`], awaiting [`Decoder::synthesize`].
pub struct ParsedPacket {
    owner: Arc<()>,
    generation: u64,
    body: Parsed,
}
#[allow(clippy::large_enum_variant)]
enum Parsed {
    Stereo {
        report: PacketReport,
        drc: DrcState,
    },
    Channels {
        report: crate::frame::ChannelPacketReport,
        drc: DrcState,
    },
    Hoa {
        report: crate::frame::HoaPacketReport,
        drc: DrcState,
        state: HoaState,
    },
    Composite {
        report: crate::frame::StreamPacketReport,
        drc: DrcState,
        state: StreamState,
    },
}

/// What [`Decoder::advance`] scanned in one outer packet.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AdvanceInfo {
    /// Frames scanned, embedded preroll frames included.
    pub frames: u64,
    /// Present channel elements in the scanned frames.
    pub present_elements: u64,
    /// Present elements whose spectra were dequantized to advance state.
    pub numeric_elements: u64,
    /// Frames carrying a complete DRC payload (parsed, not applied).
    pub drc_payload_frames: u64,
    /// Frames whose DRC payload had no earlier gain node at or before the frame
    /// start.
    pub drc_missing_history_frames: u64,
}
impl AdvanceInfo {
    fn from_stream(report: &crate::frame::StreamPacketReport) -> Self {
        let mut out = report
            .embedded_preroll
            .as_ref()
            .map_or_else(Self::default, |p| Self::from_stream(&p.report));
        out.frames += 1;
        out.present_elements += report
            .components
            .iter()
            .flat_map(|c| &c.elements)
            .filter(|e| e.present)
            .count() as u64;
        out.drc_payload_frames += u64::from(report.drc_complete == Some(true));
        out.drc_missing_history_frames += u64::from(report.drc_history_sufficient == Some(false));
        out
    }
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
) -> Result<(Vec<f32>, FrameInfo)> {
    let mut counts = FrameInfo::default();
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
                return Err(DecodeError::new("SQ synthesis", "nonfinite PCM"));
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
