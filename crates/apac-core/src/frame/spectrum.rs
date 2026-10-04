//! SQ spectra before CAC, TNS and synthesis. No native APIs or FFT are used.
use super::ParseMode;
use super::{FrameContext, FrameReport, Parser};
use crate::config::{ConfigField, ParseError, bits::BitReader};
use crate::prelude::*;
use crate::record::FieldValue;

/// Individual channel stream window information.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct IcsInfo {
    pub block_type: u8,
    pub max_sfb: usize,
    pub window_groups: Vec<u32>,
}
/// One codebook section.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Section {
    pub group: usize,
    pub start_band: usize,
    pub end_band: usize,
    pub codebook: u8,
}
/// One channel's spectral syntax and dequantized spectrum.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct ChannelSpectrum {
    pub channel_index: u8,
    pub ics: IcsInfo,
    pub global_gain: u8,
    pub sections: Vec<Section>,
    /// Group-major; zero-codebook bands have no scale factor.
    pub scale_factors: Vec<Vec<Option<i16>>>,
    /// Window-major: 1024 long coefficients, or eight consecutive 128-point windows.
    pub quantized: Vec<i32>,
    pub scaled: Vec<f32>,
    pub stream_bit_offset: usize,
    pub spectral_bit_offset: usize,
    pub end_bit_offset: usize,
}
/// The packet report up to the spectra (`parse-packets --depth spectrum`); the
/// prefix report is flattened into it.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct SpectrumReport {
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub frame: FrameReport,
    /// True only for two decoded SQ streams; absent CPEs are separately reported.
    pub spectrum_complete: bool,
    pub spectral_stage: String,
    pub channels: Vec<ChannelSpectrum>,
    /// Absent in older reports; new outputs identify their deterministic model.
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub numeric_profile: Option<String>,
}

pub(super) use crate::tables::{Codebook, Trie};
/// The codebooks themselves are read by tests; decoding uses the tries.
#[cfg(test)]
pub(super) struct Tables {
    pub spectral: &'static [Codebook],
    pub scalefactor: &'static Codebook,
}
/// Generated from `data/sq-codebooks.json` by the build script.
#[cfg(test)]
pub(super) fn tables() -> &'static Tables {
    static TABLES: Tables = Tables {
        spectral: &crate::tables::SQ_SPECTRAL,
        scalefactor: &crate::tables::SQ_SCALEFACTOR,
    };
    &TABLES
}
/// Trie 0 decodes scale factors; trie `cb` decodes spectral codebook `cb`.
fn tries() -> &'static [Trie] {
    &crate::tables::SQ_TRIES
}
fn tuple(bits: &mut BitReader<'_>, cb: u8) -> Result<([i32; 4], usize), ParseError> {
    let mut index = tries()[cb as usize].read(bits)?;
    let (size, base, bias) = match cb {
        1 | 2 => (4, 3, -1),
        3 | 4 => (4, 3, 0),
        5 | 6 => (2, 9, -4),
        7 | 8 => (2, 8, 0),
        9 | 10 => (2, 13, 0),
        11 => (2, 17, 0),
        _ => unreachable!("validated codebook"),
    };
    let mut values = [0; 4];
    for v in values[..size].iter_mut().rev() {
        *v = (index % base) as i32 + bias;
        index /= base;
    }
    let mut negative = [false; 4];
    if matches!(cb, 3 | 4 | 7..=11) {
        for (v, sign) in values[..size].iter().zip(&mut negative) {
            if *v != 0 {
                *sign = bits.read(1)? != 0;
            }
        }
    }
    if cb == 11 {
        for v in &mut values[..size] {
            if *v == 16 {
                let start = bits.position();
                let mut width = 4;
                while bits.read(1)? != 0 {
                    width += 1;
                    if width > 12 {
                        return Err(ParseError::new(
                            start,
                            "escape-range",
                            "escape exceeds verified magnitude 8191",
                        ));
                    }
                }
                *v = (1 << width) + bits.read(width)? as i32;
            }
        }
    }
    for (v, sign) in values.iter_mut().zip(negative) {
        if sign {
            *v = -*v;
        }
    }
    Ok((values, size))
}
/// Formula-generated, separately rounded inverse quantizer and gain, followed
/// by one f32 product. Every entry is a fixed IEEE value, independent of libm.
fn inverse(q: i32, sf: i16) -> f32 {
    if q == 0 {
        return 0.;
    }
    let tables = crate::numeric::tables();
    let magnitude = tables.inverse[q.unsigned_abs() as usize];
    let gain = tables.gains[(sf + 256) as usize];
    (if q < 0 { -magnitude } else { magnitude }) * gain
}
impl Parser<'_> {
    pub(super) fn stream(
        &mut self,
        ics: IcsInfo,
        channel_index: u8,
    ) -> Result<ChannelSpectrum, ParseError> {
        self.stream_at(
            &format_args!("components[0].tce[0].channels[{channel_index}]"),
            ics,
            channel_index,
        )
    }
    pub(super) fn stream_at(
        &mut self,
        prefix: &dyn core::fmt::Display,
        ics: IcsInfo,
        channel_index: u8,
    ) -> Result<ChannelSpectrum, ParseError> {
        self.stream_buffer(prefix, ics, channel_index, Vec::new())
    }
    pub(super) fn stream_buffer(
        &mut self,
        prefix: &dyn core::fmt::Display,
        ics: IcsInfo,
        channel_index: u8,
        quantized: Vec<i32>,
    ) -> Result<ChannelSpectrum, ParseError> {
        self.stream_buffer_at_rate(prefix, ics, channel_index, quantized, 48000)
    }
    pub(super) fn stream_buffer_at_rate(
        &mut self,
        prefix: &dyn core::fmt::Display,
        ics: IcsInfo,
        channel_index: u8,
        mut quantized: Vec<i32>,
        rate: u64,
    ) -> Result<ChannelSpectrum, ParseError> {
        let stream_bit_offset = self.bits.position();
        let global_gain = self.member(prefix, "global_gain", 8)? as u8;
        let mut sf = i16::from(global_gain);
        let mut sections = Vec::new();
        let mut factors = vec![vec![None; ics.max_sfb]; ics.window_groups.len()];
        let width = if ics.block_type == 2 { 3 } else { 5 };
        for (group, scales) in factors.iter_mut().enumerate() {
            let mut band = 0;
            while band < ics.max_sfb {
                let name = if self.mode.record() {
                    format!("{prefix}.sections[{}]", sections.len())
                } else {
                    String::new()
                };
                let start = self.bits.position();
                let cb = self.member(&name, "codebook", 4)? as u8;
                if cb > 11 {
                    return Err(ParseError::new(
                        start,
                        "codebook",
                        "SQ permits codebooks 0..11",
                    ));
                }
                let length_start = self.bits.position();
                let mut end = band;
                loop {
                    let part = self.member(&name, "length_part", width)? as usize;
                    end += part;
                    if end > ics.max_sfb {
                        return Err(ParseError::new(
                            length_start,
                            "section-length",
                            "section exceeds max_sfb",
                        ));
                    }
                    if part < (1 << width) - 1 {
                        break;
                    }
                }
                if end == band {
                    return Err(ParseError::new(
                        length_start,
                        "section-length",
                        "section length must be positive",
                    ));
                }
                if cb != 0 {
                    for (sfb, slot) in scales.iter_mut().enumerate().take(end).skip(band) {
                        let start = self.bits.position();
                        let delta = tries()[0].read(&mut self.bits)? as i16 - 60;
                        sf += delta;
                        // The native decoder saturates outside this interval. Reject
                        // it instead: malformed input must not silently change values.
                        if !(-256..=255).contains(&sf) {
                            return Err(ParseError::new(
                                start,
                                "scale-factor",
                                "scale factor outside -256..255",
                            ));
                        }
                        if self.mode.record() {
                            self.report.fields.push(ConfigField {
                                name: format!(
                                    "{prefix}.groups[{group}].bands[{sfb}].scale_factor_delta"
                                ),
                                bit_offset: start,
                                bit_length: self.bits.position() - start,
                                value: FieldValue::from(delta),
                            });
                        }
                        *slot = Some(sf);
                    }
                }
                sections.push(Section {
                    group,
                    start_band: band,
                    end_band: end,
                    codebook: cb,
                });
                band = end;
            }
        }
        let spectral_bit_offset = self.bits.position();
        quantized.resize(1024, 0);
        quantized.fill(0);
        let mut scaled = if self.mode.spectra() {
            vec![0.; 1024]
        } else {
            Vec::new()
        };
        let offsets = super::sfb::offsets(rate, ics.block_type == 2);
        let window_size = if ics.block_type == 2 { 128 } else { 1024 };
        for section in &sections {
            if section.codebook == 0 {
                continue;
            }
            let first_window: usize = ics.window_groups[..section.group]
                .iter()
                .map(|v| *v as usize)
                .sum();
            for band in section.start_band..section.end_band {
                for window in first_window..first_window + ics.window_groups[section.group] as usize
                {
                    let mut line = offsets[band];
                    while line < offsets[band + 1] {
                        let (values, length) = tuple(&mut self.bits, section.codebook)?;
                        for &q in &values[..length] {
                            let index = window * window_size + line;
                            quantized[index] = q;
                            if self.mode.spectra() {
                                scaled[index] =
                                    inverse(q, factors[section.group][band].expect("nonzero band"));
                            }
                            line += 1;
                        }
                    }
                }
            }
        }
        let end_bit_offset = self.bits.position();
        if self.mode.record() && end_bit_offset > spectral_bit_offset {
            self.report.fields.push(ConfigField {
                name: format!("{prefix}.spectral_codewords"),
                bit_offset: spectral_bit_offset,
                bit_length: end_bit_offset - spectral_bit_offset,
                value: FieldValue::Ordering("group_band_window_line"),
            });
        }
        Ok(ChannelSpectrum {
            channel_index,
            ics,
            global_gain,
            sections,
            scale_factors: factors,
            quantized,
            scaled,
            stream_bit_offset,
            spectral_bit_offset,
            end_bit_offset,
        })
    }
}

/// Materialize the exact same separately rounded values only when a tool needs
/// them. Untouched zero-codebook/out-of-band lines remain defined positive zero.
pub(super) fn materialize_at_rate(channel: &mut ChannelSpectrum, rate: u64) {
    if !channel.scaled.is_empty() {
        return;
    }
    channel.scaled.resize(1024, 0.);
    let (size, offsets) = if channel.ics.block_type == 2 {
        (128, super::sfb::offsets(rate, true))
    } else {
        (1024, super::sfb::offsets(rate, false))
    };
    let mut first = 0;
    for (group, factors) in channel.scale_factors.iter().enumerate() {
        for (band, sf) in factors.iter().enumerate() {
            if let Some(sf) = sf {
                for window in first..first + channel.ics.window_groups[group] as usize {
                    for line in offsets[band]..offsets[band + 1] {
                        let index = window * size + line;
                        channel.scaled[index] = inverse(channel.quantized[index], *sf);
                    }
                }
            }
        }
        first += channel.ics.window_groups[group] as usize;
    }
}

/// Parse a stereo packet through the left spectrum (and a shared-ICS right one).
pub fn parse_spectrum(context: &FrameContext, packet: &[u8]) -> Result<SpectrumReport, ParseError> {
    parse_spectrum_with(context, packet, ParseMode::Report)
}
pub(crate) fn parse_spectrum_with(
    context: &FrameContext,
    packet: &[u8],
    mode: ParseMode,
) -> Result<SpectrumReport, ParseError> {
    let mut report = super::parse_frame_with(context, packet, mode)?;
    let stage = "scaled_before_cac_tns".to_owned();
    if report.stop_reason != "sq_left_channel_stream" {
        return Ok(SpectrumReport {
            frame: report,
            spectrum_complete: false,
            spectral_stage: stage,
            channels: vec![],
            numeric_profile: Some(crate::numeric::PROFILE.into()),
        });
    }
    let payload = report.payload_bit_offset;
    let left_start = report.left_ics_bit_offset.expect("confirmed ICS");
    debug_assert!(
        !mode.record()
            || report
                .fields
                .iter()
                .find(|f| f.name == "components[0].tce[0].left_ics.block_type")
                .is_some_and(|f| f.bit_offset == left_start)
    );
    report.fields.retain(|f| f.bit_offset < left_start);
    report.unknown_ranges.pop(); // The prefix's opaque tail; retain embedded preroll.
    report.diagnostics.pop();
    let mut bits = BitReader::new(packet);
    bits.skip(left_start)?;
    let mut parser = Parser { bits, report, mode };
    let left = parser.ics(&"components[0].tce[0].left_ics")?;
    let mut channels = vec![parser.stream(left, 0)?];
    let shared = parser.flag("components[0].tce[0].shared_ics")?;
    let reason = if shared {
        "shared_ics_cac_deferred"
    } else {
        let right = parser.ics(&"components[0].tce[0].right_ics")?;
        channels.push(parser.stream(right, 1)?);
        "sq_spectra_before_tools"
    };
    let mut frame = parser.finish(reason, true, false)?;
    frame.payload_bit_offset = payload;
    Ok(SpectrumReport {
        frame,
        spectrum_complete: !shared,
        spectral_stage: stage,
        channels,
        numeric_profile: Some(crate::numeric::PROFILE.into()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn put(out: &mut Vec<bool>, value: u32, width: usize) {
        out.extend((0..width).rev().map(|i| value & (1 << i) != 0));
    }
    fn packed(bits: &[bool]) -> Vec<u8> {
        let mut bytes = vec![0; bits.len().div_ceil(8)];
        for (i, &v) in bits.iter().enumerate() {
            bytes[i / 8] |= u8::from(v) << (7 - i % 8);
        }
        bytes
    }
    fn encode(cb: u8, values: &[i32]) -> Vec<bool> {
        let (base, bias) = match cb {
            1 | 2 => (3, -1),
            3 | 4 => (3, 0),
            5 | 6 => (9, -4),
            7 | 8 => (8, 0),
            9 | 10 => (13, 0),
            _ => (17, 0),
        };
        let signs = matches!(cb, 3 | 4 | 7..=11);
        let index = values.iter().fold(0, |i, &v| {
            i * base
                + ((if signs {
                    v.abs().min(if cb == 11 { 16 } else { i32::MAX })
                } else {
                    v
                }) - bias) as usize
        });
        let book = &tables().spectral[cb as usize - 1];
        let mut out = vec![];
        put(&mut out, book.codes[index], book.bits[index]);
        if signs {
            out.extend(values.iter().filter(|v| **v != 0).map(|v| *v < 0));
        }
        if cb == 11 {
            for &v in values {
                let v = v.unsigned_abs();
                if v >= 16 {
                    let width = 31 - v.leading_zeros() as usize;
                    out.extend(vec![true; width - 4]);
                    out.push(false);
                    put(&mut out, v - (1 << width), width);
                }
            }
        }
        out
    }
    fn check(cb: u8, values: &[i32]) {
        let data = encode(cb, values);
        for suffix in [0, 63] {
            let mut bits = data.clone();
            put(&mut bits, suffix, 6);
            let bytes = packed(&bits);
            let mut r = BitReader::new(&bytes);
            let (actual, count) = tuple(&mut r, cb).unwrap();
            assert_eq!(&actual[..count], values);
            assert_eq!(r.position(), data.len());
            assert_eq!(r.read(6).unwrap(), u64::from(suffix));
        }
    }
    #[test]
    fn all_codewords_signed_tuples_and_scale_factor_lengths() {
        let mut words = 0;
        let mut variants = 0;
        for cb in 1..=11u8 {
            let (size, base, bias): (u32, usize, i32) = match cb {
                1 | 2 => (4, 3, -1),
                3 | 4 => (4, 3, 0),
                5 | 6 => (2, 9, -4),
                7 | 8 => (2, 8, 0),
                9 | 10 => (2, 13, 0),
                _ => (2, 17, 0),
            };
            for index in 0..tables().spectral[cb as usize - 1].codes.len() {
                let values: Vec<i32> = (0..size)
                    .map(|i| ((index / base.pow(size - 1 - i)) % base) as i32 + bias)
                    .collect();
                let external = matches!(cb, 3 | 4 | 7..=11);
                let count = if external {
                    values.iter().filter(|v| **v != 0).count()
                } else {
                    0
                };
                for mask in 0..1 << count {
                    let mut sign = 0;
                    let values: Vec<i32> = values
                        .iter()
                        .map(|v| {
                            if !external || *v == 0 {
                                *v
                            } else {
                                let n = if mask & (1 << sign) == 0 { *v } else { -v };
                                sign += 1;
                                n
                            }
                        })
                        .collect();
                    check(cb, &values);
                    variants += 1;
                }
                words += 1;
            }
        }
        assert_eq!((words, variants), (1241, 4363));
        for index in 0..121 {
            for suffix in [0, 63] {
                let b = &tables().scalefactor;
                let mut bits = vec![];
                put(&mut bits, b.codes[index], b.bits[index]);
                put(&mut bits, suffix, 6);
                let bytes = packed(&bits);
                let mut r = BitReader::new(&bytes);
                assert_eq!(tries()[0].read(&mut r).unwrap(), index);
                assert_eq!(r.position(), b.bits[index]);
                assert_eq!(r.read(6).unwrap(), u64::from(suffix));
            }
        }
    }
    #[test]
    fn legal_escapes_and_every_truncated_payload_bit() {
        let mut count = 0;
        for value in 16..8192 {
            for values in [[value, 0], [-value, 0], [0, value], [0, -value]] {
                check(11, &values);
                count += 1;
            }
        }
        for (a, b) in [
            (16, 17),
            (31, 32),
            (255, 256),
            (1023, 1024),
            (4095, 4096),
            (8190, 8191),
        ] {
            for sa in [-1, 1] {
                for sb in [-1, 1] {
                    check(11, &[sa * a, sb * b]);
                    count += 1;
                }
            }
        }
        assert_eq!(count, 32728);
        let mut truncated = 0;
        for values in [[16, 0], [0, -17], [31, -32], [-1023, 1024], [8191, -8190]] {
            let bits = encode(11, &values);
            let bytes = packed(&bits);
            for end in 0..bits.len() {
                let mut r = BitReader::new(&bytes);
                r.set_end(end).unwrap();
                let e = tuple(&mut r, 11).unwrap_err();
                assert_eq!(e.kind, "truncated");
                assert!(e.bit_offset <= end);
                truncated += 1;
            }
        }
        assert_eq!(truncated, 138);
        for cb in 1..=11 {
            for index in 0..tables().spectral[cb - 1].codes.len() {
                let b = &tables().spectral[cb - 1];
                let mut bits = vec![];
                put(&mut bits, b.codes[index], b.bits[index]);
                let bytes = packed(&bits);
                for end in 0..bits.len() {
                    let mut r = BitReader::new(&bytes);
                    r.set_end(end).unwrap();
                    assert!(tries()[cb].read(&mut r).is_err());
                }
            }
        }
        let mut bits = encode(11, &[16, 0]);
        bits.truncate(tables().spectral[10].bits[16 * 17] + 1);
        bits.extend([true; 9]);
        let bytes = packed(&bits);
        assert_eq!(
            tuple(&mut BitReader::new(&bytes), 11).unwrap_err().kind,
            "escape-range"
        );
    }
}
