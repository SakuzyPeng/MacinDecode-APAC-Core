//! Restricted CAF v1 input, independent of AudioToolbox.
//! Wire reference: Apple Core Audio Format Specification, archived CAF_spec v1,
//! Audio Description, Audio Data and Packet Table chunks; public CAFFile.h.
//! No packet index or unknown chunk payload is retained in memory.
use crate::{
    config::{self, MAX_COOKIE_BYTES},
    error::{Error, FilePosition, Result},
    model::*,
    packets::{MAX_PACKET_BUFFER, ReplayRange},
};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
    time::SystemTime,
};

pub(crate) const PROFILE: &str = "apac-caf-input-v1";
#[cfg(test)]
const STEREO: u32 = (101 << 16) | 2;

fn invalid(tag: &[u8; 4], offset: u64, message: impl Into<String>) -> Error {
    let mut e = Error::new("CAF input", message);
    e.file_position = Some(Box::new(FilePosition {
        byte_offset: offset,
        chunk_type: String::from_utf8_lossy(tag).into_owned(),
    }));
    e
}
fn read(file: &mut File, tag: &[u8; 4], offset: u64, out: &mut [u8]) -> Result<()> {
    file.seek(SeekFrom::Start(offset))
        .map_err(|e| invalid(tag, offset, e.to_string()))?;
    let mut done = 0;
    while done < out.len() {
        match file.read(&mut out[done..]) {
            Ok(0) => return Err(invalid(tag, offset + done as u64, "truncated input")),
            Ok(n) => done += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(invalid(tag, offset + done as u64, e.to_string())),
        }
    }
    Ok(())
}
fn digest_range(
    file: &mut File,
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
#[derive(Clone, Copy, Debug, Serialize)]
struct Chunk {
    offset: u64,
    bytes: u64,
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
fn scan(file: &mut File) -> Result<Structure> {
    let meta = file.metadata()?;
    let bytes = meta.len();
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
        modified: meta.modified().ok(),
        hash: format!("{:x}", hash.finalize()),
        skipped,
    })
}

pub(crate) struct CafReader {
    file: File,
    structure: Structure,
    info: FileInfo,
    cookie: Vec<u8>,
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
impl CafReader {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        if !path.metadata()?.is_file() {
            return Err(invalid(b"caff", 0, "requires a regular file"));
        }
        let mut file = File::open(path)?;
        if !file.metadata()?.is_file() {
            return Err(invalid(b"caff", 0, "requires a regular file"));
        }
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
        let layout = crate::channel_layout::layout(u64::from(channels));
        let hoa_count = (1..=121).contains(&channels);
        if layout.is_none() && !hoa_count {
            return Err(invalid(
                b"desc",
                desc.offset + 24,
                "requires a supported discrete layout or a qualified HOA count up to 121",
            ));
        }
        let expected = [u32::from_be_bytes(*b"apac"), 0, 0, 1024, channels, 0];
        if !matches!(rate, 44100.0 | 48000.0) {
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
        let parsed = config::parse_cookie(&cookie).map_err(|e| {
            let mut e: Error = e.into();
            e.file_position = Some(Box::new(FilePosition {
                byte_offset: kuki.offset + e.bit_offset.unwrap_or(0) as u64 / 8,
                chunk_type: "kuki".into(),
            }));
            e
        })?;
        let output_layout = if parsed
            .fields
            .iter()
            .any(|f| f.name == "components[0].type" && f.value == json!(2))
        {
            let context = crate::frame::HoaFrameContext::from_cookie(&cookie)?;
            if let Some(reason) = context.rejection() {
                return Err(Error::new(
                    "SQ decoder",
                    format!("unsupported configuration: {reason}"),
                ));
            }
            context.channel_layout().clone()
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
        for (key, want) in [
            ("sample_rate_hz", rate as u64),
            ("channels", u64::from(channels)),
            ("frame_samples", 1024),
            ("components[0].layout_tag", u64::from(layout_tag)),
        ] {
            if parsed.derived.get(key).and_then(Value::as_u64) != Some(want) {
                return Err(invalid(
                    b"kuki",
                    kuki.offset,
                    format!("cookie {key} must equal {want}"),
                ));
            }
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
        let info = FileInfo {
            schema_version: SCHEMA_VERSION,
            source: path.to_owned(),
            file_bytes: structure.bytes,
            modified_unix_seconds: structure
                .modified
                .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                .map(|d| d.as_secs()),
            environment: Environment::current(),
            container: Property::known("caff".into()),
            format: AudioFormat {
                sample_rate: rate,
                format_id: ints[0],
                format_fourcc: "apac".into(),
                flags: 0,
                bytes_per_packet: 0,
                frames_per_packet: 1024,
                bytes_per_frame: 0,
                channels,
                bits_per_channel: 0,
            },
            layout: Property::known(output_layout),
            packet_count: Property::known(count),
            packet_table: Property::known(table),
            max_packet_bytes: Property::known(0),
            cookie: Property::known(CookieInfo {
                bytes: cookie.len(),
                sha256: sha256(&cookie),
            }),
            restricts_random_access: Property {
                value: None,
                error: None,
            },
        };
        let mut reader = Self {
            file,
            structure,
            info,
            cookie,
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
        reader.next = 0;
        reader.index_offset = pakt.offset + 24;
        reader.data_offset = data.offset + 4;
        reader.data_hash = Sha256::new();
        reader.packet_hash = Sha256::new();
        reader.verified = false;
        Ok(reader)
    }
    fn hashes(&self) -> (String, String) {
        (
            format!("{:x}", self.data_hash.clone().finalize()),
            format!("{:x}", self.packet_hash.clone().finalize()),
        )
    }
    pub(crate) fn info(&self) -> &FileInfo {
        &self.info
    }
    pub(crate) fn cookie(&self) -> &[u8] {
        &self.cookie
    }
    pub(crate) fn consumed_packets(&self) -> u64 {
        self.next
    }
    pub(crate) fn report(&self) -> Value {
        let hashes = self.hashes();
        json!({"kind":"caf","profile":if self.info.format.channels == 2 {PROFILE} else {"apac-caf-input-v2"},"format":self.info.format,"packet_table":self.info.packet_table.value,"packet_count":self.info.packet_count.value,
            "file_bytes":self.structure.bytes,"layout_source":self.layout_source,"layout":self.info.layout.value,
            "edit_count":self.edit_count,"chunks":self.structure.chunks.iter().map(|(k,v)|(String::from_utf8_lossy(k).into_owned(),v)).collect::<BTreeMap<_,_>>(),
            "skipped_chunks":self.structure.skipped,"metadata_sha256":self.structure.hash,"cookie_sha256":sha256(&self.cookie),"audio_sha256":hashes.0,"packets_sha256":hashes.1,
            "consistency_verified":self.verified,"verification":"two_pass_read_consistency_no_stored_checksums","access":"sequential_from_packet_zero"})
    }
    pub(crate) fn range(&self, start: Option<u64>, requested: u64) -> Result<ReplayRange> {
        let table = self.info.packet_table.value.as_ref().unwrap();
        crate::packets::frame_range(0, table.valid_frames as u64, table, start, requested)
    }
    pub(crate) fn next_packet(&mut self) -> Result<Option<(u64, u64, Vec<u8>)>> {
        let index = (self.next < self.info.packet_count.value.unwrap()).then_some(self.next);
        self.read_packet().map_err(|mut e| {
            e.packet_index = index;
            e
        })
    }
    fn read_packet(&mut self) -> Result<Option<(u64, u64, Vec<u8>)>> {
        let pakt = self.structure.chunks[b"pakt"];
        let data = self.structure.chunks[b"data"];
        if self.next == self.info.packet_count.value.unwrap() {
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
        let result = (self.next, self.next * 1024, bytes);
        self.next += 1;
        self.data_offset = end;
        self.info.max_packet_bytes.value =
            Some(self.info.max_packet_bytes.value.unwrap().max(size as u32));
        Ok(Some(result))
    }
    pub(crate) fn verify_remaining(&mut self) -> Result<()> {
        while self.next_packet()?.is_some() {}
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs::{self, OpenOptions},
        io::Write,
        sync::atomic::{AtomicU64, Ordering},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    const COOKIE: &[u8] = &[
        0, 0, 0, 26, 100, 97, 112, 97, 0, 0, 0, 0, 8, 0, 124, 1, 128, 4, 4, 32, 0, 18, 0, 202, 0, 0,
    ];
    struct Temp(std::path::PathBuf);
    impl Temp {
        fn new(raw: &[u8]) -> Self {
            let p = std::env::temp_dir().join(format!(
                "apac-caf-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::write(&p, raw).unwrap();
            Self(p)
        }
        fn change(&self, offset: u64, bytes: &[u8]) {
            let mut f = OpenOptions::new().write(true).open(&self.0).unwrap();
            f.seek(SeekFrom::Start(offset)).unwrap();
            f.write_all(bytes).unwrap();
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.0);
        }
    }
    fn chunk(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
        let mut out = tag.to_vec();
        out.extend((payload.len() as i64).to_be_bytes());
        out.extend(payload);
        out
    }
    fn fixture() -> Vec<u8> {
        let mut out = b"caff\0\x01\0\0".to_vec();
        let mut desc = 48000f64.to_be_bytes().to_vec();
        for n in [u32::from_be_bytes(*b"apac"), 0, 0, 1024, 2, 0] {
            desc.extend(n.to_be_bytes());
        }
        out.extend(chunk(b"desc", &desc));
        out.extend(chunk(b"kuki", COOKIE));
        let mut pakt = 2i64.to_be_bytes().to_vec();
        pakt.extend(1900i64.to_be_bytes());
        pakt.extend(100i32.to_be_bytes());
        pakt.extend(48i32.to_be_bytes());
        pakt.extend([1, 2]);
        out.extend(chunk(b"pakt", &pakt));
        out.extend(chunk(b"data", &[0, 0, 0, 7, 20, 30, 40]));
        out
    }
    #[test]
    fn bounded_packet_iteration_and_exact_time_cropping() {
        let tmp = Temp::new(&fixture());
        let mut r = CafReader::open(&tmp.0).unwrap();
        assert_eq!(r.cookie(), COOKIE);
        assert_eq!(r.range(Some(1850), 100).unwrap().frames, 50);
        assert_eq!(r.range(Some(1850), 100).unwrap().raw_start, 1950);
        assert_eq!(r.next_packet().unwrap().unwrap(), (0, 0, vec![20]));
        assert_eq!(r.next_packet().unwrap().unwrap(), (1, 1024, vec![30, 40]));
        r.verify_remaining().unwrap();
        assert_eq!(r.report()["consistency_verified"], true);
        assert_eq!(r.report()["layout_source"], "cookie");
        assert_eq!(r.report()["edit_count"], 7);
    }
    #[test]
    fn every_container_truncation_is_rejected_with_a_file_position() {
        let raw = fixture();
        for end in 0..raw.len() {
            let tmp = Temp::new(&raw[..end]);
            let e = CafReader::open(&tmp.0).err().unwrap();
            assert!(e.file_position.is_some(), "{end}: {e}");
        }
    }
    #[test]
    fn terminal_unknown_data_and_reordered_chunks() {
        let raw = fixture();
        let tmp = Temp::new(&raw);
        let r = CafReader::open(&tmp.0).unwrap();
        let data = r.structure.chunks[b"data"];
        let pakt = r.structure.chunks[b"pakt"];
        let mut moved = raw[..(pakt.offset - 12) as usize].to_vec();
        moved.extend(&raw[(data.offset - 12) as usize..]);
        moved.extend(&raw[(pakt.offset - 12) as usize..(data.offset - 12) as usize]);
        let t = Temp::new(&moved);
        CafReader::open(&t.0).unwrap().verify_remaining().unwrap();
        tmp.change(data.offset - 8, &(-1i64).to_be_bytes());
        CafReader::open(&tmp.0).unwrap().verify_remaining().unwrap();
    }
    #[test]
    fn metadata_audio_boundaries_and_lengths_cannot_change_after_open() {
        for kind in ["data", "kuki", "pakt", "desc", "grow", "truncate"] {
            let raw = fixture();
            let tmp = Temp::new(&raw);
            let mut r = CafReader::open(&tmp.0).unwrap();
            match kind {
                "grow" => tmp.change(raw.len() as u64, &[1]),
                "truncate" => OpenOptions::new()
                    .write(true)
                    .open(&tmp.0)
                    .unwrap()
                    .set_len(raw.len() as u64 - 1)
                    .unwrap(),
                "data" => tmp.change(r.structure.chunks[b"data"].offset + 5, &[31]),
                "pakt" => tmp.change(r.structure.chunks[b"pakt"].offset + 24, &[2, 1]),
                _ => {
                    let tag: [u8; 4] = kind.as_bytes().try_into().unwrap();
                    tmp.change(r.structure.chunks[&tag].offset, &[255]);
                }
            }
            assert!(r.verify_remaining().is_err(), "{kind}");
        }
    }
    #[test]
    fn duplicate_and_invalid_structures_never_get_guessed() {
        let raw = fixture();
        let tmp = Temp::new(&raw);
        let r = CafReader::open(&tmp.0).unwrap();
        for tag in [*b"desc", *b"kuki", *b"pakt", *b"data"] {
            let c = r.structure.chunks[&tag];
            let mut bad = raw.clone();
            bad.extend(&raw[(c.offset - 12) as usize..c.end() as usize]);
            assert!(CafReader::open(&Temp::new(&bad).0).is_err());
        }
        for (offset, bytes) in [
            (4, vec![0, 2]),
            (6, vec![0, 1]),
            (8, b"free".to_vec()),
            (12, (-2i64).to_be_bytes().to_vec()),
            (
                r.structure.chunks[b"pakt"].offset,
                (-1i64).to_be_bytes().to_vec(),
            ),
            (r.structure.chunks[b"pakt"].offset + 24, vec![0, 3]),
        ] {
            let t = Temp::new(&raw);
            t.change(offset, &bytes);
            assert!(CafReader::open(&t.0).is_err());
        }
    }
    #[test]
    fn unknown_chunks_are_skipped_but_their_headers_are_checked() {
        let mut raw = fixture();
        raw.extend(chunk(b"test", &[1, 2, 3, 4, 5]));
        let t = Temp::new(&raw);
        let mut r = CafReader::open(&t.0).unwrap();
        assert_eq!(r.report()["skipped_chunks"], 1);
        r.verify_remaining().unwrap();
        let mut r = CafReader::open(&t.0).unwrap();
        t.change(raw.len() as u64 - 13, &999i64.to_be_bytes());
        assert!(r.verify_remaining().is_err());
    }
    #[test]
    fn stereo_layout_and_cookie_must_agree_with_description() {
        let mut raw = fixture();
        let mut chan = STEREO.to_be_bytes().to_vec();
        chan.extend([0; 8]);
        raw.extend(chunk(b"chan", &chan));
        let t = Temp::new(&raw);
        let r = CafReader::open(&t.0).unwrap();
        assert_eq!(r.report()["layout_source"], "chan");
        let offset = r.structure.chunks[b"chan"].offset;
        t.change(offset, &((102u32 << 16) | 2).to_be_bytes());
        assert!(CafReader::open(&t.0).is_err());
        let t = Temp::new(&fixture());
        t.change(20, &44100f64.to_be_bytes());
        assert!(CafReader::open(&t.0).is_err());
    }
    #[test]
    fn packet_length_varints_are_bounded_and_cover_data_exactly() {
        for sizes in [
            vec![0, 3],
            vec![1, 1],
            vec![2, 2],
            vec![0x81],
            vec![0xff; 10],
            vec![0x80; 11],
            vec![1, 2, 0],
        ] {
            let raw = fixture();
            let t = Temp::new(&raw);
            let r = CafReader::open(&t.0).unwrap();
            let p = r.structure.chunks[b"pakt"];
            let d = r.structure.chunks[b"data"];
            let mut bad = raw[..(p.offset - 12) as usize].to_vec();
            let mut payload = raw[p.offset as usize..p.offset as usize + 24].to_vec();
            payload.extend(sizes);
            bad.extend(chunk(b"pakt", &payload));
            bad.extend(&raw[(d.offset - 12) as usize..]);
            let tmp = Temp::new(&bad);
            assert!(CafReader::open(&tmp.0).is_err());
        }
    }
}
