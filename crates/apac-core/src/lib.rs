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
pub mod frame;
pub mod model;
mod numeric;
pub mod record;
pub mod synthesis;
mod tables;

pub use config::Config;
pub use error::DecodeError;
pub use synthesis::{AdvanceInfo, Decoder, FrameInfo, StreamInfo, StreamKind};

/// Transitional access for `apac-research` while the decoder and its reports
/// are separated. Not a stable interface; it shrinks as the refactor proceeds.
#[doc(hidden)]
pub mod research_support;
