mod bwe2_math;
mod channel_layout;
pub mod config;
pub mod error;
pub mod frame;
pub mod model;
mod numeric;
pub mod synthesis;

/// Transitional access for `apac-research` while the decoder and its reports
/// are separated. Not a stable interface; it shrinks as the refactor proceeds.
#[doc(hidden)]
pub mod research_support;
