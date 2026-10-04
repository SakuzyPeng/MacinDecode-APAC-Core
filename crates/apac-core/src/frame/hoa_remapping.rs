//! Immutable, bounded core-carrier remapping from cookie wire links.
use crate::config::HoaAsc;
use crate::prelude::*;

/// Profile of the static core-carrier remapping.
pub const PROFILE: &str = "apac-hoa-static-remapping-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-static-remapping-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-static-remapping-state-v1";
/// SHA-256 of `data/hoa-static-remapping-format-v1.json`, computed at build time.
pub fn format_sha256() -> &'static str {
    crate::tables::HOA_REMAPPING_FORMAT_SHA256
}

/// The static core-carrier remapping a configuration declares.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct HoaStaticRemapping {
    pub format_profile: String,
    pub format_sha256: String,
    pub wire_index_width: usize,
    pub wire_indices: Vec<u8>,
    /// Logical core slot -> physical transport carrier. Always a permutation.
    pub core_to_transport: Vec<u8>,
    pub ignored_tail: Vec<u8>,
}
impl HoaStaticRemapping {
    pub(super) fn selected(hoa: &HoaAsc, output: u8) -> Option<Self> {
        let core_to_transport: Option<Vec<_>> = hoa
            .remapping_core_to_transport
            .as_ref()?
            .iter()
            .map(|&n| u8::try_from(n).ok())
            .collect();
        let fields = |values: &[u64]| {
            values
                .iter()
                .filter_map(|&n| u8::try_from(n).ok())
                .collect()
        };
        Some(Self {
            format_profile: PROFILE.into(),
            format_sha256: format_sha256().into(),
            wire_index_width: (u8::BITS - output.saturating_sub(1).leading_zeros()) as usize,
            wire_indices: fields(&hoa.remapping),
            core_to_transport: core_to_transport?,
            ignored_tail: fields(&hoa.remapping_tail),
        })
    }
}
