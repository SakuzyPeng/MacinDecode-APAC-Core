//! The codec receives the same packet/timeline contract from all input forms.
use crate::{
    caf::CafReader,
    error::Result,
    model::FileInfo,
    mp4::Mp4Reader,
    packets::{PacketBundle, ReplayRange},
};
use serde_json::{Value, json};
use std::{fs::File, io::Read, path::Path};

pub(crate) enum Input {
    Bundle(Box<PacketBundle>),
    Caf(Box<CafReader>),
    Mp4(Box<Mp4Reader>),
}
impl Input {
    pub(super) fn open(path: &Path) -> Result<Self> {
        if path.is_dir() {
            Ok(Self::Bundle(Box::new(PacketBundle::open(path)?)))
        } else {
            if !path.metadata()?.is_file() {
                return Err(crate::error::Error::new(
                    "SQ input",
                    "requires a packet directory or regular CAF/MP4 file",
                ));
            }
            let mut magic = [0; 4];
            let n = File::open(path)?.read(&mut magic)?;
            if n == 4 && magic == *b"caff" {
                Ok(Self::Caf(Box::new(CafReader::open(path)?)))
            } else {
                Ok(Self::Mp4(Box::new(Mp4Reader::open(path)?)))
            }
        }
    }
    pub(super) fn is_container(&self) -> bool {
        !matches!(self, Self::Bundle(_))
    }
    pub(super) fn first_packet_index(&self) -> u64 {
        match self {
            Self::Bundle(v) => v.manifest().start_packet,
            Self::Caf(_) | Self::Mp4(_) => 0,
        }
    }
    pub(super) fn info(&self) -> &FileInfo {
        match self {
            Self::Bundle(v) => &v.manifest().file,
            Self::Caf(v) => v.info(),
            Self::Mp4(v) => v.info(),
        }
    }
    pub(super) fn cookie(&self) -> &[u8] {
        match self {
            Self::Bundle(v) => v.cookie(),
            Self::Caf(v) => v.cookie(),
            Self::Mp4(v) => v.cookie(),
        }
    }
    pub(super) fn range(&self, start: Option<u64>, frames: u64) -> Result<ReplayRange> {
        match self {
            Self::Bundle(v) => v.range(start, frames),
            Self::Caf(v) => v.range(start, frames),
            Self::Mp4(v) => v.range(start, frames),
        }
    }
    pub(super) fn next_packet(&mut self) -> Result<Option<(u64, u64, Vec<u8>)>> {
        match self {
            Self::Bundle(v) => v
                .next_packet()?
                .map(|(p, b)| Ok((p.packet_index, p.raw_frame()?, b)))
                .transpose(),
            Self::Caf(v) => v.next_packet(),
            Self::Mp4(v) => v.next_packet(),
        }
    }
    pub(super) fn consumed_packets(&self) -> u64 {
        match self {
            Self::Bundle(v) => v.consumed_packets(),
            Self::Caf(v) => v.consumed_packets(),
            Self::Mp4(v) => v.consumed_packets(),
        }
    }
    pub(super) fn verify_remaining(&mut self) -> Result<()> {
        match self {
            Self::Bundle(v) => v.verify_remaining(),
            Self::Caf(v) => v.verify_remaining(),
            Self::Mp4(v) => v.verify_remaining(),
        }
    }
    pub(super) fn report(&self) -> Value {
        match self {
            Self::Bundle(_) => json!({"kind":"packet_bundle","verification":"stored_sha256"}),
            Self::Caf(v) => v.report(),
            Self::Mp4(v) => v.report(),
        }
    }
}
