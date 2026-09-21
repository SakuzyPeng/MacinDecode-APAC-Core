use serde::{Deserialize, Serialize};
use std::fmt;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Error {
    pub operation: String,
    pub message: String,
    pub os_status: Option<i32>,
    pub os_status_fourcc: Option<String>,
}

impl Error {
    pub fn new(operation: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            operation: operation.into(),
            message: message.into(),
            os_status: None,
            os_status_fourcc: None,
        }
    }
    pub fn native(operation: impl Into<String>, status: i32) -> Self {
        let bytes = status.to_be_bytes();
        let fourcc = bytes
            .iter()
            .all(|b| (32..=126).contains(b))
            .then(|| String::from_utf8_lossy(&bytes).into_owned());
        Self {
            operation: operation.into(),
            message: format!("AudioToolbox returned OSStatus {status}"),
            os_status: Some(status),
            os_status_fourcc: fourcc,
        }
    }
    pub fn io(operation: impl Into<String>, error: impl fmt::Display) -> Self {
        Self::new(operation, error.to_string())
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.operation, self.message)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::io("filesystem", e)
    }
}
impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Self::io("JSON", e)
    }
}
