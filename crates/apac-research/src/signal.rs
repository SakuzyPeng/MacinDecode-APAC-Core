use crate::model::ChannelLayout;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[cfg_attr(feature = "clap", derive(clap::ValueEnum))]
#[serde(rename_all = "kebab-case")]
pub enum LayoutPreset {
    Mono,
    Stereo,
    Surround51,
    Surround71,
    Surround714,
    Hoa1,
    Hoa2,
    Hoa3,
    Surround222,
}
impl LayoutPreset {
    pub fn channels(self) -> u32 {
        match self {
            Self::Mono => 1,
            Self::Stereo => 2,
            Self::Surround51 => 6,
            Self::Surround71 => 8,
            Self::Surround714 => 12,
            Self::Hoa1 => 4,
            Self::Hoa2 => 9,
            Self::Hoa3 => 16,
            Self::Surround222 => 24,
        }
    }
    pub fn tag(self) -> u32 {
        let family = match self {
            Self::Mono => 100,
            Self::Stereo => 101,
            Self::Surround51 => 121,
            Self::Surround71 => 128,
            Self::Surround714 => 192,
            Self::Hoa1 | Self::Hoa2 | Self::Hoa3 => 190,
            Self::Surround222 => 204,
        };
        (family << 16) | self.channels()
    }
    pub fn layout(self) -> ChannelLayout {
        ChannelLayout::tagged(self.tag(), self.channels(), Some(format!("{self:?}")))
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "clap", derive(clap::ValueEnum))]
#[serde(rename_all = "kebab-case")]
pub enum Signal {
    Silence,
    Impulse,
    Sine,
    Sweep,
    Noise,
    ChannelSolo,
}
impl Signal {
    pub const ALL: [Self; 6] = [
        Self::Silence,
        Self::Impulse,
        Self::Sine,
        Self::Sweep,
        Self::Noise,
        Self::ChannelSolo,
    ];
    pub fn name(self) -> &'static str {
        match self {
            Self::Silence => "silence",
            Self::Impulse => "impulse",
            Self::Sine => "sine",
            Self::Sweep => "sweep",
            Self::Noise => "noise",
            Self::ChannelSolo => "channel-solo",
        }
    }
}

/// Stateless per-sample noise makes the result independent of I/O block size.
fn noise(seed: u64, frame: u64, channel: u32) -> f64 {
    let mut x = seed
        .wrapping_add(frame.wrapping_mul(0x9e3779b97f4a7c15))
        .wrapping_add(u64::from(channel).wrapping_mul(0xd1b54a32d192ed03));
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x ^= x >> 31;
    ((x >> 11) as f64 / (1u64 << 53) as f64) * 2. - 1.
}

pub fn generate(
    signal: Signal,
    start: u64,
    count: u32,
    total: u64,
    rate: u32,
    channels: u32,
    seed: u64,
) -> Vec<f32> {
    let mut out = Vec::with_capacity(count as usize * channels as usize);
    let duration = total as f64 / f64::from(rate);
    for frame in start..start + u64::from(count) {
        let t = frame as f64 / f64::from(rate);
        for ch in 0..channels {
            let frequency = 397. + 113. * f64::from(ch);
            let sample = match signal {
                Signal::Silence => 0.,
                Signal::Impulse => {
                    if frame == total / 3 + u64::from(ch) {
                        0.5
                    } else {
                        0.
                    }
                }
                Signal::Sine => 0.25 * (std::f64::consts::TAU * frequency * t).sin(),
                Signal::Sweep => {
                    let f0 = 40.;
                    let f1 = (f64::from(rate) * 0.4).min(18000.);
                    let k = (f1 / f0).ln() / duration;
                    0.25 * (std::f64::consts::TAU * f0 * ((k * t).exp() - 1.) / k).sin()
                }
                Signal::Noise => 0.2 * noise(seed, frame, ch),
                Signal::ChannelSolo => {
                    let slot =
                        ((u128::from(frame) * u128::from(channels)) / u128::from(total)) as u32;
                    if slot != ch {
                        0.
                    } else {
                        0.25 * (std::f64::consts::TAU * frequency * t).sin()
                    }
                }
            };
            out.push(sample as f32);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn signals_are_block_independent_and_channel_solo_is_isolated() {
        for s in Signal::ALL {
            let all = generate(s, 0, 96, 96, 48000, 24, 42);
            let mut split = generate(s, 0, 31, 96, 48000, 24, 42);
            split.extend(generate(s, 31, 65, 96, 48000, 24, 42));
            assert_eq!(all, split);
            assert!(all.iter().all(|v| v.is_finite() && v.abs() <= 0.5));
        }
        for (i, frame) in generate(Signal::ChannelSolo, 0, 96, 96, 48000, 24, 42)
            .as_chunks::<24>()
            .0
            .iter()
            .enumerate()
        {
            for (ch, &v) in frame.iter().enumerate() {
                if ch != i / 4 {
                    assert_eq!(v, 0.);
                }
            }
        }
    }
}
