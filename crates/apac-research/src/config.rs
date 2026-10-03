//! Cookie parsing re-exported from `apac-core`, plus file input.
use crate::error::Result;
pub use apac_core::config::*;
use std::{fs::File, io::Read, path::Path};

pub fn parse_file(path: &Path) -> Result<CookieReport> {
    let mut bytes = vec![];
    File::open(path)?
        .take((MAX_COOKIE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    Ok(parse_cookie(&bytes)?)
}
