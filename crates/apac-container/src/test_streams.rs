//! Test inputs shared by the reader, media and playback tests: CAF and MP4
//! files assembled from the frozen state fixtures, and a source with an
//! unreadable region.
use crate::{CafReader, Source, test_source::Shared};
use apac_core::{Decoder, StreamKind};
use serde_json::Value;
use std::{
    io::{self, Read, Seek, SeekFrom},
    ops::Range,
    time::SystemTime,
};

pub(crate) const PRIMING: i32 = 300;
pub(crate) const REMAINDER: i32 = 200;

pub(crate) fn hex(value: &Value) -> Vec<u8> {
    let text = value.as_str().unwrap();
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}
fn chunk(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
    let mut out = tag.to_vec();
    out.extend((payload.len() as i64).to_be_bytes());
    out.extend(payload);
    out
}
fn varint(mut n: u64) -> Vec<u8> {
    let mut out = vec![(n & 127) as u8];
    n >>= 7;
    while n != 0 {
        out.insert(0, (n & 127) as u8 | 128);
        n >>= 7;
    }
    out
}
/// A CAF of `packets` with fixed priming and remainder.
pub(crate) fn caf(cookie: &[u8], packets: &[Vec<u8>]) -> Option<Vec<u8>> {
    let config = apac_core::Config::parse(cookie).ok()?;
    let mut out = b"caff\0\x01\0\0".to_vec();
    let mut desc = (config.sample_rate_hz()? as f64).to_be_bytes().to_vec();
    for n in [
        u32::from_be_bytes(*b"apac"),
        0,
        0,
        1024,
        config.channels()? as u32,
        0,
    ] {
        desc.extend(n.to_be_bytes());
    }
    out.extend(chunk(b"desc", &desc));
    out.extend(chunk(b"kuki", cookie));
    let count = packets.len() as i64;
    let mut pakt = count.to_be_bytes().to_vec();
    pakt.extend((count * 1024 - i64::from(PRIMING + REMAINDER)).to_be_bytes());
    pakt.extend(PRIMING.to_be_bytes());
    pakt.extend(REMAINDER.to_be_bytes());
    for packet in packets {
        pakt.extend(varint(packet.len() as u64));
    }
    out.extend(chunk(b"pakt", &pakt));
    let mut data = vec![0, 0, 0, 1];
    data.extend(packets.concat());
    out.extend(chunk(b"data", &data));
    Some(out)
}

/// Rebuild a CAF with `extra` appended to the payload of chunk `tag`.
pub(crate) fn grow_caf_chunk(file: &[u8], tag: &[u8; 4], extra: &[u8]) -> Vec<u8> {
    let mut out = file[..8].to_vec();
    let mut at = 8;
    while at < file.len() {
        let size = u64::from_be_bytes(file[at + 4..at + 12].try_into().unwrap()) as usize;
        let mut payload = file[at + 12..at + 12 + size].to_vec();
        if &file[at..at + 4] == tag {
            payload.extend(extra);
        }
        out.extend(chunk(file[at..at + 4].try_into().unwrap(), &payload));
        at += 12 + size;
    }
    out
}

pub(crate) struct Stream {
    pub(crate) name: String,
    pub(crate) kind: StreamKind,
    pub(crate) cookie: Vec<u8>,
    pub(crate) packets: Vec<Vec<u8>>,
    /// The CAF file.
    pub(crate) file: Vec<u8>,
}
/// Fixture packet sequences that decode in order and form a valid CAF.
pub(crate) fn streams() -> Vec<Stream> {
    let files = [
        (
            "channel",
            include_str!("../../../data/channel-state-fixtures-v1.json"),
        ),
        (
            "layout",
            include_str!("../../../data/layout-state-fixtures-v1.json"),
        ),
        (
            "surround916",
            include_str!("../../../data/surround916-state-fixtures-v1.json"),
        ),
        (
            "hoa-ambient",
            include_str!("../../../data/hoa-ambient-state-v1.json"),
        ),
        (
            "hoa-orders",
            include_str!("../../../data/hoa-orders-state-v1.json"),
        ),
        (
            "hoa-mixed",
            include_str!("../../../data/hoa-mixed-state-v1.json"),
        ),
        (
            "hoa-dynamic",
            include_str!("../../../data/hoa-dynamic-state-v1.json"),
        ),
        (
            "hoa-shared",
            include_str!("../../../data/hoa-shared-state-v1.json"),
        ),
    ];
    let mut out = vec![];
    for (label, text) in files {
        let value: Value = serde_json::from_str(text).unwrap();
        let rows = match value["fixtures"].as_array() {
            Some(rows) => rows.clone(),
            None => vec![value],
        };
        for (i, row) in rows.iter().enumerate() {
            let (Some(_), Some(first)) = (row.get("cookie"), row.get("first")) else {
                continue;
            };
            let cookie = hex(&row["cookie"]);
            let first = hex(first);
            let mut candidates = vec![vec![first.clone(); 6]];
            if let Some(next) = row.get("next") {
                let next = hex(next);
                let mut packets = vec![first.clone()];
                packets.extend(vec![next; 5]);
                candidates.insert(0, packets);
            }
            for packets in candidates {
                let Ok(mut decoder) = Decoder::from_cookie(&cookie) else {
                    break;
                };
                let kind = decoder.info().kind;
                let taken = out
                    .iter()
                    .filter(|s: &&Stream| s.name.starts_with(label) && s.kind == kind)
                    .count();
                if taken < 3
                    && packets.iter().all(|p| decoder.decode_vec(p).is_ok())
                    && let Some(file) = caf(&cookie, &packets)
                    && CafReader::new(Shared::new(&file)).is_ok()
                {
                    out.push(Stream {
                        name: format!("{label}[{i}]"),
                        kind,
                        cookie: cookie.clone(),
                        packets,
                        file,
                    });
                    break;
                }
            }
        }
    }
    let kinds: Vec<_> = out.iter().map(|s| s.kind).collect();
    for kind in [StreamKind::Channels, StreamKind::Hoa, StreamKind::Composite] {
        assert!(kinds.contains(&kind), "no {kind:?} stream in {kinds:?}");
    }
    out
}

/// How [`mp4`] lays out the samples.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Layout {
    /// Samples per chunk, repeated.
    pub(crate) chunks: &'static [usize],
    /// 64-bit chunk offsets (`co64`).
    pub(crate) co64: bool,
    /// One time-to-sample run per chunk instead of one in total.
    pub(crate) split_times: bool,
}
/// Chunk, offset-width and time-run variants, as in `scripts/mp4_vectors.py`.
pub(crate) const LAYOUTS: [Layout; 3] = [
    Layout {
        chunks: &[1],
        co64: false,
        split_times: false,
    },
    Layout {
        chunks: &[2, 3, 1],
        co64: true,
        split_times: true,
    },
    Layout {
        chunks: &[4],
        co64: false,
        split_times: true,
    },
];
fn atom(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
    let mut out = (payload.len() as u32 + 8).to_be_bytes().to_vec();
    out.extend(tag);
    out.extend(payload);
    out
}
fn full(version: u8, flags: u32, rest: &[u8]) -> Vec<u8> {
    let mut out = ((u32::from(version) << 24) | flags).to_be_bytes().to_vec();
    out.extend(rest);
    out
}
fn be32(values: &[u32]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_be_bytes()).collect()
}
/// A single-track MP4 of `packets` with the priming and remainder of
/// [`caf`]; `mdat` precedes `moov`, so chunk offsets are known first.
pub(crate) fn mp4(cookie: &[u8], packets: &[Vec<u8>], layout: Layout) -> Option<Vec<u8>> {
    mp4_with_tables(cookie, packets, layout, &[])
}

/// A version-zero optional sample table, for example `ctts` or `stss`.
pub(crate) fn sample_table(tag: &[u8; 4], rows: &[&[u32]]) -> Vec<u8> {
    let mut data = be32(&[rows.len() as u32]);
    for row in rows {
        data.extend(be32(row));
    }
    atom(tag, &full(0, 0, &data))
}

/// [`mp4`] with additional boxes at the end of `stbl`.
pub(crate) fn mp4_with_tables(
    cookie: &[u8],
    packets: &[Vec<u8>],
    layout: Layout,
    extra: &[Vec<u8>],
) -> Option<Vec<u8>> {
    let config = apac_core::Config::parse(cookie).ok()?;
    let rate = u32::try_from(config.sample_rate_hz()?).ok()?;
    let count = packets.len() as u32;
    let valid = count * 1024 - (PRIMING + REMAINDER) as u32;
    let mut file = atom(b"ftyp", b"M4A \0\0\0\0M4A isommp42");
    let mut offset = file.len() as u64 + 8;
    file.extend(atom(b"mdat", &packets.concat()));
    let (mut chunks, mut runs, mut times) = (vec![], vec![], vec![]);
    let mut i = 0;
    while i < packets.len() {
        let n = layout.chunks[chunks.len() % layout.chunks.len()].min(packets.len() - i);
        if runs.last().is_none_or(|&(_, last)| last != n as u32) {
            runs.push((chunks.len() as u32 + 1, n as u32));
        }
        chunks.push(offset);
        times.push(n as u32);
        offset += packets[i..i + n]
            .iter()
            .map(|p| p.len() as u64)
            .sum::<u64>();
        i += n;
    }
    if !layout.split_times {
        times = vec![count];
    }
    let mut entry = vec![0; 6];
    entry.extend([0, 1]);
    entry.extend([0; 8]);
    entry.extend([0, 2, 0, 16, 0, 0, 0, 0]);
    entry.extend((if rate <= 65535 { rate << 16 } else { 0 }).to_be_bytes());
    entry.extend(cookie);
    let stsd = atom(
        b"stsd",
        &full(0, 0, &[be32(&[1]), atom(b"apac", &entry)].concat()),
    );
    let rows: Vec<u8> = times.iter().flat_map(|&n| be32(&[n, 1024])).collect();
    let stts = atom(
        b"stts",
        &full(0, 0, &[be32(&[times.len() as u32]), rows].concat()),
    );
    let rows: Vec<u8> = runs.iter().flat_map(|&(c, n)| be32(&[c, n, 1])).collect();
    let stsc = atom(
        b"stsc",
        &full(0, 0, &[be32(&[runs.len() as u32]), rows].concat()),
    );
    let sizes: Vec<u8> = packets
        .iter()
        .flat_map(|p| be32(&[p.len() as u32]))
        .collect();
    let stsz = atom(b"stsz", &full(0, 0, &[be32(&[0, count]), sizes].concat()));
    let offsets: Vec<u8> = if layout.co64 {
        chunks.iter().flat_map(|o| o.to_be_bytes()).collect()
    } else {
        chunks
            .iter()
            .flat_map(|&o| (o as u32).to_be_bytes())
            .collect()
    };
    let stco = atom(
        if layout.co64 { b"co64" } else { b"stco" },
        &full(0, 0, &[be32(&[chunks.len() as u32]), offsets].concat()),
    );
    let mut tables = [stsd, stts, stsc, stsz, stco].concat();
    for table in extra {
        tables.extend(table);
    }
    let stbl = atom(b"stbl", &tables);
    let url = atom(b"url ", &full(0, 1, &[]));
    let dinf = atom(
        b"dinf",
        &atom(b"dref", &full(0, 0, &[be32(&[1]), url].concat())),
    );
    let minf = atom(b"minf", &[atom(b"smhd", &[0; 8]), dinf, stbl].concat());
    let mdhd = atom(
        b"mdhd",
        &full(0, 0, &[be32(&[0, 0, rate, count * 1024, 0])].concat()),
    );
    let hdlr = atom(
        b"hdlr",
        &full(0, 0, &[&[0; 4][..], b"soun", &[0; 13]].concat()),
    );
    let mdia = atom(b"mdia", &[mdhd, hdlr, minf].concat());
    let matrix = be32(&[0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x4000_0000]);
    let tkhd = [
        be32(&[0, 0, 1, 0, valid]),
        vec![0; 8],
        vec![0, 0, 0, 0, 1, 0, 0, 0],
        matrix.clone(),
        vec![0; 8],
    ];
    let tkhd = atom(b"tkhd", &full(0, 7, &tkhd.concat()));
    let elst = [be32(&[1, valid, PRIMING as u32]), vec![0, 1, 0, 0]];
    let elst = atom(b"elst", &full(0, 0, &elst.concat()));
    let trak = atom(b"trak", &[tkhd, atom(b"edts", &elst), mdia].concat());
    let mvhd = [
        be32(&[0, 0, rate, valid, 0x10000]),
        vec![1, 0],
        vec![0; 10],
        matrix,
        vec![0; 24],
        be32(&[2]),
    ];
    let mvhd = atom(b"mvhd", &full(0, 0, &mvhd.concat()));
    file.extend(atom(b"moov", &[mvhd, trak].concat()));
    Some(file)
}

/// A [`Shared`] source whose bytes in `fence` cannot be read, like a damaged
/// region of a file.
pub(crate) struct Fenced {
    inner: Shared,
    fence: Range<u64>,
    position: u64,
}
impl Fenced {
    pub(crate) fn new(inner: Shared, fence: Range<u64>) -> Self {
        Self {
            inner,
            fence,
            position: 0,
        }
    }
}
impl Read for Fenced {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let end = self.position + out.len() as u64;
        if self.position < self.fence.end && self.fence.start < end {
            return Err(io::Error::other("unreadable region"));
        }
        let n = self.inner.read(out)?;
        self.position += n as u64;
        Ok(n)
    }
}
impl Seek for Fenced {
    fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
        self.position = self.inner.seek(to)?;
        Ok(self.position)
    }
}
impl Source for Fenced {
    fn length(&mut self) -> io::Result<u64> {
        self.inner.length()
    }
    fn revision(&mut self) -> io::Result<Option<SystemTime>> {
        self.inner.revision()
    }
}
