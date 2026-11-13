//! Range-limited PCM decoding over a [`PacketSource`]: packet selection,
//! priming and range cropping, sequential or fast (state-only prefix) access.
use crate::{PacketSource, Range};
use apac_core::{Decoder, StreamKind, error::DecodeError};
use std::{
    fmt,
    time::{Duration, Instant},
};

/// How the packets before the output are processed.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Access {
    /// Decode every packet from the first one.
    #[default]
    Sequential,
    /// Advance state only (no synthesis) up to the packet before the one
    /// holding the first output frame, then decode from that packet on.
    /// Requires [`PacketSource::supports_fast_access`].
    Fast,
}

/// A reader failure: from the source, from the decoder (with the source
/// index of the failing packet, if any), or a rejected request.
#[derive(Debug, Clone, PartialEq)]
pub enum ReadError<E> {
    /// The source failed: I/O, structure or integrity.
    Source(E),
    /// The decoder rejected the configuration or a packet.
    Decode {
        /// The decoder's error.
        error: DecodeError,
        /// Source index of the failing packet; `None` for the configuration.
        packet_index: Option<u64>,
    },
    /// The request or the source description was rejected.
    Invalid {
        /// The failing stage (`"SQ access"`, `"SQ decoder"`, `"packet bundle"`).
        operation: &'static str,
        /// The rejection text.
        message: String,
    },
}
impl<E: fmt::Display> fmt::Display for ReadError<E> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Source(error) => error.fmt(f),
            Self::Decode { error, .. } => error.fmt(f),
            Self::Invalid { operation, message } => write!(f, "{operation}: {message}"),
        }
    }
}
impl<E: fmt::Debug + fmt::Display> std::error::Error for ReadError<E> {}
fn invalid<E>(operation: &'static str, message: impl Into<String>) -> ReadError<E> {
    ReadError::Invalid {
        operation,
        message: message.into(),
    }
}

/// Time spent in each stage, accumulated over the reader's life.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Timings {
    /// Reading packets, including the final verification in `finish`.
    pub read: Duration,
    /// State-only access to the prefix.
    pub scan: Duration,
    /// Parsing decoded packets.
    pub parse: Duration,
    /// Synthesizing and committing decoded packets.
    pub synthesize: Duration,
    /// Whole decoded packets (parse through commit).
    pub packet: Duration,
}

/// What the reader processed, accumulated over its life.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Stats {
    /// Packets advanced with state-only access.
    pub prefix_packets: u64,
    /// Frames (including embedded preroll frames) advanced with state-only
    /// access.
    pub prefix_frames: u64,
    /// Prefix packets whose spectra were dequantized.
    pub prefix_numeric_packets: u64,
    /// Prefix elements whose spectra were dequantized.
    pub prefix_numeric_elements: u64,
    /// Present prefix elements only checked against finite-value bounds.
    pub prefix_bounded_elements: u64,
    /// Fully decoded packets.
    pub decoded_packets: u64,
    /// Decoded packets wholly before the output.
    pub warmup_packets: u64,
    /// Decoded stereo packets whose CPE was absent.
    pub cpe_absent_packets: u64,
    /// Absent channel elements in decoded packets.
    pub absent_elements: u64,
    /// Absent channel elements in embedded preroll frames.
    pub embedded_absent_elements: u64,
    /// Embedded preroll frames synthesized.
    pub embedded_preroll_frames: u64,
    /// Embedded preroll frames whose stereo CPE was absent.
    pub embedded_cpe_absent_frames: u64,
    /// Frames carrying a complete DRC payload (parsed, not applied).
    pub drc_payload_frames: u64,
    /// Frames whose DRC payload had no earlier gain node at or before the
    /// frame start (no preceding parsed history).
    pub drc_missing_history_frames: u64,
    /// Output frames returned by `read`.
    pub saved_frames: u64,
    /// Source index of the first fully decoded packet.
    pub first_synthesis_packet: Option<u64>,
    /// Time per stage.
    pub timings: Timings,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PassState {
    Reading,
    /// A packet beyond the range was read but not decoded.
    RangeEnd,
    /// The source ended and verified the current pass.
    Eof,
    /// A consumed packet did not commit; seek must replay the prefix.
    DecodeFailed,
    /// The source's position or integrity is uncertain; do not reuse it.
    SourceFailed,
}

/// Decodes a frame range of a [`PacketSource`] into interleaved `f32` PCM.
///
/// ```no_run
/// use apac_container::{Access, CafReader, Reader};
/// use std::fs::File;
///
/// let caf = CafReader::new(File::open("input.caf")?)?;
/// let mut reader = Reader::open(caf, None, None, Access::Sequential)?;
/// let channels = reader.decoder().info().channel_count as usize;
/// let mut pcm = vec![0f32; 1024 * channels];
/// loop {
///     let frames = reader.read(&mut pcm)?;
///     if frames == 0 {
///         break;
///     }
///     // use pcm[..frames * channels]
/// }
/// let (_source, _decoder, stats) = reader.finish()?;
/// # let _ = stats;
/// # Ok::<(), Box<dyn std::error::Error>>(())
/// ```
///
/// Integrity: every pass over the source starts at its first packet and is
/// verified when it reaches the end. A pass is never abandoned unverified:
/// before [`Reader::seek`] rewinds (and when [`Reader::open`] receives a
/// source that was already read), the rest of the current pass is read and
/// verified first.
///
/// Failures: after a packet fails to decode, `read` and `finish` are refused
/// until a successful `seek` replays the prefix (the failed packet is never
/// skipped). A source failure (read, verification or rewind) is terminal:
/// `read`, `seek` and `finish` are refused afterwards.
pub struct Reader<S> {
    source: S,
    decoder: Decoder,
    access: Access,
    range: Range,
    samples: Vec<f32>,
    stats: Stats,
    state: PassState,
}
impl<S: PacketSource> Reader<S> {
    /// Check the source against its decoder and select `frames` (default:
    /// the whole valid audio) from `start` (default: the window start).
    /// A source already read in its current pass is verified to the end,
    /// then rewound so the new decoder starts at the first packet.
    pub fn open(
        source: S,
        start: Option<u64>,
        frames: Option<u64>,
        access: Access,
    ) -> Result<Self, ReadError<S::Error>> {
        if access == Access::Fast && !source.supports_fast_access() {
            return Err(invalid("SQ access", "fast access requires a CAF/MP4 file"));
        }
        let table = source
            .table()
            .ok_or_else(|| invalid("SQ decoder", "missing packet table"))?;
        let range = source
            .range(start, frames.unwrap_or((table.valid_frames as u64).max(1)))
            .map_err(ReadError::Source)?;
        let decoder = Decoder::new(source.config()).map_err(|error| ReadError::Decode {
            error,
            packet_index: None,
        })?;
        let info = decoder.info();
        if matches!(info.kind, StreamKind::Hoa | StreamKind::Composite)
            && source.first_packet_index() != 0
        {
            return Err(invalid(
                "SQ access",
                "HOA input must include packet zero to establish sequential state",
            ));
        }
        if source.channels() != info.channel_count {
            return Err(invalid(
                "SQ decoder",
                "input channel count disagrees with decoder",
            ));
        }
        if let Some(layout) = source.layout()
            && !layout.equivalent(info.layout)
        {
            return Err(invalid(
                "SQ decoder",
                format!(
                    "input channel layout disagrees with decoder: expected cookie layout tag {:#010x}, zero bitmap and no descriptions",
                    info.layout.tag
                ),
            ));
        }
        let samples = vec![0f32; 1024 * info.channel_count as usize];
        let mut reader = Self {
            source,
            decoder,
            access,
            range,
            samples,
            stats: Stats::default(),
            state: PassState::Reading,
        };
        if reader.source.consumed_packets() != 0 {
            reader.restart_pass()?;
        }
        Ok(reader)
    }
    /// The decoder, in the state after the last processed packet.
    pub fn decoder(&self) -> &Decoder {
        &self.decoder
    }
    /// The packet source.
    pub fn source(&self) -> &S {
        &self.source
    }
    /// The frame range being read (updated by [`Reader::seek`]).
    pub fn range(&self) -> &Range {
        &self.range
    }
    /// The access mode chosen at open.
    pub fn access(&self) -> Access {
        self.access
    }
    /// Counts and timings accumulated over the reader's life.
    pub fn stats(&self) -> &Stats {
        &self.stats
    }
    fn check_failed(&self) -> Result<(), ReadError<S::Error>> {
        match self.state {
            PassState::SourceFailed => Err(invalid(
                "SQ access",
                "reader cannot continue after a source error",
            )),
            PassState::DecodeFailed => Err(invalid(
                "SQ access",
                "seek is required after a packet decode failure",
            )),
            _ => Ok(()),
        }
    }
    /// Never discard a pass that may have supplied output until verified.
    /// A failure during verification or rewind leaves this reader unusable.
    fn restart_pass(&mut self) -> Result<(), ReadError<S::Error>> {
        self.state = PassState::SourceFailed;
        let timer = Instant::now();
        let restarted = self
            .source
            .verify_remaining()
            .and_then(|()| self.source.rewind());
        self.stats.timings.read += timer.elapsed();
        restarted.map_err(ReadError::Source)?;
        self.decoder.reset();
        self.state = PassState::Reading;
        Ok(())
    }
    /// [`Reader::read_with`] without observing the output start.
    pub fn read(&mut self, out: &mut [f32]) -> Result<usize, ReadError<S::Error>> {
        self.read_with(out, |_| {})
    }
    /// Decode up to the next packet that yields range frames and write them,
    /// interleaved, to the front of `out` (at least 1024 frames of all
    /// channels). Returns the frame count; zero once the range is complete.
    /// `on_output_start` sees the decoder just before the packet holding the
    /// first output frame is processed.
    /// After a decode failure, seek before reading again. A source error
    /// permanently rejects further reads, seeks and `finish`.
    pub fn read_with(
        &mut self,
        out: &mut [f32],
        mut on_output_start: impl FnMut(&Decoder),
    ) -> Result<usize, ReadError<S::Error>> {
        self.check_failed()?;
        let channels = self.samples.len() / 1024;
        if out.len() < self.samples.len() {
            return Err(invalid(
                "SQ decoder",
                format!(
                    "output buffer holds {} samples, {} required",
                    out.len(),
                    self.samples.len()
                ),
            ));
        }
        let range = self.range;
        let fast = self.access == Access::Fast;
        let synthesis_start =
            (range.frames != 0).then(|| (range.raw_start / 1024).saturating_sub(1));
        while self.state == PassState::Reading {
            let timer = Instant::now();
            self.state = PassState::SourceFailed;
            let next = self.source.next_packet();
            self.stats.timings.read += timer.elapsed();
            let Some(packet) = next.map_err(ReadError::Source)? else {
                self.state = PassState::Eof;
                break;
            };
            let raw = packet.raw_frame;
            if !range.drain_to_eof && raw >= range.raw_end {
                self.state = PassState::RangeEnd;
                break;
            }
            self.state = PassState::DecodeFailed;
            if range.frames != 0 && raw / 1024 == range.raw_start / 1024 {
                on_output_start(&self.decoder);
            }
            let stats = &mut self.stats;
            let failed = |error| ReadError::Decode {
                error,
                packet_index: Some(packet.index),
            };
            if fast && synthesis_start.is_none_or(|start| packet.index < start) {
                let timer = Instant::now();
                let counts = self.decoder.advance(&packet.bytes).map_err(failed)?;
                self.state = PassState::Reading;
                stats.timings.scan += timer.elapsed();
                stats.prefix_packets += 1;
                stats.prefix_frames += counts.frames;
                stats.prefix_numeric_packets += u64::from(counts.numeric_elements != 0);
                stats.prefix_numeric_elements += counts.numeric_elements;
                stats.prefix_bounded_elements += counts.present_elements - counts.numeric_elements;
                stats.drc_payload_frames += counts.drc_payload_frames;
                stats.drc_missing_history_frames += counts.drc_missing_history_frames;
                continue;
            }
            stats.first_synthesis_packet.get_or_insert(packet.index);
            let timer = Instant::now();
            let decoder = &mut self.decoder;
            let samples = &mut self.samples;
            let decoded = decoder.parse(&packet.bytes).and_then(|parsed| {
                stats.timings.parse += timer.elapsed();
                let render = Instant::now();
                let frame = decoder.synthesize(parsed, samples);
                stats.timings.synthesize += render.elapsed();
                frame
            });
            let frame = decoded.map_err(failed)?;
            self.state = PassState::Reading;
            stats.timings.packet += timer.elapsed();
            stats.drc_payload_frames += frame.drc_payload_frames;
            stats.drc_missing_history_frames += frame.drc_missing_history_frames;
            stats.decoded_packets += 1;
            stats.warmup_packets += u64::from(raw + 1024 <= range.raw_start);
            stats.cpe_absent_packets += u64::from(frame.cpe_absent);
            stats.absent_elements += frame.absent_elements;
            stats.embedded_absent_elements += frame.embedded_absent_elements;
            stats.embedded_preroll_frames += frame.embedded_preroll_frames;
            stats.embedded_cpe_absent_frames += frame.embedded_cpe_absent;
            let first = raw.max(range.raw_start);
            let last = (raw + 1024).min(range.raw_end);
            if first < last {
                let from = (first - raw) as usize * channels;
                let n = (last - first) as usize * channels;
                out[..n].copy_from_slice(&self.samples[from..from + n]);
                stats.saved_frames += last - first;
                return Ok((last - first) as usize);
            }
        }
        Ok(0)
    }
    /// Restart the output at `frame` (a valid-audio frame from the window
    /// start up to the current range end), keeping the range end. The result
    /// equals a reader opened at `frame` with the same end and access mode.
    ///
    /// When every packet still to be processed would be processed the same
    /// way by such a reader, reading continues in the current pass: forward,
    /// before the packet that a fresh reader decodes first. Otherwise the
    /// current pass is verified to its end before the source is rewound and
    /// the decoder reset. The prefix is replayed; the new pass is verified
    /// again at its end. This also replays a packet that failed to decode.
    pub fn seek(&mut self, frame: u64) -> Result<(), ReadError<S::Error>> {
        if self.state == PassState::SourceFailed {
            self.check_failed()?;
        }
        let range = self.range.starting_at(frame).ok_or_else(|| {
            invalid(
                "SQ access",
                "seek position lies outside the window or past the range end",
            )
        })?;
        let next = self.source.first_packet_index() + self.source.consumed_packets();
        let first_decoded = (range.frames != 0).then(|| {
            let target = range.raw_start / 1024;
            match self.access {
                Access::Sequential => target,
                Access::Fast => target.saturating_sub(1),
            }
        });
        if matches!(self.state, PassState::RangeEnd | PassState::DecodeFailed)
            || first_decoded.is_some_and(|first| next > first)
        {
            self.restart_pass()?;
        }
        self.state = PassState::Reading;
        self.range = range;
        Ok(())
    }
    /// Complete the source's integrity checks (reading packets the range
    /// did not need) and return the source, decoder and statistics.
    /// A failed decode requires a successful seek first; source errors are
    /// terminal and cannot be cleared by finishing.
    pub fn finish(mut self) -> Result<(S, Decoder, Stats), ReadError<S::Error>> {
        self.check_failed()?;
        let timer = Instant::now();
        self.source.verify_remaining().map_err(ReadError::Source)?;
        self.stats.timings.read += timer.elapsed();
        Ok((self.source, self.decoder, self.stats))
    }
}

#[cfg(test)]
#[path = "reader_tests.rs"]
mod tests;
