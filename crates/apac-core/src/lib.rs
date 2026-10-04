//! Independent APAC decoding core.
//!
//! The crate is `no_std` with `alloc`; its only non-optional dependencies are
//! `sha2` and `libm`. The `serde` feature adds `Serialize` to the report and
//! state types. Unit tests link `std` and name it explicitly.
#![no_std]

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
pub use synthesis::{AdvanceInfo, Decoder, FrameInfo, ParsedPacket, StreamInfo, StreamKind};
