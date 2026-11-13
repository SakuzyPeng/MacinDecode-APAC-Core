//! Valid-audio frame ranges and their raw (priming-inclusive) positions.
use crate::{Error, PacketTable, Result};

fn invalid(message: &str) -> Error {
    Error::new("packet bundle", message)
}
fn add(a: u64, b: u64) -> Result<u64> {
    a.checked_add(b)
        .ok_or_else(|| invalid("integer overflow in packet/frame range"))
}

/// A requested span of valid audio inside a source's target window.
/// Frame coordinates exclude priming; `raw_*` include it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Range {
    /// First valid frame the source can supply.
    pub window_start_frame: u64,
    /// End (exclusive) of the frames the source can supply.
    pub window_end_frame: u64,
    /// First requested frame.
    pub start_frame: u64,
    /// Frames requested from `start_frame`.
    pub requested_frames: u64,
    /// Frames actually available: the request clipped to the window.
    pub frames: u64,
    /// `start_frame` counting priming frames.
    pub raw_start: u64,
    /// End (exclusive) of the output counting priming frames.
    pub raw_end: u64,
    /// The range ends with the window, so decoding continues to the end of
    /// the input instead of stopping at `raw_end`.
    pub drain_to_eof: bool,
    /// Why fewer frames than requested are available.
    pub clipped_by: Option<&'static str>,
}
impl Range {
    /// `requested` frames from `start` (default: the window start) inside
    /// the window `[window.0, window.1]` of valid audio.
    pub fn new(
        window: (u64, u64),
        table: &PacketTable,
        start: Option<u64>,
        requested: u64,
    ) -> Result<Self> {
        let (window_start, window_end) = window;
        if requested == 0 {
            return Err(invalid("frame count must be positive"));
        }
        let start = start.unwrap_or(window_start);
        if start < window_start || start > window_end {
            return Err(invalid("frame start is outside the target window"));
        }
        let requested_end = add(start, requested)?;
        let end = requested_end.min(window_end);
        let prime = table.priming_frames as u64;
        Ok(Self {
            window_start_frame: window_start,
            window_end_frame: window_end,
            start_frame: start,
            requested_frames: requested,
            frames: end - start,
            raw_start: add(prime, start)?,
            raw_end: add(prime, end)?,
            drain_to_eof: end == window_end,
            clipped_by: (end < requested_end).then_some(if end == table.valid_frames as u64 {
                "source_eof"
            } else {
                "window_end"
            }),
        })
    }
    /// The same range restarted at `start`, keeping its end: what
    /// [`Range::new`] gives for a request from `start` to the original
    /// requested end. `None` when `start` lies outside `[window start, end]`.
    pub fn starting_at(&self, start: u64) -> Option<Self> {
        let end = self.start_frame + self.frames;
        if start < self.window_start_frame || start > end {
            return None;
        }
        Some(Self {
            start_frame: start,
            requested_frames: self.start_frame + self.requested_frames - start,
            frames: end - start,
            raw_start: self.raw_start - self.start_frame + start,
            ..*self
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_restarted_range_equals_a_new_request_to_the_same_end() {
        let table = PacketTable {
            valid_frames: 5000,
            priming_frames: 2112,
            remainder_frames: 1032,
        };
        for window in [(0, 5000), (1000, 3000)] {
            for start in [window.0, window.0 + 1, window.0 + 700] {
                for requested in [1, 999, 2000, 10_000] {
                    let range = Range::new(window, &table, Some(start), requested).unwrap();
                    let requested_end = start + requested;
                    for restart in window.0..=window.1 + 1 {
                        let restarted = range.starting_at(restart);
                        if restart > range.start_frame + range.frames {
                            assert!(restarted.is_none());
                        } else if restart < requested_end {
                            let fresh =
                                Range::new(window, &table, Some(restart), requested_end - restart);
                            assert_eq!(restarted, Some(fresh.unwrap()), "{window:?} {restart}");
                        } else {
                            assert_eq!(restarted.unwrap().frames, 0);
                        }
                    }
                }
            }
        }
        let range = Range::new((0, 5000), &table, None, 10).unwrap();
        assert!(Range::new((0, 5000), &table, None, 0).is_err());
        assert!(Range::new((10, 5000), &table, Some(9), 1).is_err());
        assert!(Range::new((0, 5000), &table, Some(1), u64::MAX).is_err());
        assert_eq!((range.raw_start, range.raw_end), (2112, 2122));
    }
}
