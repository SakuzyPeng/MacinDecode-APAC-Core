//! Cookie parsing re-exported from `apac-core`, the `parse-cookie` report and
//! file input.
use crate::error::Result;
use crate::model::SCHEMA_VERSION;
pub use apac_core::config::*;
use serde::Serialize;
use std::{collections::BTreeMap, fs::File, io::Read, path::Path};

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
            cookie_bytes: config.cookie_bytes(),
            cookie_sha256: config.cookie_sha256().to_owned(),
            status: config.status().clone(),
            fields: recording.fields,
            derived: recording.derived,
            unknown_ranges: recording.unknown_ranges,
            diagnostics: recording.diagnostics,
        }
    }
}

/// Parse a cookie with recording and assemble its report.
pub fn parse_cookie(data: &[u8]) -> std::result::Result<CookieReport, ParseError> {
    let (config, recording) = parse_recorded(data)?;
    Ok(CookieReport::assemble(&config, recording))
}

pub fn parse_file(path: &Path) -> Result<CookieReport> {
    let mut bytes = vec![];
    File::open(path)?
        .take((MAX_COOKIE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    Ok(parse_cookie(&bytes)?)
}
