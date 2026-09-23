//! Independent APAC cookie syntax inspection. No Apple APIs are used here.
mod bits;
mod drc;
mod hoa;
mod parser;
mod scenes;

use crate::model::{SCHEMA_VERSION, sha256};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, fmt, fs::File, io::Read, path::Path};

pub const MAX_COOKIE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ParseStatus {
    Complete,
    Partial,
    Unsupported,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigField {
    pub name: String,
    pub bit_offset: usize,
    pub bit_length: usize,
    pub value: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnknownRange {
    pub bit_offset: usize,
    pub bit_length: usize,
    /// raw_hex starts at floor(bit_offset / 8); skip these high bits in its first byte.
    pub first_byte_skip_bits: usize,
    pub raw_hex: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Diagnostic {
    pub bit_offset: usize,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CookieReport {
    pub schema_version: u32,
    pub cookie_bytes: usize,
    pub cookie_sha256: String,
    pub status: ParseStatus,
    /// Syntax coverage, not a claim of support for decoding audio or scene rendering.
    pub fields: Vec<ConfigField>,
    pub derived: BTreeMap<String, Value>,
    pub unknown_ranges: Vec<UnknownRange>,
    pub diagnostics: Vec<Diagnostic>,
}
impl CookieReport {
    pub fn is_complete(&self) -> bool {
        self.status == ParseStatus::Complete
    }
    fn new(data: &[u8]) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            cookie_bytes: data.len(),
            cookie_sha256: sha256(data),
            status: ParseStatus::Complete,
            fields: vec![],
            derived: BTreeMap::new(),
            unknown_ranges: vec![],
            diagnostics: vec![],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParseError {
    pub bit_offset: usize,
    pub kind: String,
    pub message: String,
}
impl ParseError {
    pub fn new(bit_offset: usize, kind: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            bit_offset,
            kind: kind.into(),
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
impl From<ParseError> for crate::error::Error {
    fn from(error: ParseError) -> Self {
        let mut result = Self::new("parse-cookie", error.to_string());
        result.bit_offset = Some(error.bit_offset);
        result
    }
}

pub fn parse_cookie(data: &[u8]) -> Result<CookieReport, ParseError> {
    if data.len() > MAX_COOKIE_BYTES {
        return Err(ParseError::new(0, "input-limit", "cookie exceeds 8 MiB"));
    }
    parser::parse(data)
}

pub fn parse_file(path: &Path) -> crate::error::Result<CookieReport> {
    let mut bytes = vec![];
    File::open(path)?
        .take((MAX_COOKIE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    Ok(parse_cookie(&bytes)?)
}
