//! Destination ownership for the shared decode loop.
use crate::{
    error::{Error, Result},
    model::PcmInfo,
    output::{Budget, LimitedWriter, OutputDir, pcm_bytes, pcm_to_le},
};
use apac_container::{PcmFormat, PcmSpec, PcmWritePlan, PcmWriteResult, PcmWriter};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
};
use tempfile::NamedTempFile;

pub(super) enum Export {
    Bundle {
        directory: OutputDir,
        writer: LimitedWriter,
        hash: Sha256,
    },
    File {
        destination: PathBuf,
        writer: Box<PcmWriter<BufWriter<NamedTempFile>>>,
    },
}
pub(super) enum FinishedExport {
    Bundle {
        directory: OutputDir,
        pcm_sha256: String,
    },
    File(Box<FinishedFile>),
}
pub(super) struct FinishedFile {
    destination: PathBuf,
    file: NamedTempFile,
    result: PcmWriteResult,
}
impl Export {
    pub(super) fn create(
        destination: &Path,
        spec: PcmSpec,
        format: Option<PcmFormat>,
        limit: u64,
    ) -> Result<Self> {
        if let Some(format) = format {
            // A bad layout or impossible size must not create any output.
            let plan = PcmWritePlan::new(format, spec)?;
            Budget::new(limit).ensure(plan.file_bytes())?;
            match fs::symlink_metadata(destination) {
                Ok(_) => {
                    return Err(Error::new(
                        "PCM output",
                        "destination already exists (refusing overwrite)",
                    ));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(error) => return Err(error.into()),
            }
            let parent = destination
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or_else(|| Path::new("."));
            fs::create_dir_all(parent)?;
            let file = tempfile::Builder::new()
                .prefix(".apac-")
                .tempfile_in(parent)?;
            let writer = PcmWriter::new(BufWriter::with_capacity(65536, file), plan)?;
            Ok(Self::File {
                destination: destination.to_owned(),
                writer: Box::new(writer),
            })
        } else {
            let directory = OutputDir::create(destination, Budget::new(limit))?;
            directory.budget.ensure(
                pcm_bytes(spec.frames, spec.channels)?
                    .checked_add(65536)
                    .ok_or_else(|| Error::new("SQ decoder", "output size overflow"))?,
            )?;
            let writer = directory.writer("pcm.f32le")?;
            Ok(Self::Bundle {
                directory,
                writer,
                hash: Sha256::new(),
            })
        }
    }
    pub(super) fn write_samples(&mut self, samples: &[f32]) -> Result<()> {
        match self {
            Self::Bundle { writer, hash, .. } => {
                let bytes = pcm_to_le(samples)?;
                writer.write_all(&bytes)?;
                hash.update(&bytes);
                Ok(())
            }
            Self::File { writer, .. } => Ok(writer.write_samples(samples)?),
        }
    }
    pub(super) fn finish(self) -> Result<FinishedExport> {
        match self {
            Self::Bundle {
                directory,
                writer,
                hash,
            } => {
                writer.finish()?;
                Ok(FinishedExport::Bundle {
                    directory,
                    pcm_sha256: format!("{:x}", hash.finalize()),
                })
            }
            Self::File {
                destination,
                writer,
            } => {
                let (writer, result) = writer.finish()?;
                let file = writer.into_inner().map_err(|e| e.into_error())?;
                if file.as_file().metadata()?.len() != result.plan.file_bytes() {
                    return Err(Error::new(
                        "PCM output",
                        "file length differs from the completed container",
                    ));
                }
                Ok(FinishedExport::File(Box::new(FinishedFile {
                    destination,
                    file,
                    result,
                })))
            }
        }
    }
}
impl FinishedExport {
    pub(super) fn pcm_sha256(&self) -> &str {
        match self {
            Self::Bundle { pcm_sha256, .. } => pcm_sha256,
            Self::File(file) => &file.result.pcm_sha256,
        }
    }
    pub(super) fn complete(self, report: &mut Value, pcm: &PcmInfo) -> Result<()> {
        match self {
            Self::Bundle { directory, .. } => {
                report["pcm"] = json!(pcm);
                directory.json("pcm.json", pcm)?;
                directory.json("decode-sq.json", report)?;
                directory.complete()
            }
            Self::File(file) => {
                let plan = &file.result.plan;
                let spec = plan.spec();
                report["output"] = json!({
                    "file": file.destination,
                    "container": plan.format().as_str(),
                    "encoding": "f32le", "interleaved": true,
                    "sample_rate": spec.sample_rate, "channels": spec.channels, "frames": spec.frames,
                    "start_frame": pcm.start_frame,
                    "pcm_bytes": plan.audio_bytes(), "pcm_sha256": file.result.pcm_sha256,
                    "file_bytes": plan.file_bytes(), "file_sha256": file.result.file_sha256,
                    "source_layout": spec.layout, "channel_mask": plan.channel_mask(),
                    "source_channel_indices": plan.source_channel_indices(), "all_finite": true,
                });
                report["decoder_settings"] = json!(pcm.decoder_settings);
                report["environment"] = json!(pcm.environment);
                // The final path only becomes visible after full input verification,
                // a complete output and successful flush/sync. Never replace a path
                // created by somebody else while we decoded.
                file.commit()
            }
        }
    }
}

impl FinishedFile {
    fn commit(self) -> Result<()> {
        self.file.as_file().sync_all()?;
        self.file
            .persist_noclobber(&self.destination)
            .map_err(|e| Error::io("commit PCM file (refusing overwrite)", e.error))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ChannelLayout;

    fn export(path: &Path) -> Export {
        Export::create(
            path,
            PcmSpec {
                sample_rate: 48000,
                channels: 2,
                frames: 1,
                layout: ChannelLayout::discrete(2).unwrap(),
            },
            Some(PcmFormat::Wav),
            u64::MAX,
        )
        .unwrap()
    }

    #[test]
    fn unfinished_or_abandoned_exports_are_removed_without_publishing() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("result.wav");
        {
            let mut writer = export(&path);
            assert!(!path.exists());
            assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
            writer.write_samples(&[1., -1.]).unwrap();
            let finished = writer.finish().unwrap();
            assert!(!path.exists());
            drop(finished);
        }
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
        let writer = export(&path);
        assert!(writer.finish().is_err());
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn committing_checks_for_a_destination_created_during_decoding() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("result.wav");
        let mut writer = export(&path);
        writer.write_samples(&[1., -1.]).unwrap();
        let FinishedExport::File(finished) = writer.finish().unwrap() else {
            panic!("file export");
        };
        fs::write(&path, b"other writer owns this").unwrap();
        assert!(finished.commit().is_err());
        assert_eq!(fs::read(&path).unwrap(), b"other writer owns this");
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }
}
