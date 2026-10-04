//! Restricted, self-validating APAC container input: CAF v1 and ISO BMFF (MP4).
//!
//! Readers work over any [`Source`] (`Read + Seek` with a length and an
//! optional revision stamp). Opening reads the whole file once and records its
//! audio and packet digests; every later pass from the first packet is checked
//! against them when it reaches the end, together with a rescan of the
//! structure, so a file that changes while it is read is rejected.
use apac_core::{config::ParseError, error::DecodeError, model::ChannelLayout};
use std::{
    fmt,
    fs::File,
    io::{self, Cursor, Read, Seek},
    time::SystemTime,
};

mod caf;
mod mp4;
mod range;
mod reader;
mod source;
#[cfg(test)]
mod test_source;
pub use caf::{CafReader, CafSummary, Chunk};
pub use mp4::{BoxRange, Brands, Mp4Reader, Mp4Summary};
pub use range::Range;
pub use reader::{Access, ReadError, Reader, Stats, Timings};
pub use source::PacketSource;

pub type Result<T> = std::result::Result<T, Error>;

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
    pub byte_offset: u64,
    pub chunk_type: String,
}

/// An input, configuration or I/O failure. The operation names the failing
/// stage (`"CAF input"`, `"MP4 input"`, `"SQ decoder"`, `"parse-cookie"`,
/// `"filesystem"`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub operation: &'static str,
    pub message: String,
    pub bit_offset: Option<usize>,
    pub position: Option<Position>,
    pub packet_index: Option<u64>,
}
impl Error {
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
    pub valid_frames: i64,
    pub priming_frames: i32,
    pub remainder_frames: i32,
}

/// The validated stream a container declares.
#[derive(Debug, Clone)]
pub struct Track {
    pub sample_rate: f64,
    pub channels: u32,
    /// The output layout the cookie defines (and the file agrees with).
    pub layout: ChannelLayout,
    pub packet_count: u64,
    pub table: PacketTable,
    pub file_bytes: u64,
    /// The source revision when the file was opened.
    pub revision: Option<SystemTime>,
    pub cookie: Vec<u8>,
    pub config: apac_core::Config,
    /// The largest packet read so far.
    pub max_packet_bytes: u32,
}

/// One packet with its source index and raw (priming-inclusive) frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    pub index: u64,
    pub raw_frame: u64,
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
