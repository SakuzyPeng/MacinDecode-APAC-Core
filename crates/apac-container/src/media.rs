//! Random-access container input for playback: opening reads metadata only,
//! and packets are read through cursors that can be saved and restored.
use crate::{Chunk, Error, Result, Source, Track, caf, mp4, read_at};
use std::io::{self, SeekFrom};

/// Where the next packet of a [`Media`] input is read.
///
/// A cursor is small and `Copy`: keep one to return to a packet later. It
/// belongs to the input that made it; a cursor of another CAF or MP4 file
/// reads that file's offsets.
#[derive(Clone, Copy, Debug)]
pub struct PacketCursor(Position);
/// Kept unboxed so cursors stay `Copy`: a few hundred bytes are copied per
/// packet read.
#[derive(Clone, Copy, Debug)]
#[allow(clippy::large_enum_variant)]
enum Position {
    Caf(caf::Cursor),
    Mp4(mp4::Index),
}
impl PacketCursor {
    /// Index of the packet this cursor reads next.
    pub fn packet(&self) -> u64 {
        match &self.0 {
            Position::Caf(cursor) => cursor.next,
            Position::Mp4(index) => index.next,
        }
    }
}

enum Format {
    Caf { pakt: Chunk, data: Chunk },
    Mp4 { file_bytes: u64 },
}

/// A CAF or MP4 file opened for playback.
///
/// Opening validates the same structure, stream description, cookie,
/// channel layout and timeline as [`CafReader`](crate::CafReader) and
/// [`Mp4Reader`](crate::Mp4Reader), with the same rejection texts, but reads
/// metadata only: no audio payload and no per-packet table entry. Packets are
/// read through a [`PacketCursor`] and checked against the packet table and
/// the audio data bounds as they are read; a cursor that reaches the end of
/// the table also checks that the table and the audio data were consumed
/// exactly.
///
/// There is no integrity verification across reads (no digests, no rescan):
/// use the verified readers with [`Reader`](crate::Reader) when a file must
/// be proven unchanged while it is decoded.
pub struct Media<R> {
    file: R,
    track: Track,
    format: Format,
    start: PacketCursor,
}
impl<R: Source> Media<R> {
    /// Open a CAF file (one that starts with `caff`) or an MP4 file.
    pub fn open(mut file: R) -> Result<Self> {
        if starts_with_caff(&mut file)? {
            let header = caf::header(&mut file)?;
            let (pakt, data) = header.packets();
            Ok(Self {
                file,
                track: header.track,
                format: Format::Caf { pakt, data },
                start: PacketCursor(Position::Caf(caf::Cursor::start(pakt, data))),
            })
        } else {
            let header = mp4::header(&mut file)?;
            Ok(Self {
                file,
                format: Format::Mp4 {
                    file_bytes: header.file_bytes(),
                },
                start: PacketCursor(Position::Mp4(header.index)),
                track: header.track,
            })
        }
    }
    /// The container: `"caf"` or `"mp4"`.
    pub fn container(&self) -> &'static str {
        match self.format {
            Format::Caf { .. } => "caf",
            Format::Mp4 { .. } => "mp4",
        }
    }
    /// The validated stream description.
    pub fn track(&self) -> &Track {
        &self.track
    }
    /// A cursor at the first packet.
    pub fn start(&self) -> PacketCursor {
        self.start
    }
    /// Read the packet at `cursor` into `out` (resized to the packet) and
    /// move the cursor past it. Returns the packet index, or `None` at the
    /// end of the packet table. On error the cursor is unchanged.
    pub fn read_packet(
        &mut self,
        cursor: &mut PacketCursor,
        out: &mut Vec<u8>,
    ) -> Result<Option<u64>> {
        let index = cursor.packet();
        let count = self.track.packet_count;
        let mut next = cursor.0;
        let found = match (&mut next, &self.format) {
            (Position::Caf(at), &Format::Caf { pakt, data }) => at
                .next(&mut self.file, pakt, data, count)
                .and_then(|range| match range {
                    Some((offset, size)) => {
                        out.resize(size as usize, 0);
                        read_at(&mut self.file, "CAF input", b"data", offset, out)?;
                        Ok(Some(size))
                    }
                    None => Ok(None),
                }),
            (Position::Mp4(at), &Format::Mp4 { file_bytes }) => at
                .next(&mut self.file, file_bytes)
                .and_then(|range| match range {
                    Some((offset, size)) => {
                        out.resize(size as usize, 0);
                        read_at(&mut self.file, "MP4 input", b"mdat", offset, out)?;
                        Ok(Some(u64::from(size)))
                    }
                    None => Ok(None),
                }),
            _ => {
                return Err(Error::new(
                    "packet input",
                    "packet cursor belongs to another container",
                ));
            }
        };
        let size = found.map_err(|mut e| {
            if index < count {
                e.packet_index = Some(index);
            }
            e
        })?;
        let Some(size) = size else {
            return Ok(None);
        };
        cursor.0 = next;
        self.track.max_packet_bytes = self.track.max_packet_bytes.max(size as u32);
        Ok(Some(index))
    }
    /// The underlying source.
    pub fn into_inner(self) -> R {
        self.file
    }
}

/// Whether the first four bytes are `caff`, the rule
/// `apac-research` applies to files.
fn starts_with_caff<R: Source>(file: &mut R) -> Result<bool> {
    file.seek(SeekFrom::Start(0))?;
    let mut magic = [0; 4];
    let mut done = 0;
    while done < magic.len() {
        match file.read(&mut magic[done..]) {
            Ok(0) => break,
            Ok(n) => done += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(done == 4 && magic == *b"caff")
}

#[cfg(test)]
#[path = "media_tests.rs"]
mod tests;
