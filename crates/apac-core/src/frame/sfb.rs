//! Shared sampling-rate buckets and bounded SFB/TNS format tables.

/// Profile of the shared configuration syntax.
pub const PROFILE: &str = "apac-hoa-shared-configuration-v1";
pub const RATES: [u64; 13] = [
    96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350,
];
use crate::tables::SfbRate as Rate;
/// Generated from `data/hoa-shared-config-format-v1.json` by the build script.
pub fn format_sha256() -> &'static str {
    crate::tables::SFB_FORMAT_SHA256
}
pub(super) fn rate(hz: u64) -> &'static Rate {
    crate::tables::SFB_RATES
        .iter()
        .find(|r| r.sample_rate == hz)
        .expect("qualified sample rate")
}
pub(super) fn offsets(hz: u64, short: bool) -> &'static [usize] {
    let rate = rate(hz);
    if short { rate.short } else { rate.long }
}
pub fn index(hz: u64) -> Option<usize> {
    RATES.iter().position(|&r| r == hz)
}
pub(crate) fn extended(hz: u64) -> bool {
    !matches!(hz, 44100 | 48000)
}
pub(super) fn supports_component(profile: u8, level: u8, kind: u8, count: u64, tag: u32) -> bool {
    let Some(p) = crate::tables::SFB_PROFILES
        .iter()
        .find(|p| p.profile == profile)
    else {
        return false;
    };
    let Some(limits) = p.levels.get(usize::from(level)) else {
        return false;
    };
    limits
        .iter()
        .any(|l| l.kind == kind && count <= l.maximum_channels)
        && p.layout_tags.iter().any(|&t| {
            if t == 0 || t == 65536 || t & 65535 != 0 || t == 0xffff0000 {
                tag == t
            } else {
                tag & 0xffff0000 == t
            }
        })
}
