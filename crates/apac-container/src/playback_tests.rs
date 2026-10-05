//! Playback equals the verified sequential reader from the start and after
//! any seek, bounds the work of a seek once indexed, and stops at a failing
//! packet without guessing.
use super::*;
use crate::{
    Access, CafReader, Mp4Reader, PacketSource, Reader,
    test_source::Shared,
    test_streams::{Fenced, LAYOUTS, PRIMING, Stream, caf, grow_caf_chunk, mp4, streams},
};
use apac_core::StreamKind;

/// All valid PCM through the verified sequential reader, as bits.
fn reference<S: PacketSource<Error = Error>>(source: S) -> Vec<u32> {
    let mut reader = Reader::open(source, None, None, Access::Sequential).unwrap();
    let channels = reader.decoder().info().channel_count as usize;
    let mut pcm = vec![0f32; 1024 * channels];
    let mut out = vec![];
    loop {
        let n = reader.read(&mut pcm).unwrap();
        if n == 0 {
            return out;
        }
        out.extend(pcm[..n * channels].iter().map(|v| v.to_bits()));
    }
}
fn bits(pcm: &[f32]) -> Vec<u32> {
    pcm.iter().map(|v| v.to_bits()).collect()
}
/// Up to `limit` frames from the playback position, as bits.
fn take<R: Source>(playback: &mut Playback<R>, limit: u64) -> Vec<u32> {
    let channels = playback.decoder().info().channel_count as usize;
    let mut pcm = vec![0f32; 1024 * channels];
    let mut out = vec![];
    while ((out.len() / channels) as u64) < limit {
        let before = playback.position();
        let n = playback.read(&mut pcm).unwrap();
        if n == 0 {
            assert_eq!(before, playback.frames());
            break;
        }
        assert_eq!(playback.position(), before + n as u64);
        out.extend(bits(&pcm[..n * channels]));
    }
    out.truncate(limit.saturating_mul(channels as u64).min(out.len() as u64) as usize);
    out
}

struct Input {
    name: String,
    kind: StreamKind,
    file: Vec<u8>,
    channels: usize,
    reference: Vec<u32>,
}
impl Input {
    fn frames(&self) -> u64 {
        (self.reference.len() / self.channels) as u64
    }
    /// Up to `frames` reference frames from `start`.
    fn slice(&self, start: u64, frames: u64) -> Vec<u32> {
        let end = start.saturating_add(frames).min(self.frames());
        self.reference[start as usize * self.channels..end as usize * self.channels].to_vec()
    }
}
/// The stream's first packet followed by its later packets, the last one
/// repeated up to `count` packets; `None` unless the sequence decodes.
fn lengthened(stream: &Stream, count: usize) -> Option<Vec<Vec<u8>>> {
    let packets: Vec<_> = (0..count)
        .map(|i| stream.packets[i.min(stream.packets.len() - 1)].clone())
        .collect();
    let mut decoder = Decoder::from_cookie(&stream.cookie).ok()?;
    packets
        .iter()
        .all(|p| decoder.decode_vec(p).is_ok())
        .then_some(packets)
}
/// Every fixture stream lengthened to `count` packets, as CAF and as MP4
/// (rotating through the layouts), with the verified readers' PCM.
fn inputs(count: usize) -> Vec<Input> {
    let mut out = vec![];
    for (i, stream) in streams().iter().enumerate() {
        let Some(packets) = lengthened(stream, count) else {
            continue;
        };
        let channels = Decoder::from_cookie(&stream.cookie)
            .unwrap()
            .info()
            .channel_count as usize;
        let caf = caf(&stream.cookie, &packets).unwrap();
        let layout = LAYOUTS[i % LAYOUTS.len()];
        let mp4 = mp4(&stream.cookie, &packets, layout).unwrap();
        let reference_caf = reference(CafReader::new(Shared::new(&caf)).unwrap());
        let reference_mp4 = reference(Mp4Reader::new(Shared::new(&mp4)).unwrap());
        assert_eq!(reference_caf, reference_mp4, "{}", stream.name);
        for (container, file, reference) in
            [("caf", caf, reference_caf), ("mp4", mp4, reference_mp4)]
        {
            out.push(Input {
                name: format!("{} {container}", stream.name),
                kind: stream.kind,
                file,
                channels,
                reference,
            });
        }
    }
    let kinds: Vec<_> = out.iter().map(|i| i.kind).collect();
    for kind in [StreamKind::Channels, StreamKind::Hoa, StreamKind::Composite] {
        assert!(kinds.contains(&kind), "no {kind:?} input in {kinds:?}");
    }
    out
}
fn open(input: &Input, options: PlaybackOptions) -> Playback<Shared> {
    let media = Media::open(Shared::new(&input.file)).unwrap();
    Playback::open_with(media, options).unwrap()
}

#[test]
fn reading_from_the_start_equals_the_sequential_reader() {
    for input in inputs(20) {
        let mut playback = open(&input, PlaybackOptions::default());
        assert_eq!(playback.frames(), input.frames(), "{}", input.name);
        assert_eq!(
            take(&mut playback, u64::MAX),
            input.reference,
            "{}",
            input.name
        );
        let mut pcm = vec![0f32; 1024 * input.channels];
        assert_eq!(playback.read(&mut pcm).unwrap(), 0);
        assert_eq!(playback.position(), playback.frames());
        // The priming fits in the first packet, so nothing is skipped.
        let stats = playback.stats();
        assert_eq!((stats.advanced_packets, stats.warmup_packets), (0, 0));
        assert_eq!(stats.decoded_packets, 20);
    }
}

/// Leave valid audio only in packet zero, keeping the remaining packets as
/// container remainder. Return the audio range after the first packet.
fn trim_after_first_packet(file: &mut [u8], stream: &Stream, is_caf: bool) -> std::ops::Range<u64> {
    let valid = 1024 - PRIMING as u32;
    if is_caf {
        let summary = CafReader::new(Shared::new(file)).unwrap().summary();
        let pakt = summary.chunks[b"pakt"].offset as usize;
        file[pakt + 8..pakt + 16].copy_from_slice(&i64::from(valid).to_be_bytes());
        let remainder = (stream.packets.len() as i32 - 1) * 1024;
        file[pakt + 20..pakt + 24].copy_from_slice(&remainder.to_be_bytes());
        let data = summary.chunks[b"data"];
        data.offset + 4 + stream.packets[0].len() as u64..data.offset + data.bytes
    } else {
        let boxes = Mp4Reader::new(Shared::new(file)).unwrap().summary().boxes;
        for (tag, relative) in [(b"elst", 8), (b"mvhd", 16), (b"tkhd", 20)] {
            let at = boxes[tag].data_offset as usize + relative;
            file[at..at + 4].copy_from_slice(&valid.to_be_bytes());
        }
        let data = file.windows(4).position(|w| w == b"mdat").unwrap() as u64 + 4;
        data + stream.packets[0].len() as u64..data + stream.packets.concat().len() as u64
    }
}

#[test]
fn eof_rejects_inconsistent_packet_tables_with_or_without_trimmed_tail() {
    let stream = &streams()[0];
    for trim in [false, true] {
        let mut caf_file = stream.file.clone();
        let mut mp4_file = mp4(&stream.cookie, &stream.packets, LAYOUTS[0]).unwrap();
        if trim {
            trim_after_first_packet(&mut caf_file, stream, true);
            trim_after_first_packet(&mut mp4_file, stream, false);
        }
        let stts = Mp4Reader::new(Shared::new(&mp4_file))
            .unwrap()
            .summary()
            .boxes[b"stts"];
        let mut bad_mp4 = mp4_file.clone();
        let at = stts.data_offset as usize + 8;
        bad_mp4[at..at + 4].copy_from_slice(&(stream.packets.len() as u32 + 1).to_be_bytes());
        for (bad, is_caf) in [
            (grow_caf_chunk(&caf_file, b"pakt", &[1]), true),
            (grow_caf_chunk(&caf_file, b"data", &[0]), true),
            (bad_mp4, false),
        ] {
            let expected = if is_caf {
                CafReader::new(Shared::new(&bad)).err().unwrap()
            } else {
                Mp4Reader::new(Shared::new(&bad)).err().unwrap()
            };
            let shared = Shared::new(&bad);
            let mut playback = Playback::open(Media::open(shared.clone()).unwrap()).unwrap();
            let channels = playback.decoder().info().channel_count as usize;
            let mut pcm = vec![0.; 1024 * channels];
            let mut first = None;
            while playback.position() < playback.frames() {
                let n = playback.read(&mut pcm).unwrap();
                assert!(n > 0);
                first.get_or_insert_with(|| bits(&pcm[..n * channels]));
            }
            let stats = playback.stats().clone();
            for _ in 0..2 {
                assert_eq!(
                    playback.read(&mut pcm).unwrap_err(),
                    ReadError::Source(expected.clone())
                );
                assert_eq!(playback.position(), playback.frames());
                assert_eq!(*playback.stats(), stats);
            }
            // Returning to earlier audio still works after the EOF failure.
            playback.seek(0).unwrap();
            let n = playback.read(&mut pcm).unwrap();
            assert_eq!(bits(&pcm[..n * channels]), first.unwrap());
            playback.seek(playback.frames()).unwrap();
            assert!(playback.read(&mut pcm).is_err());
        }
    }
}

#[test]
fn eof_table_io_errors_are_retryable() {
    let stream = &streams()[0];
    for (mut file, is_caf) in [
        (stream.file.clone(), true),
        (
            mp4(&stream.cookie, &stream.packets, LAYOUTS[0]).unwrap(),
            false,
        ),
    ] {
        trim_after_first_packet(&mut file, stream, is_caf);
        let shared = Shared::new(&file);
        let mut playback = Playback::open(Media::open(shared.clone()).unwrap()).unwrap();
        let mut pcm = vec![0.; 1024 * playback.decoder().info().channel_count as usize];
        playback.read(&mut pcm).unwrap();
        let position = playback.position();
        let stats = playback.stats().clone();
        shared.truncate(0);
        let error = playback.read(&mut pcm).unwrap_err();
        assert!(matches!(&error, ReadError::Source(e) if e.packet_index == Some(1)));
        assert_eq!(playback.read(&mut pcm).unwrap_err(), error);
        assert_eq!(playback.position(), position);
        shared.replace(file);
        assert_eq!(playback.read(&mut pcm).unwrap(), 0);
        assert_eq!(playback.position(), position);
        assert_eq!(*playback.stats(), stats);
    }
}

#[test]
fn eof_checks_trimmed_tables_without_reading_audio_or_moving_the_decoder() {
    let stream = &streams()[0];
    for (mut file, is_caf) in [
        (stream.file.clone(), true),
        (
            mp4(&stream.cookie, &stream.packets, LAYOUTS[0]).unwrap(),
            false,
        ),
    ] {
        let tail = trim_after_first_packet(&mut file, stream, is_caf);
        let source = Fenced::new(Shared::new(&file), tail);
        let mut playback = Playback::open(Media::open(source).unwrap()).unwrap();
        let channels = playback.decoder().info().channel_count as usize;
        let mut pcm = vec![0.; 1024 * channels];
        let n = playback.read(&mut pcm).unwrap();
        let first = bits(&pcm[..n * channels]);
        assert_eq!(n as u64, playback.frames());
        let stats = playback.stats().clone();
        assert_eq!(playback.read(&mut pcm).unwrap(), 0);
        assert_eq!(playback.read(&mut pcm).unwrap(), 0);
        assert_eq!(*playback.stats(), stats);
        playback.seek(0).unwrap();
        playback.seek(playback.frames()).unwrap();
        assert_eq!(playback.read(&mut pcm).unwrap(), 0);
        playback.seek(0).unwrap();
        let n = playback.read(&mut pcm).unwrap();
        assert_eq!(bits(&pcm[..n * channels]), first);
    }
}

#[test]
fn every_seek_equals_the_sequential_reader_from_its_frame() {
    for input in inputs(20) {
        let frames = input.frames();
        let targets = [
            0,
            1,
            1023,
            1024,
            2047,
            frames / 2,
            frames - 1,
            frames,
            3 * 1024 + 5,
            100,
            frames - 1500,
            7000,
            7000,
            6999,
            0,
        ];
        for options in [
            PlaybackOptions::default(),
            PlaybackOptions {
                checkpoint_interval: 1,
                max_checkpoints: 2,
            },
            PlaybackOptions {
                checkpoint_interval: 3,
                max_checkpoints: 3,
            },
        ] {
            let name = format!("{} {options:?}", input.name);
            let mut playback = open(&input, options);
            assert_eq!(take(&mut playback, 1500), input.slice(0, 1500), "{name}");
            for target in targets {
                playback.seek(target).unwrap();
                assert_eq!(playback.position(), target, "{name}");
                let pcm = take(&mut playback, 2500);
                assert_eq!(pcm, input.slice(target, 2500), "{name} seek {target}");
                // Back into the frames just returned.
                let inside = target + (playback.position() - target) / 2;
                playback.seek(inside).unwrap();
                let pcm = take(&mut playback, 600);
                assert_eq!(pcm, input.slice(inside, 600), "{name} seek {inside}");
            }
            assert!(playback.checkpoints() <= options.max_checkpoints, "{name}");
        }
    }
}

#[test]
fn reading_keeps_checkpoints_and_a_full_index_thins_out() {
    let input = &inputs(20)[0];
    let options = PlaybackOptions {
        checkpoint_interval: 2,
        max_checkpoints: 1024,
    };
    let mut playback = open(input, options);
    assert_eq!(take(&mut playback, u64::MAX), input.reference);
    assert_eq!(
        (playback.checkpoints(), playback.checkpoint_interval()),
        (10, 2)
    );
    // With room for four, the index halves at packets 4, 8 and 16.
    let options = PlaybackOptions {
        checkpoint_interval: 1,
        max_checkpoints: 4,
    };
    let mut playback = open(input, options);
    assert_eq!(take(&mut playback, u64::MAX), input.reference);
    assert_eq!(
        (playback.checkpoints(), playback.checkpoint_interval()),
        (3, 8)
    );
    for target in [input.frames() - 1, 9000, 1] {
        playback.seek(target).unwrap();
        assert_eq!(take(&mut playback, 1500), input.slice(target, 1500));
    }
}

#[test]
fn a_complete_index_bounds_the_work_of_every_seek() {
    let options = PlaybackOptions {
        checkpoint_interval: 4,
        max_checkpoints: 1024,
    };
    for input in inputs(40) {
        let name = &input.name;
        let frames = input.frames();
        let mut playback = open(&input, options);
        // Index ahead in small steps, interleaved with playback, which keeps
        // checkpoints of its own past the index.
        assert!(!playback.index_complete());
        assert!(!playback.extend_index(5).unwrap());
        assert_eq!(playback.stats().indexed_packets, 5);
        assert_eq!(take(&mut playback, 15000), input.slice(0, 15000), "{name}");
        playback.seek(frames / 2).unwrap();
        assert_eq!(
            take(&mut playback, 3000),
            input.slice(frames / 2, 3000),
            "{name}"
        );
        let mut calls = 0;
        while !playback.extend_index(3).unwrap() {
            calls += 1;
            assert!(calls < 100, "{name}");
        }
        assert!(playback.index_complete() && playback.extend_index(10).unwrap());
        assert_eq!(playback.indexed_frames(), frames, "{name}");
        let mut pcm = vec![0f32; 1024 * input.channels];
        for target in (0..frames).step_by(1237).chain([frames - 1, 77, 40000]) {
            let before = playback.stats().clone();
            playback.seek(target).unwrap();
            let n = playback.read(&mut pcm).unwrap();
            let after = playback.stats();
            assert!(
                after.advanced_packets - before.advanced_packets < options.checkpoint_interval,
                "{name} seek {target}"
            );
            assert!(
                after.decoded_packets - before.decoded_packets <= 2,
                "{name} seek {target}"
            );
            assert_eq!(
                bits(&pcm[..n * input.channels]),
                input.slice(target, n as u64),
                "{name} seek {target}"
            );
        }
    }
}

#[test]
fn a_failing_packet_stops_playback_there_and_earlier_positions_stay_reachable() {
    let stream = &streams()[0];
    let mut packets = lengthened(stream, 12).unwrap();
    let good = caf(&stream.cookie, &packets).unwrap();
    let reference = reference(CafReader::new(Shared::new(&good)).unwrap());
    packets[7] = vec![0xff];
    let file = caf(&stream.cookie, &packets).unwrap();
    let options = PlaybackOptions {
        checkpoint_interval: 2,
        max_checkpoints: 64,
    };
    let mut playback =
        Playback::open_with(Media::open(Shared::new(&file)).unwrap(), options).unwrap();
    let channels = playback.decoder().info().channel_count as usize;
    let mut pcm = vec![0f32; 1024 * channels];
    let mut got = vec![];
    let error = loop {
        match playback.read(&mut pcm) {
            Ok(n) => {
                assert!(n > 0);
                got.extend(bits(&pcm[..n * channels]));
            }
            Err(error) => break error,
        }
    };
    assert!(
        matches!(
            error,
            ReadError::Decode {
                packet_index: Some(7),
                ..
            }
        ),
        "{error:?}"
    );
    let stopped = 7 * 1024 - PRIMING as u64;
    assert_eq!(playback.position(), stopped);
    assert_eq!(got, reference[..stopped as usize * channels]);
    assert_eq!(playback.read(&mut pcm).unwrap_err(), error);
    assert_eq!(playback.position(), stopped);
    // Earlier positions decode as before; later ones fail at the same packet,
    // whether reached by advancing or by decoding.
    playback.seek(1000).unwrap();
    assert_eq!(
        take(&mut playback, 3000),
        reference[1000 * channels..4000 * channels]
    );
    for target in [9 * 1024, stopped + 1024] {
        playback.seek(target).unwrap();
        let error = playback.read(&mut pcm).unwrap_err();
        assert!(
            matches!(
                error,
                ReadError::Decode {
                    packet_index: Some(7),
                    ..
                }
            ),
            "{error:?}"
        );
    }
    let error = playback.extend_index(u64::MAX).unwrap_err();
    assert!(
        matches!(
            error,
            ReadError::Decode {
                packet_index: Some(7),
                ..
            }
        ),
        "{error:?}"
    );
    assert!(!playback.index_complete());
    playback.seek(5000).unwrap();
    assert_eq!(
        take(&mut playback, 1000),
        reference[5000 * channels..6000 * channels]
    );
}

#[test]
fn a_source_error_keeps_the_position_and_a_retry_continues() {
    for input in inputs(10).iter().take(2) {
        let shared = Shared::new(&input.file);
        let mut playback = Playback::open(Media::open(shared.clone()).unwrap()).unwrap();
        let mut pcm = vec![0f32; 1024 * input.channels];
        let mut got = vec![];
        for _ in 0..2 {
            let n = playback.read(&mut pcm).unwrap();
            got.extend(bits(&pcm[..n * input.channels]));
        }
        shared.truncate(input.file.len() as u64 - input.file.len() as u64 / 4);
        let error = loop {
            match playback.read(&mut pcm) {
                Ok(n) => {
                    assert!(n > 0, "{}", input.name);
                    got.extend(bits(&pcm[..n * input.channels]));
                }
                Err(error) => break error,
            }
        };
        assert!(matches!(error, ReadError::Source(_)), "{error:?}");
        let position = playback.position();
        assert_eq!(playback.read(&mut pcm).unwrap_err(), error);
        assert_eq!(playback.position(), position);
        shared.replace(input.file.clone());
        got.extend(take(&mut playback, u64::MAX));
        assert_eq!(got, input.reference, "{}", input.name);
    }
}

#[test]
fn requests_outside_the_stream_are_rejected_and_playback_is_send() {
    fn send<T: Send>() {}
    send::<Playback<std::fs::File>>();
    let input = &inputs(6)[0];
    for options in [
        PlaybackOptions {
            checkpoint_interval: 0,
            ..Default::default()
        },
        PlaybackOptions {
            max_checkpoints: 1,
            ..Default::default()
        },
    ] {
        let media = Media::open(Shared::new(&input.file)).unwrap();
        let error = Playback::open_with(media, options).err().unwrap();
        assert!(matches!(
            error,
            ReadError::Invalid {
                operation: "SQ access",
                ..
            }
        ));
    }
    let mut playback = open(input, PlaybackOptions::default());
    let frames = playback.frames();
    let error = playback.seek(frames + 1).unwrap_err();
    assert!(matches!(
        error,
        ReadError::Invalid {
            operation: "SQ access",
            ..
        }
    ));
    assert_eq!(playback.position(), 0);
    let mut short = vec![0f32; 1024 * input.channels - 1];
    let error = playback.read(&mut short).unwrap_err();
    assert!(matches!(
        error,
        ReadError::Invalid {
            operation: "SQ decoder",
            ..
        }
    ));
    playback.seek(frames).unwrap();
    let mut pcm = vec![0f32; 1024 * input.channels];
    assert_eq!(playback.read(&mut pcm).unwrap(), 0);
    playback.seek(0).unwrap();
    assert_eq!(take(&mut playback, u64::MAX), input.reference);
}

/// Benchmark inputs for `examples/realtime.rs`: every fixture stream
/// lengthened to `APAC_BENCH_PACKETS` packets (default 3000, about 64 s at
/// 48 kHz), written as CAF and MP4 into the new directory `APAC_BENCH_DIR`.
///
/// ```sh
/// APAC_BENCH_DIR=bench-streams cargo test --release -p apac-container --lib \
///     write_benchmark_streams -- --ignored
/// ```
#[test]
#[ignore = "writes benchmark files; run explicitly with APAC_BENCH_DIR set"]
fn write_benchmark_streams() {
    let dir = std::path::PathBuf::from(
        std::env::var_os("APAC_BENCH_DIR").expect("set APAC_BENCH_DIR to a new directory"),
    );
    let count = std::env::var("APAC_BENCH_PACKETS").map_or(3000, |v| v.parse().unwrap());
    std::fs::create_dir(&dir).unwrap();
    for (i, stream) in streams().iter().enumerate() {
        let Some(packets) = lengthened(stream, count) else {
            continue;
        };
        let name: String = stream
            .name
            .chars()
            .filter_map(|c| match c {
                '[' => Some('-'),
                ']' => None,
                c => Some(c),
            })
            .collect();
        let caf = caf(&stream.cookie, &packets).unwrap();
        let mp4 = mp4(&stream.cookie, &packets, LAYOUTS[i % LAYOUTS.len()]).unwrap();
        std::fs::write(dir.join(format!("{name}.caf")), caf).unwrap();
        std::fs::write(dir.join(format!("{name}.m4a")), mp4).unwrap();
    }
}
