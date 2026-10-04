//! Restricted CAF v1 input, independent of AudioToolbox.
//! Wire reference: Apple Core Audio Format Specification, archived CAF_spec v1,
//! Audio Description, Audio Data and Packet Table chunks; public CAFFile.h.
//! No packet index or unknown chunk payload is retained in memory.
use crate::{Error, Packet, PacketTable, Result, Source, Track, read_at};
use apac_core::{
    config::{self, MAX_COOKIE_BYTES},
    frame::MAX_PACKET_BUFFER,
    model::ChannelLayout,
    research_support::{channel_layout, frame},
};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, time::SystemTime};

fn invalid(tag: &[u8; 4], offset: u64, message: impl Into<String>) -> Error {
    Error::at("CAF input", tag, offset, message)
}
fn read<R: Source>(file: &mut R, tag: &[u8; 4], offset: u64, out: &mut [u8]) -> Result<()> {
    read_at(file, "CAF input", tag, offset, out)
}
fn digest_range<R: Source>(
    file: &mut R,
    tag: &[u8; 4],
    start: u64,
    bytes: u64,
    hash: &mut Sha256,
) -> Result<()> {
    let mut buffer = [0u8; 65536];
    let mut offset = start;
    while offset - start < bytes {
        let n = (bytes - (offset - start)).min(buffer.len() as u64) as usize;
        read(file, tag, offset, &mut buffer[..n])?;
        hash.update(&buffer[..n]);
        offset += n as u64;
    }
    Ok(())
}
/// A retained chunk's payload range.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Chunk {
    pub offset: u64,
    pub bytes: u64,
}
impl Chunk {
    fn end(self) -> u64 {
        self.offset + self.bytes
    }
}
struct Structure {
    chunks: BTreeMap<[u8; 4], Chunk>,
    bytes: u64,
    modified: Option<SystemTime>,
    hash: String,
    skipped: u64,
}
fn scan<R: Source>(file: &mut R) -> Result<Structure> {
    let bytes = file.length()?;
    let modified = file.revision()?;
    let mut header = [0u8; 8];
    read(file, b"caff", 0, &mut header)?;
    if header != *b"caff\0\x01\0\0" {
        return Err(invalid(b"caff", 0, "requires CAF v1 with zero flags"));
    }
    let mut hash = Sha256::new();
    hash.update(header);
    let mut chunks = BTreeMap::new();
    let mut cursor = 8;
    let mut skipped = 0;
    while cursor < bytes {
        let mut raw = [0u8; 12];
        read(file, b"caff", cursor, &mut raw)?;
        let tag: [u8; 4] = raw[..4].try_into().unwrap();
        if cursor == 8 && tag != *b"desc" {
            return Err(invalid(&tag, cursor, "desc must be the first chunk"));
        }
        let signed = i64::from_be_bytes(raw[4..].try_into().unwrap());
        let offset = cursor
            .checked_add(12)
            .ok_or_else(|| invalid(&tag, cursor, "chunk offset overflow"))?;
        let size = if signed == -1 && tag == *b"data" {
            bytes.saturating_sub(offset)
        } else {
            u64::try_from(signed)
                .map_err(|_| invalid(&tag, cursor + 4, "only terminal data permits size -1"))?
        };
        let end = offset
            .checked_add(size)
            .filter(|&n| n <= bytes)
            .ok_or_else(|| invalid(&tag, cursor + 4, "chunk extends beyond file or overflows"))?;
        hash.update(raw);
        if matches!(&tag, b"desc" | b"kuki" | b"pakt" | b"data" | b"chan") {
            if chunks
                .insert(
                    tag,
                    Chunk {
                        offset,
                        bytes: size,
                    },
                )
                .is_some()
            {
                return Err(invalid(&tag, cursor, "duplicate singleton chunk"));
            }
            let valid = match &tag {
                b"desc" => size == 32,
                b"chan" => (12..=12 + 255 * 20).contains(&size) && (size - 12) % 20 == 0,
                b"kuki" => size > 0 && size <= MAX_COOKIE_BYTES as u64,
                b"pakt" => size >= 24,
                _ => size >= 4,
            };
            if !valid {
                return Err(invalid(
                    &tag,
                    cursor + 4,
                    "invalid or unsupported chunk size",
                ));
            }
            digest_range(
                file,
                &tag,
                offset,
                if tag == *b"data" { 4 } else { size },
                &mut hash,
            )?;
        } else {
            skipped += 1;
        }
        cursor = end;
    }
    for tag in [*b"desc", *b"kuki", *b"pakt", *b"data"] {
        if !chunks.contains_key(&tag) {
            return Err(invalid(&tag, bytes, "missing required chunk"));
        }
    }
    Ok(Structure {
        chunks,
        bytes,
        modified,
        hash: format!("{:x}", hash.finalize()),
        skipped,
    })
}

/// The structure, digests and verification state a CAF input report shows.
#[derive(Debug, Clone)]
pub struct CafSummary {
    pub chunks: BTreeMap<[u8; 4], Chunk>,
    pub file_bytes: u64,
    pub skipped_chunks: u64,
    /// Digest of the header, chunk headers and retained chunk payloads.
    pub metadata_sha256: String,
    /// `"chan"` when a channel layout chunk was checked, else `"cookie"`.
    pub layout_source: &'static str,
    pub edit_count: u32,
    /// Digests of the current pass's audio bytes and packet identities.
    pub audio_sha256: String,
    pub packets_sha256: String,
    /// Whether the current pass reached the end and matched the first one.
    pub verified: bool,
}

/// Sequential, two-pass-verified CAF v1 packet reader.
pub struct CafReader<R> {
    file: R,
    structure: Structure,
    track: Track,
    layout_source: &'static str,
    edit_count: u32,
    next: u64,
    index_offset: u64,
    data_offset: u64,
    data_hash: Sha256,
    packet_hash: Sha256,
    expected: Option<(String, String)>,
    verified: bool,
}
impl<R: Source> CafReader<R> {
    /// Validate the file and read it once to record its digests; the reader
    /// is then positioned at packet zero.
    pub fn new(mut file: R) -> Result<Self> {
        let structure = scan(&mut file)?;
        let chunks = &structure.chunks;
        let desc = chunks[b"desc"];
        let mut raw = [0; 32];
        read(&mut file, b"desc", desc.offset, &mut raw)?;
        let rate = f64::from_be_bytes(raw[..8].try_into().unwrap());
        let ints: Vec<u32> = raw[8..]
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| u32::from_be_bytes(*b))
            .collect();
        let channels = ints[4];
        let layout = channel_layout::layout(u64::from(channels));
        let hoa_count = (1..=255).contains(&channels);
        if layout.is_none() && !hoa_count {
            return Err(invalid(
                b"desc",
                desc.offset + 24,
                "requires a supported discrete layout or a qualified HOA stream count up to 255",
            ));
        }
        let expected = [u32::from_be_bytes(*b"apac"), 0, 0, 1024, channels, 0];
        if !rate.is_finite() || rate.fract() != 0. || frame::sfb::index(rate as u64).is_none() {
            return Err(invalid(
                b"desc",
                desc.offset,
                format!("unsupported sample rate {rate}"),
            ));
        }
        for (i, (&v, &want)) in ints.iter().zip(&expected).enumerate() {
            if v != want {
                return Err(invalid(
                    b"desc",
                    desc.offset + 8 + 4 * i as u64,
                    format!("unsupported description field {i}: {v}, expected {want}"),
                ));
            }
        }
        let kuki = chunks[b"kuki"];
        let mut cookie = vec![0; kuki.bytes as usize];
        read(&mut file, b"kuki", kuki.offset, &mut cookie)?;
        let parsed = config::Config::parse(&cookie).map_err(|e| {
            let mut e: Error = e.into();
            e.position = Some(crate::Position {
                byte_offset: kuki.offset + e.bit_offset.unwrap_or(0) as u64 / 8,
                chunk_type: "kuki".into(),
            });
            e
        })?;
        let output_layout = if parsed.has_hoa_component() {
            let context = frame::DecodedFrameContext::from_config(&parsed)?;
            if let Some(reason) = context.rejection() {
                return Err(Error::new(
                    "SQ decoder",
                    format!("unsupported configuration: {reason}"),
                ));
            }
            context
                .channel_layout()
                .expect("qualified stream layout")
                .clone()
        } else {
            let layout = layout.ok_or_else(|| {
                invalid(
                    b"kuki",
                    kuki.offset,
                    "this channel count requires a qualified HOA ASC",
                )
            })?;
            ChannelLayout::tagged(
                ((layout.family as u32) << 16) | channels,
                channels,
                Some(layout.name.into()),
            )
        };
        let layout_tag = output_layout.tag;
        for (key, value, want) in [
            ("sample_rate_hz", parsed.sample_rate_hz(), rate as u64),
            ("channels", parsed.channels(), u64::from(channels)),
            ("frame_samples", parsed.frame_samples(), 1024),
        ] {
            if value != Some(want) {
                return Err(invalid(
                    b"kuki",
                    kuki.offset,
                    format!("cookie {key} must equal {want}"),
                ));
            }
        }
        if parsed.is_single_component()
            && parsed.component_layout_tag(0) != Some(u64::from(layout_tag))
        {
            return Err(invalid(
                b"kuki",
                kuki.offset,
                "cookie layout tag disagrees with output",
            ));
        }
        let layout_source = if let Some(chan) = chunks.get(b"chan") {
            let mut expected = Vec::with_capacity(12 + 20 * output_layout.descriptions.len());
            expected.extend_from_slice(&layout_tag.to_be_bytes());
            expected.extend_from_slice(&output_layout.bitmap.to_be_bytes());
            expected.extend_from_slice(&(output_layout.descriptions.len() as u32).to_be_bytes());
            for description in &output_layout.descriptions {
                expected.extend_from_slice(&description.label.to_be_bytes());
                expected.extend_from_slice(&description.flags.to_be_bytes());
                for coordinate in description.coordinates {
                    expected.extend_from_slice(&coordinate.to_be_bytes());
                }
            }
            if chan.bytes != expected.len() as u64 {
                return Err(invalid(
                    b"chan",
                    chan.offset,
                    "channel layout size disagrees with cookie",
                ));
            }
            let mut raw = vec![0; expected.len()];
            read(&mut file, b"chan", chan.offset, &mut raw)?;
            if raw != expected {
                return Err(invalid(
                    b"chan",
                    chan.offset,
                    "channel layout tag, bitmap or descriptions disagree with cookie",
                ));
            }
            "chan"
        } else {
            "cookie"
        };
        let pakt = chunks[b"pakt"];
        let mut raw = [0; 24];
        read(&mut file, b"pakt", pakt.offset, &mut raw)?;
        let count = i64::from_be_bytes(raw[..8].try_into().unwrap());
        let table = PacketTable {
            valid_frames: i64::from_be_bytes(raw[8..16].try_into().unwrap()),
            priming_frames: i32::from_be_bytes(raw[16..20].try_into().unwrap()),
            remainder_frames: i32::from_be_bytes(raw[20..].try_into().unwrap()),
        };
        if count < 0
            || table.valid_frames < 0
            || table.priming_frames < 0
            || table.remainder_frames < 0
        {
            return Err(invalid(
                b"pakt",
                pakt.offset,
                "negative packet/frame counts",
            ));
        }
        let count = count as u64;
        let total = (table.valid_frames as u64)
            .checked_add(table.priming_frames as u64)
            .and_then(|v| v.checked_add(table.remainder_frames as u64));
        if count.checked_mul(1024).is_none()
            || count.checked_mul(1024) != total
            || count > pakt.bytes - 24
        {
            return Err(invalid(
                b"pakt",
                pakt.offset,
                "packet count, table capacity and frame totals disagree",
            ));
        }
        let data = chunks[b"data"];
        let mut raw = [0; 4];
        read(&mut file, b"data", data.offset, &mut raw)?;
        let track = Track {
            sample_rate: rate,
            channels,
            layout: output_layout,
            packet_count: count,
            table,
            file_bytes: structure.bytes,
            revision: structure.modified,
            cookie,
            config: parsed,
            max_packet_bytes: 0,
        };
        let mut reader = Self {
            file,
            structure,
            track,
            layout_source,
            edit_count: u32::from_be_bytes(raw),
            next: 0,
            index_offset: pakt.offset + 24,
            data_offset: data.offset + 4,
            data_hash: Sha256::new(),
            packet_hash: Sha256::new(),
            expected: None,
            verified: false,
        };
        while reader.next_packet()?.is_some() {}
        reader.expected = Some(reader.hashes());
        reader.rewind();
        Ok(reader)
    }
    /// Return to packet zero; the next pass is verified again at its end.
    pub fn rewind(&mut self) {
        self.next = 0;
        self.index_offset = self.structure.chunks[b"pakt"].offset + 24;
        self.data_offset = self.structure.chunks[b"data"].offset + 4;
        self.data_hash = Sha256::new();
        self.packet_hash = Sha256::new();
        self.verified = false;
    }
    fn hashes(&self) -> (String, String) {
        (
            format!("{:x}", self.data_hash.clone().finalize()),
            format!("{:x}", self.packet_hash.clone().finalize()),
        )
    }
    pub fn track(&self) -> &Track {
        &self.track
    }
    /// Packets read in the current pass.
    pub fn consumed_packets(&self) -> u64 {
        self.next
    }
    pub fn summary(&self) -> CafSummary {
        let (audio_sha256, packets_sha256) = self.hashes();
        CafSummary {
            chunks: self.structure.chunks.clone(),
            file_bytes: self.structure.bytes,
            skipped_chunks: self.structure.skipped,
            metadata_sha256: self.structure.hash.clone(),
            layout_source: self.layout_source,
            edit_count: self.edit_count,
            audio_sha256,
            packets_sha256,
            verified: self.verified,
        }
    }
    /// The next packet of the current pass; at the end, the pass is checked
    /// against the first one and the file structure is rescanned.
    pub fn next_packet(&mut self) -> Result<Option<Packet>> {
        let index = (self.next < self.track.packet_count).then_some(self.next);
        self.read_packet().map_err(|mut e| {
            e.packet_index = index;
            e
        })
    }
    fn read_packet(&mut self) -> Result<Option<Packet>> {
        let pakt = self.structure.chunks[b"pakt"];
        let data = self.structure.chunks[b"data"];
        if self.next == self.track.packet_count {
            if self.index_offset != pakt.end() {
                return Err(invalid(
                    b"pakt",
                    self.index_offset,
                    "packet table has trailing entries or bytes",
                ));
            }
            if self.data_offset != data.end() {
                return Err(invalid(
                    b"data",
                    self.data_offset,
                    "packet sizes do not exactly cover audio data",
                ));
            }
            if !self.verified {
                if self
                    .expected
                    .as_ref()
                    .is_some_and(|wanted| *wanted != self.hashes())
                {
                    return Err(invalid(
                        b"data",
                        data.offset,
                        "audio or packet boundaries changed after validation",
                    ));
                }
                let current = scan(&mut self.file)?;
                if current.hash != self.structure.hash
                    || current.bytes != self.structure.bytes
                    || current.modified != self.structure.modified
                {
                    return Err(invalid(b"caff", 0, "input changed during reading"));
                }
                self.verified = true;
            }
            return Ok(None);
        }
        let first = self.index_offset;
        let mut size = 0u64;
        for i in 0..10 {
            if self.index_offset >= pakt.end() {
                return Err(invalid(
                    b"pakt",
                    self.index_offset,
                    "truncated packet length",
                ));
            }
            let mut byte = [0];
            read(&mut self.file, b"pakt", self.index_offset, &mut byte)?;
            self.index_offset += 1;
            size = size
                .checked_mul(128)
                .and_then(|v| v.checked_add(u64::from(byte[0] & 127)))
                .ok_or_else(|| invalid(b"pakt", first, "packet length overflow"))?;
            if byte[0] & 128 == 0 {
                break;
            }
            if i == 9 {
                return Err(invalid(b"pakt", first, "packet length exceeds 10 bytes"));
            }
        }
        if size == 0 || size > MAX_PACKET_BUFFER as u64 {
            return Err(invalid(b"pakt", first, "packet length outside 1..16 MiB"));
        }
        let end = self
            .data_offset
            .checked_add(size)
            .filter(|&v| v <= data.end())
            .ok_or_else(|| invalid(b"data", self.data_offset, "packet exceeds audio data"))?;
        let mut bytes = vec![0; size as usize];
        read(&mut self.file, b"data", self.data_offset, &mut bytes)?;
        self.data_hash.update(&bytes);
        // Stable packet identity: source index, data-relative offset, size and duration.
        for value in [self.next, self.data_offset - data.offset - 4, size, 1024] {
            self.packet_hash.update(value.to_le_bytes());
        }
        self.packet_hash.update(Sha256::digest(&bytes));
        let result = Packet {
            index: self.next,
            raw_frame: self.next * 1024,
            bytes,
        };
        self.next += 1;
        self.data_offset = end;
        self.track.max_packet_bytes = self.track.max_packet_bytes.max(size as u32);
        Ok(Some(result))
    }
    /// Read the rest of the current pass, completing its verification.
    pub fn verify_remaining(&mut self) -> Result<()> {
        while self.next_packet()?.is_some() {}
        Ok(())
    }
}

#[cfg(test)]
#[path = "caf_tests.rs"]
mod tests;
