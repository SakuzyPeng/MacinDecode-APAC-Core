//! Range-limited PCM decoding over a [`PacketSource`]: packet selection,
//! priming and range cropping, sequential or fast (state-only prefix) access.
use crate::{PacketSource, Range};
use apac_core::{Decoder, error::DecodeError, synthesis::StreamKind};
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
    Source(E),
    Decode {
        error: DecodeError,
        packet_index: Option<u64>,
    },
    Invalid {
        operation: &'static str,
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
    /// Packets and frames advanced with state-only access.
    pub prefix_packets: u64,
    pub prefix_frames: u64,
    /// Prefix packets, and their elements, whose spectra were dequantized.
    pub prefix_numeric_packets: u64,
    pub prefix_numeric_elements: u64,
    /// Present prefix elements only checked against finite-value bounds.
    pub prefix_bounded_elements: u64,
    /// Fully decoded packets, and those of them wholly before the output.
    pub decoded_packets: u64,
    pub warmup_packets: u64,
    pub cpe_absent_packets: u64,
    pub absent_elements: u64,
    pub embedded_absent_elements: u64,
    pub embedded_preroll_frames: u64,
    pub embedded_cpe_absent_frames: u64,
    pub drc_payload_frames: u64,
    pub drc_missing_history_frames: u64,
    /// Output frames returned by `read`.
    pub saved_frames: u64,
    /// Source index of the first fully decoded packet.
    pub first_synthesis_packet: Option<u64>,
    pub timings: Timings,
}

/// Decodes a frame range of a [`PacketSource`] into interleaved `f32` PCM.
pub struct Reader<S> {
    source: S,
    decoder: Decoder,
    access: Access,
    range: Range,
    samples: Vec<f32>,
    stats: Stats,
    /// The current pass is over: the source ended, or a packet past the
    /// range was read (and not decoded).
    ended: bool,
}
impl<S: PacketSource> Reader<S> {
    /// Check the source against its decoder and select `frames` (default:
    /// the whole valid audio) from `start` (default: the window start).
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
        Ok(Self {
            source,
            decoder,
            access,
            range,
            samples,
            stats: Stats::default(),
            ended: false,
        })
    }
    pub fn decoder(&self) -> &Decoder {
        &self.decoder
    }
    pub fn source(&self) -> &S {
        &self.source
    }
    pub fn range(&self) -> &Range {
        &self.range
    }
    pub fn access(&self) -> Access {
        self.access
    }
    pub fn stats(&self) -> &Stats {
        &self.stats
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
    pub fn read_with(
        &mut self,
        out: &mut [f32],
        mut on_output_start: impl FnMut(&Decoder),
    ) -> Result<usize, ReadError<S::Error>> {
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
        while !self.ended {
            let timer = Instant::now();
            let next = self.source.next_packet();
            self.stats.timings.read += timer.elapsed();
            let Some(packet) = next.map_err(ReadError::Source)? else {
                self.ended = true;
                break;
            };
            let raw = packet.raw_frame;
            if !range.drain_to_eof && raw >= range.raw_end {
                self.ended = true;
                break;
            }
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
    /// Complete the source's integrity checks (reading packets the range
    /// did not need) and return the source, decoder and statistics.
    pub fn finish(mut self) -> Result<(S, Decoder, Stats), ReadError<S::Error>> {
        let timer = Instant::now();
        self.source.verify_remaining().map_err(ReadError::Source)?;
        self.stats.timings.read += timer.elapsed();
        Ok((self.source, self.decoder, self.stats))
    }
}

#[cfg(test)]
#[path = "reader_tests.rs"]
mod tests;
