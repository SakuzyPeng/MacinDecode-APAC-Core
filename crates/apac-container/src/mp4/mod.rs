//! Restricted, self-contained APAC ISO BMFF input.
//!
//! Wire references: ISO/IEC 14496-12:2022, box/sample-table/edit-list syntax;
//! Apple's public QuickTime File Format, Sound Sample Descriptions (version 0).
//! APAC's dapa and 2/16 sample-entry placeholders were additionally checked
//! against AudioFile/AudioCodecs 7.0. The cookie defines the actual channel map.
mod boxes;
mod tables;
#[cfg(test)]
mod tests;

use crate::{Error, Packet, PacketTable, Position, Result, Source, Track};
use apac_core::{
    config::{self, MAX_COOKIE_BYTES},
    research_support::frame::{self, DecodedFrameContext},
};
use boxes::{Atom, Structure, atom, invalid, read, scan, u32be, u64be};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use tables::Index;

fn clock(file: &mut impl Source, a: Atom, movie: bool) -> Result<(u32, u64)> {
    let version = a.full(file, &[0, 1])?;
    a.exact(match (movie, version) {
        (true, 0) => 100,
        (true, _) => 112,
        (false, 0) => 24,
        _ => 36,
    })?;
    let start = if version == 0 { 12 } else { 20 };
    let rate = u32be(&a.take::<4>(file, start)?);
    let duration = if version == 0 {
        u64::from(u32be(&a.take::<4>(file, start + 4)?))
    } else {
        u64be(&a.take::<8>(file, start + 4)?)
    };
    if rate == 0
        || duration
            == if version == 0 {
                u64::from(u32::MAX)
            } else {
                u64::MAX
            }
    {
        return Err(a.error("zero timescale or unknown duration"));
    }
    Ok((rate, duration))
}
fn track(file: &mut impl Source, a: Atom) -> Result<(u32, u64)> {
    let flags = a.take::<4>(file, 0)?;
    let version = flags[0];
    if version > 1 || flags[1] != 0 || flags[2] != 0 || flags[3] & !7 != 0 || flags[3] & 1 == 0 {
        return Err(a.error("requires enabled version 0/1 track"));
    }
    a.exact(if version == 0 { 84 } else { 96 })?;
    let start = if version == 0 { 12 } else { 20 };
    let id = u32be(&a.take::<4>(file, start)?);
    let duration = if version == 0 {
        u64::from(u32be(&a.take::<4>(file, start + 8)?))
    } else {
        u64be(&a.take::<8>(file, start + 8)?)
    };
    let volume = a.take::<2>(file, if version == 0 { 36 } else { 48 })?;
    let dimensions = a.take::<8>(file, a.bytes() - 8)?;
    if id == 0 || volume != [1, 0] || dimensions != [0; 8] {
        return Err(
            a.error("requires nonzero track ID, unity audio volume and no video dimensions")
        );
    }
    Ok((id, duration))
}
fn edit(file: &mut impl Source, a: Atom) -> Result<(u64, u64)> {
    let version = a.full(file, &[0, 1])?;
    if u32be(&a.take::<4>(file, 4)?) != 1 {
        return Err(a.error("requires one edit list entry"));
    }
    a.exact(if version == 0 { 20 } else { 28 })?;
    let (duration, start) = if version == 0 {
        let raw = a.take::<8>(file, 8)?;
        (
            u64::from(u32be(&raw)),
            i64::from(i32::from_be_bytes(raw[4..].try_into().unwrap())),
        )
    } else {
        let raw = a.take::<16>(file, 8)?;
        (
            u64be(&raw),
            i64::from_be_bytes(raw[8..].try_into().unwrap()),
        )
    };
    if start < 0 || a.take::<4>(file, a.bytes() - 4)? != [0, 1, 0, 0] {
        return Err(a.error("empty edits, negative media time or non-unity rate are unsupported"));
    }
    Ok((duration, start as u64))
}
fn cookie(file: &mut impl Source, s: &Structure) -> Result<(Vec<u8>, u32, Atom)> {
    let stsd = s.get(b"stsd")?;
    stsd.full(file, &[0])?;
    if u32be(&stsd.take::<4>(file, 4)?) != 1 {
        return Err(stsd.error("requires one sample description"));
    }
    let entry = atom(file, stsd.data + 8, stsd.end, false)?;
    if entry.tag != *b"apac" || entry.end != stsd.end {
        return Err(entry.error("requires a single unencrypted apac entry"));
    }
    let fields = entry.take::<28>(file, 0)?;
    if fields[..6] != [0; 6]
        || fields[6..8] != [0, 1]
        || fields[8..16] != [0; 8]
        || fields[16..24] != [0, 2, 0, 16, 0, 0, 0, 0]
    {
        return Err(entry.error("requires version 0 apac, data reference 1 and the observed 2-channel/16-bit placeholders"));
    }
    let rate = u32be(&fields[24..]);
    if rate & 0xffff != 0 || (rate != 0 && frame::sfb::index(u64::from(rate >> 16)).is_none()) {
        return Err(entry.error("requires an integral supported sample rate"));
    }
    let mut found = None;
    let mut pos = entry.data + 28;
    while pos < entry.end {
        let a = atom(file, pos, entry.end, false)?;
        if a.tag == *b"dapa" {
            if found.is_some()
                || a.data - a.offset != 8
                || a.end - a.offset > MAX_COOKIE_BYTES as u64
            {
                return Err(a.error("duplicate, extended or oversized dapa cookie"));
            }
            found = Some(a);
        } else if matches!(&a.tag, b"sinf" | b"wave" | b"chan") {
            return Err(a.error("unsupported APAC sample entry extension"));
        }
        pos = a.end;
    }
    let a = found.ok_or_else(|| entry.error("missing dapa cookie"))?;
    let mut bytes = vec![0; (a.end - a.offset) as usize];
    read(file, b"dapa", a.offset, &mut bytes)?;
    Ok((bytes, rate >> 16, a))
}

/// The file brands from `ftyp`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Brands {
    pub major: String,
    pub minor_version: u32,
    pub compatible: Vec<String>,
}

/// A retained box: its header offset, payload offset and end.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BoxRange {
    pub offset: u64,
    pub data_offset: u64,
    pub end: u64,
}

/// The structure, timeline, digests and verification state an MP4 input
/// report shows.
#[derive(Debug, Clone)]
pub struct Mp4Summary {
    pub brands: Brands,
    pub track_id: u32,
    /// The sample entry's rate; zero when the cookie alone defines it.
    pub sample_entry_rate: u32,
    pub movie_timescale: u32,
    pub edit_duration: u64,
    pub file_bytes: u64,
    pub boxes: BTreeMap<[u8; 4], BoxRange>,
    pub mdat_count: u64,
    pub skipped_boxes: u64,
    pub sgpd_count: u64,
    pub sbgp_count: u64,
    /// Digest of every box header and retained box payload.
    pub metadata_sha256: String,
    /// Digests of the current pass's audio bytes and packet identities.
    pub audio_sha256: String,
    pub packets_sha256: String,
    /// Whether the current pass reached the end and matched the first one.
    pub verified: bool,
}

/// Sequential, two-pass-verified reader for a single-track APAC MP4.
pub struct Mp4Reader<R> {
    file: R,
    structure: Structure,
    track: Track,
    index: Index,
    brands: Brands,
    track_id: u32,
    movie_timescale: u32,
    edit_duration: u64,
    sample_entry_rate: u32,
    audio_hash: Sha256,
    packet_hash: Sha256,
    expected: Option<(String, String)>,
    verified: bool,
}
impl<R: Source> Mp4Reader<R> {
    /// Validate the file and read it once to record its digests; the reader
    /// is then positioned at the first sample.
    pub fn new(mut file: R) -> Result<Self> {
        let structure = scan(&mut file)?;
        let ftyp = structure.get(b"ftyp")?;
        // The bounded report exposes at most 64 compatible brands.
        if ftyp.bytes() < 8 || ftyp.bytes() > 264 || (ftyp.bytes() - 8) % 4 != 0 {
            return Err(ftyp.error("invalid brand list or more than 64 compatible brands"));
        }
        let major = ftyp.take::<4>(&mut file, 0)?;
        let mut compatible = Vec::new();
        for i in 0..(ftyp.bytes() - 8) / 4 {
            compatible
                .push(String::from_utf8_lossy(&ftyp.take::<4>(&mut file, 8 + i * 4)?).into_owned());
        }
        if major == *b"qt  "
            || !(matches!(&major, b"mp41" | b"mp42" | b"isom" | b"M4A ")
                || compatible
                    .iter()
                    .any(|s| matches!(s.as_str(), "mp41" | "mp42" | "isom" | "M4A ")))
        {
            return Err(ftyp.error("unsupported ISO BMFF file brand"));
        }
        let brands = Brands {
            major: String::from_utf8_lossy(&major).into_owned(),
            minor_version: u32be(&ftyp.take::<4>(&mut file, 4)?),
            compatible,
        };
        let hdlr = structure.get(b"hdlr")?;
        hdlr.full(&mut file, &[0])?;
        if hdlr.bytes() < 24 || hdlr.take::<4>(&mut file, 8)? != *b"soun" {
            return Err(hdlr.error("requires a single audio handler"));
        }
        let smhd = structure.get(b"smhd")?;
        smhd.exact(8)?;
        if smhd.take::<8>(&mut file, 0)? != [0; 8] {
            return Err(smhd.error("requires zero sound balance/version/flags"));
        }
        let dref = structure.get(b"dref")?;
        dref.full(&mut file, &[0])?;
        if u32be(&dref.take::<4>(&mut file, 4)?) != 1 {
            return Err(dref.error("requires one self-contained data reference"));
        }
        let url = atom(&mut file, dref.data + 8, dref.end, false)?;
        if url.tag != *b"url "
            || url.end != dref.end
            || url.bytes() != 4
            || url.take::<4>(&mut file, 0)? != [0, 0, 0, 1]
        {
            return Err(url.error("external or unsupported data reference"));
        }
        let (cookie, sample_entry_rate, cookie_atom) = cookie(&mut file, &structure)?;
        let (parsed, context) = config::Config::parse(&cookie)
            .and_then(|parsed| {
                let context = DecodedFrameContext::from_config(&parsed)?;
                Ok((parsed, context))
            })
            .map_err(|e| {
                let mut e: Error = e.into();
                e.position = Some(Position {
                    byte_offset: cookie_atom.offset + e.bit_offset.unwrap_or(0) as u64 / 8,
                    chunk_type: "dapa".into(),
                });
                e
            })?;
        // Preserve the decoder's established configuration rejection operation.
        if let Some(reason) = context.rejection() {
            return Err(Error::new(
                "SQ decoder",
                format!("unsupported configuration: {reason}"),
            ));
        }
        let rate = context.sample_rate_hz() as u32;
        if sample_entry_rate != 0 && rate != sample_entry_rate {
            return Err(cookie_atom.error("cookie sample rate disagrees with sample entry"));
        }
        if parsed.frame_samples() != Some(1024) {
            return Err(cookie_atom.error("requires 1024-frame cookie"));
        }
        let channels = context.channel_count();
        let (movie_timescale, movie_duration) = clock(&mut file, structure.get(b"mvhd")?, true)?;
        let (media_timescale, media_duration) = clock(&mut file, structure.get(b"mdhd")?, false)?;
        let (track_id, track_duration) = track(&mut file, structure.get(b"tkhd")?)?;
        let elst = structure.get(b"elst")?;
        let (edit_duration, priming) = edit(&mut file, elst)?;
        if media_timescale != rate
            || movie_duration != edit_duration
            || track_duration != edit_duration
        {
            return Err(elst.error("movie/track/edit duration or media timescale disagrees"));
        }
        let converted = u128::from(edit_duration) * u128::from(rate);
        if converted % u128::from(movie_timescale) != 0 {
            return Err(elst.error("edit duration is not an integral audio frame count"));
        }
        let valid = u64::try_from(converted / u128::from(movie_timescale))
            .map_err(|_| elst.error("edit duration overflow"))?;
        let index = Index::open(&mut file, &structure)?;
        let total = u64::from(index.count) * 1024;
        if media_duration != total {
            return Err(structure
                .get(b"mdhd")?
                .error("media duration disagrees with sample count"));
        }
        let remainder = total
            .checked_sub(priming)
            .and_then(|v| v.checked_sub(valid))
            .ok_or_else(|| elst.error("edit lies outside the media timeline"))?;
        let table = PacketTable {
            valid_frames: i64::try_from(valid)
                .map_err(|_| elst.error("valid frame count exceeds supported range"))?,
            priming_frames: i32::try_from(priming)
                .map_err(|_| elst.error("priming exceeds supported range"))?,
            remainder_frames: i32::try_from(remainder)
                .map_err(|_| elst.error("remainder exceeds supported range"))?,
        };
        let track = Track {
            sample_rate: f64::from(rate),
            channels,
            layout: context.channel_layout().unwrap().clone(),
            packet_count: u64::from(index.count),
            table,
            file_bytes: structure.bytes,
            revision: structure.modified,
            cookie,
            config: parsed,
            max_packet_bytes: 0,
        };
        let mut out = Self {
            file,
            structure,
            track,
            index,
            brands,
            track_id,
            movie_timescale,
            edit_duration,
            sample_entry_rate,
            audio_hash: Sha256::new(),
            packet_hash: Sha256::new(),
            expected: None,
            verified: false,
        };
        while out.next_packet()?.is_some() {}
        out.expected = Some(out.hashes());
        out.rewind()?;
        Ok(out)
    }
    /// Return to the first sample; the next pass is verified again at its end.
    pub fn rewind(&mut self) -> Result<()> {
        self.index = Index::open(&mut self.file, &self.structure)?;
        self.audio_hash = Sha256::new();
        self.packet_hash = Sha256::new();
        self.verified = false;
        Ok(())
    }
    fn hashes(&self) -> (String, String) {
        (
            format!("{:x}", self.audio_hash.clone().finalize()),
            format!("{:x}", self.packet_hash.clone().finalize()),
        )
    }
    pub fn track(&self) -> &Track {
        &self.track
    }
    /// Samples read in the current pass.
    pub fn consumed_packets(&self) -> u64 {
        self.index.next
    }
    pub fn summary(&self) -> Mp4Summary {
        let (audio_sha256, packets_sha256) = self.hashes();
        Mp4Summary {
            brands: self.brands.clone(),
            track_id: self.track_id,
            sample_entry_rate: self.sample_entry_rate,
            movie_timescale: self.movie_timescale,
            edit_duration: self.edit_duration,
            file_bytes: self.structure.bytes,
            boxes: self
                .structure
                .boxes
                .iter()
                .map(|(&tag, a)| {
                    (
                        tag,
                        BoxRange {
                            offset: a.offset,
                            data_offset: a.data,
                            end: a.end,
                        },
                    )
                })
                .collect(),
            mdat_count: self.structure.mdat_count,
            skipped_boxes: self.structure.skipped,
            sgpd_count: self.structure.sgpd_count,
            sbgp_count: self.structure.sbgp_count,
            metadata_sha256: self.structure.hash.clone(),
            audio_sha256,
            packets_sha256,
            verified: self.verified,
        }
    }
    /// The next sample of the current pass; at the end, the pass is checked
    /// against the first one and the file structure is rescanned.
    pub fn next_packet(&mut self) -> Result<Option<Packet>> {
        let index = self.index.next;
        self.read_packet().map_err(|mut e| {
            if index < u64::from(self.index.count) {
                e.packet_index = Some(index);
            }
            e
        })
    }
    fn read_packet(&mut self) -> Result<Option<Packet>> {
        let index = self.index.next;
        if let Some((offset, size)) = self.index.next(&mut self.file, self.structure.bytes)? {
            let mut raw = vec![0; size as usize];
            read(&mut self.file, b"mdat", offset, &mut raw)?;
            self.audio_hash.update(&raw);
            for v in [index, offset, u64::from(size), 1024] {
                self.packet_hash.update(v.to_le_bytes());
            }
            self.packet_hash.update(Sha256::digest(&raw));
            self.track.max_packet_bytes = self.track.max_packet_bytes.max(size);
            Ok(Some(Packet {
                index,
                raw_frame: index * 1024,
                bytes: raw,
            }))
        } else {
            if !self.verified {
                if self.expected.as_ref().is_some_and(|v| *v != self.hashes()) {
                    return Err(invalid(
                        b"mdat",
                        0,
                        "audio or packet boundaries changed after validation",
                    ));
                }
                let now = scan(&mut self.file)?;
                if now.hash != self.structure.hash
                    || now.bytes != self.structure.bytes
                    || now.modified != self.structure.modified
                {
                    return Err(invalid(b"ftyp", 0, "input changed during reading"));
                }
                self.verified = true;
            }
            Ok(None)
        }
    }
    /// Read the rest of the current pass, completing its verification.
    pub fn verify_remaining(&mut self) -> Result<()> {
        while self.next_packet()?.is_some() {}
        Ok(())
    }
}
