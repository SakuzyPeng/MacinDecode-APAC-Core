//! Reader behavior on in-memory CAF streams assembled from the frozen state
//! fixtures: the decode-sq loop it replaces is transcribed below as the
//! reference.
use super::*;
use crate::{
    CafReader, Error, Packet, PacketTable,
    test_source::Shared,
    test_streams::{PRIMING, REMAINDER, Stream, caf, streams},
};
use apac_core::model::ChannelLayout;

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
struct Probe<S: PacketSource> {
    inner: S,
    rewinds: usize,
    rewind_error: Option<S::Error>,
    verification_error: Option<S::Error>,
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
            rewind_error: None,
            verification_error: None,
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
        self.inner.rewind()?;
        self.rewind_error.take().map_or(Ok(()), Err)
    }
    fn consumed_packets(&self) -> u64 {
        self.inner.consumed_packets()
    }
    fn verify_remaining(&mut self) -> Result<(), S::Error> {
        self.inner.verify_remaining()?;
        self.verification_error.take().map_or(Ok(()), Err)
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

fn state(decoder: &Decoder) -> String {
    let state = decoder.metadata_state();
    format!("{:?}", (state.drc, state.components, state.hoa))
}
/// PCM and final decoder state of a fresh reader from `start` to the end of
/// the whole valid audio.
fn fresh(stream: &Stream, start: u64, access: Access) -> (Vec<u32>, String) {
    let valid = stream.packets.len() as u64 * 1024 - (PRIMING + REMAINDER) as u64;
    let frames = (start < valid).then(|| valid - start);
    let mut reader = open(stream, Some(start), frames, access);
    let pcm = read_all(&mut reader);
    (pcm, state(reader.decoder()))
}

#[test]
fn seeking_equals_a_fresh_reader_and_rewinds_only_when_needed() {
    for stream in streams() {
        let valid = stream.packets.len() as u64 * 1024 - (PRIMING + REMAINDER) as u64;
        for access in [Access::Sequential, Access::Fast] {
            let name = format!("{} {access:?}", stream.name);
            let mut reader = Reader::open(probe(&stream), None, None, access).unwrap();
            let mut buffer = vec![0f32; reader.samples.len()];
            assert!(reader.read(&mut buffer).unwrap() > 0);
            // (target, rewinds expected so far): forward within reach
            // continues; backward, or after the pass ended, rewinds.
            for (target, rewinds) in [
                (3 * 1024, 0),
                (1000, 1),
                (valid - 5, 2),
                (0, 3),
                (valid, 3),
                (2500, 4),
            ] {
                reader.seek(target).unwrap();
                let pcm = read_all(&mut reader);
                assert_eq!(
                    (pcm, state(reader.decoder())),
                    fresh(&stream, target, access),
                    "{name} seek {target}"
                );
                assert_eq!(reader.source().rewinds, rewinds, "{name} seek {target}");
            }
            assert!(reader.seek(valid + 1).is_err());
            let (source, _, _) = reader.finish().unwrap();
            assert!(source.inner.summary().verified);
        }
    }
}

#[test]
fn a_range_stopped_early_rewinds_on_any_seek() {
    let stream = &streams()[0];
    for access in [Access::Sequential, Access::Fast] {
        let mut reader = Reader::open(probe(stream), Some(1), Some(5), access).unwrap();
        read_all(&mut reader);
        // The packet past the range was read without being decoded.
        reader.seek(3).unwrap();
        assert_eq!(reader.source().rewinds, 1);
        let mut expected = open(stream, Some(3), Some(3), access);
        assert_eq!(read_all(&mut reader), read_all(&mut expected));
        assert!(reader.seek(7).is_err());
        assert!(reader.finish().is_ok());
    }
}

#[test]
fn a_file_changed_between_passes_fails_the_next_pass() {
    let stream = &streams()[0];
    let file = Shared::new(&stream.file);
    let source = CafReader::new(file.clone()).unwrap();
    let mut reader = Reader::open(source, None, None, Access::Sequential).unwrap();
    read_all(&mut reader);
    reader.seek(0).unwrap();
    // A byte of the stream description: decoding is unaffected, the
    // structure digest is not.
    file.change(8 + 12 + 12, &[1]);
    let mut buffer = vec![0f32; reader.samples.len()];
    let error = loop {
        match reader.read(&mut buffer) {
            Ok(0) => panic!("changed file passed verification"),
            Ok(_) => {}
            Err(error) => break error,
        }
    };
    match error {
        ReadError::Source(e) => assert_eq!(e.message, "input changed during reading"),
        other => panic!("{other:?}"),
    }
}

#[test]
fn reused_sources_restart_with_complete_decoder_history() {
    for stream in streams() {
        for access in [Access::Sequential, Access::Fast] {
            let mut original = open(&stream, None, None, access);
            let expected = read_all(&mut original);
            let (finished, _, _) = original.finish().unwrap();
            let mut partial = CafReader::new(Shared::new(&stream.file)).unwrap();
            partial.next_packet().unwrap().unwrap();
            for source in [finished, partial] {
                let mut reader = Reader::open(Probe::new(source), None, None, access).unwrap();
                assert_eq!(reader.source().rewinds, 1);
                assert_eq!(reader.source().consumed_packets(), 0);
                assert_eq!(
                    read_all(&mut reader),
                    expected,
                    "{} {access:?}",
                    stream.name
                );
                assert_eq!(reader.stats().saved_frames, reader.range().frames);
                let (source, _, _) = reader.finish().unwrap();
                assert!(source.inner.summary().verified);
            }
        }
    }
}

#[test]
fn failed_decodes_require_seek_and_replay_the_failed_packet() {
    let stream = &streams()[0];
    let cookie = CafReader::new(Shared::new(&stream.file))
        .unwrap()
        .track()
        .cookie
        .clone();
    let mut packets = vec![stream.packets[0].clone(); 6];
    packets[0] = vec![0xff];
    let file = caf(&cookie, &packets).unwrap();
    let valid = packets.len() as u64 * 1024 - (PRIMING + REMAINDER) as u64;
    for access in [Access::Sequential, Access::Fast] {
        // Exercise both full decoding and a state-only prefix failure, then
        // seek forward to ordinary output or an empty range at EOF.
        for start in [0, 3072] {
            for target in [4096, valid] {
                let source = Probe::new(CafReader::new(Shared::new(&file)).unwrap());
                let mut reader = Reader::open(source, Some(start), None, access).unwrap();
                let mut buffer = vec![0.5; reader.samples.len()];
                let initial = reader.read(&mut buffer).unwrap_err();
                assert!(matches!(
                    initial,
                    ReadError::Decode {
                        packet_index: Some(0),
                        ..
                    }
                ));
                assert!(matches!(
                    reader.read(&mut buffer),
                    Err(ReadError::Invalid { .. })
                ));
                assert_eq!(reader.source().consumed_packets(), 1);
                assert!(buffer.iter().all(|&v| v == 0.5));
                assert!(reader.seek(valid + 1).is_err());
                reader.seek(target).unwrap();
                assert_eq!(reader.source().rewinds, 1);
                let source = CafReader::new(Shared::new(&file)).unwrap();
                let mut fresh = Reader::open(source, Some(target), None, access).unwrap();
                assert_eq!(reader.read(&mut buffer), fresh.read(&mut buffer));
                assert!(matches!(reader.finish(), Err(ReadError::Invalid { .. })));
            }
        }
    }
}

#[test]
fn a_source_read_error_cannot_be_cleared_by_seek_or_finish() {
    let stream = &streams()[0];
    let file = Shared::new(&stream.file);
    let source = CafReader::new(file.clone()).unwrap();
    let data_offset = source.summary().chunks[b"data"].offset + 4;
    let mut reader = Reader::open(source, None, None, Access::Sequential).unwrap();
    file.truncate(data_offset);
    let mut buffer = vec![0.5; reader.samples.len()];
    assert!(matches!(
        reader.read(&mut buffer),
        Err(ReadError::Source(_))
    ));
    file.change_quietly(0, &stream.file);
    assert!(matches!(
        reader.read(&mut buffer),
        Err(ReadError::Invalid { .. })
    ));
    assert!(matches!(reader.seek(0), Err(ReadError::Invalid { .. })));
    assert!(matches!(reader.finish(), Err(ReadError::Invalid { .. })));
}

#[test]
fn verification_and_rewind_errors_leave_the_reader_unusable() {
    let stream = &streams()[0];
    for during_rewind in [false, true] {
        let mut source = probe(stream);
        let error = Error::new("test source", "one-shot restart error");
        if during_rewind {
            source.rewind_error = Some(error.clone());
        } else {
            source.verification_error = Some(error.clone());
        }
        let mut reader = Reader::open(source, None, None, Access::Sequential).unwrap();
        let mut buffer = vec![0f32; reader.samples.len()];
        assert!(reader.read(&mut buffer).unwrap() > 0);
        let range = *reader.range();
        assert_eq!(reader.seek(1), Err(ReadError::Source(error)));
        assert_eq!(*reader.range(), range);
        assert_eq!(reader.source().rewinds, usize::from(during_rewind));
        // The injected error has been consumed, but retrying must not erase
        // the failure even when the source would now allow another pass.
        assert!(matches!(reader.seek(3072), Err(ReadError::Invalid { .. })));
        assert!(matches!(
            reader.read(&mut buffer),
            Err(ReadError::Invalid { .. })
        ));
        assert!(matches!(reader.finish(), Err(ReadError::Invalid { .. })));
    }
}

/// A same-size, valid mutation whose PCM differs after priming. Keeping the
/// packet boundaries and revision unchanged isolates the per-pass digest.
fn changed_first_packet(stream: &Stream) -> Vec<u8> {
    let source = CafReader::new(Shared::new(&stream.file)).unwrap();
    let cookie = &source.track().cookie;
    let first = &stream.packets[0];
    let baseline = Decoder::from_cookie(cookie)
        .unwrap()
        .decode_vec(first)
        .unwrap();
    let skip = PRIMING as usize * source.channels() as usize;
    (0..first.len() * 8)
        .find_map(|bit| {
            let mut bytes = first.clone();
            bytes[bit / 8] ^= 1 << (bit % 8);
            let pcm = Decoder::from_cookie(cookie)
                .unwrap()
                .decode_vec(&bytes)
                .ok()?;
            pcm[skip..]
                .iter()
                .zip(&baseline[skip..])
                .any(|(a, b)| a.to_bits() != b.to_bits())
                .then_some(bytes)
        })
        .expect("fixture admits a valid audio mutation")
}

#[test]
fn seeking_verifies_audio_already_returned_before_discarding_a_pass() {
    let stream = &streams()[0];
    let changed = changed_first_packet(stream);
    for access in [Access::Sequential, Access::Fast] {
        let file = Shared::new(&stream.file);
        let source = CafReader::new(file.clone()).unwrap();
        let data_offset = source.summary().chunks[b"data"].offset + 4;
        let mut reader = Reader::open(source, None, None, access).unwrap();
        file.change_quietly(data_offset, &changed);
        let mut buffer = vec![0f32; reader.samples.len()];
        let n = reader.read(&mut buffer).unwrap();
        let mut clean = open(stream, None, None, access);
        let mut expected = vec![0f32; buffer.len()];
        assert_eq!(clean.read(&mut expected).unwrap(), n);
        assert_ne!(buffer, expected);
        file.change_quietly(data_offset, &stream.packets[0]);
        let error = reader.seek(0).unwrap_err();
        assert!(matches!(error, ReadError::Source(Error { ref message, .. })
            if message == "audio or packet boundaries changed after validation"));
        assert!(matches!(reader.seek(3072), Err(ReadError::Invalid { .. })));
        assert!(matches!(
            reader.read(&mut buffer),
            Err(ReadError::Invalid { .. })
        ));
        assert!(matches!(reader.finish(), Err(ReadError::Invalid { .. })));
    }
}

#[test]
fn reopening_a_source_preserves_its_unfinished_integrity_check() {
    let stream = &streams()[0];
    let file = Shared::new(&stream.file);
    let mut source = CafReader::new(file.clone()).unwrap();
    let data_offset = source.summary().chunks[b"data"].offset + 4;
    file.change_quietly(data_offset, &changed_first_packet(stream));
    source.next_packet().unwrap().unwrap();
    file.change_quietly(data_offset, &stream.packets[0]);
    assert!(matches!(
        Reader::open(source, None, None, Access::Sequential),
        Err(ReadError::Source(Error { ref message, .. }))
            if message == "audio or packet boundaries changed after validation"
    ));
}
