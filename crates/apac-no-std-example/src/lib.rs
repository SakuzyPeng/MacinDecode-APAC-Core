//! Decoding APAC packets with `apac-core` in a `no_std` + `alloc` crate.
//!
//! The decoder needs a global allocator (provided by the final binary, e.g.
//! an embedded heap or the wasm allocator) and nothing else from the
//! platform: no file system, clock or floating-point library. Build it for
//! the bare-metal targets with
//!
//! ```sh
//! cargo build -p apac-no-std-example --target thumbv7em-none-eabihf
//! cargo build -p apac-no-std-example --target wasm32v1-none
//! ```
//!
//! Packets come from wherever the platform stores them; this example takes
//! them as byte slices and writes PCM into a caller-provided buffer, so the
//! only allocations are the decoder's own state.
#![no_std]
#![warn(missing_docs)]

#[cfg(test)]
extern crate std;

use apac_core::{Config, DecodeError, Decoder};

/// Decode `packets` of the stream that `cookie` describes into `out` as
/// interleaved `f32` samples, 1024 frames per packet, and return the number
/// of frames written.
///
/// `out` must hold `1024 × channels` samples per packet; decoding stops with
/// an error at the first packet that does not fit or does not decode, after
/// the frames of the earlier packets were written.
pub fn decode_packets<'a>(
    cookie: &[u8],
    packets: impl IntoIterator<Item = &'a [u8]>,
    out: &mut [f32],
) -> Result<usize, DecodeError> {
    let config = Config::parse(cookie)?;
    let mut decoder = Decoder::new(&config)?;
    let info = decoder.info();
    let channels = info.channel_count as usize;
    let frame_samples = info.frame_samples as usize;
    let mut frames = 0;
    for packet in packets {
        let start = frames * channels;
        let Some(slot) = out.get_mut(start..start + frame_samples * channels) else {
            return Err(DecodeError::new(
                "example",
                "output buffer is too small for the next packet",
            ));
        };
        decoder.decode(packet, slot)?;
        frames += frame_samples;
    }
    Ok(frames)
}

#[cfg(test)]
mod tests {
    use std::{string::String, vec, vec::Vec};

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
            .collect()
    }
    fn fixtures() -> Vec<(Vec<u8>, Vec<Vec<u8>>)> {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/channel-state-fixtures-v1.json"))
                .unwrap();
        data["fixtures"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| {
                let field = |key: &str| hex(f[key].as_str().unwrap());
                (field("cookie"), vec![field("first"), field("next")])
            })
            .collect()
    }

    #[test]
    fn decodes_like_the_decoder_into_the_caller_buffer() {
        for (cookie, packets) in fixtures() {
            let mut decoder = apac_core::Decoder::from_cookie(&cookie).unwrap();
            let expected: Vec<u32> = packets
                .iter()
                .flat_map(|p| decoder.decode_vec(p).unwrap())
                .map(f32::to_bits)
                .collect();
            let mut out = vec![1f32; expected.len() + 7];
            let frames =
                super::decode_packets(&cookie, packets.iter().map(Vec::as_slice), &mut out)
                    .unwrap();
            assert_eq!(
                frames * decoder.info().channel_count as usize,
                expected.len()
            );
            let bits: Vec<u32> = out[..expected.len()].iter().map(|v| v.to_bits()).collect();
            assert_eq!(bits, expected);
            assert!(out[expected.len()..].iter().all(|&v| v == 1.));
        }
    }

    #[test]
    fn a_short_buffer_keeps_the_frames_that_fit() {
        let (cookie, packets) = fixtures().swap_remove(1);
        let mut out = vec![0f32; 1024 * 2 + 1];
        let error = super::decode_packets(&cookie, packets.iter().map(Vec::as_slice), &mut out)
            .unwrap_err();
        assert_eq!(
            error.message,
            String::from("output buffer is too small for the next packet")
        );
        let mut decoder = apac_core::Decoder::from_cookie(&cookie).unwrap();
        let first = decoder.decode_vec(&packets[0]).unwrap();
        assert_eq!(out[..first.len()], first[..]);
    }
}
