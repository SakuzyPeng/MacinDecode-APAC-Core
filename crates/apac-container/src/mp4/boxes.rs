//! Bounded ISO BMFF box traversal. Unknown payloads are never buffered.
use crate::{Error, OpenMode, Result, Source, read_at};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, time::SystemTime};

pub(super) fn invalid(tag: &[u8; 4], offset: u64, message: impl Into<String>) -> Error {
    Error::at("MP4 input", tag, offset, message)
}
pub(super) fn read(
    file: &mut impl Source,
    tag: &[u8; 4],
    offset: u64,
    out: &mut [u8],
) -> Result<()> {
    read_at(file, "MP4 input", tag, offset, out)
}
pub(super) fn u32be(raw: &[u8]) -> u32 {
    u32::from_be_bytes(raw[..4].try_into().unwrap())
}
pub(super) fn u64be(raw: &[u8]) -> u64 {
    u64::from_be_bytes(raw[..8].try_into().unwrap())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct Atom {
    pub tag: [u8; 4],
    pub offset: u64,
    pub data: u64,
    pub end: u64,
}
impl Atom {
    pub fn bytes(self) -> u64 {
        self.end - self.data
    }
    pub fn error(self, message: impl Into<String>) -> Error {
        invalid(&self.tag, self.offset, message)
    }
    pub fn take<const N: usize>(self, file: &mut impl Source, relative: u64) -> Result<[u8; N]> {
        let offset = self
            .data
            .checked_add(relative)
            .filter(|&v| v <= self.end && N as u64 <= self.end - v)
            .ok_or_else(|| self.error("field exceeds box boundary"))?;
        let mut raw = [0; N];
        read(file, &self.tag, offset, &mut raw)?;
        Ok(raw)
    }
    pub fn exact(self, bytes: u64) -> Result<()> {
        if self.bytes() != bytes {
            return Err(self.error("box length disagrees with fields or entry count"));
        }
        Ok(())
    }
    pub fn full(self, file: &mut impl Source, versions: &[u8]) -> Result<u8> {
        let raw = self.take::<4>(file, 0)?;
        if !versions.contains(&raw[0]) || raw[1..] != [0; 3] {
            return Err(self.error("unsupported version or flags"));
        }
        Ok(raw[0])
    }
}

pub(super) fn atom(file: &mut impl Source, offset: u64, limit: u64, top: bool) -> Result<Atom> {
    if offset > limit || limit - offset < 8 {
        return Err(invalid(b"box ", offset, "truncated box header"));
    }
    let mut raw = [0; 8];
    read(file, b"box ", offset, &mut raw)?;
    let tag = raw[4..].try_into().unwrap();
    let short = u32be(&raw);
    let mut head = 8;
    let bytes = if short == 1 {
        if limit - offset < 16 {
            return Err(invalid(&tag, offset, "truncated extended size"));
        }
        read(file, &tag, offset + 8, &mut raw)?;
        head = 16;
        u64be(&raw)
    } else if short == 0 {
        if !top || tag != *b"mdat" {
            return Err(invalid(
                &tag,
                offset,
                "size zero only supports terminal top-level mdat",
            ));
        }
        limit - offset
    } else {
        u64::from(short)
    };
    if tag == *b"uuid" {
        head += 16;
    }
    if bytes < head || bytes > limit - offset {
        return Err(invalid(
            &tag,
            offset,
            "box exceeds parent/file or has invalid length",
        ));
    }
    Ok(Atom {
        tag,
        offset,
        data: offset + head,
        end: offset + bytes,
    })
}

fn hash_range(
    file: &mut impl Source,
    a: Atom,
    start: u64,
    end: u64,
    hash: &mut Option<Sha256>,
) -> Result<()> {
    let Some(hash) = hash else {
        return Ok(());
    };
    let mut cursor = start;
    let mut buffer = [0; 65536];
    while cursor < end {
        let n = (end - cursor).min(buffer.len() as u64) as usize;
        read(file, &a.tag, cursor, &mut buffer[..n])?;
        hash.update(&buffer[..n]);
        cursor += n as u64;
    }
    Ok(())
}

pub(super) struct Structure {
    pub boxes: BTreeMap<[u8; 4], Atom>,
    pub bytes: u64,
    pub modified: Option<SystemTime>,
    pub hash: String,
    pub mdat_count: u64,
    pub sgpd_count: u64,
    pub sbgp_count: u64,
    pub skipped: u64,
}
impl Structure {
    pub fn get(&self, tag: &[u8; 4]) -> Result<Atom> {
        self.boxes
            .get(tag)
            .copied()
            .ok_or_else(|| invalid(tag, self.bytes, "missing required box"))
    }
}

fn children(
    file: &mut impl Source,
    start: u64,
    end: u64,
    parent: &[u8; 4],
    state: &mut Structure,
    hash: &mut Option<Sha256>,
) -> Result<()> {
    let mut pos = start;
    while pos < end {
        let a = atom(file, pos, end, parent == b"root")?;
        hash_range(file, a, a.offset, a.data, hash)?;
        if matches!(
            &a.tag,
            b"moof" | b"mvex" | b"cmov" | b"sinf" | b"enca" | b"stz2" | b"senc" | b"saiz" | b"saio"
        ) {
            return Err(
                a.error("fragmentation, encryption or this sample table form is unsupported")
            );
        }
        let container = matches!(
            (parent, &a.tag),
            (b"root", b"moov")
                | (b"moov", b"trak")
                | (b"trak", b"edts" | b"mdia")
                | (b"mdia", b"minf")
                | (b"minf", b"stbl" | b"dinf")
        );
        let leaf = matches!(
            (parent, &a.tag),
            (b"root", b"ftyp")
                | (b"moov", b"mvhd")
                | (b"trak", b"tkhd")
                | (b"edts", b"elst")
                | (b"mdia", b"mdhd" | b"hdlr")
                | (b"minf", b"smhd")
                | (b"dinf", b"dref")
                | (
                    b"stbl",
                    b"stsd" | b"stsz" | b"stsc" | b"stco" | b"co64" | b"stts" | b"ctts" | b"stss"
                )
        );
        if container || leaf {
            if state.boxes.insert(a.tag, a).is_some() {
                return Err(a.error("duplicate singleton box or multiple tracks"));
            }
            if container {
                children(file, a.data, a.end, &a.tag, state, hash)?;
            } else {
                hash_range(file, a, a.data, a.end, hash)?;
            }
        } else if parent == b"stbl" && matches!(&a.tag, b"sgpd" | b"sbgp") {
            // Sample groups may repeat (e.g. roll and prol). Sequential decoding
            // does not interpret them. Keep the first range for report compatibility
            // and hash every occurrence without allocating per-group state.
            state.boxes.entry(a.tag).or_insert(a);
            if a.tag == *b"sgpd" {
                state.sgpd_count += 1;
            } else {
                state.sbgp_count += 1;
            }
            hash_range(file, a, a.data, a.end, hash)?;
        } else if parent == b"root" && a.tag == *b"mdat" {
            state.mdat_count += 1;
        } else {
            state.skipped += 1;
        }
        pos = a.end;
    }
    Ok(())
}
pub(super) fn scan(file: &mut impl Source, mode: OpenMode) -> Result<Structure> {
    let bytes = file.length()?;
    let modified = file.revision()?;
    let mut state = Structure {
        boxes: BTreeMap::new(),
        bytes,
        modified,
        hash: String::new(),
        mdat_count: 0,
        sgpd_count: 0,
        sbgp_count: 0,
        skipped: 0,
    };
    let mut hash = (mode == OpenMode::Verified).then(Sha256::new);
    let bytes = state.bytes;
    children(file, 0, bytes, b"root", &mut state, &mut hash)?;
    for tag in [
        b"ftyp", b"moov", b"mvhd", b"trak", b"tkhd", b"mdia", b"mdhd", b"hdlr", b"smhd", b"dref",
        b"elst", b"stsd", b"stsz", b"stsc", b"stts",
    ] {
        state.get(tag)?;
    }
    if state.mdat_count == 0 {
        return Err(invalid(b"mdat", bytes, "missing audio data"));
    }
    if state.boxes.contains_key(b"stco") == state.boxes.contains_key(b"co64") {
        return Err(invalid(b"stco", bytes, "requires exactly one stco or co64"));
    }
    state.hash = hash
        .map(|h| format!("{:x}", h.finalize()))
        .unwrap_or_default();
    Ok(state)
}

/// The supported physical chunk order lets this cursor find mdat ranges with
/// constant memory, even in files containing many data boxes.
#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct MediaCursor {
    top: u64,
    current: Option<Atom>,
}
impl MediaCursor {
    pub fn check(
        &mut self,
        file: &mut impl Source,
        bytes: u64,
        offset: u64,
        end: u64,
    ) -> Result<()> {
        loop {
            if let Some(a) = self.current {
                if offset >= a.data && offset < a.end {
                    if end <= a.end {
                        return Ok(());
                    }
                    return Err(invalid(
                        b"mdat",
                        offset,
                        "sample crosses audio box boundary",
                    ));
                }
                if offset < a.data {
                    return Err(invalid(
                        b"mdat",
                        offset,
                        "sample points outside audio payload",
                    ));
                }
            }
            if self.top == bytes {
                return Err(invalid(
                    b"mdat",
                    offset,
                    "sample points outside audio payload",
                ));
            }
            let a = atom(file, self.top, bytes, true)?;
            self.top = a.end;
            if a.tag == *b"mdat" {
                self.current = Some(a);
            }
        }
    }
}
