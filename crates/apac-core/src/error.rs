//! Decoder errors: the stage that failed, its message and, when known, the
//! bit position in the packet or cookie.
use crate::config::ParseError;
use crate::prelude::*;
use core::fmt;

/// Result of decoder operations.
pub type Result<T> = core::result::Result<T, DecodeError>;

/// A decoder failure; decoder state is unchanged when one is returned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeError {
    /// The failing stage, e.g. "SQ synthesis" or "HOA packet".
    pub operation: &'static str,
    /// The error text, stable across releases.
    pub message: String,
    /// Bit offset in the packet or cookie, when the syntax located the failure.
    pub bit_offset: Option<usize>,
}

impl DecodeError {
    /// An error without position.
    pub fn new(operation: &'static str, message: impl Into<String>) -> Self {
        Self {
            operation,
            message: message.into(),
            bit_offset: None,
        }
    }
}
impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.operation, self.message)
    }
}
impl core::error::Error for DecodeError {}
impl From<ParseError> for DecodeError {
    fn from(error: ParseError) -> Self {
        let mut result = Self::new("parse-cookie", error.to_string());
        result.bit_offset = Some(error.bit_offset);
        result
    }
}
