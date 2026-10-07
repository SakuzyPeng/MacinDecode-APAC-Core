use super::*;
use apac_core::model::ChannelDescription;
use std::{collections::BTreeMap, io};

fn spec(channels: u32, frames: u64) -> PcmSpec {
    PcmSpec {
        sample_rate: 48000,
        channels,
        frames,
        layout: ChannelLayout::discrete(channels).unwrap(),
    }
}
fn u16le(bytes: &[u8]) -> u16 {
    u16::from_le_bytes(bytes[..2].try_into().unwrap())
}
fn u32le(bytes: &[u8]) -> u32 {
    u32::from_le_bytes(bytes[..4].try_into().unwrap())
}
fn u64le(bytes: &[u8]) -> u64 {
    u64::from_le_bytes(bytes[..8].try_into().unwrap())
}
fn u32be(bytes: &[u8]) -> u32 {
    u32::from_be_bytes(bytes[..4].try_into().unwrap())
}
fn u64be(bytes: &[u8]) -> u64 {
    u64::from_be_bytes(bytes[..8].try_into().unwrap())
}
fn wave_chunks(raw: &[u8]) -> BTreeMap<&[u8], &[u8]> {
    assert_eq!(&raw[8..12], b"WAVE");
    let mut chunks = BTreeMap::new();
    let mut pos = 12;
    while pos < raw.len() {
        let tag = &raw[pos..pos + 4];
        let n = if tag == b"data" && &raw[..4] == b"RF64" {
            u64le(&raw[28..]) as usize
        } else {
            u32le(&raw[pos + 4..]) as usize
        };
        assert!(chunks.insert(tag, &raw[pos + 8..pos + 8 + n]).is_none());
        pos += 8 + n + n % 2;
    }
    assert_eq!(pos, raw.len());
    chunks
}
fn caf_chunks(raw: &[u8]) -> BTreeMap<&[u8], &[u8]> {
    assert_eq!(&raw[..8], b"caff\0\x01\0\0");
    let mut chunks = BTreeMap::new();
    let mut pos = 8;
    while pos < raw.len() {
        let n = u64be(&raw[pos + 4..]) as usize;
        assert!(
            chunks
                .insert(&raw[pos..pos + 4], &raw[pos + 12..pos + 12 + n])
                .is_none()
        );
        pos += 12 + n;
    }
    assert_eq!(pos, raw.len());
    chunks
}
fn emit(format: PcmFormat, spec: PcmSpec, samples: &[f32]) -> (Vec<u8>, PcmWriteResult) {
    let mut writer = PcmWriter::new(Vec::new(), PcmWritePlan::new(format, spec).unwrap()).unwrap();
    writer.write_samples(samples).unwrap();
    writer.finish().unwrap()
}

#[test]
fn wave_speaker_masks_and_channel_order_match_extensible_format() {
    for (channels, mask, order) in [
        (1, 0x4, vec![0]),
        (2, 0x3, vec![0, 1]),
        (6, 0x3f, vec![0, 1, 2, 3, 4, 5]),
        (8, 0x63f, vec![0, 1, 2, 3, 6, 7, 4, 5]),
        (12, 0x2d63f, vec![0, 1, 2, 3, 6, 7, 4, 5, 8, 9, 10, 11]),
    ] {
        let samples: Vec<_> = (0..channels * 2).map(|n| n as f32 + 0.25).collect();
        let (raw, result) = emit(PcmFormat::Wav, spec(channels, 2), &samples);
        assert_eq!(&raw[..4], b"RIFF");
        assert_eq!(u32le(&raw[4..]) as usize + 8, raw.len());
        let chunks = wave_chunks(&raw);
        let fmt = chunks[b"fmt ".as_slice()];
        assert_eq!(fmt.len(), 40);
        assert_eq!(u16le(fmt), 0xfffe);
        assert_eq!(u16le(&fmt[2..]), channels as u16);
        assert_eq!(u32le(&fmt[4..]), 48000);
        assert_eq!(u32le(&fmt[8..]), 48000 * channels * 4);
        assert_eq!(u16le(&fmt[12..]), channels as u16 * 4);
        assert_eq!(u16le(&fmt[14..]), 32);
        assert_eq!(u16le(&fmt[16..]), 22);
        assert_eq!(u16le(&fmt[18..]), 32);
        assert_eq!(u32le(&fmt[20..]), mask);
        assert_eq!(
            &fmt[24..],
            &[3, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113]
        );
        assert_eq!(u32le(chunks[b"fact".as_slice()]), 2);
        let expected: Vec<_> = samples
            .chunks_exact(channels as usize)
            .flat_map(|frame| order.iter().flat_map(|&c| frame[c].to_le_bytes()))
            .collect();
        assert_eq!(chunks[b"data".as_slice()], expected);
        assert_eq!(
            result.pcm_sha256,
            format!("{:x}", Sha256::digest(&expected))
        );
        assert_eq!(result.file_sha256, format!("{:x}", Sha256::digest(&raw)));
        assert_eq!(result.plan.file_bytes(), raw.len() as u64);
    }
}

#[test]
fn rf64_declares_64_bit_sizes_and_can_be_forced_for_small_files() {
    let (raw, result) = emit(PcmFormat::Rf64, spec(2, 2), &[0., -0., 1., -1.]);
    assert_eq!(&raw[..4], b"RF64");
    assert_eq!(u32le(&raw[4..]), u32::MAX);
    assert_eq!(result.plan.format(), PcmFormat::Rf64);
    let chunks = wave_chunks(&raw);
    let ds = chunks[b"ds64".as_slice()];
    assert_eq!(ds.len(), 28);
    assert_eq!(u64le(ds) + 8, raw.len() as u64);
    assert_eq!(u64le(&ds[8..]), 16);
    assert_eq!(u64le(&ds[16..]), 2);
    assert_eq!(u32le(&ds[24..]), 0);
    assert_eq!(u32le(&raw[112..]), u32::MAX);
    assert_eq!(u32le(chunks[b"fact".as_slice()]), u32::MAX);
    let last_riff_frames = (u64::from(u32::MAX) - 72) / 4;
    let small = PcmWritePlan::new(PcmFormat::Wav, spec(1, last_riff_frames)).unwrap();
    let large = PcmWritePlan::new(PcmFormat::Wav, spec(1, last_riff_frames + 1)).unwrap();
    assert_eq!(small.format(), PcmFormat::Wav);
    assert_eq!(large.format(), PcmFormat::Rf64);
    assert_eq!(u64le(&large.header[28..]), 4 * (last_riff_frames + 1));
    let huge = PcmWritePlan::new(PcmFormat::Rf64, spec(1, u64::from(u32::MAX) + 1)).unwrap();
    assert_eq!(u64le(&huge.header[36..]), u64::from(u32::MAX) + 1);
    assert_eq!(u32le(&huge.header[104..]), u32::MAX);
}

#[test]
fn caf_preserves_float_bits_and_tagged_or_described_layouts() {
    let mut specs: Vec<_> = [1, 2, 6, 8, 12, 16, 24].map(|n| spec(n, 2)).into();
    for n in [1, 4, 9, 16] {
        for family in [190, 191] {
            specs.push(PcmSpec {
                layout: ChannelLayout::tagged((family << 16) | n, n, None),
                ..spec_with_count(n, 2)
            });
        }
    }
    let mut layout = ChannelLayout::tagged(0, 255, None);
    layout.descriptions = (0..255)
        .map(|i| ChannelDescription {
            label: (2 << 16) | (i % 16),
            flags: 0,
            coordinates: [-0., 0., 0.],
        })
        .collect();
    specs.push(PcmSpec {
        layout,
        ..spec_with_count(255, 2)
    });
    for spec in specs {
        let samples: Vec<_> = (0..spec.channels * 2)
            .map(|i| {
                f32::from_bits(match i % 4 {
                    0 => 0x8000_0000,
                    1 => 1,
                    2 => 0x3f80_0001,
                    _ => 0xbf80_0000,
                })
            })
            .collect();
        let (raw, result) = emit(PcmFormat::Caf, spec.clone(), &samples);
        let chunks = caf_chunks(&raw);
        assert_eq!(chunks.len(), 3);
        let desc = chunks[b"desc".as_slice()];
        assert_eq!(f64::from_be_bytes(desc[..8].try_into().unwrap()), 48000.);
        assert_eq!(&desc[8..12], b"lpcm");
        assert_eq!(u32be(&desc[12..]), 3);
        assert_eq!(u32be(&desc[16..]), spec.channels * 4);
        assert_eq!(u32be(&desc[20..]), 1);
        assert_eq!(u32be(&desc[24..]), spec.channels);
        assert_eq!(u32be(&desc[28..]), 32);
        let chan = chunks[b"chan".as_slice()];
        assert_eq!(u32be(chan), spec.layout.tag);
        assert_eq!(u32be(&chan[4..]), spec.layout.bitmap);
        assert_eq!(u32be(&chan[8..]) as usize, spec.layout.descriptions.len());
        for (actual, expected) in chan[12..]
            .as_chunks::<20>()
            .0
            .iter()
            .zip(&spec.layout.descriptions)
        {
            assert_eq!(u32be(actual), expected.label);
            assert_eq!(u32be(&actual[4..]), expected.flags);
            for (bytes, coordinate) in actual[8..]
                .as_chunks::<4>()
                .0
                .iter()
                .zip(expected.coordinates)
            {
                assert_eq!(u32be(bytes), coordinate.to_bits());
            }
        }
        let data = chunks[b"data".as_slice()];
        assert_eq!(&data[..4], &[0; 4]);
        let expected: Vec<_> = samples.iter().flat_map(|v| v.to_le_bytes()).collect();
        assert_eq!(&data[4..], expected);
        assert_eq!(result.pcm_sha256, format!("{:x}", Sha256::digest(expected)));
        assert_eq!(result.file_sha256, format!("{:x}", Sha256::digest(&raw)));
    }
}
fn spec_with_count(channels: u32, frames: u64) -> PcmSpec {
    PcmSpec {
        sample_rate: 48000,
        channels,
        frames,
        layout: ChannelLayout::tagged((147 << 16) | channels, channels, None),
    }
}

#[test]
fn ambiguous_wave_layouts_and_invalid_sizes_fail_before_writing() {
    for layout in [
        ChannelLayout::discrete(16).unwrap(),
        ChannelLayout::discrete(24).unwrap(),
        ChannelLayout::tagged((190 << 16) | 4, 4, None),
        ChannelLayout::tagged((147 << 16) | 2, 2, None),
    ] {
        let n = layout.tag & 0xffff;
        for format in [PcmFormat::Wav, PcmFormat::Rf64] {
            let error = PcmWritePlan::new(
                format,
                PcmSpec {
                    layout: layout.clone(),
                    ..spec_with_count(n, 1)
                },
            )
            .unwrap_err();
            assert!(error.message.contains("-o output.caf"));
        }
    }
    for format in [PcmFormat::Wav, PcmFormat::Rf64, PcmFormat::Caf] {
        assert!(PcmWritePlan::new(format, spec(2, u64::MAX)).is_err());
        for channels in [0, 256] {
            assert!(PcmWritePlan::new(format, spec_with_count(channels, 0)).is_err());
        }
        assert!(
            PcmWritePlan::new(
                format,
                PcmSpec {
                    sample_rate: 0,
                    ..spec(2, 0)
                }
            )
            .is_err()
        );
    }
    assert!(
        PcmWritePlan::new(
            PcmFormat::Wav,
            PcmSpec {
                sample_rate: u32::MAX,
                ..spec(2, 0)
            }
        )
        .is_err()
    );
    let mut invalid = spec(2, 0);
    invalid.layout.bitmap = 1;
    assert!(PcmWritePlan::new(PcmFormat::Caf, invalid).is_err());
}

#[test]
fn empty_audio_and_short_or_invalid_sample_sequences_have_explicit_outcomes() {
    for format in [PcmFormat::Wav, PcmFormat::Rf64, PcmFormat::Caf] {
        let (_, result) = emit(format, spec(2, 0), &[]);
        assert_eq!(result.plan.audio_bytes(), 0);
        assert_eq!(result.pcm_sha256, format!("{:x}", Sha256::digest([])));
        let writer =
            PcmWriter::new(Vec::new(), PcmWritePlan::new(format, spec(2, 1)).unwrap()).unwrap();
        assert!(writer.finish().is_err());
        for samples in [
            vec![1.],
            vec![0.; 4],
            vec![f32::NAN, 0.],
            vec![0., f32::INFINITY],
        ] {
            let mut writer =
                PcmWriter::new(Vec::new(), PcmWritePlan::new(format, spec(2, 1)).unwrap()).unwrap();
            assert!(writer.write_samples(&samples).is_err());
            assert!(writer.write_samples(&[0., 0.]).is_err());
            assert!(writer.finish().is_err());
        }
    }
}

struct FailingSink {
    remaining: usize,
    fail_flush: bool,
}
impl Write for FailingSink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Err(io::Error::other("injected write failure"));
        }
        let n = bytes.len().min(self.remaining);
        self.remaining -= n;
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.fail_flush {
            Err(io::Error::other("injected flush failure"))
        } else {
            Ok(())
        }
    }
}
#[test]
fn partial_writes_and_flush_failures_never_produce_success() {
    let plan = PcmWritePlan::new(PcmFormat::Wav, spec(2, 1)).unwrap();
    assert!(
        PcmWriter::new(
            FailingSink {
                remaining: 4,
                fail_flush: false
            },
            plan.clone()
        )
        .is_err()
    );
    let mut writer = PcmWriter::new(
        FailingSink {
            remaining: 83,
            fail_flush: false,
        },
        plan.clone(),
    )
    .unwrap();
    assert!(writer.write_samples(&[1., 2.]).is_err());
    assert!(writer.finish().is_err());
    let mut writer = PcmWriter::new(
        FailingSink {
            remaining: usize::MAX,
            fail_flush: true,
        },
        plan,
    )
    .unwrap();
    writer.write_samples(&[1., 2.]).unwrap();
    assert!(writer.finish().is_err());
}
