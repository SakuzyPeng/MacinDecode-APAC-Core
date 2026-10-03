//! Independent APAC cookie syntax inspection. No Apple APIs are used here.
use crate::prelude::*;
pub(crate) mod bits;
mod drc;
mod drc_metadata;
mod hoa;
#[cfg(test)]
mod legacy_tests;
mod model;
#[cfg(test)]
pub(crate) mod model_tests;
mod parser;
pub(crate) mod passive;
mod passive_compression;
mod passive_metadata;
mod passive_renderer;
mod scenes;

use crate::model::SCHEMA_VERSION;
use serde::Serialize;
use std::{collections::BTreeMap, fmt};

pub const MAX_COOKIE_BYTES: usize = 8 * 1024 * 1024;

pub use crate::record::{ConfigField, DigestUnit, FieldValue};
pub(crate) use model::*;
pub use model::{Config, Field, Located};
#[cfg(test)]
use parser::InBand;
pub(crate) use parser::{parse_drc_header_at, parse_scene_at};
pub use passive::PositionSyntax;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ParseStatus {
    Complete,
    Partial,
    Unsupported,
}

#[derive(Debug, Clone, Serialize)]
pub struct UnknownRange {
    pub bit_offset: usize,
    pub bit_length: usize,
    /// raw_hex starts at floor(bit_offset / 8); skip these high bits in its first byte.
    pub first_byte_skip_bits: usize,
    pub raw_hex: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Diagnostic {
    pub bit_offset: usize,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CookieReport {
    pub schema_version: u32,
    pub cookie_bytes: usize,
    pub cookie_sha256: String,
    pub status: ParseStatus,
    /// Syntax coverage, not a claim of support for decoding audio or scene rendering.
    pub fields: Vec<ConfigField>,
    pub derived: BTreeMap<String, FieldValue>,
    pub unknown_ranges: Vec<UnknownRange>,
    pub diagnostics: Vec<Diagnostic>,
}
impl CookieReport {
    pub fn is_complete(&self) -> bool {
        self.status == ParseStatus::Complete
    }
    /// The report of one recorded parse.
    pub fn assemble(config: &Config, recording: Recording) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            cookie_bytes: config.cookie_bytes,
            cookie_sha256: config.cookie_sha256.clone(),
            status: config.status.clone(),
            fields: recording.fields,
            derived: recording.derived,
            unknown_ranges: recording.unknown_ranges,
            diagnostics: recording.diagnostics,
        }
    }
}

/// The recorded syntax of one parse: field events in stream order, derived
/// values, unparsed ranges and diagnostics. Decoding never reads it.
#[derive(Debug, Clone, Default)]
pub struct Recording {
    pub fields: Vec<ConfigField>,
    pub derived: BTreeMap<String, FieldValue>,
    pub unknown_ranges: Vec<UnknownRange>,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ParseError {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub element_index: Option<usize>,
    pub bit_offset: usize,
    /// A fixed error class such as "truncated" or "max-sfb".
    pub kind: &'static str,
    pub message: Cow<'static, str>,
}
impl ParseError {
    pub fn new(
        bit_offset: usize,
        kind: &'static str,
        message: impl Into<Cow<'static, str>>,
    ) -> Self {
        Self {
            element_index: None,
            bit_offset,
            kind,
            message: message.into(),
        }
    }
}
impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} at bit {}: {}",
            self.kind, self.bit_offset, self.message
        )
    }
}
impl std::error::Error for ParseError {}

pub fn parse_cookie(data: &[u8]) -> Result<CookieReport, ParseError> {
    Ok(parse_cookie_and_config(data)?.0)
}

/// The recorded report and the typed configuration from a single parse.
pub fn parse_cookie_and_config(data: &[u8]) -> Result<(CookieReport, Config), ParseError> {
    let (config, recording) = parse_recorded(data)?;
    Ok((CookieReport::assemble(&config, recording), config))
}

/// The typed configuration and the recorded syntax from a single parse.
pub fn parse_recorded(data: &[u8]) -> Result<(Config, Recording), ParseError> {
    let (config, recording) = parse_with(data, true)?;
    Ok((config, recording.expect("recording requested")))
}

fn parse_with(data: &[u8], record: bool) -> Result<(Config, Option<Recording>), ParseError> {
    if data.len() > MAX_COOKIE_BYTES {
        return Err(ParseError::new(0, "input-limit", "cookie exceeds 8 MiB"));
    }
    parser::parse(data, record)
}
