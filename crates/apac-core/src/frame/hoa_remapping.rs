//! Immutable, bounded core-carrier remapping from cookie wire links.
use crate::config::CookieReport;
use crate::prelude::*;
use serde::{Deserialize, Serialize};

pub const PROFILE: &str = "apac-hoa-static-remapping-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-static-remapping-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-static-remapping-state-v1";
pub fn format_sha256() -> &'static str {
    static HASH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    HASH.get_or_init(|| {
        crate::model::sha256(include_bytes!(
            "../../../../data/hoa-static-remapping-format-v1.json"
        ))
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    pub(super) fn selected(parsed: &CookieReport, output: u8) -> Option<Self> {
        let mapping = parsed
            .derived
            .get("components[0].hoa.remapping_core_to_transport")?
            .as_array()?;
        let core_to_transport: Option<Vec<_>> = mapping
            .iter()
            .map(|v| v.as_u64().and_then(|n| u8::try_from(n).ok()))
            .collect();
        let fields = |prefix: &str| {
            parsed
                .fields
                .iter()
                .filter(|f| f.name.starts_with(prefix))
                .filter_map(|f| f.value.as_u64().and_then(|n| u8::try_from(n).ok()))
                .collect()
        };
        Some(Self {
            format_profile: PROFILE.into(),
            format_sha256: format_sha256().into(),
            wire_index_width: (u8::BITS - output.saturating_sub(1).leading_zeros()) as usize,
            wire_indices: fields("components[0].hoa.remapping["),
            core_to_transport: core_to_transport?,
            ignored_tail: fields("components[0].hoa.remapping_tail["),
        })
    }
}
