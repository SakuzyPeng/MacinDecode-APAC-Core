//! Shared sampling-rate buckets and bounded SFB/TNS format tables.
use serde::Deserialize;
use std::{collections::BTreeMap, sync::OnceLock};

pub const PROFILE: &str = "apac-hoa-shared-configuration-v1";
pub const RATES: [u64; 13] = [
    96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350,
];
#[derive(Deserialize)]
pub(super) struct Rate {
    pub sample_rate: u64,
    pub sfb_rate: u64,
    long: String,
    short: String,
    pub tns_long_limit: usize,
    pub tns_short_limit: usize,
}
#[derive(Deserialize)]
struct Format {
    format_profile: String,
    format_sha256: String,
    rates: Vec<Rate>,
    offset_arrays: BTreeMap<String, Vec<usize>>,
    profiles: Vec<Profile>,
}
#[derive(Deserialize)]
struct Limit {
    r#type: u8,
    maximum_channels: u64,
}
#[derive(Deserialize)]
struct Profile {
    profile: u8,
    levels: Vec<Vec<Limit>>,
    layout_tags: Vec<u32>,
}
fn format() -> &'static Format {
    static DATA: OnceLock<Format> = OnceLock::new();
    DATA.get_or_init(|| {
        let result: Format = serde_json::from_str(include_str!(
            "../../../../data/hoa-shared-config-format-v1.json"
        ))
        .expect("shared rate tables");
        assert_eq!(result.format_profile, PROFILE);
        assert_eq!(
            result
                .rates
                .iter()
                .map(|r| r.sample_rate)
                .collect::<Vec<_>>(),
            RATES
        );
        for values in result.offset_arrays.values() {
            assert_eq!(values.first(), Some(&0));
            assert!(matches!(values.last(), Some(128 | 1024)));
            assert!(values.windows(2).all(|p| p[0] < p[1]));
        }
        result
    })
}
pub fn format_sha256() -> &'static str {
    &format().format_sha256
}
pub(super) fn rate(hz: u64) -> &'static Rate {
    format()
        .rates
        .iter()
        .find(|r| r.sample_rate == hz)
        .expect("qualified sample rate")
}
pub(super) fn offsets(hz: u64, short: bool) -> &'static [usize] {
    let rate = rate(hz);
    let key = if short { &rate.short } else { &rate.long };
    match key.as_str() {
        "legacy-long" => &super::spectrum::tables().long_offsets,
        "legacy-short" => &super::spectrum::tables().short_offsets,
        _ => &format().offset_arrays[key],
    }
}
pub fn index(hz: u64) -> Option<usize> {
    RATES.iter().position(|&r| r == hz)
}
pub(crate) fn extended(hz: u64) -> bool {
    !matches!(hz, 44100 | 48000)
}
pub(super) fn supports_component(profile: u8, level: u8, kind: u8, count: u64, tag: u32) -> bool {
    let Some(p) = format().profiles.iter().find(|p| p.profile == profile) else {
        return false;
    };
    let Some(limits) = p.levels.get(usize::from(level)) else {
        return false;
    };
    limits
        .iter()
        .any(|l| l.r#type == kind && count <= l.maximum_channels)
        && p.layout_tags.iter().any(|&t| {
            if t == 0 || t == 65536 || t & 65535 != 0 || t == 0xffff0000 {
                tag == t
            } else {
                tag & 0xffff0000 == t
            }
        })
}
