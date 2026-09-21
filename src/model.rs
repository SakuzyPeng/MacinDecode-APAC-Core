use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::PathBuf};

pub const SCHEMA_VERSION: u32 = 1;
pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn fourcc(value: u32) -> String {
    String::from_utf8_lossy(&value.to_be_bytes()).into_owned()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Property<T> {
    pub value: Option<T>,
    pub error: Option<ErrorRecord>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ErrorRecord {
    pub operation: String,
    pub os_status: Option<i32>,
    pub message: String,
}
impl<T> Property<T> {
    pub fn known(value: T) -> Self {
        Self {
            value: Some(value),
            error: None,
        }
    }
    pub fn from_result(result: Result<T>) -> Self {
        match result {
            Ok(v) => Self::known(v),
            Err(e) => Self {
                value: None,
                error: Some(ErrorRecord {
                    operation: e.operation,
                    os_status: e.os_status,
                    message: e.message,
                }),
            },
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Environment {
    pub tool_version: String,
    pub os: String,
    pub architecture: String,
    pub system_version: String,
}
impl Environment {
    pub fn current() -> Self {
        let version = if cfg!(target_os = "macos") {
            std::process::Command::new("/usr/bin/sw_vers")
                .output()
                .ok()
                .filter(|o| o.status.success())
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        } else {
            None
        };
        Self {
            tool_version: env!("CARGO_PKG_VERSION").into(),
            os: std::env::consts::OS.into(),
            architecture: std::env::consts::ARCH.into(),
            system_version: version.unwrap_or_else(|| "unavailable".into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AudioFormat {
    pub sample_rate: f64,
    pub format_id: u32,
    pub format_fourcc: String,
    pub flags: u32,
    pub bytes_per_packet: u32,
    pub frames_per_packet: u32,
    pub bytes_per_frame: u32,
    pub channels: u32,
    pub bits_per_channel: u32,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ChannelDescription {
    pub label: u32,
    pub flags: u32,
    pub coordinates: [f32; 3],
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ChannelLayout {
    pub tag: u32,
    pub bitmap: u32,
    pub descriptions: Vec<ChannelDescription>,
    pub name: Option<String>,
    pub ambisonic_order: Option<u32>,
    pub ambisonic_channel_order: Option<String>,
    pub ambisonic_normalization: Option<String>,
}
impl ChannelLayout {
    /// Names are presentation strings; numerical layout semantics determine equality.
    pub fn equivalent(&self, other: &Self) -> bool {
        self.tag == other.tag
            && self.bitmap == other.bitmap
            && self.descriptions == other.descriptions
            && self.ambisonic_order == other.ambisonic_order
            && self.ambisonic_channel_order == other.ambisonic_channel_order
            && self.ambisonic_normalization == other.ambisonic_normalization
    }
    pub fn tagged(tag: u32, channels: u32, name: Option<String>) -> Self {
        let hoa = tag >> 16 == 190 || tag >> 16 == 191;
        let root = (channels as f64).sqrt() as u32;
        Self {
            tag,
            bitmap: 0,
            descriptions: vec![],
            name,
            ambisonic_order: (hoa && root > 0 && root * root == channels).then(|| root - 1),
            ambisonic_channel_order: hoa.then(|| "ACN".into()),
            ambisonic_normalization: hoa
                .then(|| if tag >> 16 == 190 { "SN3D" } else { "N3D" }.into()),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PacketTable {
    pub valid_frames: i64,
    pub priming_frames: i32,
    pub remainder_frames: i32,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CookieInfo {
    pub bytes: usize,
    pub sha256: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileInfo {
    pub schema_version: u32,
    pub source: PathBuf,
    pub file_bytes: u64,
    pub modified_unix_seconds: Option<u64>,
    pub environment: Environment,
    pub container: Property<String>,
    pub format: AudioFormat,
    pub layout: Property<ChannelLayout>,
    pub packet_count: Property<u64>,
    pub max_packet_bytes: Property<u32>,
    pub packet_table: Property<PacketTable>,
    pub cookie: Property<CookieInfo>,
    pub restricts_random_access: Property<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PcmInfo {
    pub schema_version: u32,
    pub complete: bool,
    pub pcm_file: String,
    pub encoding: String,
    pub interleaved: bool,
    pub sample_rate: f64,
    pub channels: u32,
    pub layout: Property<ChannelLayout>,
    pub start_frame: u64,
    pub requested_frames: u64,
    pub frames: u64,
    pub bytes: u64,
    pub sha256: String,
    pub source: Option<PathBuf>,
    pub source_cookie_sha256: Option<String>,
    pub source_packet_table: Option<PacketTable>,
    pub environment: Environment,
    pub decoder_settings: BTreeMap<String, Property<serde_json::Value>>,
    pub all_finite: bool,
}
impl PcmInfo {
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != SCHEMA_VERSION
            || !self.complete
            || self.encoding != "f32le"
            || !self.interleaved
        {
            return Err(Error::new(
                "PCM metadata",
                "requires a complete schema v1 interleaved f32le bundle",
            ));
        }
        if !self.sample_rate.is_finite()
            || self.sample_rate <= 0.0
            || self.channels == 0
            || self.channels > 1024
        {
            return Err(Error::new(
                "PCM metadata",
                "invalid sample rate or channel count",
            ));
        }
        let bytes = self
            .frames
            .checked_mul(u64::from(self.channels))
            .and_then(|v| v.checked_mul(4));
        if bytes != Some(self.bytes) {
            return Err(Error::new(
                "PCM metadata",
                "frame count and byte count disagree",
            ));
        }
        if self.sha256.len() != 64 || !self.sha256.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(Error::new("PCM metadata", "invalid SHA-256"));
        }
        if !self.all_finite {
            return Err(Error::new(
                "PCM metadata",
                "bundle reports non-finite samples",
            ));
        }
        Ok(())
    }
}
