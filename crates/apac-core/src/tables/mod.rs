//! Format and numeric tables generated at build time from `data/*.json`.
//!
//! `build/main.rs` validates every source file with the checks the runtime
//! loaders used to perform, applies their derivations and writes the result
//! as `static` data. Floating-point values are emitted from their exact bit
//! patterns, so nothing is parsed or computed when the decoder starts.
use crate::config::{ParseError, bits::BitReader};
use crate::prelude::*;

#[cfg(test)]
mod legacy_tests;
pub mod trie_build;

/// MSB-first Huffman decoding trie; see [`trie_build`] for the node layout.
pub struct Trie(Cow<'static, [trie_build::Node]>);

impl Trie {
    pub const fn from_static(nodes: &'static [trie_build::Node]) -> Self {
        Self(Cow::Borrowed(nodes))
    }
    /// Runtime construction for tables that are not yet generated statically.
    pub fn build(codes: &[u32], bits: &[usize]) -> Self {
        Self(Cow::Owned(trie_build::build(codes, bits)))
    }
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn nodes(&self) -> &[trie_build::Node] {
        &self.0
    }
    pub fn read(&self, bits: &mut BitReader<'_>) -> Result<usize, ParseError> {
        let start = bits.position();
        let mut node = 0;
        loop {
            let [zero, one, symbol] = self.0[node];
            if symbol != trie_build::NO_SYMBOL {
                return Ok(usize::from(symbol));
            }
            let next = if bits.read(1)? == 0 { zero } else { one };
            if next == 0 {
                return Err(ParseError::new(start, "huffman", "invalid Huffman prefix"));
            }
            node = usize::from(next);
        }
    }
}

/// Codewords and their lengths, retained where decoding or tests read them.
pub struct Codebook {
    #[cfg_attr(not(test), allow(dead_code))]
    pub codes: &'static [u32],
    pub bits: &'static [usize],
}

/// One CAC gain index's rotation: Float64 bit patterns and the channel swap.
pub struct CacRotation {
    pub a_f64: u64,
    pub b_f64: u64,
    pub swap: bool,
}

/// One explicit sampling rate: its SFB/TNS table bucket and band offsets.
pub struct SfbRate {
    pub sample_rate: u64,
    pub sfb_rate: u64,
    pub long: &'static [usize],
    pub short: &'static [usize],
    pub tns_long_limit: usize,
    pub tns_short_limit: usize,
}
/// Channel limit for one ASC type at one profile level.
pub struct SfbLimit {
    pub kind: u8,
    pub maximum_channels: u64,
}
pub struct SfbProfile {
    pub profile: u8,
    pub levels: &'static [&'static [SfbLimit]],
    pub layout_tags: &'static [u32],
}

/// Shared DRC Huffman entry: codeword width, codeword and decoded value.
pub struct DrcCode {
    pub width: usize,
    pub code: u16,
    pub value: i32,
}

pub struct HoaProfileLimit {
    pub profile_id: u8,
    pub maximum_output_channels_by_level: &'static [u64],
}

/// Spatial-control grid `method * 16 + subbands - 1`: long-window band ends.
pub struct HoaControlGrid {
    pub long_ends: &'static [usize],
}

/// Subband ends of one grid for long and short windows.
pub struct HoaSubbandGrid {
    pub long_ends: &'static [usize],
    pub short_ends: &'static [usize],
}
/// Effective dynamic-selection subbands for each of the three methods.
pub struct HoaDynamicSubbands {
    pub long_ends: [&'static [usize]; 3],
    pub short_ends: [&'static [usize]; 3],
}

/// One accepted source layout and its bounded matrix (Float32 bit patterns).
pub struct HoaSourceLayout {
    pub tag: u32,
    pub channel_labels: &'static [u32],
    pub lfe_indices: &'static [usize],
    pub matrix_id: &'static str,
    pub matrix: &'static [u32],
    pub matrix_columns: usize,
    pub matrix_available: bool,
}

include!(concat!(env!("OUT_DIR"), "/tables.rs"));
