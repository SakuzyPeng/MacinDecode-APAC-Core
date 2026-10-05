//! Seekable decoding for playback over a [`Media`] input: frame-exact seeks
//! that replay a bounded number of packets from decoder checkpoints, with
//! an [`Indexer`] that builds the checkpoints on another thread.
use crate::{Error, Media, PacketCursor, ReadError, Source};
use apac_core::{Checkpoint, Decoder};
use std::sync::atomic::{AtomicU64, Ordering};

/// Identifies a [`Playback`] so that [`IndexBatch`]es of its indexers are
/// not merged into another.
static NEXT_PLAYBACK: AtomicU64 = AtomicU64::new(0);

/// How a [`Playback`] keeps its seek index.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlaybackOptions {
    /// Packets between checkpoints at first; at least 1.
    pub checkpoint_interval: u64,
    /// Checkpoints kept at most; at least 2. When the index is full, every
    /// other checkpoint is dropped and the interval doubles.
    pub max_checkpoints: usize,
}
impl Default for PlaybackOptions {
    /// 64 packets (about 1.4 s at 48 kHz) and 1024 checkpoints.
    fn default() -> Self {
        Self {
            checkpoint_interval: 64,
            max_checkpoints: 1024,
        }
    }
}

/// What a [`Playback`] processed, accumulated over its life.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PlaybackStats {
    /// Packets before an output position that only advanced the state.
    pub advanced_packets: u64,
    /// Fully decoded packets, including warm-up packets.
    pub decoded_packets: u64,
    /// Fully decoded packets whose PCM lies before the output position:
    /// the predecessor that rebuilds the overlap after a seek.
    pub warmup_packets: u64,
    /// Checkpoints restored by seeks.
    pub restored_checkpoints: u64,
    /// Packets advanced by [`Playback::extend_index`].
    pub indexed_packets: u64,
}

/// Checkpoint `i` is the state before packet `i × interval`, from the first
/// packet on without gaps: every position is reached by advancing from an
/// earlier checkpoint, and each checkpoint is kept as it is passed.
struct SeekIndex {
    interval: u64,
    max: usize,
    entries: Vec<(PacketCursor, Checkpoint)>,
}
impl SeekIndex {
    /// Whether the state before `packet` is the next checkpoint to keep.
    fn due(&self, packet: u64) -> bool {
        packet == self.entries.len() as u64 * self.interval
    }
    /// Keep the state before the due packet. A full index first drops every
    /// other checkpoint and doubles the interval; the packet may then no
    /// longer be due.
    fn push(&mut self, cursor: PacketCursor, checkpoint: Checkpoint) {
        if self.entries.len() == self.max {
            let mut keep = false;
            self.entries.retain(|_| {
                keep = !keep;
                keep
            });
            self.interval *= 2;
            if !self.due(cursor.packet()) {
                return;
            }
        }
        self.entries.push((cursor, checkpoint));
    }
    /// The last checkpoint at or before `packet`.
    fn before(&self, packet: u64) -> &(PacketCursor, Checkpoint) {
        let index = usize::try_from(packet / self.interval).unwrap_or(usize::MAX);
        &self.entries[index.min(self.entries.len() - 1)]
    }
    fn last(&self) -> &(PacketCursor, Checkpoint) {
        self.entries.last().expect("the initial checkpoint")
    }
}

fn invalid(operation: &'static str, message: impl Into<String>) -> ReadError<Error> {
    ReadError::Invalid {
        operation,
        message: message.into(),
    }
}

/// Decodes a [`Media`] input into interleaved `f32` PCM with frame-exact,
/// bounded-cost seeking, for players.
///
/// The output covers the valid audio (priming and remainder removed) and is
/// bit-identical to `decode-sq` and to [`Reader`](crate::Reader) for the same
/// frames, from the start or after any seek.
///
/// APAC state depends on every earlier packet, so seeking restores a
/// [`Checkpoint`] taken at most one checkpoint interval before the target,
/// advances the state to the packet before the target and decodes that
/// packet to rebuild the overlap. Checkpoints are kept as packets are passed
/// while reading, and ahead of playback by [`Playback::extend_index`]; until
/// the index reaches a position, a seek there advances from the last
/// checkpoint. [`Playback::seek`] only chooses where to start: the work
/// happens in the next [`Playback::read`].
///
/// To index while playing, scan on another thread with an [`Indexer`]
/// ([`Playback::indexer`]) and merge its batches with
/// [`Playback::merge_index`].
///
/// A packet that fails to decode stops reading where it is: the error names
/// the packet, the position does not move and a retry fails the same way.
/// No packet is skipped and no state is guessed, so positions after it
/// cannot be reached; earlier positions stay seekable. A source error is
/// reported the same way and may succeed when retried.
pub struct Playback<R> {
    /// Accepts only the batches of its own indexers.
    id: u64,
    media: Media<R>,
    decoder: Decoder,
    /// The next packet for `decoder`.
    cursor: PacketCursor,
    /// Whether the decoder's overlap equals a sequential decode's at
    /// `cursor`: after a full decode, or at the first packet.
    synthesized: bool,
    /// The next output frame and the end of the valid audio, counting
    /// priming frames.
    output: u64,
    end: u64,
    priming: u64,
    samples: Vec<f32>,
    packet: Vec<u8>,
    index: SeekIndex,
    /// The decoder and cursor of [`Playback::extend_index`].
    scanner: Option<(Decoder, PacketCursor)>,
    /// The scanner reached the end of the packet table.
    complete: bool,
    /// The current playback pass has checked the remaining packet table.
    ended: bool,
    stats: PlaybackStats,
}
impl<R: Source> Playback<R> {
    /// [`Playback::open_with`] with the default options.
    pub fn open(media: Media<R>) -> Result<Self, ReadError<Error>> {
        Self::open_with(media, PlaybackOptions::default())
    }
    /// Build the decoder for `media`'s stream, with the configuration checks
    /// and rejection texts of [`Reader::open`](crate::Reader::open), and
    /// position the output at the first valid frame.
    pub fn open_with(media: Media<R>, options: PlaybackOptions) -> Result<Self, ReadError<Error>> {
        if options.checkpoint_interval == 0 || options.max_checkpoints < 2 {
            return Err(invalid(
                "SQ access",
                "requires a positive checkpoint interval and room for two checkpoints",
            ));
        }
        let track = media.track();
        let decoder = Decoder::new(&track.config).map_err(|error| ReadError::Decode {
            error,
            packet_index: None,
        })?;
        let info = decoder.info();
        if track.channels != info.channel_count {
            return Err(invalid(
                "SQ decoder",
                "input channel count disagrees with decoder",
            ));
        }
        if !track.layout.equivalent(info.layout) {
            return Err(invalid(
                "SQ decoder",
                format!(
                    "input channel layout disagrees with decoder: expected cookie layout tag {:#010x}, zero bitmap and no descriptions",
                    info.layout.tag
                ),
            ));
        }
        let priming = track.table.priming_frames as u64;
        let end = priming + track.table.valid_frames as u64;
        let samples = vec![0f32; 1024 * info.channel_count as usize];
        let cursor = media.start();
        let index = SeekIndex {
            interval: options.checkpoint_interval,
            max: options.max_checkpoints,
            entries: vec![(cursor, decoder.checkpoint())],
        };
        Ok(Self {
            id: NEXT_PLAYBACK.fetch_add(1, Ordering::Relaxed),
            media,
            decoder,
            cursor,
            synthesized: true,
            output: priming,
            end,
            priming,
            samples,
            packet: Vec::new(),
            index,
            scanner: None,
            complete: false,
            ended: false,
            stats: PlaybackStats::default(),
        })
    }
    /// The decoder, in the state after the last processed packet.
    pub fn decoder(&self) -> &Decoder {
        &self.decoder
    }
    /// The input.
    pub fn media(&self) -> &Media<R> {
        &self.media
    }
    /// The input, giving up the decoder and the index.
    pub fn into_media(self) -> Media<R> {
        self.media
    }
    /// Frames of valid audio.
    pub fn frames(&self) -> u64 {
        self.end - self.priming
    }
    /// The valid-audio frame the next [`Playback::read`] returns first.
    pub fn position(&self) -> u64 {
        self.output - self.priming
    }
    /// Counts accumulated over this playback's life.
    pub fn stats(&self) -> &PlaybackStats {
        &self.stats
    }
    /// Packets between the checkpoints kept now.
    pub fn checkpoint_interval(&self) -> u64 {
        self.index.interval
    }
    /// Checkpoints kept now.
    pub fn checkpoints(&self) -> usize {
        self.index.entries.len()
    }
    /// The valid-audio frame up to which the index reaches: a seek up to
    /// this frame plus one checkpoint interval replays at most one interval.
    pub fn indexed_frames(&self) -> u64 {
        let packet = self.index.last().0.packet();
        (packet * 1024)
            .saturating_sub(self.priming)
            .min(self.frames())
    }
    /// Whether [`Playback::extend_index`] or a merged [`Indexer`] reached the
    /// end of the packet table, so that every seek replays at most one
    /// checkpoint interval.
    pub fn index_complete(&self) -> bool {
        self.complete
    }
    /// Decode up to the next packet holding output frames and write them,
    /// interleaved, to the front of `out` (at least 1024 frames of all
    /// channels). Returns the frame count, zero at the end of the valid
    /// audio. Before returning zero, checks the remaining packet table,
    /// including packets wholly trimmed by the container, without reading
    /// their audio. These checks do not change the decoder or the position.
    pub fn read(&mut self, out: &mut [f32]) -> Result<usize, ReadError<Error>> {
        if out.len() < self.samples.len() {
            return Err(invalid(
                "SQ decoder",
                format!(
                    "output buffer holds {} samples, {} required",
                    out.len(),
                    self.samples.len()
                ),
            ));
        }
        let channels = self.samples.len() / 1024;
        while self.output < self.end {
            let target = self.output / 1024;
            let index = self.cursor.packet();
            if self.index.due(index) {
                self.index.push(self.cursor, self.decoder.checkpoint());
            }
            let mut cursor = self.cursor;
            if self
                .media
                .read_packet(&mut cursor, &mut self.packet)
                .map_err(ReadError::Source)?
                .is_none()
            {
                // Opening checked that the packets cover the valid audio.
                return Err(invalid(
                    "SQ access",
                    "packet table ended before the valid audio",
                ));
            }
            let failed = |error| ReadError::Decode {
                error,
                packet_index: Some(index),
            };
            if index + 1 < target {
                self.decoder.advance(&self.packet).map_err(failed)?;
                self.cursor = cursor;
                self.synthesized = false;
                self.stats.advanced_packets += 1;
                continue;
            }
            debug_assert!(self.synthesized || index + 1 == target);
            self.decoder
                .decode(&self.packet, &mut self.samples)
                .map_err(failed)?;
            self.cursor = cursor;
            self.synthesized = true;
            self.stats.decoded_packets += 1;
            let raw = index * 1024;
            let first = raw.max(self.output);
            let last = (raw + 1024).min(self.end);
            if first < last {
                let frames = (last - first) as usize;
                let from = (first - raw) as usize * channels;
                out[..frames * channels]
                    .copy_from_slice(&self.samples[from..from + frames * channels]);
                self.output = last;
                return Ok(frames);
            }
            self.stats.warmup_packets += 1;
        }
        if !self.ended {
            // A checkpoint ahead of playback has already checked its table
            // prefix. This also makes a seek to EOF use any existing index.
            let last = self.index.last().0;
            let cursor = if last.packet() > self.cursor.packet() {
                last
            } else {
                self.cursor
            };
            self.media.check_end(cursor).map_err(ReadError::Source)?;
            self.ended = true;
        }
        Ok(0)
    }
    /// Move the output to valid-audio frame `frame` (up to
    /// [`Playback::frames`]). The decoder continues from where it is when
    /// that is closest, or restores the last checkpoint before the target's
    /// predecessor; the next [`Playback::read`] replays the packets between.
    pub fn seek(&mut self, frame: u64) -> Result<(), ReadError<Error>> {
        if frame > self.frames() {
            return Err(invalid(
                "SQ access",
                "seek position lies outside the valid audio",
            ));
        }
        let output = self.priming + frame;
        let target = output / 1024;
        let next = self.cursor.packet();
        let start = target.saturating_sub(1);
        let continues = output >= self.end
            || (self.synthesized && next == target)
            || (target > 0 && next <= start && self.index.before(start).0.packet() <= next);
        if !continues {
            let (cursor, checkpoint) = self.index.before(start);
            self.decoder
                .restore(checkpoint)
                .map_err(|error| ReadError::Decode {
                    error,
                    packet_index: None,
                })?;
            self.cursor = *cursor;
            self.synthesized = cursor.packet() == 0;
            self.stats.restored_checkpoints += 1;
        }
        self.output = output;
        self.ended = false;
        Ok(())
    }
    /// Advance the index up to `max_packets` packets ahead of its last
    /// checkpoint, keeping checkpoints on the way, without synthesis and
    /// without moving the output. Returns whether the index is complete:
    /// then every packet was scanned and the end of the packet table checked.
    ///
    /// Call it with a large budget before playback starts (for example on a
    /// loader thread before handing the playback to the decoding thread), or
    /// with small budgets when the decoding thread is idle. An error names
    /// the packet that failed; the index stays where it is.
    pub fn extend_index(&mut self, max_packets: u64) -> Result<bool, ReadError<Error>> {
        for _ in 0..max_packets {
            if self.complete {
                break;
            }
            let last = self.index.last();
            if self
                .scanner
                .as_ref()
                .is_none_or(|(_, cursor)| cursor.packet() < last.0.packet())
            {
                let mut decoder = match self.scanner.take() {
                    Some((decoder, _)) => decoder,
                    None => self.decoder.clone(),
                };
                decoder
                    .restore(&last.1)
                    .map_err(|error| ReadError::Decode {
                        error,
                        packet_index: None,
                    })?;
                self.scanner = Some((decoder, last.0));
            }
            let (decoder, cursor) = self.scanner.as_mut().expect("scanner");
            let index = cursor.packet();
            if self.index.due(index) {
                self.index.push(*cursor, decoder.checkpoint());
            }
            let mut next = *cursor;
            if self
                .media
                .read_packet(&mut next, &mut self.packet)
                .map_err(ReadError::Source)?
                .is_none()
            {
                self.complete = true;
                break;
            }
            decoder
                .advance(&self.packet)
                .map_err(|error| ReadError::Decode {
                    error,
                    packet_index: Some(index),
                })?;
            *cursor = next;
            self.stats.indexed_packets += 1;
        }
        Ok(self.complete)
    }
    /// An [`Indexer`] that continues this playback's index from its last
    /// checkpoint, reading packets from `source`: a second handle to the same
    /// input (for example the file opened again), which the indexer opens as
    /// a [`Media`]. Rejected when that input differs from this playback's in
    /// table layout, stream description, cookie, timeline, length or revision
    /// known at opening. As with [`Media`], packet payloads and deferred table
    /// entries are not compared: the caller must supply the same input.
    pub fn indexer<S: Source>(&self, source: S) -> Result<Indexer<S>, ReadError<Error>> {
        let media = Media::open(source).map_err(ReadError::Source)?;
        if !media.same_input(&self.media) {
            return Err(invalid(
                "SQ access",
                "indexer input differs from the playback input",
            ));
        }
        let (cursor, checkpoint) = self.index.last();
        let mut decoder = self.decoder.clone();
        decoder
            .restore(checkpoint)
            .map_err(|error| ReadError::Decode {
                error,
                packet_index: None,
            })?;
        Ok(Indexer {
            playback: self.id,
            media,
            decoder,
            cursor: *cursor,
            last_checkpoint: cursor.packet(),
            interval: self.index.interval,
            packet: Vec::new(),
            batch: IndexBatch {
                playback: self.id,
                start: cursor.packet(),
                end: cursor.packet(),
                entries: Vec::new(),
                complete: false,
            },
            complete: false,
            scanned_packets: 0,
        })
    }
    /// Add the checkpoints of a batch taken from one of this playback's
    /// indexers. Batches must be merged in the order they were taken; the
    /// index keeps the checkpoints it is due, with the same interval and
    /// thinning as checkpoints kept by reading. Returns whether the index is
    /// complete. A batch of another playback, or one that leaves a gap
    /// because an earlier batch was not merged, is rejected and changes
    /// nothing.
    pub fn merge_index(&mut self, batch: IndexBatch) -> Result<bool, ReadError<Error>> {
        if batch.playback != self.id {
            return Err(invalid(
                "SQ access",
                "index batch belongs to a different playback",
            ));
        }
        let due = self.index.entries.len() as u64 * self.index.interval;
        if due < batch.start {
            return Err(invalid(
                "SQ access",
                "index batch does not continue the index",
            ));
        }
        for (cursor, checkpoint) in batch.entries {
            if self.index.due(cursor.packet()) {
                self.index.push(cursor, checkpoint);
            }
        }
        if batch.complete {
            self.complete = true;
        }
        Ok(self.complete)
    }
}

/// Scans a [`Playback`]'s input ahead of it, on any thread, and collects
/// checkpoints for [`Playback::merge_index`].
///
/// An indexer reads its own handle to the input and advances a clone of the
/// playback's decoder without synthesis, from the playback's last checkpoint
/// when it was made. It keeps the checkpoint before every packet at a
/// multiple of the playback's checkpoint interval at that time. Take the
/// checkpoints collected so far with [`Indexer::take_batch`] and hand them to
/// the playback's thread; they accumulate until taken.
///
/// ```no_run
/// # use apac_container::{Media, Playback};
/// # use std::{fs::File, sync::mpsc};
/// # fn main() -> Result<(), Box<dyn std::error::Error>> {
/// let path = "input.m4a";
/// let mut playback = Playback::open(Media::open(File::open(path)?)?)?;
/// let mut indexer = playback.indexer(File::open(path)?)?;
/// let (batches, received) = mpsc::channel();
/// std::thread::spawn(move || {
///     loop {
///         let result = indexer.run(256);
///         if batches.send(indexer.take_batch()).is_err() || !matches!(result, Ok(false)) {
///             break;
///         }
///     }
/// });
/// // On the decoding thread, between reads and seeks:
/// for batch in received.try_iter() {
///     playback.merge_index(batch)?;
/// }
/// # Ok(())
/// # }
/// ```
///
/// A packet that fails stops the indexer there, as in
/// [`Playback::extend_index`]: the error names the packet and the
/// checkpoints before it can still be taken.
pub struct Indexer<S> {
    playback: u64,
    media: Media<S>,
    decoder: Decoder,
    /// The next packet to scan.
    cursor: PacketCursor,
    /// Last checkpoint saved, including those already handed off in a batch.
    last_checkpoint: u64,
    interval: u64,
    packet: Vec<u8>,
    batch: IndexBatch,
    complete: bool,
    scanned_packets: u64,
}
impl<S: Source> Indexer<S> {
    /// Scan up to `max_packets` packets, collecting checkpoints. Returns
    /// whether the end of the packet table was reached, so that every packet
    /// was scanned and the end of the table checked.
    pub fn run(&mut self, max_packets: u64) -> Result<bool, ReadError<Error>> {
        for _ in 0..max_packets {
            if self.complete {
                break;
            }
            let index = self.cursor.packet();
            if index > self.last_checkpoint && index.is_multiple_of(self.interval) {
                self.batch
                    .entries
                    .push((self.cursor, self.decoder.checkpoint()));
                self.last_checkpoint = index;
            }
            let mut next = self.cursor;
            if self
                .media
                .read_packet(&mut next, &mut self.packet)
                .map_err(ReadError::Source)?
                .is_none()
            {
                self.complete = true;
                self.batch.complete = true;
                break;
            }
            self.decoder
                .advance(&self.packet)
                .map_err(|error| ReadError::Decode {
                    error,
                    packet_index: Some(index),
                })?;
            self.cursor = next;
            self.batch.end = next.packet();
            self.scanned_packets += 1;
        }
        Ok(self.complete)
    }
    /// The checkpoints collected since the last batch was taken.
    pub fn take_batch(&mut self) -> IndexBatch {
        let next = IndexBatch {
            playback: self.playback,
            start: self.batch.end,
            end: self.batch.end,
            entries: Vec::new(),
            complete: self.complete,
        };
        std::mem::replace(&mut self.batch, next)
    }
    /// Whether [`Indexer::run`] reached the end of the packet table.
    pub fn is_complete(&self) -> bool {
        self.complete
    }
    /// Packets scanned so far.
    pub fn scanned_packets(&self) -> u64 {
        self.scanned_packets
    }
}

/// Checkpoints an [`Indexer`] collected over a run of packets, for
/// [`Playback::merge_index`] on the playback's thread.
pub struct IndexBatch {
    playback: u64,
    /// The first packet scanned for this batch.
    start: u64,
    /// The packet after the last one scanned.
    end: u64,
    entries: Vec<(PacketCursor, Checkpoint)>,
    /// The indexer reached the end of the packet table.
    complete: bool,
}
impl IndexBatch {
    /// Checkpoints in the batch.
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    /// Whether the batch holds no checkpoint.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    /// The packets scanned for this batch, as a range of packet indices.
    pub fn packets(&self) -> std::ops::Range<u64> {
        self.start..self.end
    }
    /// Whether the indexer had reached the end of the packet table.
    pub fn is_complete(&self) -> bool {
        self.complete
    }
}

#[cfg(test)]
#[path = "playback_tests.rs"]
mod tests;
