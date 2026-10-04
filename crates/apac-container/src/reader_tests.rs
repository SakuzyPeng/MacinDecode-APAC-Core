//! Reader behavior on in-memory CAF streams assembled from the frozen state
//! fixtures: the decode-sq loop it replaces is transcribed below as the
//! reference.
use super::*;
use crate::{CafReader, Error, Packet, PacketTable, test_source::Shared};
use apac_core::{model::ChannelLayout, synthesis::StreamKind};
use serde_json::Value;

const PRIMING: i32 = 300;
const REMAINDER: i32 = 200;

fn hex(value: &Value) -> Vec<u8> {
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
fn caf(cookie: &[u8], packets: &[Vec<u8>]) -> Option<Vec<u8>> {
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

struct Stream {
    name: String,
    kind: StreamKind,
    packets: Vec<Vec<u8>>,
    file: Vec<u8>,
}
/// Fixture packet sequences that decode in order and form a valid CAF.
fn streams() -> Vec<Stream> {
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

fn open(
    stream: &Stream,
    start: Option<u64>,
    frames: Option<u64>,
    access: Access,
) -> Reader<CafReader<Shared>> {
    let source = CafReader::new(Shared::new(&stream.file)).unwrap();
    Reader::open(source, start, frames, access).unwrap()
}
fn read_all<S: PacketSource>(reader: &mut Reader<S>) -> Vec<u32>
where
    S::Error: fmt::Debug,
{
    let mut out = vec![];
    let mut buffer = vec![0f32; reader.samples.len()];
    loop {
        let n = reader.read(&mut buffer).unwrap();
        if n == 0 {
            return out;
        }
        let channels = reader.samples.len() / 1024;
        out.extend(buffer[..n * channels].iter().map(|v| v.to_bits()));
    }
}

/// The former decode-sq loop over the same packets, with a bare decoder.
fn reference(stream: &Stream, start: Option<u64>, frames: Option<u64>, fast: bool) -> Vec<u32> {
    let cookie = CafReader::new(Shared::new(&stream.file))
        .unwrap()
        .track()
        .cookie
        .clone();
    let valid = stream.packets.len() as u64 * 1024 - (PRIMING + REMAINDER) as u64;
    let table = PacketTable {
        valid_frames: valid as i64,
        priming_frames: PRIMING,
        remainder_frames: REMAINDER,
    };
    let range = Range::new((0, valid), &table, start, frames.unwrap_or(valid.max(1))).unwrap();
    let mut decoder = Decoder::from_cookie(&cookie).unwrap();
    let channels = decoder.info().channel_count as u64;
    let synthesis_start = (range.frames != 0).then(|| (range.raw_start / 1024).saturating_sub(1));
    let mut samples = vec![0f32; 1024 * channels as usize];
    let mut out = vec![];
    for (index, bytes) in stream.packets.iter().enumerate() {
        let (index, raw) = (index as u64, index as u64 * 1024);
        if !range.drain_to_eof && raw >= range.raw_end {
            break;
        }
        if fast && synthesis_start.is_none_or(|start| index < start) {
            decoder.advance(bytes).unwrap();
            continue;
        }
        decoder.decode(bytes, &mut samples).unwrap();
        let first = raw.max(range.raw_start);
        let last = (raw + 1024).min(range.raw_end);
        if first < last {
            out.extend(
                samples[((first - raw) * channels) as usize..((last - raw) * channels) as usize]
                    .iter()
                    .map(|v| v.to_bits()),
            );
        }
    }
    out
}

#[test]
fn reads_equal_the_former_decode_loop_in_both_access_modes() {
    for stream in streams() {
        let valid = stream.packets.len() as u64 * 1024 - (PRIMING + REMAINDER) as u64;
        for (start, frames) in [
            (None, None),
            (Some(1), Some(1500)),
            (Some(valid / 2), None),
            (Some(2100), Some(5)),
            (Some(valid), None),
        ] {
            for access in [Access::Sequential, Access::Fast] {
                let mut reader = open(&stream, start, frames, access);
                let pcm = read_all(&mut reader);
                let expected = reference(&stream, start, frames, access == Access::Fast);
                assert_eq!(
                    pcm, expected,
                    "{} {start:?} {frames:?} {access:?}",
                    stream.name
                );
                let range = *reader.range();
                let channels = reader.decoder().info().channel_count as u64;
                let (source, _, stats) = reader.finish().unwrap();
                assert_eq!(stats.saved_frames * channels, pcm.len() as u64);
                assert_eq!(stats.saved_frames, range.frames);
                if range.drain_to_eof {
                    let packets = stream.packets.len() as u64;
                    assert_eq!(stats.decoded_packets + stats.prefix_packets, packets);
                }
                assert!(source.summary().verified);
            }
        }
    }
}

/// Wraps a source to count rewinds or misdescribe the stream.
struct Probe<S> {
    inner: S,
    rewinds: usize,
    fast: bool,
    first: u64,
    extra_channels: u32,
    layout: Option<ChannelLayout>,
    table: bool,
}
impl<S: PacketSource> Probe<S> {
    fn new(inner: S) -> Self {
        Self {
            inner,
            rewinds: 0,
            fast: true,
            first: 0,
            extra_channels: 0,
            layout: None,
            table: true,
        }
    }
}
impl<S: PacketSource> PacketSource for Probe<S> {
    type Error = S::Error;
    fn config(&self) -> &apac_core::Config {
        self.inner.config()
    }
    fn cookie(&self) -> &[u8] {
        self.inner.cookie()
    }
    fn channels(&self) -> u32 {
        self.inner.channels() + self.extra_channels
    }
    fn layout(&self) -> Option<&ChannelLayout> {
        self.layout.as_ref().or(self.inner.layout())
    }
    fn table(&self) -> Option<PacketTable> {
        self.inner.table().filter(|_| self.table)
    }
    fn range(&self, start: Option<u64>, requested: u64) -> Result<Range, S::Error> {
        self.inner.range(start, requested)
    }
    fn first_packet_index(&self) -> u64 {
        self.first
    }
    fn supports_fast_access(&self) -> bool {
        self.fast && self.inner.supports_fast_access()
    }
    fn next_packet(&mut self) -> Result<Option<Packet>, S::Error> {
        self.inner.next_packet()
    }
    fn rewind(&mut self) -> Result<(), S::Error> {
        self.rewinds += 1;
        self.inner.rewind()
    }
    fn consumed_packets(&self) -> u64 {
        self.inner.consumed_packets()
    }
    fn verify_remaining(&mut self) -> Result<(), S::Error> {
        self.inner.verify_remaining()
    }
}
fn probe(stream: &Stream) -> Probe<CafReader<Shared>> {
    Probe::new(CafReader::new(Shared::new(&stream.file)).unwrap())
}
fn rejection<S: PacketSource>(result: Result<Reader<S>, ReadError<S::Error>>) -> (String, String)
where
    S::Error: fmt::Debug,
{
    match result.err().expect("rejected") {
        ReadError::Invalid { operation, message } => (operation.into(), message),
        other => panic!("{other:?}"),
    }
}

#[test]
fn every_rejection_keeps_the_decode_sq_text() {
    let streams = streams();
    let hoa = streams.iter().find(|s| s.kind == StreamKind::Hoa).unwrap();
    let mut source = probe(hoa);
    source.fast = false;
    assert_eq!(
        rejection(Reader::open(source, None, None, Access::Fast)),
        (
            "SQ access".into(),
            "fast access requires a CAF/MP4 file".into()
        )
    );
    let mut source = probe(hoa);
    source.table = false;
    assert_eq!(
        rejection(Reader::open(source, None, None, Access::Sequential)),
        ("SQ decoder".into(), "missing packet table".into())
    );
    for stream in &streams {
        let mut source = probe(stream);
        source.first = 1;
        let result = Reader::open(source, None, None, Access::Sequential);
        if matches!(stream.kind, StreamKind::Hoa | StreamKind::Composite) {
            assert_eq!(
                rejection(result),
                (
                    "SQ access".into(),
                    "HOA input must include packet zero to establish sequential state".into()
                )
            );
        } else {
            assert!(result.is_ok());
        }
    }
    let mut source = probe(hoa);
    source.extra_channels = 1;
    assert_eq!(
        rejection(Reader::open(source, None, None, Access::Sequential)),
        (
            "SQ decoder".into(),
            "input channel count disagrees with decoder".into()
        )
    );
    let mut source = probe(hoa);
    let mut layout = source.inner.track().layout.clone();
    layout.bitmap = 1;
    let tag = layout.tag;
    source.layout = Some(layout);
    assert_eq!(
        rejection(Reader::open(source, None, None, Access::Sequential)),
        (
            "SQ decoder".into(),
            format!(
                "input channel layout disagrees with decoder: expected cookie layout tag {tag:#010x}, zero bitmap and no descriptions"
            )
        )
    );
    let range_error =
        |start, frames| match Reader::open(probe(hoa), start, frames, Access::Sequential) {
            Err(ReadError::Source(Error {
                operation, message, ..
            })) => (operation, message),
            other => panic!("{:?}", other.err()),
        };
    assert_eq!(
        range_error(None, Some(0)),
        ("packet bundle", "frame count must be positive".into())
    );
    assert_eq!(
        range_error(Some(1 << 40), None),
        (
            "packet bundle",
            "frame start is outside the target window".into()
        )
    );
}

#[test]
fn the_hoa_rule_matches_the_report_profile_rule_on_the_streams() {
    // apac-research's hoa_numeric_profile is Some exactly for HOA and
    // composite decoders; the reader keys the packet-zero rule on the kind.
    for stream in streams() {
        let reader = open(&stream, None, None, Access::Sequential);
        let decoder = reader.decoder();
        assert_eq!(
            matches!(decoder.info().kind, StreamKind::Hoa | StreamKind::Composite),
            decoder.hoa().is_some() || decoder.composite().is_some()
        );
    }
}

#[test]
fn a_short_output_buffer_is_rejected() {
    let stream = &streams()[0];
    let mut reader = open(stream, None, None, Access::Sequential);
    let mut short = vec![0f32; reader.samples.len() - 1];
    assert!(matches!(
        reader.read(&mut short),
        Err(ReadError::Invalid { .. })
    ));
}
