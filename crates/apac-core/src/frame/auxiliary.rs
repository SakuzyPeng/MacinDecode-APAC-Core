//! Bounded passive payloads; no renderer, transcode output, or audio processing.
use super::Parser;
use crate::config::{CookieReport, ParseError};
use crate::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrimmingDeclaration {
    pub profile: String,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub leading_frames: u16,
    pub trailing_frames: u16,
    pub valid_start_frame: u16,
    pub valid_end_frame: u16,
    /// Raw codec blocks retain 1024 samples, as in the native packet API.
    /// Container priming/remainder is applied once by the existing file timeline.
    pub processing_applied: bool,
}
pub(crate) fn read_trimming(
    parser: &mut Parser<'_>,
) -> Result<Option<TrimmingDeclaration>, ParseError> {
    let start = parser.bits.position();
    if !parser.flag("ancillary.trimming_present")? {
        return Ok(None);
    }
    let leading = parser.escaped("ancillary.trimming.leading_frames", &[11, 16, 20])?;
    let trailing = parser.escaped("ancillary.trimming.trailing_frames", &[11, 16, 20])?;
    if leading + trailing > 1024 {
        return Err(ParseError::new(
            start,
            "trimming-range",
            "trimming declarations exceed the 1024-sample codec block",
        ));
    }
    Ok(Some(TrimmingDeclaration {
        profile: "apac-source-trimming-declaration-v1".into(),
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        leading_frames: leading as u16,
        trailing_frames: trailing as u16,
        valid_start_frame: leading as u16,
        valid_end_frame: 1024 - trailing as u16,
        processing_applied: false,
    }))
}

#[derive(Debug, Clone, Default)]
pub(crate) struct AuxiliaryConfiguration {
    pub present: bool,
    variable_parameter: bool,
    graph: Option<SceneGraphState>,
}
impl AuxiliaryConfiguration {
    pub fn from_report(report: &CookieReport) -> Self {
        let flag = |name| {
            report
                .fields
                .iter()
                .any(|f| f.name == name && f.value == json!(true))
        };
        Self {
            present: flag("ancillary.custom_data_present"),
            variable_parameter: flag("ancillary.custom_data.flag_a"),
            graph: report
                .derived
                .get("ancillary.scene_graph.syntax")
                .map(|value| SceneGraphState {
                    positions: serde_json::from_value(value.clone())
                        .expect("verified position syntax"),
                    history_sha256: crate::model::sha256(
                        &serde_json::to_vec(
                            &report
                                .fields
                                .iter()
                                .filter(|f| f.name.starts_with("ancillary.scene_graph."))
                                .collect::<Vec<_>>(),
                        )
                        .expect("finite graph declaration"),
                    ),
                }),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SceneGraphState {
    positions: Vec<crate::config::passive::PositionSyntax>,
    history_sha256: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SceneGraphPayload {
    pub profile: String,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub update_present: bool,
    pub position_count: usize,
    pub metadata_sha256: String,
    pub processing_applied: bool,
}
pub(crate) fn read_graph(
    parser: &mut Parser<'_>,
    config: &AuxiliaryConfiguration,
    state: &mut super::DrcState,
) -> Result<Option<SceneGraphPayload>, ParseError> {
    let Some(initial) = &config.graph else {
        return Ok(None);
    };
    let graph = state.scene_graph.get_or_insert_with(|| initial.clone());
    let start = parser.bits.position();
    let present = parser.flag("ancillary.scene_graph_update_present")?;
    if present {
        let (report, end) = crate::config::passive::graph_update(
            parser.bits.data(),
            parser.bits.position(),
            &mut graph.positions,
        )?;
        if !report.is_complete() {
            return Err(ParseError::new(
                parser.bits.position(),
                "scene-graph-update",
                "incomplete passive scene graph syntax",
            ));
        }
        parser.bits.skip(end - parser.bits.position())?;
        if parser.capture {
            parser.report.fields.extend(report.fields);
        }
        graph.history_sha256 = crate::model::sha256(
            &serde_json::to_vec(&(
                graph.history_sha256.as_str(),
                crate::model::sha256(parser.bits.data()),
                start,
                end,
            ))
            .expect("finite graph provenance"),
        );
    }
    Ok(Some(SceneGraphPayload {
        profile: "apac-passive-scene-graph-v1".into(),
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        update_present: present,
        position_count: graph.positions.len(),
        metadata_sha256: graph.history_sha256.clone(),
        processing_applied: false,
    }))
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuxiliaryPayload {
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub present: bool,
    pub declared_bytes: Option<usize>,
    pub parameter: Option<u64>,
    pub payload_bytes: usize,
    pub payload_sha256: Option<String>,
}
pub(crate) fn read(
    parser: &mut Parser<'_>,
    config: &AuxiliaryConfiguration,
) -> Result<Option<AuxiliaryPayload>, ParseError> {
    if !config.present {
        return Ok(None);
    }
    let start = parser.bits.position();
    let present = parser.flag("ancillary.custom_data.present")?;
    let mut result = AuxiliaryPayload {
        start_bit_offset: start,
        end_bit_offset: 0,
        present,
        declared_bytes: None,
        parameter: None,
        payload_bytes: 0,
        payload_sha256: None,
    };
    if present {
        let length_at = parser.bits.position();
        let bytes =
            parser.escaped("ancillary.custom_data.bytes_minus_one", &[4, 8, 16])? as usize + 1;
        let header = parser.bits.position();
        let end = header.checked_add(bytes * 8).ok_or_else(|| {
            ParseError::new(length_at, "overflow", "custom payload length overflow")
        })?;
        let previous = parser.bits.set_end(end)?;
        result.declared_bytes = Some(bytes);
        if config.variable_parameter {
            result.parameter =
                Some(parser.escaped("ancillary.custom_data.parameter", &[10, 13, 16])?);
            let padding = (8 - (parser.bits.position() - header) % 8) % 8;
            parser.take("ancillary.custom_data.header_padding", padding)?;
        }
        let payload = (end - parser.bits.position()) / 8;
        if payload > 8192 {
            return Err(ParseError::new(
                length_at,
                "custom-data-length",
                "custom frame payload exceeds 8192 bytes",
            ));
        }
        let start = parser.bits.position();
        let mut data = Vec::with_capacity(payload);
        for _ in 0..payload {
            data.push(parser.bits.read(8)? as u8);
        }
        let digest = crate::model::sha256(&data);
        if parser.capture {
            parser.report.fields.push(crate::config::ConfigField {
                name: "ancillary.custom_data.payload".into(),
                bit_offset: start,
                bit_length: payload * 8,
                value: json!({"bytes":payload,"sha256":digest}),
            });
        }
        result.payload_bytes = payload;
        result.payload_sha256 = Some(digest);
        parser.bits.set_end(previous)?;
    }
    result.end_bit_offset = parser.bits.position();
    Ok(Some(result))
}
