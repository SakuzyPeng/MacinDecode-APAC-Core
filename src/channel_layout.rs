//! Qualified discrete layouts; not a channel-count-derived generic mapping.
pub(crate) const EXTENDED_PROFILE: &str = "apac-channel-layout-v2";

pub(crate) struct Layout {
    pub family: u64,
    pub level: u64,
    pub types: &'static [u8],
    pub labels: &'static [&'static str],
    pub name: &'static str,
    pub preroll_bytes: u64,
}

/// Capacities were read after AudioCodecs 7.0 ASP initialization at both rates.
/// Component SHA-256: 826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd.
pub(crate) fn layout(channels: u64) -> Option<Layout> {
    let (family, level, types, labels, name, preroll_bytes): (u64, u64, &[u8], &[&str], &str, u64) =
        match channels {
            1 => (100, 0, &[0], &["Mono"], "Mono", 2048),
            2 => (101, 0, &[1], &["L", "R"], "Stereo", 4096),
            6 => (
                121,
                1,
                &[1, 0, 3, 1],
                &["L", "R", "C", "LFE", "Ls", "Rs"],
                "Surround51",
                12288,
            ),
            8 => (
                128,
                2,
                &[1, 0, 3, 1, 1],
                &["L", "R", "C", "LFE", "Ls", "Rs", "Rls", "Rrs"],
                "Surround71",
                16384,
            ),
            12 => (
                192,
                3,
                &[1, 0, 3, 1, 1, 1, 1],
                &[
                    "L", "R", "C", "LFE", "Ls", "Rs", "Rls", "Rrs", "Vhl", "Vhr", "Ltr", "Rtr",
                ],
                "Surround714",
                24576,
            ),
            // Public ChannelLayoutForTag returns labels 35/36 (Lw/Rw), despite
            // the SDK's CICP_13 comment saying Lc/Rc. Preserve the tagged order.
            24 => (
                204,
                4,
                &[1, 0, 3, 1, 1, 0, 3, 1, 1, 0, 0, 1, 1, 0, 0, 1],
                &[
                    "Lw", "Rw", "C", "LFE2", "Rls", "Rrs", "L", "R", "Cs", "LFE3", "Lss", "Rss",
                    "Vhl", "Vhr", "Vhc", "Ts", "Ltr", "Rtr", "Ltm", "Rtm", "Ctr", "Cb", "Lb", "Rb",
                ],
                "Surround222",
                49152,
            ),
            _ => return None,
        };
    Some(Layout {
        family,
        level,
        types,
        labels,
        name,
        preroll_bytes,
    })
}

pub(crate) fn profile(layout_tag: u32) -> Option<&'static str> {
    matches!(
        (layout_tag >> 16, layout_tag & 0xffff),
        (192, 12) | (204, 24)
    )
    .then_some(EXTENDED_PROFILE)
}
