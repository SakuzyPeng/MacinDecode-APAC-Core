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
        let root = channels.isqrt();
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
    /// The qualified discrete layout for a channel count (Mono, Stereo, 5.1,
    /// 7.1, 7.1.4 and 22.2), tagged with its layout family; `None` for any
    /// other count, which only a qualified HOA configuration can carry.
    pub fn discrete(channels: u32) -> Option<Self> {
        let layout = crate::channel_layout::layout(u64::from(channels))?;
        Some(Self::tagged(
            ((layout.family as u32) << 16) | channels,
            channels,
            Some(layout.name.into()),
        ))
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn integer_square_root_matches_the_former_float_root() {
        let former = |n: u32| (f64::from(n)).sqrt() as u32;
        let squares = (1..=u32::from(u16::MAX)).flat_map(|k| [k * k - 1, k * k, k * k + 1]);
        for n in (0..=1 << 20).chain(squares).chain([u32::MAX]) {
            assert_eq!(n.isqrt(), former(n), "{n}");
        }
    }
    #[test]
    fn discrete_layouts_match_the_tagged_family_layouts() {
        use super::ChannelLayout;
        for channels in 0..=256u32 {
            let former = crate::channel_layout::layout(u64::from(channels)).map(|layout| {
                ChannelLayout::tagged(
                    ((layout.family as u32) << 16) | channels,
                    channels,
                    Some(layout.name.into()),
                )
            });
            assert_eq!(ChannelLayout::discrete(channels), former, "{channels}");
        }
        assert_eq!(ChannelLayout::discrete(2).unwrap().tag, (101 << 16) | 2);
    }
}
