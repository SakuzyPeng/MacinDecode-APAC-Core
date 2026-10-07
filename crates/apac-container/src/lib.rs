//! Restricted, self-validating APAC container input: CAF v1 and ISO BMFF (MP4).
//!
//! Readers work over any [`Source`] (`Read + Seek` with a length and an
//! optional revision stamp). Opening reads the whole file once and records its
//! audio and packet digests; every later pass from the first packet is checked
//! against them when it reaches the end, together with a rescan of the
//! structure, so a file that changes while it is read is rejected.
//!
//! On top of the readers, [`PacketSource`] describes any packet input
//! (containers here, packet bundles in `apac-research`), [`Range`] selects a
//! frame window from its packet table, and [`Reader`] decodes that window
//! into interleaved `f32` PCM with sequential or fast access, bidirectional
//! seeking and per-pass integrity verification.
//!
//! For players, [`Media`] opens a CAF or MP4 file reading metadata only and
//! reads packets through saveable [`PacketCursor`]s, without integrity
//! verification; [`Playback`] decodes it with frame-exact seeks whose cost
//! is bounded by decoder checkpoints ([`apac_core::Checkpoint`]), which an
//! [`Indexer`] can build on another thread.
//!
//! [`PcmWritePlan`] and [`PcmWriter`] stream decoded Float32 audio into WAV,
//! RF64 or CAF over any `Write` sink without using a native codec.
#![warn(missing_docs)]
use apac_core::{config::ParseError, error::DecodeError, model::ChannelLayout};
use std::{
    fmt,
    fs::File,
    io::{self, Cursor, Read, Seek},
    time::SystemTime,
};

mod caf;
mod media;
mod mp4;
mod pcm;
mod playback;
mod range;
mod reader;
mod source;
#[cfg(test)]
mod test_source;
#[cfg(test)]
mod test_streams;
pub use caf::{CafReader, CafSummary, Chunk};
pub use media::{Media, PacketCursor};
pub use mp4::{BoxRange, Brands, Mp4Reader, Mp4Summary};
pub use pcm::{PcmFormat, PcmSpec, PcmWritePlan, PcmWriteResult, PcmWriter};
pub use playback::{IndexBatch, Indexer, Playback, PlaybackOptions, PlaybackStats};
pub use range::Range;
pub use reader::{Access, ReadError, Reader, Stats, Timings};
pub use source::PacketSource;

/// Result of container operations.
pub type Result<T> = std::result::Result<T, Error>;

/// Verified readers retain digests and eagerly validate sample tables;
/// playback defers table entries until the corresponding packets are read.
#[derive(Clone, Copy, PartialEq, Eq)]
enum OpenMode {
    Verified,
    Playback,
}

/// Byte input for the container readers.
pub trait Source: Read + Seek {
    /// Total length in bytes.
    fn length(&mut self) -> io::Result<u64>;
    /// An opaque modification stamp compared before and after reading, if
    /// the source has one.
    fn revision(&mut self) -> io::Result<Option<SystemTime>> {
        Ok(None)
    }
}
impl Source for File {
    fn length(&mut self) -> io::Result<u64> {
        Ok(self.metadata()?.len())
    }
    fn revision(&mut self) -> io::Result<Option<SystemTime>> {
        Ok(self.metadata()?.modified().ok())
    }
}
impl<T: AsRef<[u8]>> Source for Cursor<T> {
    fn length(&mut self) -> io::Result<u64> {
        Ok(self.get_ref().as_ref().len() as u64)
    }
}

/// Where in the file an input error was found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Position {
    /// Byte offset of the offending field from the start of the file.
    pub byte_offset: u64,
    /// The four-character chunk (CAF) or box (MP4) type holding it.
    pub chunk_type: String,
}

/// An input, configuration or I/O failure. The operation names the failing
/// stage (`"CAF input"`, `"MP4 input"`, `"SQ decoder"`, `"parse-cookie"`,
/// `"filesystem"`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    /// The failing stage.
    pub operation: &'static str,
    /// The rejection text, stable across releases.
    pub message: String,
    /// Bit offset within the cookie or packet, when the syntax located it.
    pub bit_offset: Option<usize>,
    /// Location in the file, when the failure is tied to one.
    pub position: Option<Position>,
    /// The packet being processed, when the failure is tied to one.
    pub packet_index: Option<u64>,
}
impl Error {
    /// An error without location.
    pub fn new(operation: &'static str, message: impl Into<String>) -> Self {
        Self {
            operation,
            message: message.into(),
            bit_offset: None,
            position: None,
            packet_index: None,
        }
    }
    pub(crate) fn at(
        operation: &'static str,
        tag: &[u8; 4],
        offset: u64,
        message: impl Into<String>,
    ) -> Self {
        let mut error = Self::new(operation, message);
        error.position = Some(Position {
            byte_offset: offset,
            chunk_type: String::from_utf8_lossy(tag).into_owned(),
        });
        error
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.operation, self.message)
    }
}
impl std::error::Error for Error {}
impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self::new("filesystem", error.to_string())
    }
}
impl From<DecodeError> for Error {
    fn from(error: DecodeError) -> Self {
        let mut result = Self::new(error.operation, error.message);
        result.bit_offset = error.bit_offset;
        result
    }
}
impl From<ParseError> for Error {
    fn from(error: ParseError) -> Self {
        DecodeError::from(error).into()
    }
}

/// The container's packet table, in audio frames.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PacketTable {
    /// Frames of decoded audio, excluding priming and remainder.
    pub valid_frames: i64,
    /// Encoder delay at the start of the first packet.
    pub priming_frames: i32,
    /// Padding at the end of the last packet.
    pub remainder_frames: i32,
}

/// The validated stream a container declares.
#[derive(Debug, Clone)]
pub struct Track {
    /// Declared sample rate in Hz (an integral, supported rate).
    pub sample_rate: f64,
    /// Declared channel count, equal to the cookie's.
    pub channels: u32,
    /// The output layout the cookie defines (and the file agrees with).
    pub layout: ChannelLayout,
    /// Number of packets in the file.
    pub packet_count: u64,
    /// The packet table.
    pub table: PacketTable,
    /// File length in bytes when it was opened.
    pub file_bytes: u64,
    /// The source revision when the file was opened.
    pub revision: Option<SystemTime>,
    /// The magic cookie bytes.
    pub cookie: Vec<u8>,
    /// The cookie, parsed once.
    pub config: apac_core::Config,
    /// The largest packet read so far.
    pub max_packet_bytes: u32,
}

/// One packet with its source index and raw (priming-inclusive) frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    /// Packet index in the source, from zero.
    pub index: u64,
    /// First frame of the packet, counting priming frames.
    pub raw_frame: u64,
    /// The packet payload.
    pub bytes: Vec<u8>,
}

/// Read exactly `out.len()` bytes at `offset`, attributing failures to `tag`.
pub(crate) fn read_at<R: Source>(
    source: &mut R,
    operation: &'static str,
    tag: &[u8; 4],
    offset: u64,
    out: &mut [u8],
) -> Result<()> {
    source
        .seek(io::SeekFrom::Start(offset))
        .map_err(|e| Error::at(operation, tag, offset, e.to_string()))?;
    let mut done = 0;
    while done < out.len() {
        match source.read(&mut out[done..]) {
            Ok(0) => {
                return Err(Error::at(
                    operation,
                    tag,
                    offset + done as u64,
                    "truncated input",
                ));
            }
            Ok(n) => done += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => {
                return Err(Error::at(
                    operation,
                    tag,
                    offset + done as u64,
                    e.to_string(),
                ));
            }
        }
    }
    Ok(())
}
