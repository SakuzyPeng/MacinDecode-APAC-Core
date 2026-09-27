//! Shared-ICS SQ spectra and bounded CAC before TNS. No inter-frame CAC state.
use super::spectrum::{Codebook, Trie, tables as spectral_tables};
use super::{FrameContext, IcsInfo, Parser, SpectrumReport, parse_spectrum};
use crate::config::{ConfigField, ParseError, bits::BitReader};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::OnceLock;

pub const NUMERIC_PROFILE: &str = "apac-cac-math-v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacRun {
    pub gain_index: u8,
    pub repeat_code: u8,
    pub bit_offset: usize,
    pub bit_length: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacData {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub runs: Vec<CacRun>,
    /// Group-major, one gain index for each active SFB, shared by its windows.
    pub gain_indices: Vec<Vec<u8>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacChannelSpectrum {
    pub channel_index: u8,
    /// Window-major Float32 spectrum, after CAC and before TNS.
    pub scaled: Vec<f32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacReport {
    #[serde(flatten)]
    pub spectrum: SpectrumReport,
    pub shared_ics: bool,
    /// Stage completion only; does not change whole-frame completion semantics.
    pub cac_complete: bool,
    pub cac_numeric_profile: String,
    pub cac: Option<CacData>,
    pub output_stage: String,
    pub channels_after_cac: Vec<CacChannelSpectrum>,
}

#[derive(Deserialize)]
struct Books {
    gain: Codebook,
    repeat: Codebook,
}
fn books() -> &'static Books {
    static BOOKS: OnceLock<Books> = OnceLock::new();
    BOOKS.get_or_init(|| {
        let books: Books = serde_json::from_str(include_str!("../../data/cac-codebooks.json"))
            .expect("built-in CAC format constants");
        assert_eq!(books.gain.codes.len(), 35);
        assert_eq!(books.repeat.codes.len(), 44);
        books
    })
}
fn tries() -> &'static [Trie; 2] {
    static TRIES: OnceLock<[Trie; 2]> = OnceLock::new();
    TRIES.get_or_init(|| [Trie::new(&books().gain), Trie::new(&books().repeat)])
}
#[derive(Deserialize)]
struct Rotation {
    a_f64: u64,
    b_f64: u64,
    swap: bool,
}
#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    rotations: Vec<Rotation>,
}
fn math() -> &'static Math {
    static MATH: OnceLock<Math> = OnceLock::new();
    MATH.get_or_init(|| {
        let data: Math = serde_json::from_str(include_str!("../../data/cac-math-v1.json"))
            .expect("built-in CAC mathematical constants");
        assert_eq!(data.numeric_profile, NUMERIC_PROFILE);
        assert_eq!(data.rotations.len(), 35);
        data
    })
}
pub(crate) fn math_sha256() -> &'static str {
    &math().tables_sha256
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
        indices.extend(std::iter::repeat_n(gain_index, count));
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
    read_data_at(parser, ics, "components[0].tce[0].cac")
}
pub(super) fn read_data_at(
    parser: &mut Parser<'_>,
    ics: &IcsInfo,
    prefix: &str,
) -> Result<CacData, ParseError> {
    let start = parser.bits.position();
    let (runs, indices) = decode_runs(&mut parser.bits, ics.max_sfb * ics.window_groups.len())?;
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
                value: json!(value),
            });
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

fn rotate(x: f32, y: f32, gain: u8) -> (f32, f32) {
    if gain == 0 {
        return (x, y);
    }
    let entry = &math().rotations[usize::from(gain)];
    let (a, b) = (f64::from_bits(entry.a_f64), f64::from_bits(entry.b_f64));
    let (x, y) = (f64::from(x), f64::from(y));
    // The products and sum each round separately. Do not fuse or reassociate.
    let sum = (a * x + b * y) as f32;
    let difference = (b * x - a * y) as f32;
    let canonical = |value: f32| if value == 0. { 0. } else { value };
    if entry.swap {
        (canonical(difference), canonical(sum))
    } else {
        (canonical(sum), canonical(difference))
    }
}

fn apply(spectrum: &SpectrumReport, data: &CacData) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    apply_channels(&spectrum.channels, data)
}
pub(super) fn apply_channels(
    channels: &[super::ChannelSpectrum],
    data: &CacData,
) -> Result<Vec<CacChannelSpectrum>, ParseError> {
    let mut left = channels[0].scaled.clone();
    let mut right = channels[1].scaled.clone();
    let ics = &channels[0].ics;
    let short = ics.block_type == 2;
    let (size, offsets) = if short {
        (128, &spectral_tables().short_offsets)
    } else {
        (1024, &spectral_tables().long_offsets)
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

pub fn parse_cac(context: &FrameContext, packet: &[u8]) -> Result<CacReport, ParseError> {
    let mut spectrum = parse_spectrum(context, packet)?;
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
        let mut parser = Parser { bits, report };
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
            for (index, (&code, &width)) in book.codes.iter().zip(&book.bits).enumerate() {
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
    fn rotations_preserve_identity_energy_and_polarity() {
        assert_eq!(rotate(2., -3., 0), (2., -3.));
        for gain in 1..=34 {
            let (x, y) = rotate(1., 0., gain);
            assert!((f64::from(x) * f64::from(x) + f64::from(y) * f64::from(y) - 1.).abs() < 1e-7);
            let zeros = rotate(0., 0., gain);
            assert_eq!((zeros.0.to_bits(), zeros.1.to_bits()), (0, 0));
        }
        let (left, right) = rotate(1., 1., 9);
        assert_eq!(left.to_bits(), 0);
        assert!(right > 1.4);
        let (left, right) = rotate(1., 1., 26);
        assert!(left < -1.4);
        assert_eq!(right.to_bits(), 0);
    }
}
