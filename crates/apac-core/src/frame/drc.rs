//! Bounded APAC UniDRC gain payloads. Gain values are exact eighth-decibel
//! integers; parsing does not select a DRC instruction or apply audio gains.
use super::ParseMode;
use super::{Bwe2Report, FrameContext, Parser, packet_config, parse_bwe2};
use crate::config::{
    self, Config, ConfigField, DrcDeclaration, FieldExt, ParseError, ParseStatus, bits::BitReader,
};
use crate::prelude::*;
use crate::record::{DigestUnit, FieldValue};

/// Version of the DRC payload parsing rules.
pub const RULES_VERSION: &str = "apac-drc-payload-v1";
const ROOT: &str = "ancillary.loudness_drc";
const COEFF: &str = "ancillary.loudness_drc.coefficients[0]";
const SET: &str = "ancillary.loudness_drc.coefficients[0].gain_sets[0]";

// ISO/IEC 23003-4 normal gain-delta wire codewords, independently organized by
// signed eighth-dB value. Public cross-check: libxaac 6c771f2ccdef83ebc6dbf7694b1a0ce9c0585d46,
// decoder/drc_src/impd_drc_rom.c, ia_drc_gain_tbls_prof_0_1. Native encoder/decoder
// agreement is an optional hash-gated diagnostic, never a build dependency.
const GAIN_CODES: [(u16, usize); 25] = [
    (0x000, 4),
    (0x039, 9),
    (0x0e2, 11),
    (0x0e3, 11),
    (0x070, 10),
    (0x1ac, 10),
    (0x1ad, 10),
    (0x0d5, 9),
    (0x00f, 7),
    (0x034, 7),
    (0x036, 7),
    (0x019, 6),
    (0x002, 5),
    (0x00f, 5),
    (0x001, 3),
    (0x003, 2),
    (0x002, 3),
    (0x002, 2),
    (0x018, 6),
    (0x006, 6),
    (0x037, 7),
    (0x01d, 8),
    (0x0d7, 9),
    (0x0d4, 9),
    (0x00e, 5),
];

/// SHA-256 of the DRC gain codebook.
pub fn codebook_sha256() -> String {
    let mut bytes = Vec::with_capacity(100);
    for (index, &(code, width)) in GAIN_CODES.iter().enumerate() {
        bytes.extend(code.to_le_bytes());
        bytes.push(width as u8);
        bytes.push((index as i8 - 16) as u8);
    }
    crate::model::sha256(&bytes)
}

/// DRC coding parameters of one gain set.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcParameters {
    pub coefficient_location: u8,
    pub gain_sequences: u8,
    pub gain_sets: u8,
    pub bands: u8,
    pub coding_profile: u8,
    pub interpolation: String,
    pub full_frame: bool,
    pub time_alignment: bool,
    pub frame_samples: u16,
    pub time_delta_min: u16,
}
/// The DRC declaration in effect, with its source and recorded fields.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcConfiguration {
    pub parameters: DrcParameters,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub shared_parameters: Option<Vec<super::DrcSequenceParameters>>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub shared_coefficient_index: Option<usize>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub shared_profile: Option<String>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub shared_format_sha256: Option<String>,
    /// Declaration only, including instructions, characteristics and filters.
    /// Coordinates refer to `source`, not necessarily the current packet.
    pub source: String,
    pub source_sha256: String,
    pub loudness_metadata_source: String,
    pub loudness_metadata_source_sha256: String,
    pub loudness_metadata: Vec<ConfigField>,
    pub fields: Vec<ConfigField>,
}
#[derive(Debug, Clone)]
pub(super) struct DrcContext {
    pub present: bool,
    pub configuration: Option<DrcConfiguration>,
    pub rejection: Option<String>,
}
impl DrcConfiguration {
    fn from_declaration(
        declaration: &DrcDeclaration,
        source: &str,
        channels: u64,
    ) -> Result<Self, String> {
        // Only whether every check passes matters here: a deviation selects the
        // shared declaration model below rather than rejecting.
        let mut rejected = Vec::new();
        let d = declaration;
        let coefficient = d.coefficients.first();
        let set = coefficient.and_then(|c| c.gain_sets.first());
        packet_config::check(
            format_args!("{ROOT}.header_present"),
            d.header_present,
            true,
            source,
            &mut rejected,
        );
        packet_config::check(
            format_args!("{ROOT}.config_present"),
            d.config_present,
            true,
            source,
            &mut rejected,
        );
        for (suffix, field, expected) in [
            ("coefficient_count", d.coefficient_count, 1),
            ("base_channel_count", d.base_channel_count, channels),
        ] {
            packet_config::check(
                format_args!("{ROOT}.{suffix}"),
                field,
                expected,
                source,
                &mut rejected,
            );
        }
        for (suffix, field) in [
            ("location", coefficient.and_then(|c| c.location)),
            (
                "gain_sequence_count",
                coefficient.and_then(|c| c.gain_sequence_count),
            ),
            ("gain_set_count", coefficient.and_then(|c| c.gain_set_count)),
        ] {
            packet_config::check(
                format_args!("{COEFF}.{suffix}"),
                field,
                1,
                source,
                &mut rejected,
            );
        }
        let frame_size_present = coefficient.and_then(|c| c.frame_size_present);
        if frame_size_present.is(true) {
            packet_config::check(
                format_args!("{COEFF}.frame_size_minus_one"),
                coefficient.and_then(|c| c.frame_size_minus_one),
                1023,
                source,
                &mut rejected,
            );
        } else {
            packet_config::check(
                format_args!("{COEFF}.frame_size_present"),
                frame_size_present,
                false,
                source,
                &mut rejected,
            );
        }
        for (suffix, field, expected) in [
            ("coding_profile", set.and_then(|s| s.coding_profile), 0),
            ("band_count", set.and_then(|s| s.band_count), 1),
        ] {
            packet_config::check(
                format_args!("{SET}.{suffix}"),
                field,
                expected,
                source,
                &mut rejected,
            );
        }
        for (suffix, field, expected) in [
            (
                "interpolation_type",
                set.and_then(|s| s.interpolation_type),
                true,
            ),
            ("full_frame", set.and_then(|s| s.full_frame), false),
            ("time_alignment", set.and_then(|s| s.time_alignment), false),
            (
                "time_delta_min_present",
                set.and_then(|s| s.time_delta_min_present),
                true,
            ),
        ] {
            packet_config::check(
                format_args!("{SET}.{suffix}"),
                field,
                expected,
                source,
                &mut rejected,
            );
        }
        packet_config::check(
            format_args!("{SET}.time_delta_min_minus_one"),
            set.and_then(|s| s.time_delta_min_minus_one),
            63,
            source,
            &mut rejected,
        );
        // Preserve legacy metadata identities for the original qualified sets.
        // Other declarations use the shared syntax model. This decoder's off
        // policy never selects or applies a set, unlike native mandatory sets.
        let unqualified_effect = d
            .instruction_effects
            .iter()
            .any(|v| ![2, 5, 32].contains(v));
        let shared_declarations = [
            d.channel_layout_present,
            d.downmix_instructions_present,
            d.loudness_eq_present,
            d.eq_present,
            d.scene_extension_present,
            d.loudness_extensions_present,
        ]
        .iter()
        .any(|f| f.is(true))
            || d.nested_declarations;
        let shared_parameters =
            if rejected.is_empty() && !unqualified_effect && !shared_declarations {
                None
            } else {
                Some(super::drc_shared::parameters(d, channels)?)
            };
        Ok(Self {
            shared_coefficient_index: shared_parameters
                .as_ref()
                .and_then(|_| super::drc_shared::coefficient_index(d)),
            shared_profile: shared_parameters
                .as_ref()
                .map(|_| super::drc_shared::PROFILE.into()),
            shared_format_sha256: shared_parameters
                .as_ref()
                .map(|_| super::drc_shared::format_sha256().into()),
            parameters: shared_parameters
                .as_ref()
                .and_then(|s| s.first())
                .map(|s| s.parameters.clone())
                .unwrap_or_else(|| {
                    if shared_parameters.is_some() {
                        super::drc_shared::empty_parameters(d)
                    } else {
                        DrcParameters {
                            coefficient_location: 1,
                            gain_sequences: 1,
                            gain_sets: 1,
                            bands: 1,
                            coding_profile: 0,
                            interpolation: "linear".into(),
                            full_frame: false,
                            time_alignment: false,
                            frame_samples: 1024,
                            time_delta_min: 64,
                        }
                    }
                }),
            shared_parameters,
            source: source.into(),
            source_sha256: d.source_sha256.clone(),
            loudness_metadata_source: source.into(),
            loudness_metadata_source_sha256: d.source_sha256.clone(),
            loudness_metadata: d.loudness_metadata.clone(),
            fields: d.fields.clone(),
        })
    }
}
impl DrcContext {
    pub fn from_config(config: &Config) -> Self {
        Self::for_channels(config, 2)
    }
    pub(super) fn for_channels(config: &Config, channels: u64) -> Self {
        let present = config.ancillary.loudness_drc_present.is(true);
        let configuration = if present {
            DrcConfiguration::from_declaration(&config.ancillary.drc, "cookie", channels).map(Some)
        } else {
            Ok(None)
        };
        match configuration {
            Ok(configuration) => Self {
                present,
                configuration,
                rejection: None,
            },
            Err(rejection) => Self {
                present,
                configuration: None,
                rejection: Some(rejection),
            },
        }
    }
}
/// One decoded DRC gain node.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcNode {
    pub gain_eighth_db: i32,
    pub time: i32,
    pub gain_bit_offset: usize,
    pub gain_bit_length: usize,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub slope_index: Option<u8>,
}
/// One DRC time-delta codeword.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcTimeDelta {
    pub value: u32,
    pub bit_offset: usize,
    pub bit_length: usize,
}
/// A DRC gain extension payload, recorded by digest.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcGainExtension {
    pub extension_type: u8,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub payload_bits: usize,
    pub payload_sha256: String,
}
/// One frame's DRC payload (parsed, never applied).
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcPayload {
    pub start_bit_offset: usize,
    pub header_end_bit_offset: usize,
    pub end_bit_offset: usize,
    pub header_present: bool,
    pub config_present: bool,
    pub configuration: DrcConfiguration,
    pub coding_mode: u8,
    pub frame_end: bool,
    /// Temporal codewords precede the gain codewords. Frame-end can be implicit.
    pub time_deltas: Vec<DrcTimeDelta>,
    pub encoded_times: Vec<i32>,
    pub nodes: Vec<DrcNode>,
    pub extension_present: bool,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub sequences: Option<Vec<super::DrcGainSequence>>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub configuration_changed: Option<bool>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub shared_syntax_profile: Option<String>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub gain_extensions: Option<Vec<DrcGainExtension>>,
}
/// The packet report up to DRC (`parse-packets --depth drc`); the BWE2 report
/// is flattened into it.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct DrcReport {
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub bwe2: Bwe2Report,
    pub drc_rules_version: String,
    pub drc_codebook_sha256: String,
    pub drc_complete: bool,
    pub drc_payload_present: bool,
    /// Whether a parsed preceding frame provides a gain node at or before this
    /// frame's start. This does not qualify future lookahead or audio processing.
    /// A stateless call starts without previous gain nodes.
    pub drc_history_sufficient: bool,
    pub drc_processing_applied: bool,
    pub drc: Option<DrcPayload>,
    pub drc_preroll: Option<Box<DrcReport>>,
}
/// DRC syntax state carried from packet to packet.
#[derive(Debug, Clone)]
pub struct DrcState {
    /// Channels of the stream.
    pub channels: u64,
    /// The DRC declaration in effect (cookie or in-band header).
    pub configuration: Option<DrcConfiguration>,
    /// Gain nodes of the previous frame.
    pub previous_nodes: Vec<DrcNode>,
    /// Gain nodes per sequence of the previous frame (shared syntax).
    pub previous_sequences: Vec<Vec<DrcNode>>,
    /// Whether any frame used the shared DRC syntax.
    pub shared_syntax_used: bool,
    /// Scene graph state, once an update was seen.
    pub scene_graph: Option<super::auxiliary::SceneGraphState>,
}
impl DrcState {
    /// The initial state of a stereo stream.
    pub fn new(context: &FrameContext) -> Self {
        Self {
            channels: 2,
            configuration: context.drc.configuration.clone(),
            previous_nodes: Vec::new(),
            previous_sequences: Vec::new(),
            shared_syntax_used: false,
            scene_graph: None,
        }
    }
    pub(crate) fn advance(&mut self, payload: &DrcPayload) {
        self.shared_syntax_used |= payload.shared_syntax_profile.is_some();
        self.previous_nodes = payload.nodes.clone();
        self.previous_sequences = payload
            .sequences
            .as_ref()
            .map_or_else(Vec::new, |v| v.iter().map(|s| s.nodes.clone()).collect());
    }
    pub(crate) fn history_sufficient(&self) -> bool {
        if self
            .configuration
            .as_ref()
            .and_then(|c| c.shared_parameters.as_ref())
            .is_some_and(Vec::is_empty)
        {
            return true;
        }
        if self.previous_sequences.is_empty() {
            self.previous_nodes.iter().any(|n| n.time < 1024)
        } else {
            self.previous_sequences
                .iter()
                .all(|s| s.iter().any(|n| n.time < 1024))
        }
    }
}
pub(super) fn gain_delta(bits: &mut BitReader<'_>) -> Result<i32, ParseError> {
    let start = bits.position();
    let mut code = 0u16;
    for width in 1..=11 {
        code = (code << 1) | bits.read(1)? as u16;
        if let Some(i) = GAIN_CODES
            .iter()
            .position(|&(v, n)| n == width && v == code)
        {
            return Ok(i as i32 - 16);
        }
    }
    Err(ParseError::new(
        start,
        "drc-gain-code",
        "invalid DRC gain delta codeword",
    ))
}
pub(super) fn time_delta(bits: &mut BitReader<'_>, ratio: u32) -> Result<u32, ParseError> {
    Ok(match bits.read(2)? {
        0 => 1,
        1 => 2 + bits.read(2)? as u32,
        2 => 6 + bits.read(3)? as u32,
        _ => {
            let at = bits.position();
            let width = if ratio == 0 {
                32
            } else {
                (32 - (2 * ratio - 1).leading_zeros()) as usize
            };
            let value = bits.read(width)? as u32;
            value.checked_add(14).ok_or_else(|| {
                ParseError::new(at, "drc-time-delta", "time delta exceeds its 32-bit range")
            })?
        }
    })
}
fn field(parser: &mut Parser<'_>, name: impl core::fmt::Display, start: usize, value: FieldValue) {
    if !parser.mode.record() {
        return;
    }
    parser.report.fields.push(ConfigField {
        name: format!("{ROOT}.{name}"),
        bit_offset: start,
        bit_length: parser.bits.position() - start,
        value,
    });
}
fn gain_extensions(
    parser: &mut Parser<'_>,
    present: bool,
) -> Result<Option<Vec<DrcGainExtension>>, ParseError> {
    if !present {
        return Ok(None);
    }
    let mut entries = Vec::new();
    loop {
        let start = parser.bits.position();
        let root = if entries.is_empty() {
            ROOT.to_string()
        } else {
            format!("{ROOT}.gain_extensions[{}]", entries.len())
        };
        let kind = parser.take(format_args!("{root}.gain_extension_type"), 4)? as u8;
        if kind == 0 {
            break;
        }
        if entries.len() == 4096 {
            return Err(ParseError::new(
                start,
                "input-limit",
                "too many gain extension records",
            ));
        }
        let width = parser.take(format_args!("{root}.length_width_minus_four"), 3)? as usize + 4;
        let length =
            parser.take(format_args!("{root}.payload_bits_minus_one"), width)? as usize + 1;
        let payload_start = parser.bits.position();
        if length > parser.bits.remaining() {
            return Err(ParseError::new(
                payload_start,
                "truncated",
                "gain extension exceeds input",
            ));
        }
        let mut data = vec![0u8; length.div_ceil(8)];
        for i in 0..length {
            data[i / 8] |= (parser.bits.read(1)? as u8) << (7 - i % 8);
        }
        let sha = crate::model::sha256(&data);
        if parser.mode.record() {
            parser.report.fields.push(ConfigField {
                name: format!("{root}.opaque_payload"),
                bit_offset: payload_start,
                bit_length: length,
                value: FieldValue::Digest {
                    unit: DigestUnit::Bits,
                    count: length,
                    sha256: sha.clone(),
                },
            });
        }
        entries.push(DrcGainExtension {
            extension_type: kind,
            start_bit_offset: start,
            end_bit_offset: parser.bits.position(),
            payload_bits: length,
            payload_sha256: sha,
        });
    }
    Ok((!entries.is_empty()).then_some(entries))
}
pub(super) fn read_payload(
    parser: &mut Parser<'_>,
    state: &mut DrcState,
    rate: u64,
) -> Result<DrcPayload, ParseError> {
    let start = parser.bits.position();
    let (header, declaration, header_end) = config::parse_drc_header_at(
        parser.bits.data(),
        start,
        rate,
        state.channels,
        parser.mode.record(),
    )?;
    parser.bits.skip(header_end - start)?;
    if parser.mode.record() {
        parser.report.fields.extend(header.fields);
    }
    if !header.complete {
        return Err(ParseError::new(
            header_end,
            "drc-header",
            header
                .reason
                .unwrap_or_else(|| "unsupported DRC header".into()),
        ));
    }
    let header_present = declaration.header_present.is(true);
    let config_present = declaration.config_present.is(true);
    let mut configuration_changed = false;
    if config_present {
        let next = DrcConfiguration::from_declaration(&declaration, "packet", state.channels)
            .map_err(|message| ParseError::new(start, "drc-configuration", message))?;
        if state.configuration.as_ref().is_some_and(|previous| {
            previous.parameters != next.parameters
                || previous.shared_parameters != next.shared_parameters
        }) {
            configuration_changed = true;
            state.previous_nodes.clear();
            state.previous_sequences.clear();
        }
        state.configuration = Some(next);
    } else if header_present {
        let previous = state.configuration.as_mut().ok_or_else(|| {
            ParseError::new(start, "drc-history", "header reuses missing configuration")
        })?;
        previous.loudness_metadata = declaration.loudness_metadata;
        previous.loudness_metadata_source = "packet".into();
        previous.loudness_metadata_source_sha256 = parser.report.packet_sha256.clone();
    }
    let configuration = state.configuration.clone().ok_or_else(|| {
        ParseError::new(
            start,
            "drc-history",
            "gain payload has no verified configuration",
        )
    })?;
    if let Some(parameters) = &configuration.shared_parameters {
        let sequences = parameters
            .iter()
            .map(|s| super::drc_shared::read_sequence(parser, s))
            .collect::<Result<Vec<_>, _>>()?;
        let first = sequences.first();
        let extension_present = configuration.shared_coefficient_index.is_some()
            && parser.flag(format_args!("{ROOT}.gain_extension_present"))?;
        let gain_extensions = gain_extensions(parser, extension_present)?;
        return Ok(DrcPayload {
            start_bit_offset: start,
            header_end_bit_offset: header_end,
            end_bit_offset: parser.bits.position(),
            header_present,
            config_present,
            coding_mode: first.map_or(0, |s| s.coding_mode),
            frame_end: first.is_none_or(|s| s.frame_end),
            time_deltas: first.map_or_else(Vec::new, |s| s.time_deltas.clone()),
            encoded_times: first.map_or_else(Vec::new, |s| s.encoded_times.clone()),
            nodes: first.map_or_else(Vec::new, |s| s.nodes.clone()),
            extension_present,
            sequences: Some(sequences),
            configuration_changed: configuration_changed.then_some(true),
            shared_syntax_profile: Some(super::drc_shared::PROFILE.into()),
            gain_extensions,
            configuration,
        });
    }
    let mode = parser.take(format_args!("{ROOT}.coding_mode"), 1)? as u8;
    let (mut count, mut frame_end) = (1usize, true);
    let mut deltas = Vec::new();
    let mut times = Vec::new();
    let (frames, dt) = (
        i32::from(configuration.parameters.frame_samples),
        i32::from(configuration.parameters.time_delta_min),
    );
    if mode == 1 {
        let position = parser.bits.position();
        while parser.bits.read(1)? == 0 {
            count += 1;
            if count > 256 {
                return Err(ParseError::new(
                    position,
                    "drc-node-count",
                    "DRC node count exceeds 256",
                ));
            }
        }
        field(parser, "node_count", position, FieldValue::from(count));
        frame_end = parser.flag(format_args!("{ROOT}.frame_end"))?;
        let mut previous = -1;
        for i in 0..count - usize::from(frame_end) {
            let position = parser.bits.position();
            let value = time_delta(&mut parser.bits, (frames / dt) as u32)?;
            field(
                parser,
                format_args!("time_deltas[{i}]"),
                position,
                FieldValue::from(value),
            );
            let next = i32::try_from(i64::from(previous) + i64::from(value) * i64::from(dt))
                .map_err(|_| {
                    ParseError::new(
                        position,
                        "drc-node-time",
                        "DRC node coordinate exceeds its signed range",
                    )
                })?;
            if next <= previous {
                return Err(ParseError::new(
                    position,
                    "drc-node-time",
                    "DRC node time must advance",
                ));
            }
            times.push(next);
            previous = next;
            deltas.push(DrcTimeDelta {
                value,
                bit_offset: position,
                bit_length: parser.bits.position() - position,
            });
        }
    }
    if frame_end {
        if times.contains(&(frames - 1)) {
            return Err(ParseError::new(
                parser.bits.position(),
                "drc-node-time",
                "explicit node duplicates implicit frame end",
            ));
        }
        times.push(frames - 1);
    }
    let encoded_times = times.clone();
    // APAC selects the native timing mode which preserves future coordinates.
    // The general UniDRC reservoir rotation is not this wire context. Implicit
    // frame end must still follow every explicitly encoded node.
    if times.len() != count || times.windows(2).any(|w| w[0] >= w[1]) {
        return Err(ParseError::new(
            parser.bits.position(),
            "drc-node-time",
            "nonadvancing DRC nodes",
        ));
    }
    let mut nodes = Vec::with_capacity(count);
    let mut gain = 0i32;
    for (i, time) in times.into_iter().enumerate() {
        let position = parser.bits.position();
        if i == 0 {
            let negative = parser.flag(format_args!("{ROOT}.initial_gain_negative"))?;
            let magnitude = parser.take(format_args!("{ROOT}.initial_gain_magnitude"), 8)? as i32;
            gain = if negative { -magnitude } else { magnitude };
        } else {
            let delta = gain_delta(&mut parser.bits)?;
            field(
                parser,
                format_args!("gain_deltas[{i}]"),
                position,
                FieldValue::from(delta),
            );
            gain = gain.checked_add(delta).ok_or_else(|| {
                ParseError::new(position, "drc-gain-range", "gain accumulator overflow")
            })?;
        }
        nodes.push(DrcNode {
            gain_eighth_db: gain,
            time,
            gain_bit_offset: position,
            gain_bit_length: parser.bits.position() - position,
            slope_index: None,
        });
    }
    let extension_present = parser.flag(format_args!("{ROOT}.gain_extension_present"))?;
    let gain_extensions = gain_extensions(parser, extension_present)?;
    let shared_syntax_profile = (configuration_changed
        || gain_extensions.is_some()
        || encoded_times.iter().any(|&t| t >= 2 * frames))
    .then(|| super::drc_shared::PROFILE.into());
    Ok(DrcPayload {
        start_bit_offset: start,
        header_end_bit_offset: header_end,
        end_bit_offset: parser.bits.position(),
        header_present,
        config_present,
        configuration,
        coding_mode: mode,
        frame_end,
        time_deltas: deltas,
        encoded_times,
        nodes,
        extension_present,
        sequences: None,
        configuration_changed: configuration_changed.then_some(true),
        shared_syntax_profile,
        gain_extensions,
    })
}

/// Inspect DRC up to the trimming entry. This API never enables playback DRC.
pub fn parse_drc(context: &FrameContext, packet: &[u8]) -> Result<DrcReport, ParseError> {
    parse_drc_with_state(context, packet, &mut DrcState::new(context))
}
/// [`parse_drc`] continuing and updating `state` (left unchanged on error).
pub fn parse_drc_with_state(
    context: &FrameContext,
    packet: &[u8],
    state: &mut DrcState,
) -> Result<DrcReport, ParseError> {
    let mut out = DrcReport {
        bwe2: parse_bwe2(context, packet)?,
        drc_rules_version: RULES_VERSION.into(),
        drc_codebook_sha256: codebook_sha256(),
        drc_complete: false,
        drc_payload_present: context.drc.present,
        drc_history_sufficient: state.history_sufficient(),
        drc_processing_applied: false,
        drc: None,
        drc_preroll: None,
    };
    let frame = &mut out.bwe2.tns.cac.spectrum.frame;
    if let Some(reason) = context
        .packet_configuration
        .syntax_rejection
        .as_ref()
        .or(context.drc.rejection.as_ref())
    {
        frame.stop_reason = format!("unsupported DRC configuration: {reason}");
        frame.status = ParseStatus::Unsupported;
        return Ok(out);
    }
    let absent = frame.stop_reason == "cpe_absent";
    if !out.bwe2.bwe2_complete && !absent {
        return Ok(out);
    }
    let position = frame.stop_bit_offset;
    frame
        .unknown_ranges
        .retain(|r| r.bit_offset != position || r.bit_length != packet.len() * 8 - position);
    frame.diagnostics.clear();
    let mut next = state.clone();
    if let Some((start, end)) = frame.preroll {
        let inner = parse_drc_with_state(context, &packet[start / 8..end / 8], &mut next).map_err(
            |mut e| {
                e.bit_offset += start;
                e
            },
        )?;
        if !inner.drc_complete {
            return Ok(out);
        }
        out.drc_preroll = Some(Box::new(inner));
    }
    let mut parser = Parser {
        mode: ParseMode::Report,
        bits: BitReader::new(packet),
        report: frame.clone(),
    };
    parser.bits.skip(position)?;
    let padding = (8 - position % 8) % 8;
    if padding != 0 && parser.take("core.alignment_padding", padding)? != 0 {
        return Err(ParseError::new(
            position,
            "core-alignment",
            "nonzero core alignment",
        ));
    }
    parser.report.component_end_bit_offset = Some(parser.bits.position());
    if context.packet_configuration.scene_present
        && parser.flag("ancillary.audio_scenes_update_present")?
    {
        let (scene, scenes, end) = config::parse_scene_at(packet, parser.bits.position(), true)?;
        parser.bits.skip(end - parser.bits.position())?;
        parser.report.fields.extend(scene.fields);
        let rejected = packet_config::neutral_scene(&scenes, "packet");
        if !scene.complete || !rejected.is_empty() {
            return Err(ParseError::new(
                end,
                "drc-scene",
                format!("unsupported scene before DRC: {}", rejected.join("; ")),
            ));
        }
    }
    if context.drc.present {
        out.drc = Some(read_payload(
            &mut parser,
            &mut next,
            context.sample_rate_hz.unwrap_or(0),
        )?);
    }
    out.drc_history_sufficient = next.history_sufficient();
    if let Some(payload) = &out.drc {
        next.advance(payload);
    }
    out.drc_complete = true;
    let prefix = parser.report.prefix_complete;
    out.bwe2.tns.cac.spectrum.frame =
        parser.finish("drc_payload_before_trimming", prefix, false)?;
    *state = next;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn wire(value: u16, width: usize) -> Vec<u8> {
        let mut out = vec![0; width.div_ceil(8)];
        for i in 0..width {
            if value & (1 << (width - 1 - i)) != 0 {
                out[i / 8] |= 1 << (7 - i % 8);
            }
        }
        out
    }
    fn report() -> super::super::FrameReport {
        super::super::FrameReport {
            schema_version: 1,
            cookie_sha256: String::new(),
            packet_sha256: String::new(),
            packet_bytes: 0,
            status: ParseStatus::Partial,
            prefix_complete: true,
            fields: Vec::new(),
            derived: Default::default(),
            stop_reason: String::new(),
            stop_bit_offset: 0,
            cpe_absent: false,
            preroll: None,
            left_ics_bit_offset: None,
            payload_bit_offset: None,
            component_end_bit_offset: None,
            unknown_ranges: Vec::new(),
            diagnostics: Vec::new(),
        }
    }
    fn state() -> DrcState {
        DrcState {
            channels: 2,
            configuration: Some(DrcConfiguration {
                shared_parameters: None,
                shared_coefficient_index: None,
                shared_profile: None,
                shared_format_sha256: None,
                parameters: DrcParameters {
                    coefficient_location: 1,
                    gain_sequences: 1,
                    gain_sets: 1,
                    bands: 1,
                    coding_profile: 0,
                    interpolation: "linear".into(),
                    full_frame: false,
                    time_alignment: false,
                    frame_samples: 1024,
                    time_delta_min: 64,
                },
                source: "cookie".into(),
                source_sha256: String::new(),
                fields: Vec::new(),
                loudness_metadata: Vec::new(),
                loudness_metadata_source: "cookie".into(),
                loudness_metadata_source_sha256: String::new(),
            }),
            previous_nodes: Vec::new(),
            previous_sequences: Vec::new(),
            shared_syntax_used: false,
            scene_graph: None,
        }
    }
    fn raw(text: &str) -> Vec<u8> {
        let mut out = vec![0; text.len().div_ceil(8)];
        for (i, v) in text.bytes().enumerate() {
            out[i / 8] |= (v - b'0') << (7 - i % 8);
        }
        out
    }
    #[test]
    fn initial_gain_and_payload_truncate_at_every_bit_without_zero_fill() {
        // No header, constant gain, negative magnitude 255, no extension.
        let data = raw("00111111111010100101");
        let mut p = Parser {
            mode: ParseMode::Report,
            bits: BitReader::new(&data),
            report: report(),
        };
        let decoded = read_payload(&mut p, &mut state(), 48000).unwrap();
        assert_eq!(decoded.nodes[0].gain_eighth_db, -255);
        assert_eq!(decoded.nodes[0].time, 1023);
        assert_eq!(decoded.end_bit_offset, 12);
        assert_eq!(p.bits.read(8).unwrap(), 0xa5);
        for end in 0..12 {
            let mut p = Parser {
                mode: ParseMode::Report,
                bits: BitReader::new(&data),
                report: report(),
            };
            p.bits.set_end(end).unwrap();
            assert_eq!(
                read_payload(&mut p, &mut state(), 48000).unwrap_err().kind,
                "truncated"
            );
        }
    }
    #[test]
    fn oversized_node_count_and_nonterminating_extension_are_errors() {
        let count = raw(&format!("01{}1", "0".repeat(256)));
        let mut p = Parser {
            mode: ParseMode::Report,
            bits: BitReader::new(&count),
            report: report(),
        };
        assert_eq!(
            read_payload(&mut p, &mut state(), 48000).unwrap_err().kind,
            "drc-node-count"
        );
        let extension = raw("0000000000010001");
        let mut p = Parser {
            mode: ParseMode::Report,
            bits: BitReader::new(&extension),
            report: report(),
        };
        assert_eq!(
            read_payload(&mut p, &mut state(), 48000).unwrap_err().kind,
            "truncated"
        );
    }
    #[test]
    fn opaque_gain_extension_is_bounded_and_preserves_the_following_marker() {
        let wire = "000000000001000100000101010000";
        let data = raw(&format!("{wire}101101"));
        let mut parser = Parser {
            mode: ParseMode::Report,
            bits: BitReader::new(&data),
            report: report(),
        };
        let payload = read_payload(&mut parser, &mut state(), 48000).unwrap();
        assert_eq!(payload.end_bit_offset, wire.len());
        assert_eq!(parser.bits.read(6).unwrap(), 0b101101);
        assert_eq!(payload.gain_extensions.as_ref().unwrap()[0].payload_bits, 3);
        assert!(payload.shared_syntax_profile.is_some());
        for end in 0..wire.len() {
            let mut bits = BitReader::new(&data);
            bits.set_end(end).unwrap();
            let mut parser = Parser {
                mode: ParseMode::Report,
                bits,
                report: report(),
            };
            assert!(
                read_payload(&mut parser, &mut state(), 48000).is_err(),
                "{end}"
            );
        }
    }
    #[test]
    fn every_gain_word_marker_and_bit_truncation() {
        for (i, &(code, width)) in GAIN_CODES.iter().enumerate() {
            let mut data = wire(code, width);
            data.extend([0xa5, 0x5a]);
            let mut reader = BitReader::new(&data);
            assert_eq!(gain_delta(&mut reader).unwrap(), i as i32 - 16);
            assert_eq!(reader.position(), width);
            assert_eq!(
                reader.read(8 - width % 8).unwrap(),
                if width % 8 == 0 { 0xa5 } else { 0 }
            );
            for end in 0..width {
                let mut reader = BitReader::new(&data);
                reader.set_end(end).unwrap();
                let error = gain_delta(&mut reader).unwrap_err();
                assert_eq!(error.kind, "truncated");
                assert_eq!(error.bit_offset, end);
            }
        }
    }
    #[test]
    fn every_time_word_marker_and_bit_truncation() {
        for v in 1..=45 {
            let (code, width) = match v {
                1 => (0, 2),
                2..=5 => (4 + v - 2, 4),
                6..=13 => (16 + v - 6, 5),
                _ => (96 + v - 14, 7),
            };
            let mut data = wire(code, width);
            data.push(0xa5);
            let mut reader = BitReader::new(&data);
            assert_eq!(time_delta(&mut reader, 16).unwrap(), u32::from(v));
            assert_eq!(reader.position(), width);
            reader.skip((8 - width % 8) % 8).unwrap();
            assert_eq!(reader.read(8).unwrap(), 0xa5);
            for end in 0..width {
                let mut reader = BitReader::new(&data);
                reader.set_end(end).unwrap();
                let error = time_delta(&mut reader, 16).unwrap_err();
                assert_eq!(error.kind, "truncated");
                assert!(error.bit_offset <= end);
            }
        }
    }
}
