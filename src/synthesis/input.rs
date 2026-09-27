//! The codec receives the same packet/timeline contract from both input forms.
use crate::{
    caf::CafReader,
    error::Result,
    model::FileInfo,
    packets::{PacketBundle, ReplayRange},
};
use serde_json::{Value, json};
use std::path::Path;

pub(super) enum Input {
    Bundle(Box<PacketBundle>),
    Caf(Box<CafReader>),
}
impl Input {
    pub(super) fn open(path: &Path) -> Result<Self> {
        if path.is_dir() {
            Ok(Self::Bundle(Box::new(PacketBundle::open(path)?)))
        } else {
            Ok(Self::Caf(Box::new(CafReader::open(path)?)))
        }
    }
    pub(super) fn info(&self) -> &FileInfo {
        match self {
            Self::Bundle(v) => &v.manifest().file,
            Self::Caf(v) => v.info(),
        }
    }
    pub(super) fn cookie(&self) -> &[u8] {
        match self {
            Self::Bundle(v) => v.cookie(),
            Self::Caf(v) => v.cookie(),
        }
    }
    pub(super) fn range(&self, start: Option<u64>, frames: u64) -> Result<ReplayRange> {
        match self {
            Self::Bundle(v) => v.range(start, frames),
            Self::Caf(v) => v.range(start, frames),
        }
    }
    pub(super) fn next_packet(&mut self) -> Result<Option<(u64, u64, Vec<u8>)>> {
        match self {
            Self::Bundle(v) => v
                .next_packet()?
                .map(|(p, b)| Ok((p.packet_index, p.raw_frame()?, b)))
                .transpose(),
            Self::Caf(v) => v.next_packet(),
        }
    }
    pub(super) fn consumed_packets(&self) -> u64 {
        match self {
            Self::Bundle(v) => v.consumed_packets(),
            Self::Caf(v) => v.consumed_packets(),
        }
    }
    pub(super) fn verify_remaining(&mut self) -> Result<()> {
        match self {
            Self::Bundle(v) => v.verify_remaining(),
            Self::Caf(v) => v.verify_remaining(),
        }
    }
    pub(super) fn report(&self) -> Value {
        match self {
            Self::Bundle(_) => json!({"kind":"packet_bundle","verification":"stored_sha256"}),
            Self::Caf(v) => v.report(),
        }
    }
}
