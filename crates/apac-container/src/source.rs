//! A uniform packet stream over containers and other packet stores.
use crate::{CafReader, Error, Mp4Reader, Packet, PacketTable, Range, Source};
use apac_core::{Config, model::ChannelLayout};

/// A sequential packet stream with its stream description. A pass starts at
/// the first packet; implementations verify a pass when it reaches the end.
pub trait PacketSource {
    type Error;
    fn config(&self) -> &Config;
    fn cookie(&self) -> &[u8];
    /// The channel count the source declares.
    fn channels(&self) -> u32;
    /// The channel layout the source declares, if it has one.
    fn layout(&self) -> Option<&ChannelLayout>;
    fn table(&self) -> Option<PacketTable>;
    /// `requested` frames from `start` inside the source's target window.
    fn range(&self, start: Option<u64>, requested: u64) -> Result<Range, Self::Error>;
    /// Source index of the first packet of a pass.
    fn first_packet_index(&self) -> u64;
    /// Whether the source holds the whole stream, so a prefix may be scanned
    /// with state-only access.
    fn supports_fast_access(&self) -> bool;
    fn next_packet(&mut self) -> Result<Option<Packet>, Self::Error>;
    /// Start a new pass at the first packet.
    fn rewind(&mut self) -> Result<(), Self::Error>;
    /// Packets read in the current pass.
    fn consumed_packets(&self) -> u64;
    /// Read the rest of the current pass, completing its verification.
    fn verify_remaining(&mut self) -> Result<(), Self::Error>;
}

macro_rules! container_source {
    ($reader:ident, $rewind:expr) => {
        impl<R: Source> PacketSource for $reader<R> {
            type Error = Error;
            fn config(&self) -> &Config {
                &self.track().config
            }
            fn cookie(&self) -> &[u8] {
                &self.track().cookie
            }
            fn channels(&self) -> u32 {
                self.track().channels
            }
            fn layout(&self) -> Option<&ChannelLayout> {
                Some(&self.track().layout)
            }
            fn table(&self) -> Option<PacketTable> {
                Some(self.track().table)
            }
            fn range(&self, start: Option<u64>, requested: u64) -> Result<Range, Error> {
                let table = self.track().table;
                Range::new((0, table.valid_frames as u64), &table, start, requested)
            }
            fn first_packet_index(&self) -> u64 {
                0
            }
            fn supports_fast_access(&self) -> bool {
                true
            }
            fn next_packet(&mut self) -> Result<Option<Packet>, Error> {
                $reader::next_packet(self)
            }
            fn rewind(&mut self) -> Result<(), Error> {
                $rewind(self)
            }
            fn consumed_packets(&self) -> u64 {
                $reader::consumed_packets(self)
            }
            fn verify_remaining(&mut self) -> Result<(), Error> {
                $reader::verify_remaining(self)
            }
        }
    };
}
container_source!(CafReader, |r: &mut CafReader<R>| {
    r.rewind();
    Ok(())
});
container_source!(Mp4Reader, |r: &mut Mp4Reader<R>| r.rewind());
