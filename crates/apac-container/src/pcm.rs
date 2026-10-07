//! PCM file headers and bounded, sequential sample writing.
use crate::{Error, Result};
use apac_core::ChannelLayout;
use sha2::{Digest, Sha256};
use std::{io::Write, str::FromStr};

/// Float32 output container. WAV automatically becomes RF64 when necessary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PcmFormat {
    /// RIFF WAVE, or RF64 when the complete RIFF size would exceed 32 bits.
    Wav,
    /// RF64, including for small files.
    Rf64,
    /// Core Audio Format containing little-endian Float32 LPCM.
    Caf,
}
impl PcmFormat {
    /// Lowercase container name used in export reports.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Wav => "wav",
            Self::Rf64 => "rf64",
            Self::Caf => "caf",
        }
    }
}
impl FromStr for PcmFormat {
    type Err = Error;
    fn from_str(value: &str) -> Result<Self> {
        match value {
            "wav" => Ok(Self::Wav),
            "rf64" => Ok(Self::Rf64),
            "caf" => Ok(Self::Caf),
            _ => Err(invalid("format must be wav, rf64 or caf")),
        }
    }
}

/// Exact decoded range and its source channel order, before container mapping.
#[derive(Debug, Clone)]
pub struct PcmSpec {
    /// Integer sample rate in Hz.
    pub sample_rate: u32,
    /// Interleaved channel count, from 1 through 255.
    pub channels: u32,
    /// Exact number of frames to write, including zero for an empty range.
    pub frames: u64,
    /// Layout of the samples supplied to the writer.
    pub layout: ChannelLayout,
}

/// Validated immutable header, sizes and channel mapping, prepared before I/O.
#[derive(Debug, Clone)]
pub struct PcmWritePlan {
    format: PcmFormat,
    spec: PcmSpec,
    header: Vec<u8>,
    audio_bytes: u64,
    file_bytes: u64,
    channel_mask: Option<u32>,
    source_channel_indices: Vec<usize>,
}
impl PcmWritePlan {
    /// Validate the layout and calculate the complete file size without I/O.
    pub fn new(format: PcmFormat, spec: PcmSpec) -> Result<Self> {
        if spec.sample_rate == 0 || !(1..=255).contains(&spec.channels) {
            return Err(invalid(
                "requires a positive sample rate and 1..=255 channels",
            ));
        }
        let block_align = spec.channels * 4;
        let audio_bytes = spec
            .frames
            .checked_mul(u64::from(block_align))
            .ok_or_else(overflow)?;
        let (format, header, channel_mask, source_channel_indices) = match format {
            PcmFormat::Wav | PcmFormat::Rf64 => {
                let (mask, order) = wave_layout(&spec)?;
                let byte_rate = spec
                    .sample_rate
                    .checked_mul(block_align)
                    .ok_or_else(overflow)?;
                // RIFF/WAVE + extensible fmt + fact + data header = 80 bytes.
                let riff_size = audio_bytes.checked_add(72).ok_or_else(overflow)?;
                let rf64 = format == PcmFormat::Rf64 || riff_size > u64::from(u32::MAX);
                let format = if rf64 {
                    PcmFormat::Rf64
                } else {
                    PcmFormat::Wav
                };
                let mut header = Vec::with_capacity(if rf64 { 116 } else { 80 });
                header.extend(if rf64 { b"RF64" } else { b"RIFF" });
                le32(&mut header, if rf64 { u32::MAX } else { riff_size as u32 });
                header.extend(b"WAVE");
                if rf64 {
                    header.extend(b"ds64");
                    le32(&mut header, 28);
                    le64(&mut header, riff_size.checked_add(36).ok_or_else(overflow)?);
                    le64(&mut header, audio_bytes);
                    le64(&mut header, spec.frames);
                    le32(&mut header, 0);
                }
                header.extend(b"fmt ");
                le32(&mut header, 40);
                le16(&mut header, 0xfffe); // WAVE_FORMAT_EXTENSIBLE
                le16(&mut header, spec.channels as u16);
                le32(&mut header, spec.sample_rate);
                le32(&mut header, byte_rate);
                le16(&mut header, block_align as u16);
                le16(&mut header, 32);
                le16(&mut header, 22);
                le16(&mut header, 32);
                le32(&mut header, mask);
                // KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, in GUID wire byte order.
                header.extend([3, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113]);
                header.extend(b"fact");
                le32(&mut header, 4);
                // RF64 stores the true sample count in ds64, including small files.
                le32(
                    &mut header,
                    if rf64 { u32::MAX } else { spec.frames as u32 },
                );
                header.extend(b"data");
                le32(
                    &mut header,
                    if rf64 { u32::MAX } else { audio_bytes as u32 },
                );
                (format, header, Some(mask), order)
            }
            PcmFormat::Caf => {
                validate_caf_layout(&spec)?;
                let data_size = audio_bytes
                    .checked_add(4)
                    .filter(|&n| n <= i64::MAX as u64)
                    .ok_or_else(overflow)?;
                let mut header = b"caff\0\x01\0\0desc".to_vec();
                be64(&mut header, 32);
                header.extend(f64::from(spec.sample_rate).to_be_bytes());
                header.extend(b"lpcm");
                // CAF's flags are Float=1, LittleEndian=2 (not ASBD flags).
                be32(&mut header, 3);
                be32(&mut header, block_align);
                be32(&mut header, 1);
                be32(&mut header, spec.channels);
                be32(&mut header, 32);
                header.extend(b"chan");
                be64(&mut header, 12 + 20 * spec.layout.descriptions.len() as u64);
                be32(&mut header, spec.layout.tag);
                be32(&mut header, spec.layout.bitmap);
                be32(&mut header, spec.layout.descriptions.len() as u32);
                for description in &spec.layout.descriptions {
                    be32(&mut header, description.label);
                    be32(&mut header, description.flags);
                    for coordinate in description.coordinates {
                        header.extend(coordinate.to_be_bytes());
                    }
                }
                header.extend(b"data");
                be64(&mut header, data_size);
                be32(&mut header, 0); // edit count, already cropped to valid audio
                (format, header, None, (0..spec.channels as usize).collect())
            }
        };
        let file_bytes = audio_bytes
            .checked_add(header.len() as u64)
            .ok_or_else(overflow)?;
        Ok(Self {
            format,
            spec,
            header,
            audio_bytes,
            file_bytes,
            channel_mask,
            source_channel_indices,
        })
    }
    /// Actual container, after WAV's automatic RF64 selection.
    pub fn format(&self) -> PcmFormat {
        self.format
    }
    /// Input sample specification, including the original channel layout.
    pub fn spec(&self) -> &PcmSpec {
        &self.spec
    }
    /// PCM payload size, excluding all container headers.
    pub fn audio_bytes(&self) -> u64 {
        self.audio_bytes
    }
    /// Complete file size, including headers.
    pub fn file_bytes(&self) -> u64 {
        self.file_bytes
    }
    /// WAVE speaker mask; CAF instead carries the source Core Audio layout.
    pub fn channel_mask(&self) -> Option<u32> {
        self.channel_mask
    }
    /// For each output channel, the zero-based input channel to copy.
    pub fn source_channel_indices(&self) -> &[usize] {
        &self.source_channel_indices
    }
}

fn wave_layout(spec: &PcmSpec) -> Result<(u32, Vec<usize>)> {
    let mapping = match (spec.layout.tag, spec.channels) {
        (0x0064_0001, 1) => Some((0x4, vec![0])),
        (0x0065_0002, 2) => Some((0x3, vec![0, 1])),
        (0x0079_0006, 6) => Some((0x3f, (0..6).collect())),
        (0x0080_0008, 8) => Some((0x63f, vec![0, 1, 2, 3, 6, 7, 4, 5])),
        (0x00c0_000c, 12) => Some((0x2d63f, vec![0, 1, 2, 3, 6, 7, 4, 5, 8, 9, 10, 11])),
        _ => None,
    };
    if let Some(mapping) = mapping
        && ChannelLayout::discrete(spec.channels)
            .is_some_and(|layout| layout.equivalent(&spec.layout))
    {
        return Ok(mapping);
    }
    Err(invalid(
        "WAV/RF64 cannot represent this layout unambiguously; export CAF instead: mapac decode-sq INPUT -o output.caf",
    ))
}

fn validate_caf_layout(spec: &PcmSpec) -> Result<()> {
    let layout = &spec.layout;
    let valid = match layout.tag {
        0 => layout.bitmap == 0 && layout.descriptions.len() == spec.channels as usize,
        0x0001_0000 => {
            layout.descriptions.is_empty() && layout.bitmap.count_ones() == spec.channels
        }
        tag => {
            tag & 0xffff == spec.channels && layout.bitmap == 0 && layout.descriptions.is_empty()
        }
    };
    if !valid
        || layout
            .descriptions
            .iter()
            .any(|d| d.coordinates.iter().any(|c| !c.is_finite()))
    {
        return Err(invalid(
            "CAF channel layout does not describe the declared channels",
        ));
    }
    Ok(())
}

/// Final hashes in the file's channel order, returned only after a full write.
#[derive(Debug)]
pub struct PcmWriteResult {
    /// Validated format, layout, sizes and channel mapping of this file.
    pub plan: PcmWritePlan,
    /// SHA-256 of just the interleaved little-endian Float32 payload.
    pub pcm_sha256: String,
    /// SHA-256 of the complete container, including headers.
    pub file_sha256: String,
}

/// Sequential Float32 writer with a bounded conversion buffer and no seeking.
///
/// Samples are interleaved in the source layout. A failed write poisons the
/// writer; [`Self::finish`] never reports partial output as complete.
pub struct PcmWriter<W: Write> {
    writer: W,
    plan: PcmWritePlan,
    frames: u64,
    written_bytes: u64,
    pcm_hash: Sha256,
    file_hash: Sha256,
    buffer: Vec<u8>,
    failed: bool,
}
impl<W: Write> PcmWriter<W> {
    /// Write the validated header. The caller controls destination ownership.
    pub fn new(mut writer: W, plan: PcmWritePlan) -> Result<Self> {
        writer.write_all(&plan.header)?;
        let mut file_hash = Sha256::new();
        file_hash.update(&plan.header);
        Ok(Self {
            written_bytes: plan.header.len() as u64,
            writer,
            plan,
            frames: 0,
            pcm_hash: Sha256::new(),
            file_hash,
            buffer: Vec::with_capacity(65536),
            failed: false,
        })
    }
    /// Write complete frames without changing sample bits, except channel order.
    pub fn write_samples(&mut self, samples: &[f32]) -> Result<()> {
        if self.failed {
            return Err(invalid("writer failed previously"));
        }
        let result = self.write_block(samples);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn write_block(&mut self, samples: &[f32]) -> Result<()> {
        let channels = self.plan.spec.channels as usize;
        if !samples.len().is_multiple_of(channels) || samples.iter().any(|s| !s.is_finite()) {
            return Err(invalid(
                "requires complete frames containing only finite Float32 samples",
            ));
        }
        let frames = self
            .frames
            .checked_add((samples.len() / channels) as u64)
            .ok_or_else(overflow)?;
        if frames > self.plan.spec.frames {
            return Err(invalid("more frames than declared"));
        }
        for block in samples.chunks(channels * 64) {
            self.buffer.clear();
            for frame in block.chunks_exact(channels) {
                for &source in &self.plan.source_channel_indices {
                    self.buffer.extend_from_slice(&frame[source].to_le_bytes());
                }
            }
            self.writer.write_all(&self.buffer)?;
            self.pcm_hash.update(&self.buffer);
            self.file_hash.update(&self.buffer);
            self.written_bytes += self.buffer.len() as u64;
        }
        self.frames = frames;
        Ok(())
    }
    /// Flush and return the sink and final hashes after verifying exact length.
    pub fn finish(mut self) -> Result<(W, PcmWriteResult)> {
        if self.failed
            || self.frames != self.plan.spec.frames
            || self.written_bytes != self.plan.file_bytes
        {
            return Err(invalid("incomplete PCM output"));
        }
        self.writer.flush()?;
        Ok((
            self.writer,
            PcmWriteResult {
                plan: self.plan,
                pcm_sha256: format!("{:x}", self.pcm_hash.finalize()),
                file_sha256: format!("{:x}", self.file_hash.finalize()),
            },
        ))
    }
}

fn invalid(message: impl Into<String>) -> Error {
    Error::new("PCM output", message)
}
fn overflow() -> Error {
    invalid("container size overflow")
}
fn le16(out: &mut Vec<u8>, n: u16) {
    out.extend(n.to_le_bytes());
}
fn le32(out: &mut Vec<u8>, n: u32) {
    out.extend(n.to_le_bytes());
}
fn le64(out: &mut Vec<u8>, n: u64) {
    out.extend(n.to_le_bytes());
}
fn be32(out: &mut Vec<u8>, n: u32) {
    out.extend(n.to_be_bytes());
}
fn be64(out: &mut Vec<u8>, n: u64) {
    out.extend(n.to_be_bytes());
}

#[cfg(test)]
#[path = "pcm_tests.rs"]
mod tests;
