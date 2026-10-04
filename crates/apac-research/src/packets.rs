//! Portable, bounded reading of packet exports. The original source is metadata only.
use crate::{
    config,
    error::{Error, Result},
    model::*,
};
use apac_container::{Packet, PacketSource, Range};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{BufRead, BufReader, Read, Seek, SeekFrom},
    path::{Component, Path},
};

pub use apac_core::frame::MAX_PACKET_BUFFER;
pub const MAX_PREROLL_PACKETS: u64 = 4096;
const MAX_METADATA: u64 = 1024 * 1024;
const MAX_INDEX_LINE: u64 = 65536;

fn invalid(message: impl Into<String>) -> Error {
    Error::new("packet bundle", message)
}
pub fn add(a: u64, b: u64) -> Result<u64> {
    a.checked_add(b)
        .ok_or_else(|| invalid("integer overflow in packet/frame range"))
}
pub fn required<'a, T>(property: &'a Property<T>, context: &str) -> Result<&'a T> {
    if let (Some(value), None) = (&property.value, &property.error) {
        return Ok(value);
    }
    if let Some(error) = &property.error {
        let op = format!("{context}: {}", error.operation);
        return Err(match error.os_status {
            Some(status) => Error::native(op, status),
            None => Error::new(op, &error.message),
        });
    }
    Err(invalid(format!("{context} is unavailable")))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyInfo {
    pub independently_decodable: bool,
    pub preroll_packet_count: u32,
}
#[derive(Debug, Clone)]
pub struct PacketAccess {
    pub packet_index: u64,
    pub raw_frame_position: Property<i64>,
    pub dependency: Property<DependencyInfo>,
    pub roll_distance: Property<i64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PacketRecord {
    pub schema_version: u32,
    pub packet_index: u64,
    pub export_offset: u64,
    pub bytes: u32,
    pub frames: u32,
    pub sha256: String,
    pub raw_frame_position: Property<i64>,
    pub dependency: Property<DependencyInfo>,
    pub roll_distance: Property<i64>,
}
impl PacketRecord {
    pub fn access(&self) -> PacketAccess {
        PacketAccess {
            packet_index: self.packet_index,
            raw_frame_position: self.raw_frame_position.clone(),
            dependency: self.dependency.clone(),
            roll_distance: self.roll_distance.clone(),
        }
    }
    pub fn raw_frame(&self) -> Result<u64> {
        u64::try_from(*required(
            &self.raw_frame_position,
            "packet raw frame position",
        )?)
        .map_err(|_| invalid("negative raw frame position"))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayWindow {
    pub requested_start_packet: u64,
    pub requested_packets: u64,
    pub actual_target_packets: u64,
    pub included_preroll_packets: u64,
    pub target_raw_start: u64,
    pub target_raw_end: u64,
}
#[derive(Debug, Clone, Deserialize)]
pub struct PacketManifest {
    pub schema_version: u32,
    pub complete: bool,
    pub file: FileInfo,
    pub start_packet: u64,
    pub requested_packets: u64,
    pub actual_packets: u64,
    pub packet_data_file: String,
    pub packet_index_file: String,
    pub cookie_file: String,
    pub packet_data_bytes: u64,
    pub packet_data_sha256: String,
    #[serde(default)]
    pub replay_window: Option<ReplayWindow>,
}

/// Apply both meanings of dependency metadata: roll is before the target;
/// preroll is the refresh distance after an independent starting packet.
pub fn check_access(first: &PacketAccess, target: &PacketAccess) -> Result<()> {
    if first.packet_index > target.packet_index {
        return Err(invalid("access point follows target"));
    }
    let distance = target.packet_index - first.packet_index;
    if distance > MAX_PREROLL_PACKETS {
        return Err(invalid("preroll exceeds the 4096-packet research limit"));
    }
    // The stream origin initializes the decoder even if optional random-access queries fail.
    if first.packet_index == 0 {
        return Ok(());
    }
    let roll = u64::try_from(*required(&target.roll_distance, "target roll distance")?)
        .map_err(|_| invalid("negative roll distance"))?;
    let dependency = required(&first.dependency, "starting packet dependency")?;
    if !dependency.independently_decodable
        || roll > distance
        || u64::from(dependency.preroll_packet_count) > distance
    {
        return Err(invalid(
            "insufficient preroll or non-independent starting packet",
        ));
    }
    Ok(())
}

pub fn select_preroll(
    target: u64,
    mut query: impl FnMut(u64) -> Result<PacketAccess>,
    mut previous: impl FnMut(u64) -> Result<i64>,
) -> Result<u64> {
    if target == 0 {
        return Ok(0);
    }
    let access = query(target)?;
    if access.packet_index != target {
        return Err(invalid("incorrect dependency query result"));
    }
    let roll = u64::try_from(*required(&access.roll_distance, "target roll distance")?)
        .map_err(|_| invalid("negative roll distance"))?;
    if roll > MAX_PREROLL_PACKETS || roll > target {
        return Err(invalid("roll distance is out of range"));
    }
    let mut candidate = target - roll;
    loop {
        if target - candidate > MAX_PREROLL_PACKETS {
            return Err(invalid("preroll search exceeds 4096 packets"));
        }
        if candidate == 0 {
            return Ok(0);
        }
        let first = query(candidate)?;
        if first.packet_index != candidate {
            return Err(invalid("incorrect access-point query result"));
        }
        let dependency = required(&first.dependency, "starting packet dependency")?;
        if dependency.independently_decodable
            && u64::from(dependency.preroll_packet_count) <= target - candidate
        {
            check_access(&first, &access)?;
            return Ok(candidate);
        }
        let next = u64::try_from(previous(candidate)?)
            .map_err(|_| invalid("no earlier independent packet"))?;
        if next >= candidate {
            return Err(invalid("independent-packet search made no progress"));
        }
        candidate = next;
    }
}

fn open_member(root: &Path, relative: &str) -> Result<File> {
    let path = Path::new(relative);
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(invalid(
            "bundle members must be relative paths without traversal",
        ));
    }
    let resolved = root.join(path).canonicalize()?;
    if !resolved.starts_with(root) {
        return Err(invalid("bundle member resolves outside the bundle"));
    }
    if !resolved.metadata()?.is_file() {
        return Err(invalid("bundle member is not a regular file"));
    }
    let file = File::open(resolved)?;
    if !file.metadata()?.is_file() {
        return Err(invalid("bundle member is not a regular file"));
    }
    Ok(file)
}
fn read_limited(mut file: File, limit: u64) -> Result<Vec<u8>> {
    if file.metadata()?.len() > limit {
        return Err(invalid("metadata/cookie exceeds size limit"));
    }
    let mut bytes = Vec::new();
    file.by_ref().take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(invalid("metadata/cookie grew beyond size limit"));
    }
    Ok(bytes)
}

#[derive(Debug, Clone, Serialize)]
pub struct ReplayRange {
    pub window_start_frame: u64,
    pub window_end_frame: u64,
    pub start_frame: u64,
    pub requested_frames: u64,
    pub frames: u64,
    pub raw_start: u64,
    pub raw_end: u64,
    pub drain_to_eof: bool,
    pub clipped_by: Option<&'static str>,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct InputPacket {
    pub offset: u32,
    pub bytes: u32,
    pub frames: u32,
}
pub struct PacketBatch {
    pub data: Vec<u8>,
    pub packets: Vec<InputPacket>,
}

/// Holds open bundle files throughout validation and playback; never opens file.source.
pub struct PacketBundle {
    manifest: PacketManifest,
    cookie: Vec<u8>,
    config: config::Config,
    frames_per_packet: Option<u64>,
    data: BufReader<File>,
    index: BufReader<File>,
    line: Vec<u8>,
    next: u64,
    offset: u64,
    next_frame: Option<u64>,
    first: Option<PacketRecord>,
    target: Option<PacketRecord>,
    raw_start: u64,
    raw_end: u64,
    raw_total: u64,
    window_start: u64,
    window_end: u64,
    index_hash: Sha256,
    data_hash: Sha256,
    verified_index_hash: Option<Vec<u8>>,
}

impl PacketBundle {
    pub fn open(directory: &Path) -> Result<Self> {
        let root = directory.canonicalize()?;
        if !root.is_dir() {
            return Err(invalid("requires a complete packet directory"));
        }
        match root.join(".incomplete.json").symlink_metadata() {
            Ok(_) => return Err(invalid("bundle still has an incomplete marker")),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        let manifest: PacketManifest = serde_json::from_slice(&read_limited(
            open_member(&root, "manifest.json")?,
            MAX_METADATA,
        )?)?;
        if manifest.schema_version != SCHEMA_VERSION
            || !manifest.complete
            || manifest.file.schema_version != SCHEMA_VERSION
        {
            return Err(invalid("requires a complete schema-v1 packet manifest"));
        }
        if manifest.actual_packets == 0 || manifest.requested_packets == 0 {
            return Err(invalid("empty packet window"));
        }
        if manifest.start_packet != 0 && manifest.replay_window.is_none() {
            return Err(invalid(
                "nonzero legacy raw window: export again with --with-preroll",
            ));
        }
        let format = &manifest.file.format;
        if format.format_id != u32::from_be_bytes(*b"apac")
            || format.format_fourcc != "apac"
            || !format.sample_rate.is_finite()
            || format.sample_rate <= 0.
            || format.channels == 0
            || format.channels > 1024
        {
            return Err(invalid("invalid Apple APAC stream description"));
        }
        if let Some(layout) = &manifest.file.layout.value {
            if manifest.file.layout.error.is_some()
                || layout.descriptions.len() > 1024
                || layout
                    .descriptions
                    .iter()
                    .any(|d| d.coordinates.iter().any(|v| !v.is_finite()))
            {
                return Err(invalid("invalid channel layout"));
            }
            let n = match layout.tag >> 16 {
                0 => layout.descriptions.len() as u32,
                1 => layout.bitmap.count_ones(),
                _ => layout.tag & 0xffff,
            };
            if n != format.channels {
                return Err(invalid(
                    "layout channel count differs from stream description",
                ));
            }
            let derived = ChannelLayout::tagged(layout.tag, n, None);
            if layout.ambisonic_order != derived.ambisonic_order
                || layout.ambisonic_channel_order != derived.ambisonic_channel_order
                || layout.ambisonic_normalization != derived.ambisonic_normalization
            {
                return Err(invalid("ambisonic labels disagree with layout tag"));
            }
        }
        let packet_count = *required(&manifest.file.packet_count, "source packet count")?;
        let end = add(manifest.start_packet, manifest.actual_packets)?;
        if end > packet_count {
            return Err(invalid("exported packet range exceeds source packet count"));
        }
        let table = required(&manifest.file.packet_table, "source packet table")?;
        if table.priming_frames < 0 || table.valid_frames < 0 || table.remainder_frames < 0 {
            return Err(invalid("negative packet-table frame count"));
        }
        let raw_total = add(
            add(table.priming_frames as u64, table.valid_frames as u64)?,
            table.remainder_frames as u64,
        )?;
        if let Some(window) = &manifest.replay_window {
            if window.actual_target_packets == 0
                || window.requested_packets != manifest.requested_packets
                || window.actual_target_packets > window.requested_packets
                || window.included_preroll_packets > MAX_PREROLL_PACKETS
                || add(manifest.start_packet, window.included_preroll_packets)?
                    != window.requested_start_packet
                || add(window.requested_start_packet, window.actual_target_packets)? != end
            {
                return Err(invalid("inconsistent replay window"));
            }
        } else if manifest.actual_packets > manifest.requested_packets {
            return Err(invalid("raw export contains more packets than requested"));
        }
        let cookie = read_limited(
            open_member(&root, &manifest.cookie_file)?,
            config::MAX_COOKIE_BYTES as u64,
        )?;
        let expected = required(&manifest.file.cookie, "cookie fingerprint")?;
        if cookie.len() != expected.bytes || sha256(&cookie) != expected.sha256 {
            return Err(invalid("cookie length or SHA-256 mismatch"));
        }
        let parsed = config::Config::parse(&cookie)?;
        for (name, value, expected) in [
            (
                "sample_rate_hz",
                parsed.sample_rate_hz(),
                format.sample_rate,
            ),
            ("channels", parsed.channels(), f64::from(format.channels)),
        ] {
            if value.is_some_and(|v| v as f64 != expected) {
                return Err(invalid(format!(
                    "cookie-derived {name} disagrees with manifest"
                )));
            }
        }
        let cookie_frames = parsed.frame_samples();
        if format.frames_per_packet != 0
            && cookie_frames.is_some_and(|v| v != u64::from(format.frames_per_packet))
        {
            return Err(invalid(
                "cookie-derived frame_samples disagrees with manifest",
            ));
        }
        // A zero ASBD duration leaves packet timing unspecified; a known cookie
        // duration still constrains every packet and its absolute frame position.
        let frames_per_packet = cookie_frames.or_else(|| {
            (format.frames_per_packet != 0).then_some(u64::from(format.frames_per_packet))
        });
        if frames_per_packet
            .is_some_and(|frames| packet_count.checked_mul(frames) != Some(raw_total))
        {
            return Err(invalid("packet table and packet duration disagree"));
        }
        let data = open_member(&root, &manifest.packet_data_file)?;
        if data.metadata()?.len() != manifest.packet_data_bytes {
            return Err(invalid("packet data length mismatch"));
        }
        let index = open_member(&root, &manifest.packet_index_file)?;
        let mut bundle = Self {
            manifest,
            cookie,
            config: parsed,
            frames_per_packet,
            data: BufReader::new(data),
            index: BufReader::new(index),
            line: Vec::new(),
            next: 0,
            offset: 0,
            next_frame: None,
            first: None,
            target: None,
            raw_start: 0,
            raw_end: 0,
            raw_total,
            window_start: 0,
            window_end: 0,
            index_hash: Sha256::new(),
            data_hash: Sha256::new(),
            verified_index_hash: None,
        };
        while bundle.next_packet()?.is_some() {}
        bundle.verified_index_hash = Some(bundle.index_hash.clone().finalize().to_vec());
        let first = bundle
            .first
            .as_ref()
            .ok_or_else(|| invalid("missing first packet"))?;
        let target = bundle
            .target
            .as_ref()
            .ok_or_else(|| invalid("missing target packet"))?;
        bundle.raw_start = first.raw_frame()?;
        bundle.raw_end = bundle
            .next_frame
            .ok_or_else(|| invalid("missing raw frame end"))?;
        if let Some(w) = &bundle.manifest.replay_window {
            if target.raw_frame()? != w.target_raw_start || bundle.raw_end != w.target_raw_end {
                return Err(invalid(
                    "replay window disagrees with packet time positions",
                ));
            }
            check_access(&first.access(), &target.access())?;
        }
        let table = required(&bundle.manifest.file.packet_table, "packet table")?;
        let prime = table.priming_frames as u64;
        let end_valid = add(prime, table.valid_frames as u64)?;
        bundle.window_start = target.raw_frame()?.clamp(prime, end_valid) - prime;
        bundle.window_end = bundle.raw_end.clamp(prime, end_valid) - prime;
        bundle.rewind()?;
        Ok(bundle)
    }
    pub fn manifest(&self) -> &PacketManifest {
        &self.manifest
    }
    pub fn cookie(&self) -> &[u8] {
        &self.cookie
    }
    /// The cookie's typed configuration, parsed once when the bundle opened.
    pub fn config(&self) -> &config::Config {
        &self.config
    }
    pub fn raw_start(&self) -> u64 {
        self.raw_start
    }
    pub fn raw_end(&self) -> u64 {
        self.raw_end
    }
    pub fn consumed_packets(&self) -> u64 {
        self.next
    }
    pub fn consumed_frames(&self) -> u64 {
        self.next_frame.unwrap_or(self.raw_start) - self.raw_start
    }
    /// Finish integrity checks without decoding unused packets of a shorter request.
    pub fn verify_remaining(&mut self) -> Result<()> {
        while self.next_packet()?.is_some() {}
        Ok(())
    }

    /// Read one hash-checked packet, retaining its original index and timeline.
    /// Call `verify_remaining` before trusting a result obtained from a subset.
    pub fn next_packet(&mut self) -> Result<Option<(PacketRecord, Vec<u8>)>> {
        let index = (self.next < self.manifest.actual_packets)
            .then(|| self.manifest.start_packet.checked_add(self.next))
            .flatten();
        self.read_packet().map_err(|mut error| {
            error.packet_index = index;
            error
        })
    }

    pub fn range(&self, start: Option<u64>, requested: u64) -> Result<ReplayRange> {
        Ok(PacketSource::range(self, start, requested)?.into())
    }

    fn rewind(&mut self) -> Result<()> {
        self.data.seek(SeekFrom::Start(0))?;
        self.index.seek(SeekFrom::Start(0))?;
        self.next = 0;
        self.offset = 0;
        self.next_frame = None;
        self.index_hash = Sha256::new();
        self.data_hash = Sha256::new();
        Ok(())
    }
    fn read_packet(&mut self) -> Result<Option<(PacketRecord, Vec<u8>)>> {
        self.line.clear();
        let size = self
            .index
            .by_ref()
            .take(MAX_INDEX_LINE + 1)
            .read_until(b'\n', &mut self.line)?;
        if size as u64 > MAX_INDEX_LINE {
            return Err(invalid("packet index line exceeds 64 KiB"));
        }
        if size == 0 {
            if self.next != self.manifest.actual_packets
                || self.offset != self.manifest.packet_data_bytes
            {
                return Err(invalid("truncated packet index or data size mismatch"));
            }
            if format!("{:x}", self.data_hash.clone().finalize())
                != self.manifest.packet_data_sha256
            {
                return Err(invalid("packet data SHA-256 mismatch"));
            }
            if let Some(expected) = &self.verified_index_hash
                && self.index_hash.clone().finalize().as_slice() != expected.as_slice()
            {
                return Err(invalid("packet index changed after validation"));
            }
            let mut byte = [0];
            if self.data.read(&mut byte)? != 0 {
                return Err(invalid("trailing packet data"));
            }
            return Ok(None);
        }
        self.index_hash.update(&self.line);
        let record: PacketRecord = serde_json::from_slice(&self.line)?;
        let source_packet = add(self.manifest.start_packet, self.next)?;
        if self.next >= self.manifest.actual_packets
            || record.schema_version != SCHEMA_VERSION
            || record.packet_index != source_packet
            || record.export_offset != self.offset
            || record.bytes == 0
            || record.bytes as usize > MAX_PACKET_BUFFER
            || record.frames == 0
        {
            return Err(invalid(
                "invalid packet sequence, byte range, or frame count",
            ));
        }
        let format = &self.manifest.file.format;
        if self
            .frames_per_packet
            .is_some_and(|frames| u64::from(record.frames) != frames)
            || (format.bytes_per_packet != 0 && record.bytes != format.bytes_per_packet)
        {
            return Err(invalid(
                "packet description disagrees with stream description or cookie",
            ));
        }
        let raw = record.raw_frame()?;
        if self.verified_index_hash.is_some() && self.next == 0 && raw != self.raw_start {
            return Err(invalid("first packet frame changed after validation"));
        }
        if self.next_frame.is_some_and(|v| v != raw)
            || (source_packet == 0 && raw != 0)
            || self
                .frames_per_packet
                .is_some_and(|frames| source_packet.checked_mul(frames) != Some(raw))
        {
            return Err(invalid("packet frame timeline has a gap or overlap"));
        }
        let end = add(raw, u64::from(record.frames))?;
        let source_packets = *required(&self.manifest.file.packet_count, "source packet count")?;
        if end > self.raw_total || (source_packet + 1 == source_packets && end != self.raw_total) {
            return Err(invalid("packet timeline disagrees with packet table"));
        }
        let end_byte = add(self.offset, u64::from(record.bytes))?;
        if end_byte > self.manifest.packet_data_bytes {
            return Err(invalid("packet extends beyond data file"));
        }
        let mut bytes = vec![0; record.bytes as usize];
        self.data.read_exact(&mut bytes)?;
        if sha256(&bytes) != record.sha256 {
            return Err(invalid(format!(
                "SHA-256 mismatch for packet {source_packet}"
            )));
        }
        self.data_hash.update(&bytes);
        if self.next == 0 {
            self.first = Some(record.clone());
        }
        let target = self
            .manifest
            .replay_window
            .as_ref()
            .map_or(0, |w| w.requested_start_packet);
        if source_packet == target {
            self.target = Some(record.clone());
        }
        self.next += 1;
        self.offset = end_byte;
        self.next_frame = Some(end);
        Ok(Some((record, bytes)))
    }
    pub fn next_batch(&mut self, wanted: u32) -> Result<PacketBatch> {
        if !(1..=64).contains(&wanted) {
            return Err(invalid("input batch must contain 1..64 packets"));
        }
        let mut batch = PacketBatch {
            data: Vec::new(),
            packets: Vec::new(),
        };
        // A batch has at most 16 MiB. Peek the next record before consuming data.
        for _ in 0..wanted {
            let index_position = self.index.stream_position()?;
            let data_position = self.data.stream_position()?;
            let old_hash = self.index_hash.clone();
            let old_data_hash = self.data_hash.clone();
            let old_frame = self.next_frame;
            let old_offset = self.offset;
            let old_next = self.next;
            let Some((packet, data)) = self.next_packet()? else {
                break;
            };
            if batch.data.len() + data.len() > MAX_PACKET_BUFFER {
                self.index.seek(SeekFrom::Start(index_position))?;
                self.data.seek(SeekFrom::Start(data_position))?;
                self.index_hash = old_hash;
                self.data_hash = old_data_hash;
                self.next_frame = old_frame;
                self.offset = old_offset;
                self.next = old_next;
                break;
            }
            batch.packets.push(InputPacket {
                offset: batch.data.len() as u32,
                bytes: packet.bytes,
                frames: packet.frames,
            });
            batch.data.extend_from_slice(&data);
        }
        Ok(batch)
    }
}

/// Common valid-audio cropping; input access/dependency policy belongs to the reader.
impl From<Range> for ReplayRange {
    fn from(range: Range) -> Self {
        Self {
            window_start_frame: range.window_start_frame,
            window_end_frame: range.window_end_frame,
            start_frame: range.start_frame,
            requested_frames: range.requested_frames,
            frames: range.frames,
            raw_start: range.raw_start,
            raw_end: range.raw_end,
            drain_to_eof: range.drain_to_eof,
            clipped_by: range.clipped_by,
        }
    }
}
impl From<&PacketTable> for apac_container::PacketTable {
    fn from(table: &PacketTable) -> Self {
        Self {
            valid_frames: table.valid_frames,
            priming_frames: table.priming_frames,
            remainder_frames: table.remainder_frames,
        }
    }
}

/// A bundle's pass covers its exported packets from `start_packet`; the
/// target window is the valid audio of its replay window.
impl PacketSource for PacketBundle {
    type Error = Error;
    fn config(&self) -> &config::Config {
        &self.config
    }
    fn cookie(&self) -> &[u8] {
        &self.cookie
    }
    fn channels(&self) -> u32 {
        self.manifest.file.format.channels
    }
    fn layout(&self) -> Option<&ChannelLayout> {
        self.manifest.file.layout.value.as_ref()
    }
    fn table(&self) -> Option<apac_container::PacketTable> {
        self.manifest
            .file
            .packet_table
            .value
            .as_ref()
            .map(Into::into)
    }
    fn range(&self, start: Option<u64>, requested: u64) -> Result<Range> {
        let table = required(&self.manifest.file.packet_table, "packet table")?;
        Ok(Range::new(
            (self.window_start, self.window_end),
            &table.into(),
            start,
            requested,
        )?)
    }
    fn first_packet_index(&self) -> u64 {
        self.manifest.start_packet
    }
    fn supports_fast_access(&self) -> bool {
        false
    }
    fn next_packet(&mut self) -> Result<Option<Packet>> {
        PacketBundle::next_packet(self)?
            .map(|(record, bytes)| {
                Ok(Packet {
                    index: record.packet_index,
                    raw_frame: record.raw_frame()?,
                    bytes,
                })
            })
            .transpose()
    }
    fn rewind(&mut self) -> Result<()> {
        PacketBundle::rewind(self)
    }
    fn consumed_packets(&self) -> u64 {
        self.next
    }
    fn verify_remaining(&mut self) -> Result<()> {
        PacketBundle::verify_remaining(self)
    }
}
