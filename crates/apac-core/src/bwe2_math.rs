//! Optional BWE2 numerical adapter; wire parsing and report types stay in the core.
use crate::prelude::*;

pub const PROFILE: &str = "apac-bwe2-math-v2";
/// Frozen identity of the optional BWE2 codebooks and gains, even when not linked.
pub fn format_sha256() -> &'static str {
    "651850263d6adf1c2e5c2285910dc1fd6f0921293b6c0e7578a0cb780384a661"
}
/// Frozen identity of the optional BWE2 mathematical tables, even when not linked.
pub fn math_sha256() -> &'static str {
    "101d1db498c130be86f5d053bc0c5d291a052179603bd221ea08af383601663c"
}

/// Diagnostic LPC/LSF analysis of one BWE2 restoration, recorded in reports.
#[derive(Debug, Clone)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[allow(missing_docs)]
pub struct Analysis {
    pub conditioned_lsf: [f64; 16],
    pub source_lpc: [f64; 17],
    /// Diagnostic expansion; the target envelope is evaluated from LSF factors.
    pub target_lpc: [f64; 17],
}
pub(crate) fn restore(
    input: &[f32],
    short: bool,
    cutoff: usize,
    groups: &[u32],
    indices: [u16; 2],
    gains: &[u8],
) -> Result<(Vec<f32>, Option<Analysis>), String> {
    restore_captured(input, short, cutoff, groups, indices, gains, true)
}
pub(crate) fn restore_captured(
    input: &[f32],
    short: bool,
    cutoff: usize,
    groups: &[u32],
    indices: [u16; 2],
    gains: &[u8],
    capture: bool,
) -> Result<(Vec<f32>, Option<Analysis>), String> {
    #[cfg(feature = "bwe2")]
    {
        apac_bwe2::restore_captured(input, short, cutoff, groups, indices, gains, capture).map(
            |(scaled, analysis)| {
                (
                    scaled,
                    analysis.map(|a| Analysis {
                        conditioned_lsf: a.conditioned_lsf,
                        source_lpc: a.source_lpc,
                        target_lpc: a.target_lpc,
                    }),
                )
            },
        )
    }
    #[cfg(not(feature = "bwe2"))]
    {
        let _ = (input, short, cutoff, groups, indices, gains, capture);
        // The shared wire parser rejects active BWE2 before reaching this adapter.
        Err("BWE2 numerical restoration is unavailable without the bwe2 feature".into())
    }
}

#[cfg(all(test, feature = "bwe2"))]
mod tests {
    #[test]
    fn optional_crate_preserves_the_frozen_report_identities() {
        assert_eq!(super::PROFILE, apac_bwe2::PROFILE);
        assert_eq!(super::format_sha256(), apac_bwe2::format_sha256());
        assert_eq!(super::math_sha256(), apac_bwe2::math_sha256());
    }
}
