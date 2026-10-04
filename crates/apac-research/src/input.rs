//! The codec receives the same packet/timeline contract from all input forms.
use crate::{
    error::{Error, FilePosition, Result},
    model::*,
    packets::{PacketBundle, ReplayRange},
};
use apac_container::{CafReader, Mp4Reader, Track};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs::File, io::Read, path::Path, time::SystemTime};

const CAF_PROFILE: &str = "apac-caf-input-v1";
const MP4_PROFILE: &str = "apac-mp4-input-v1";

pub(crate) enum Input {
    Bundle(Box<PacketBundle>),
    Caf(Box<CafInput>),
    Mp4(Box<Mp4Input>),
}

/// Open a container file, rejecting anything but a regular file as the
/// container's own input error at offset zero.
fn open_regular(path: &Path, operation: &str, tag: &str) -> Result<File> {
    let regular = || {
        let mut error = Error::new(operation, "requires a regular file");
        error.file_position = Some(Box::new(FilePosition {
            byte_offset: 0,
            chunk_type: tag.into(),
        }));
        error
    };
    if !path.metadata()?.is_file() {
        return Err(regular());
    }
    let file = File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err(regular());
    }
    Ok(file)
}

pub(crate) struct CafInput {
    reader: CafReader<File>,
    info: FileInfo,
}
impl CafInput {
    fn open(path: &Path) -> Result<Self> {
        let reader = CafReader::new(open_regular(path, "CAF input", "caff")?)?;
        let info = file_info(path, "caff", reader.track());
        Ok(Self { reader, info })
    }
    fn report(&self) -> Value {
        let s = self.reader.summary();
        let info = &self.info;
        json!({"kind":"caf","profile":if info.format.channels == 2 {CAF_PROFILE} else {"apac-caf-input-v2"},"format":info.format,"packet_table":info.packet_table.value,"packet_count":info.packet_count.value,
            "file_bytes":s.file_bytes,"layout_source":s.layout_source,"layout":info.layout.value,
            "edit_count":s.edit_count,"chunks":s.chunks.iter().map(|(k,v)|(String::from_utf8_lossy(k).into_owned(),json!({"offset":v.offset,"bytes":v.bytes}))).collect::<BTreeMap<_,_>>(),
            "skipped_chunks":s.skipped_chunks,"metadata_sha256":s.metadata_sha256,"cookie_sha256":sha256(&self.reader.track().cookie),"audio_sha256":s.audio_sha256,"packets_sha256":s.packets_sha256,
            "consistency_verified":s.verified,"verification":"two_pass_read_consistency_no_stored_checksums","access":"sequential_from_packet_zero"})
    }
}

pub(crate) struct Mp4Input {
    reader: Mp4Reader<File>,
    info: FileInfo,
}
impl Mp4Input {
    fn open(path: &Path) -> Result<Self> {
        let reader = Mp4Reader::new(open_regular(path, "MP4 input", "ftyp")?)?;
        let info = file_info(path, "mp4f", reader.track());
        Ok(Self { reader, info })
    }
    fn report(&self) -> Value {
        let s = self.reader.summary();
        let info = &self.info;
        let ranges = s
            .boxes
            .iter()
            .map(|(k, v)| {
                (
                    String::from_utf8_lossy(k).into_owned(),
                    json!({"offset":v.offset,"data_offset":v.data_offset,"bytes":v.end-v.offset}),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let mut report = json!({"kind":"mp4","profile":MP4_PROFILE,"brands":{"major":s.brands.major,"minor_version":s.brands.minor_version,"compatible":s.brands.compatible},"track_id":s.track_id,
            "sample_entry":{"version":0,"channelcount":2,"samplesize":16,"sample_rate":f64::from(s.sample_entry_rate)},
            "format":info.format,"layout_source":"cookie","layout":info.layout.value,
            "packet_count":info.packet_count.value,"packet_table":info.packet_table.value,
            "timeline":{"source":"single_elst","movie_timescale":s.movie_timescale,"media_timescale":info.format.sample_rate as u32,"edit_duration":s.edit_duration,"rounding":"exact_integral_frames"},
            "file_bytes":s.file_bytes,"boxes":ranges,"mdat_count":s.mdat_count,"skipped_boxes":s.skipped_boxes,
            "sample_group_box_counts":{"sgpd":s.sgpd_count,"sbgp":s.sbgp_count},
            "metadata_sha256":s.metadata_sha256,"cookie_sha256":sha256(&self.reader.track().cookie),"audio_sha256":s.audio_sha256,"packets_sha256":s.packets_sha256,
            "access":"sequential_from_packet_zero","consistency_verified":s.verified,"verification":"two_pass_read_consistency_no_stored_checksums"});
        if s.sample_entry_rate == 0 {
            report["sample_entry"]["sample_rate_source"] = json!("cookie");
            report["sample_entry"]["sample_rate_profile"] = json!("apac-mp4-cookie-sample-rate-v1");
        }
        report
    }
}

/// The file description of a validated container track.
fn file_info(path: &Path, container: &str, track: &Track) -> FileInfo {
    FileInfo {
        schema_version: SCHEMA_VERSION,
        source: path.to_owned(),
        file_bytes: track.file_bytes,
        modified_unix_seconds: track
            .revision
            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
            .map(|d| d.as_secs()),
        environment: Environment::current(),
        container: Property::known(container.into()),
        format: AudioFormat {
            sample_rate: track.sample_rate,
            format_id: u32::from_be_bytes(*b"apac"),
            format_fourcc: "apac".into(),
            flags: 0,
            bytes_per_packet: 0,
            frames_per_packet: 1024,
            bytes_per_frame: 0,
            channels: track.channels,
            bits_per_channel: 0,
        },
        layout: Property::known(track.layout.clone()),
        packet_count: Property::known(track.packet_count),
        max_packet_bytes: Property::known(track.max_packet_bytes),
        packet_table: Property::known(PacketTable {
            valid_frames: track.table.valid_frames,
            priming_frames: track.table.priming_frames,
            remainder_frames: track.table.remainder_frames,
        }),
        cookie: Property::known(CookieInfo {
            bytes: track.cookie.len(),
            sha256: sha256(&track.cookie),
        }),
        restricts_random_access: Property {
            value: None,
            error: None,
        },
    }
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
                Ok(Self::Caf(Box::new(CafInput::open(path)?)))
            } else {
                Ok(Self::Mp4(Box::new(Mp4Input::open(path)?)))
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
            Self::Caf(v) => &v.info,
            Self::Mp4(v) => &v.info,
        }
    }
    pub(super) fn config(&self) -> &crate::config::Config {
        match self {
            Self::Bundle(v) => v.config(),
            Self::Caf(v) => &v.reader.track().config,
            Self::Mp4(v) => &v.reader.track().config,
        }
    }
    pub(super) fn cookie(&self) -> &[u8] {
        match self {
            Self::Bundle(v) => v.cookie(),
            Self::Caf(v) => &v.reader.track().cookie,
            Self::Mp4(v) => &v.reader.track().cookie,
        }
    }
    pub(super) fn range(&self, start: Option<u64>, frames: u64) -> Result<ReplayRange> {
        match self {
            Self::Bundle(v) => v.range(start, frames),
            Self::Caf(_) | Self::Mp4(_) => {
                let table = self.info().packet_table.value.as_ref().unwrap();
                crate::packets::frame_range(0, table.valid_frames as u64, table, start, frames)
            }
        }
    }
    pub(super) fn next_packet(&mut self) -> Result<Option<(u64, u64, Vec<u8>)>> {
        match self {
            Self::Bundle(v) => v
                .next_packet()?
                .map(|(p, b)| Ok((p.packet_index, p.raw_frame()?, b)))
                .transpose(),
            Self::Caf(v) => Ok(v
                .reader
                .next_packet()?
                .map(|p| (p.index, p.raw_frame, p.bytes))),
            Self::Mp4(v) => Ok(v
                .reader
                .next_packet()?
                .map(|p| (p.index, p.raw_frame, p.bytes))),
        }
    }
    pub(super) fn consumed_packets(&self) -> u64 {
        match self {
            Self::Bundle(v) => v.consumed_packets(),
            Self::Caf(v) => v.reader.consumed_packets(),
            Self::Mp4(v) => v.reader.consumed_packets(),
        }
    }
    pub(super) fn verify_remaining(&mut self) -> Result<()> {
        match self {
            Self::Bundle(v) => v.verify_remaining(),
            Self::Caf(v) => Ok(v.reader.verify_remaining()?),
            Self::Mp4(v) => Ok(v.reader.verify_remaining()?),
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

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
        let mut out = tag.to_vec();
        out.extend((payload.len() as i64).to_be_bytes());
        out.extend(payload);
        out
    }
    /// Two stereo packets: 1900 valid frames after 100 priming, 48 remainder.
    fn caf() -> Vec<u8> {
        let mut out = b"caff\0\x01\0\0".to_vec();
        let mut desc = 48000f64.to_be_bytes().to_vec();
        for n in [u32::from_be_bytes(*b"apac"), 0, 0, 1024, 2, 0] {
            desc.extend(n.to_be_bytes());
        }
        out.extend(chunk(b"desc", &desc));
        out.extend(chunk(
            b"kuki",
            &[
                0, 0, 0, 26, 100, 97, 112, 97, 0, 0, 0, 0, 8, 0, 124, 1, 128, 4, 4, 32, 0, 18, 0,
                202, 0, 0,
            ],
        ));
        let mut pakt = 2i64.to_be_bytes().to_vec();
        pakt.extend(1900i64.to_be_bytes());
        pakt.extend(100i32.to_be_bytes());
        pakt.extend(48i32.to_be_bytes());
        pakt.extend([1, 2]);
        out.extend(chunk(b"pakt", &pakt));
        out.extend(chunk(b"data", &[0, 0, 0, 7, 20, 30, 40]));
        out
    }

    #[test]
    fn caf_input_reports_its_structure_range_and_verification() {
        let path = std::env::temp_dir().join(format!("apac-input-caf-{}", std::process::id()));
        std::fs::write(&path, caf()).unwrap();
        let result = (|| {
            let mut input = Input::open(&path)?;
            let range = input.range(Some(1850), 100)?;
            assert_eq!((range.frames, range.raw_start), (50, 1950));
            assert_eq!(input.info().max_packet_bytes.value, Some(2));
            assert_eq!(input.report()["consistency_verified"], false);
            input.verify_remaining()?;
            Ok::<_, Error>(input.report())
        })();
        std::fs::remove_file(&path).unwrap();
        let report = result.unwrap();
        let keys: Vec<_> = report.as_object().unwrap().keys().cloned().collect();
        assert_eq!(
            keys,
            [
                "access",
                "audio_sha256",
                "chunks",
                "consistency_verified",
                "cookie_sha256",
                "edit_count",
                "file_bytes",
                "format",
                "kind",
                "layout",
                "layout_source",
                "metadata_sha256",
                "packet_count",
                "packet_table",
                "packets_sha256",
                "profile",
                "skipped_chunks",
                "verification"
            ]
        );
        assert_eq!(report["profile"], CAF_PROFILE);
        assert_eq!(report["consistency_verified"], true);
        assert_eq!(report["edit_count"], 7);
        assert_eq!(report["chunks"]["data"], json!({"offset": 140, "bytes": 7}));
        assert_eq!(report["audio_sha256"], sha256(&[20, 30, 40]));
    }
}
