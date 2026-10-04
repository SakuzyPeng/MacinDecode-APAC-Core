//! Shared-ICS SQ spectra and bounded CAC before TNS. No inter-frame CAC state.
//!
//! The CAC syntax is read here; the inverse mixing comes from the `apac-cac` crate
//! (the `cac` feature). Without it, only frames whose gain indices are all 0 (no mixing)
//! pass, and any other frame is rejected at its first nonzero gain run.
use super::ParseMode;
use super::spectrum::{Codebook, Trie};
use super::{FrameContext, IcsInfo, Parser, SpectrumReport};
use crate::config::{ConfigField, ParseError, bits::BitReader};
use crate::prelude::*;
use crate::record::FieldValue;

/// The CAC arithmetic profile.
pub const NUMERIC_PROFILE: &str = "apac-cac-math-v1";

/// One run-length coded CAC gain run.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct CacRun {
    pub gain_index: u8,
    pub repeat_code: u8,
    pub bit_offset: usize,
    pub bit_length: usize,
}
/// CAC syntax of a shared-ICS channel pair.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct CacData {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub runs: Vec<CacRun>,
    /// Group-major, one gain index for each active SFB, shared by its windows.
    pub gain_indices: Vec<Vec<u8>>,
}
/// One channel's spectrum after CAC.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct CacChannelSpectrum {
    pub channel_index: u8,
    /// Window-major Float32 spectrum, after CAC and before TNS.
    pub scaled: Vec<f32>,
}
/// The packet report up to CAC (`parse-packets --depth cac`); the spectrum
/// report is flattened into it.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct CacReport {
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub spectrum: SpectrumReport,
    pub shared_ics: bool,
    /// Stage completion only; does not change whole-frame completion semantics.
    pub cac_complete: bool,
    pub cac_numeric_profile: String,
    pub cac: Option<CacData>,
    pub output_stage: String,
    pub channels_after_cac: Vec<CacChannelSpectrum>,
}

struct Books {
    gain: &'static Codebook,
    #[cfg_attr(not(test), allow(dead_code))]
    repeat: &'static Codebook,
}
/// Generated from `data/cac-codebooks.json` by the build script.
fn books() -> &'static Books {
    static BOOKS: Books = Books {
        gain: &crate::tables::CAC_GAIN,
        repeat: &crate::tables::CAC_REPEAT,
    };
    &BOOKS
}
fn tries() -> &'static [Trie; 2] {
    &crate::tables::CAC_TRIES
}
/// `tables_sha256` of `data/cac-math-v1.json`: the frozen identity of the rotations that
/// `apac-cac` carries, reported whether or not this build includes them.
const MATH_SHA256: &str = "a72b01a9ad01961516d2d5207da0a61c491ae1a10c76ed9a0406fdc1d0db6994";
/// SHA-256 of the CAC numeric tables.
pub fn math_sha256() -> &'static str {
    MATH_SHA256
}

/// Inverse mixing of one spectral line pair for a gain index: `apac_cac::rotate`.
type Inverse = fn(f32, f32, u8) -> (f32, f32);
#[cfg(feature = "cac")]
const INVERSE: Option<Inverse> = Some(apac_cac::rotate);
#[cfg(not(feature = "cac"))]
const INVERSE: Option<Inverse> = None;

fn inverse_for(runs: &[CacRun], inverse: Option<Inverse>) -> Result<Inverse, ParseError> {
    match inverse {
        Some(rotate) => Ok(rotate),
        None => match runs.iter().find(|run| run.gain_index != 0) {
            Some(run) => Err(ParseError::new(
                run.bit_offset,
                "cac-unavailable",
                format!(
                    "CAC gain index {} needs the apac-cac inverse mixing; this build has no cac feature",
                    run.gain_index
                ),
            )),
            None => Ok(|x, y, _| (x, y)),
        },
    }
}

/// Each ordinary repeat encodes 1..43 slots. The terminal code has capacity 44,
/// and only its unused suffix may extend beyond the remaining active slots.
fn decode_runs(
    bits: &mut BitReader<'_>,
    slots: usize,
) -> Result<(Vec<CacRun>, Vec<u8>), ParseError> {
    let mut runs = Vec::new();
    let mut indices = Vec::new();
    if slots == 0 {
        return Ok((runs, indices));
    }
    for _ in 0..120 {
        let start = bits.position();
        let gain_index = tries()[0].read(bits)? as u8;
        let repeat_start = bits.position();
        let repeat_code = tries()[1].read(bits)? as u8;
        let remaining = slots - indices.len();
        let count = if repeat_code == 43 {
            if !(1..=44).contains(&remaining) {
                return Err(ParseError::new(
                    repeat_start,
                    "cac-terminal",
                    "CAC terminal must cover 1..44 remaining slots",
                ));
            }
            remaining
        } else {
            let count = usize::from(repeat_code) + 1;
            if count >= remaining {
                return Err(ParseError::new(
                    repeat_start,
                    "cac-run",
                    "nonterminal CAC run exhausts or exceeds active slots",
                ));
            }
            count
        };
        indices.extend(core::iter::repeat_n(gain_index, count));
        runs.push(CacRun {
            gain_index,
            repeat_code,
            bit_offset: start,
            bit_length: bits.position() - start,
        });
        if repeat_code == 43 {
            return Ok((runs, indices));
        }
    }
    Err(ParseError::new(
        bits.position(),
        "cac-limit",
        "CAC exceeds 120 run records without a terminal",
    ))
}

fn read_data(parser: &mut Parser<'_>, ics: &IcsInfo) -> Result<CacData, ParseError> {
    read_data_at(parser, ics, &"components[0].tce[0].cac")
}
pub(super) fn read_data_at(
    parser: &mut Parser<'_>,
    ics: &IcsInfo,
    prefix: &dyn core::fmt::Display,
) -> Result<CacData, ParseError> {
    let start = parser.bits.position();
    let (runs, indices) = decode_runs(&mut parser.bits, ics.max_sfb * ics.window_groups.len())?;
    // State-only scans may never materialize spectra. Enforce the same support
    // boundary here, before any parser mode can commit the packet's state.
    inverse_for(&runs, INVERSE)?;
    if parser.mode.record() {
        for (i, run) in runs.iter().enumerate() {
            let gain_bits = books().gain.bits[usize::from(run.gain_index)];
            for (name, value, offset, length) in [
                ("gain_index", run.gain_index, run.bit_offset, gain_bits),
                (
                    "repeat_code",
                    run.repeat_code,
                    run.bit_offset + gain_bits,
                    run.bit_length - gain_bits,
                ),
            ] {
                parser.report.fields.push(ConfigField {
                    name: format!("{prefix}.runs[{i}].{name}"),
                    bit_offset: offset,
                    bit_length: length,
                    value: FieldValue::from(value),
                });
            }
        }
    }
    let gain_indices = (0..ics.window_groups.len())
        .map(|g| indices[g * ics.max_sfb..(g + 1) * ics.max_sfb].to_vec())
        .collect();
    Ok(CacData {
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        runs,
        gain_indices,
    })
}

fn apply(spectrum: &SpectrumReport, data: &CacData) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    apply_channels(&spectrum.channels, data)
}
pub(super) fn apply_channels(
    channels: &[super::ChannelSpectrum],
    data: &CacData,
) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    apply_channels_at_rate(channels, data, 48000)
}
pub(super) fn apply_channels_at_rate(
    channels: &[super::ChannelSpectrum],
    data: &CacData,
    rate: u64,
) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    apply_with(channels, data, rate, INVERSE)
}
fn apply_with(
    channels: &[super::ChannelSpectrum],
    data: &CacData,
    rate: u64,
    inverse: Option<Inverse>,
) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    // Gain index 0 leaves the pair unchanged, so it needs no inverse mixing.
    let rotate = inverse_for(&data.runs, inverse)?;
    let mut left = channels[0].scaled.clone();
    let mut right = channels[1].scaled.clone();
    let ics = &channels[0].ics;
    let short = ics.block_type == 2;
    let (size, offsets) = if short {
        (128, super::sfb::offsets(rate, true))
    } else {
        (1024, super::sfb::offsets(rate, false))
    };
    let mut first = 0;
    for (group, &length) in ics.window_groups.iter().enumerate() {
        for (sfb, &gain) in data.gain_indices[group].iter().enumerate() {
            for window in first..first + length as usize {
                for line in offsets[sfb]..offsets[sfb + 1] {
                    let i = window * size + line;
                    (left[i], right[i]) = rotate(left[i], right[i], gain);
                }
            }
        }
        first += length as usize;
    }
    if left.iter().chain(&right).any(|v| !v.is_finite()) {
        return Err(ParseError::new(
            data.end_bit_offset,
            "cac-nonfinite",
            "nonfinite CAC spectrum",
        ));
    }
    Ok(vec![
        CacChannelSpectrum {
            channel_index: 0,
            scaled: left,
        },
        CacChannelSpectrum {
            channel_index: 1,
            scaled: right,
        },
    ])
}

/// Parse a stereo packet through CAC, stopping at TNS.
pub fn parse_cac(context: &FrameContext, packet: &[u8]) -> Result<CacReport, ParseError> {
    parse_cac_with(context, packet, ParseMode::Report)
}
pub(crate) fn parse_cac_with(
    context: &FrameContext,
    packet: &[u8],
    mode: ParseMode,
) -> Result<CacReport, ParseError> {
    let mut spectrum = super::spectrum::parse_spectrum_with(context, packet, mode)?;
    let shared = spectrum.frame.stop_reason == "shared_ics_cac_deferred";
    let (cac, channels_after_cac) = if shared {
        let position = spectrum.frame.stop_bit_offset;
        let payload = spectrum.frame.payload_bit_offset;
        let mut report = spectrum.frame.clone();
        if report
            .unknown_ranges
            .last()
            .is_some_and(|r| r.bit_offset == position)
        {
            report.unknown_ranges.pop();
        }
        report.diagnostics.pop();
        let mut bits = BitReader::new(packet);
        bits.skip(position)?;
        let mut parser = Parser { bits, report, mode };
        let ics = spectrum.channels[0].ics.clone();
        let right = parser.stream(ics.clone(), 1)?;
        spectrum.channels.push(right);
        spectrum.spectrum_complete = true;
        let data = read_data(&mut parser, &ics)?;
        let output = apply(&spectrum, &data)?;
        spectrum.frame = parser.finish("sq_after_cac_before_tns", true, false)?;
        spectrum.frame.payload_bit_offset = payload;
        (Some(data), output)
    } else if spectrum.spectrum_complete {
        (
            None,
            spectrum
                .channels
                .iter()
                .map(|c| CacChannelSpectrum {
                    channel_index: c.channel_index,
                    scaled: c.scaled.clone(),
                })
                .collect(),
        )
    } else {
        (None, vec![])
    };
    Ok(CacReport {
        cac_complete: channels_after_cac.len() == 2,
        spectrum,
        shared_ics: shared,
        cac_numeric_profile: NUMERIC_PROFILE.into(),
        cac,
        output_stage: "scaled_after_cac_before_tns".into(),
        channels_after_cac,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn word(out: &mut Vec<bool>, code: u32, bits: usize) {
        out.extend((0..bits).rev().map(|bit| code & (1 << bit) != 0));
    }
    fn packed(bits: &[bool]) -> Vec<u8> {
        let mut data = vec![0; bits.len().div_ceil(8)];
        for (i, bit) in bits.iter().enumerate() {
            if *bit {
                data[i / 8] |= 1 << (7 - i % 8);
            }
        }
        data
    }
    #[test]
    fn every_cac_codeword_preserves_following_bits_and_rejects_truncation() {
        for (book, trie) in [(&books().gain, &tries()[0]), (&books().repeat, &tries()[1])] {
            for (index, (&code, &width)) in book.codes.iter().zip(book.bits).enumerate() {
                for marker in [0, 0xffff] {
                    let mut bits = vec![];
                    word(&mut bits, code, width);
                    word(&mut bits, marker, 16);
                    let bytes = packed(&bits);
                    let mut reader = BitReader::new(&bytes);
                    assert_eq!(trie.read(&mut reader).unwrap(), index);
                    assert_eq!(reader.position(), width);
                    assert_eq!(reader.read(16).unwrap(), u64::from(marker));
                    for cut in 0..width {
                        let mut reader = BitReader::new(&bytes);
                        reader.set_end(cut).unwrap();
                        let error = trie.read(&mut reader).unwrap_err();
                        assert!(error.bit_offset <= cut);
                    }
                }
            }
        }
    }
    #[test]
    fn terminal_capacity_and_nonterminal_coverage_are_bounded() {
        let mut bits = vec![];
        word(&mut bits, books().gain.codes[9], books().gain.bits[9]);
        word(&mut bits, 0, 4);
        let bytes = packed(&bits);
        for slots in [1, 43, 44] {
            assert_eq!(
                decode_runs(&mut BitReader::new(&bytes), slots).unwrap().1,
                vec![9; slots]
            );
        }
        assert!(decode_runs(&mut BitReader::new(&bytes), 45).is_err());
        let mut bits = vec![];
        for _ in 0..120 {
            word(&mut bits, books().gain.codes[0], books().gain.bits[0]);
            word(&mut bits, books().repeat.codes[0], books().repeat.bits[0]);
        }
        let bytes = packed(&bits);
        assert!(decode_runs(&mut BitReader::new(&bytes), 1).is_err());
        assert_eq!(
            decode_runs(&mut BitReader::new(&bytes), 121)
                .unwrap_err()
                .kind,
            "cac-limit"
        );
    }
    #[test]
    fn math_identity_is_the_apac_cac_table() {
        assert_eq!(apac_cac::MATH_SHA256, MATH_SHA256);
        assert_eq!(apac_cac::NUMERIC_PROFILE, NUMERIC_PROFILE);
    }
    #[test]
    fn without_inverse_mixing_only_zero_gains_pass() {
        let ics = IcsInfo {
            block_type: 0,
            max_sfb: 2,
            window_groups: vec![1],
        };
        let channel = |channel_index, values: [f32; 2]| super::super::ChannelSpectrum {
            channel_index,
            ics: ics.clone(),
            global_gain: 100,
            sections: vec![],
            scale_factors: vec![],
            quantized: vec![],
            // Band 0 covers lines 0..4 and band 1 lines 4..8 at 48 kHz.
            scaled: [values.as_slice(), &[0.; 2], values.as_slice(), &[0.; 1018]].concat(),
            stream_bit_offset: 0,
            spectral_bit_offset: 0,
            end_bit_offset: 0,
        };
        let channels = [channel(0, [1., 2.]), channel(1, [3., 4.])];
        let run = |gain_index, bit_offset| CacRun {
            gain_index,
            repeat_code: 43,
            bit_offset,
            bit_length: 4,
        };
        let data = |gains: [u8; 2]| CacData {
            start_bit_offset: 100,
            end_bit_offset: 108,
            runs: vec![run(gains[0], 100), run(gains[1], 104)],
            gain_indices: vec![gains.to_vec()],
        };
        let unchanged = apply_with(&channels, &data([0, 0]), 48000, None).unwrap();
        assert_eq!(&unchanged[0].scaled[..2], &[1., 2.]);
        assert_eq!(&unchanged[1].scaled[..2], &[3., 4.]);
        let full = apply_with(&channels, &data([0, 0]), 48000, INVERSE).unwrap();
        assert_eq!(full[0].scaled, unchanged[0].scaled);
        let error = apply_with(&channels, &data([0, 9]), 48000, None).unwrap_err();
        assert_eq!((error.kind, error.bit_offset), ("cac-unavailable", 104));
        let mixed = apply_with(&channels, &data([0, 9]), 48000, INVERSE).unwrap();
        assert_eq!(mixed[0].scaled[..4], unchanged[0].scaled[..4]);
        assert_ne!(mixed[0].scaled[4..6], unchanged[0].scaled[4..6]);
    }
}
