//! Layout types and identities shared by the decoder and its reports.
use crate::prelude::*;
use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: u32 = 1;

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
pub struct ChannelDescription {
    pub label: u32,
    pub flags: u32,
    pub coordinates: [f32; 3],
}
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
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
