//! Bounded two-channel BWE2 syntax, after TNS and before core alignment.
use super::ParseMode;
use super::{FrameContext, IcsInfo, Parser, TnsReport};
use crate::prelude::*;
use crate::record::FieldValue;
use crate::{
    bwe2_math,
    config::{ConfigField, ParseError, bits::BitReader},
};

/// The BWE2 arithmetic profile.
pub const NUMERIC_PROFILE: &str = bwe2_math::PROFILE;
/// One channel's BWE2 envelope parameters as read from the wire.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2Parameters {
    pub lsf_indices: [u16; 2],
    pub gain_indices: Vec<u8>,
    /// The range actually read from the wire, also when referenced by the other channel.
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
}
/// One channel's BWE2 activity and parameter source.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2ChannelData {
    pub channel_index: u8,
    pub active: bool,
    pub parameter_source_channel: Option<u8>,
    pub parameters: Option<Bwe2Parameters>,
}
/// Stereo BWE2 syntax.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2Data {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub control_bits: [bool; 2],
    pub channels: Vec<Bwe2ChannelData>,
}
/// One copy-up region of the spectral restoration.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2Region {
    pub window_index: usize,
    pub source_start_line: usize,
    pub source_end_line: usize,
    pub target_start_line: usize,
    pub target_end_line: usize,
    pub repetitions: usize,
}
/// One channel's spectrum after BWE2.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2ChannelSpectrum {
    pub channel_index: u8,
    pub regions: Vec<Bwe2Region>,
    /// False for a disabled channel or exact zero source energy.
    pub processing_applied: bool,
    pub analysis: Option<bwe2_math::Analysis>,
    pub scaled: Vec<f32>,
}
/// The packet report up to BWE2 (`parse-packets --depth bwe2`); the TNS report
/// is flattened into it.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Bwe2Report {
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub tns: TnsReport,
    pub bwe2_complete: bool,
    pub bwe2_numeric_profile: String,
    pub bwe2_format_sha256: String,
    pub bwe2_tables_sha256: String,
    pub bwe2_stage: String,
    pub bwe2: Option<Bwe2Data>,
    pub channels_after_bwe2: Vec<Bwe2ChannelSpectrum>,
}
/// One channel element's BWE2 syntax.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct ElementBwe2Data {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub control_bits: Vec<bool>,
    pub channels: Vec<Bwe2ChannelData>,
}
fn read_data(bits: &mut BitReader<'_>, ics: &[IcsInfo]) -> Result<Bwe2Data, ParseError> {
    let data = read_element_data(bits, ics)?;
    Ok(Bwe2Data {
        start_bit_offset: data.start_bit_offset,
        end_bit_offset: data.end_bit_offset,
        control_bits: [data.control_bits[0], data.control_bits[1]],
        channels: data.channels,
    })
}
pub(super) fn read_element_data(
    bits: &mut BitReader<'_>,
    ics: &[IcsInfo],
) -> Result<ElementBwe2Data, ParseError> {
    let data = read_element_syntax(bits, ics)?;
    require_available(&data, crate::BWE2_ENABLED)?;
    Ok(data)
}
fn read_element_syntax(
    bits: &mut BitReader<'_>,
    ics: &[IcsInfo],
) -> Result<ElementBwe2Data, ParseError> {
    let start = bits.position();
    // Both CPE controls precede every parameter record. 10 reuses left data;
    // 01 carries right only; 11 carries independent data, in channel order.
    let flags = (0..ics.len())
        .map(|_| bits.read(1).map(|v| v != 0))
        .collect::<Result<Vec<_>, _>>()?;
    let mut channels: Vec<Bwe2ChannelData> = Vec::new();
    for ch in 0..ics.len() {
        let active = if ics.len() == 1 {
            flags[ch]
        } else {
            ics[ch].max_sfb > 0 && (flags[ch] || (ch == 1 && channels[0].active))
        };
        let (source, parameters) = if !active {
            (None, None)
        } else if flags[ch] {
            let at = bits.position();
            let lsf_indices = [bits.read(9)? as u16, bits.read(9)? as u16];
            let mut gains = Vec::new();
            for _ in 0..if ics[ch].max_sfb == 0 {
                0
            } else {
                ics[ch].window_groups.len()
            } {
                gains.push(bits.read(6)? as u8);
            }
            (
                Some(ch as u8),
                Some(Bwe2Parameters {
                    lsf_indices,
                    gain_indices: gains,
                    start_bit_offset: at,
                    end_bit_offset: bits.position(),
                }),
            )
        } else {
            let parameters = channels[0]
                .parameters
                .as_ref()
                .expect("active left parameters");
            if parameters.gain_indices.len() < ics[ch].window_groups.len() {
                return Err(ParseError::new(
                    start + 1,
                    "bwe2-reuse",
                    "BWE2 left parameters do not define every right window group",
                ));
            }
            (Some(0), Some(parameters.clone()))
        };
        channels.push(Bwe2ChannelData {
            channel_index: ch as u8,
            active,
            parameter_source_channel: source,
            parameters,
        });
    }
    let data = ElementBwe2Data {
        start_bit_offset: start,
        end_bit_offset: bits.position(),
        control_bits: flags,
        channels,
    };
    Ok(data)
}

fn require_available(data: &ElementBwe2Data, available: bool) -> Result<(), ParseError> {
    if !available && let Some(channel) = data.channels.iter().find(|channel| channel.active) {
        return Err(ParseError::new(
            data.start_bit_offset + usize::from(channel.channel_index),
            "bwe2-unavailable",
            format!(
                "active BWE2 channel {} needs apac-bwe2 spectral restoration; this build has no bwe2 feature",
                channel.channel_index,
            ),
        ));
    }
    Ok(())
}
pub(super) fn cutoff_at_rate(ics: &IcsInfo, rate: u64) -> usize {
    let (offsets, scale) = if ics.block_type == 2 {
        (super::sfb::offsets(rate, true), 8)
    } else {
        (super::sfb::offsets(rate, false), 1)
    };
    if offsets[ics.max_sfb] * scale < 384 {
        256 / scale
    } else {
        384 / scale
    }
}
pub(super) fn regions(ics: &IcsInfo) -> (usize, Vec<Bwe2Region>) {
    regions_at_rate(ics, 48000)
}
pub(super) fn regions_at_rate(ics: &IcsInfo, rate: u64) -> (usize, Vec<Bwe2Region>) {
    let short = ics.block_type == 2;
    let (size, offsets, scale) = if short {
        (128, super::sfb::offsets(rate, true), 8)
    } else {
        (1024, super::sfb::offsets(rate, false), 1)
    };
    let lower = offsets[ics.max_sfb] * scale < 384;
    let source = 128 / scale;
    let cutoff = if lower { 256 / scale } else { 384 / scale };
    let result = (0..scale)
        .map(|window_index| Bwe2Region {
            window_index,
            source_start_line: window_index * size + source,
            source_end_line: window_index * size + cutoff,
            target_start_line: window_index * size + cutoff,
            target_end_line: window_index * size + cutoff + 512 / scale,
            repetitions: if lower { 4 } else { 2 },
        })
        .collect();
    (cutoff, result)
}
/// Parse a stereo packet through BWE2, stopping before core alignment.
pub fn parse_bwe2(context: &FrameContext, packet: &[u8]) -> Result<Bwe2Report, ParseError> {
    parse_bwe2_with(context, packet, ParseMode::Report)
}
pub(crate) fn parse_bwe2_with(
    context: &FrameContext,
    packet: &[u8],
    mode: ParseMode,
) -> Result<Bwe2Report, ParseError> {
    let mut tns = super::tns::parse_tns_with(context, packet, mode)?;
    let mut data = None;
    let mut output = Vec::new();
    if tns.tns_complete {
        let previous = &tns.cac.spectrum.frame;
        let position = previous.stop_bit_offset;
        let payload = previous.payload_bit_offset;
        let mut report = previous.clone();
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
        let ics: Vec<_> = tns
            .cac
            .spectrum
            .channels
            .iter()
            .map(|c| c.ics.clone())
            .collect();
        let parsed = read_data(&mut bits, &ics)?;
        for (ch, parameters) in parsed.channels.iter().enumerate() {
            let input = &tns.channels_after_tns[ch].scaled;
            let (scaled, analysis, ranges) = if let Some(parameters) = &parameters.parameters {
                let (cutoff, ranges) = regions(&ics[ch]);
                let (scaled, analysis) = bwe2_math::restore(
                    input,
                    ics[ch].block_type == 2,
                    cutoff,
                    &ics[ch].window_groups,
                    parameters.lsf_indices,
                    &parameters.gain_indices,
                )
                .map_err(|message| {
                    ParseError::new(
                        parsed.end_bit_offset,
                        "bwe2-numeric",
                        format!("channel {ch}: {message}"),
                    )
                })?;
                (scaled, analysis, ranges)
            } else {
                (input.clone(), None, vec![])
            };
            output.push(Bwe2ChannelSpectrum {
                channel_index: ch as u8,
                processing_applied: analysis.is_some(),
                analysis,
                regions: ranges,
                scaled,
            });
        }
        if mode.record() {
            report.fields.push(ConfigField {
                name: "components[0].bwe2".into(),
                bit_offset: parsed.start_bit_offset,
                bit_length: parsed.end_bit_offset - parsed.start_bit_offset,
                value: FieldValue::Bwe2(Box::new(parsed.clone())),
            });
        }
        tns.cac.spectrum.frame = Parser { bits, report, mode }.finish(
            "sq_after_bwe2_before_core_alignment",
            true,
            false,
        )?;
        tns.cac.spectrum.frame.payload_bit_offset = payload;
        data = Some(parsed);
    }
    Ok(Bwe2Report {
        bwe2_complete: output.len() == 2,
        tns,
        bwe2_numeric_profile: NUMERIC_PROFILE.into(),
        bwe2_format_sha256: bwe2_math::format_sha256().into(),
        bwe2_tables_sha256: bwe2_math::math_sha256().into(),
        bwe2_stage: "scaled_after_bwe2_before_synthesis".into(),
        bwe2: data,
        channels_after_bwe2: output,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Default)]
    struct Bits {
        data: Vec<u8>,
        length: usize,
    }
    impl Bits {
        fn put(&mut self, value: u64, width: usize) {
            for i in (0..width).rev() {
                if self.length.is_multiple_of(8) {
                    self.data.push(0);
                }
                self.data[self.length / 8] |= (((value >> i) & 1) as u8) << (7 - self.length % 8);
                self.length += 1;
            }
        }
    }
    fn contexts(groups: Vec<u32>) -> [IcsInfo; 2] {
        [
            IcsInfo {
                block_type: if groups.len() > 1 { 2 } else { 0 },
                max_sfb: 1,
                window_groups: groups,
            },
            IcsInfo {
                block_type: 0,
                max_sfb: 0,
                window_groups: vec![1],
            },
        ]
    }
    #[test]
    fn all_index_and_gain_codes_truncations_and_following_markers() {
        for axis in 0..3 {
            for value in 0..if axis == 2 { 64 } else { 512 } {
                let mut b = Bits::default();
                b.put(2, 2);
                b.put(if axis == 0 { value } else { 0 }, 9);
                b.put(if axis == 1 { value } else { 0 }, 9);
                b.put(if axis == 2 { value } else { 0 }, 6);
                let end = b.length;
                b.put(0xac35, 16);
                let mut reader = BitReader::new(&b.data);
                let parsed = read_element_syntax(&mut reader, &contexts(vec![1])).unwrap();
                let p = parsed.channels[0].parameters.as_ref().unwrap();
                let actual = [
                    u64::from(p.lsf_indices[0]),
                    u64::from(p.lsf_indices[1]),
                    u64::from(p.gain_indices[0]),
                ];
                assert_eq!(actual[axis], value);
                assert_eq!(reader.position(), end);
                assert_eq!(reader.read(16).unwrap(), 0xac35);
                for cut in 0..end {
                    let mut reader = BitReader::new(&b.data);
                    reader.set_end(cut).unwrap();
                    let expected = match cut {
                        0 => 0,
                        1 => 1,
                        2..=10 => 2,
                        11..=19 => 11,
                        _ => 20,
                    };
                    assert_eq!(
                        read_element_syntax(&mut reader, &contexts(vec![1]))
                            .unwrap_err()
                            .bit_offset,
                        expected
                    );
                }
            }
        }
    }
    #[test]
    fn zero_bands_skip_payload_and_reuse_never_reads_undefined_groups() {
        let mut ics = contexts(vec![1]);
        ics[0].max_sfb = 0;
        let mut reader = BitReader::new(&[0xf0]);
        let parsed = read_element_syntax(&mut reader, &ics).unwrap();
        assert_eq!(parsed.end_bit_offset, 2);
        assert!(parsed.channels.iter().all(|c| !c.active));
        let mut b = Bits::default();
        b.put(2, 2);
        b.put(0, 18);
        b.put(0, 6);
        ics[0].max_sfb = 1;
        ics[1] = IcsInfo {
            block_type: 2,
            max_sfb: 1,
            window_groups: vec![1; 8],
        };
        let e = read_element_syntax(&mut BitReader::new(&b.data), &ics).unwrap_err();
        assert_eq!(e.kind, "bwe2-reuse");
        assert_eq!(e.bit_offset, 1);
    }

    #[test]
    fn availability_follows_wire_activity_including_zero_band_sce() {
        let mut pair = contexts(vec![1]);
        pair[1].max_sfb = 1;
        for flags in 0..4 {
            let mut bits = Bits::default();
            bits.put(0, 7);
            bits.put(flags, 2);
            for flag in [2, 1] {
                if flags & flag != 0 {
                    bits.put(511, 9);
                    bits.put(511, 9);
                    bits.put(0, 6); // Gain index zero still needs BWE2.
                }
            }
            let mut reader = BitReader::new(&bits.data);
            reader.skip(7).unwrap();
            let data = read_element_syntax(&mut reader, &pair).unwrap();
            require_available(&data, true).unwrap();
            if flags == 0 {
                require_available(&data, false).unwrap();
            } else {
                let error = require_available(&data, false).unwrap_err();
                assert_eq!(error.kind, "bwe2-unavailable");
                assert_eq!(error.bit_offset, if flags == 1 { 8 } else { 7 });
            }
        }
        pair[0].max_sfb = 0;
        pair[1].max_sfb = 0;
        let data = read_element_syntax(&mut BitReader::new(&[0xc0]), &pair).unwrap();
        require_available(&data, false).unwrap();
        let mut bits = Bits::default();
        bits.put(1, 1);
        bits.put(511, 9);
        bits.put(511, 9);
        let data = read_element_syntax(&mut BitReader::new(&bits.data), &pair[..1]).unwrap();
        assert!(
            data.channels[0]
                .parameters
                .as_ref()
                .unwrap()
                .gain_indices
                .is_empty()
        );
        assert_eq!(
            require_available(&data, false).unwrap_err().kind,
            "bwe2-unavailable"
        );
    }
}
