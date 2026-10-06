//! Independent APAC decoding core.
//!
//! The crate is `no_std` with `alloc`; its only non-optional dependencies are
//! `sha2` and `libm`. The `serde` feature adds `Serialize` to the report and
//! state types. Unit tests link `std` and name it explicitly.
//!
//! # Layers
//!
//! - **Decoding** (crate root): parse a cookie once into a [`Config`], build a
//!   [`Decoder`] and decode packets into interleaved `f32` PCM, 1024 frames
//!   per packet. [`Decoder::advance`] moves the state without synthesis for
//!   fast access; [`Decoder::checkpoint`] and [`Decoder::restore`] save and
//!   return to the state between packets for seeking; [`Decoder::parse`] and
//!   [`Decoder::synthesize`] split [`Decoder::decode`] into its two stages.
//!   A failed call changes nothing.
//! - **Inspection** ([`inspect`]): the report layer. Packet and frame
//!   parsers record syntax and stage results as serializable reports, and the
//!   `*_with_state` variants follow a stream exactly as the decoder does.
//! - **Identity** ([`identity`]): the frozen profile strings and table
//!   digests that published reports carry.
//!
//! Unsupported configurations and syntax are rejected with a reason, never
//! guessed.
//! HOA recovery supports orders 0 through 3 (salient orders 1 through 3),
//! with at most 16 coefficients in each HOA recovery/output domain. Raw cookie
//! inspection can still describe higher orders without enabling their decoding.
//!
//! # Example
//!
//! ```
//! use apac_core::{Config, Decoder};
//!
//! fn hex(text: &str) -> Vec<u8> {
//!     (0..text.len())
//!         .step_by(2)
//!         .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
//!         .collect()
//! }
//! // A stereo cookie and two packets from `data/channel-state-fixtures-v1.json`.
//! let cookie = hex(concat!(
//!     "00000051646170610000000008007c0180040420001200ca01803020001104080a80800010",
//!     "0101500611000700796f3000845124648923246d0082483f17100400808000800c08c48880",
//!     "00201000269400",
//! ));
//! let packets = [
//!     hex(concat!(
//!         "601a010a000a0072de600108a248c9124648da0104907e2e20080101000100181189110000",
//!         "40200048088180",
//!     )),
//!     hex("600a000d0085000020"),
//! ];
//!
//! let config = Config::parse(&cookie)?;
//! let mut decoder = Decoder::new(&config)?;
//! let info = decoder.info();
//! assert_eq!((info.sample_rate_hz, info.channel_count), (48000, 2));
//!
//! let mut pcm = vec![0f32; info.frame_samples as usize * info.channel_count as usize];
//! for packet in &packets {
//!     decoder.decode(packet, &mut pcm)?;
//!     // pcm now holds 1024 interleaved stereo frames
//! }
//! # Ok::<(), apac_core::DecodeError>(())
//! ```
#![no_std]
#![warn(missing_docs)]

extern crate alloc;
#[cfg(test)]
extern crate std;

mod prelude;

mod bwe2_math;
mod channel_layout;
pub mod config;
pub mod error;
mod frame;
pub mod identity;
pub mod inspect;
pub mod model;
mod numeric;
pub mod record;
mod synthesis;
mod tables;

pub use config::{Config, ParseError};
pub use error::DecodeError;
pub use frame::MAX_PACKET_BUFFER;
pub use model::ChannelLayout;
pub use synthesis::{
    AdvanceInfo, Checkpoint, Decoder, FrameInfo, ParsedPacket, StreamInfo, StreamKind,
};

/// Whether this build includes CAC inverse mixing via the `cac` feature.
///
/// Reflects this crate's features after Cargo unifies dependency features,
/// which may differ from the calling crate's own feature flags.
pub const CAC_ENABLED: bool = cfg!(feature = "cac");
