//! macOS AudioToolbox reference tools. The crate is empty on other systems.
#![cfg(target_os = "macos")]
pub mod collect;
mod compatibility;
pub mod native;
pub mod replay;
pub mod research;

use apac_research::{error, model, output, packets, signal};
