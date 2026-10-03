use crate::error::{Error, Result};
use serde::Serialize;
use std::{
    cell::Cell,
    fs::{self, File, OpenOptions},
    io::{self, BufWriter, Write},
    path::{Path, PathBuf},
    rc::Rc,
};

#[derive(Clone)]
pub struct Budget {
    limit: u64,
    used: Rc<Cell<u64>>,
}
impl Budget {
    pub fn new(limit: u64) -> Self {
        Self {
            limit,
            used: Rc::new(Cell::new(0)),
        }
    }
    pub fn remaining(&self) -> u64 {
        self.limit.saturating_sub(self.used.get())
    }
    pub fn used(&self) -> u64 {
        self.used.get()
    }
    pub fn ensure(&self, bytes: u64) -> Result<()> {
        if bytes > self.remaining() {
            return Err(Error::new(
                "output limit",
                format!(
                    "requires {bytes} bytes; {} bytes remain within the configured quota",
                    self.remaining()
                ),
            ));
        }
        Ok(())
    }
    pub fn charge(&self, bytes: u64) -> Result<()> {
        self.ensure(bytes)?;
        self.used.set(self.used.get() + bytes);
        Ok(())
    }
}

pub struct LimitedWriter {
    file: BufWriter<File>,
    budget: Budget,
}
impl LimitedWriter {
    pub fn create(path: &Path, budget: Budget) -> Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|e| Error::io(format!("create {} (refusing overwrite)", path.display()), e))?;
        Ok(Self {
            file: BufWriter::with_capacity(65536, file),
            budget,
        })
    }
    pub fn finish(mut self) -> Result<()> {
        self.file.flush()?;
        Ok(())
    }
}
impl Write for LimitedWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.budget
            .ensure(buf.len() as u64)
            .map_err(io::Error::other)?;
        let n = self.file.write(buf)?;
        self.budget.charge(n as u64).map_err(io::Error::other)?;
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.file.flush()
    }
}

/// A directory is complete only after its marker has been removed successfully.
pub struct OutputDir {
    pub root: PathBuf,
    pub budget: Budget,
}
impl OutputDir {
    pub fn create(path: &Path, budget: Budget) -> Result<Self> {
        budget.ensure(128)?;
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent)?;
        }
        fs::create_dir(path).map_err(|e| {
            Error::io(
                format!("create {} (requires a new directory)", path.display()),
                e,
            )
        })?;
        let result = Self {
            root: path.to_owned(),
            budget,
        };
        result.json(".incomplete.json", &serde_json::json!({"complete":false,"reason":"operation has not completed; partial files are not reference data"}))?;
        Ok(result)
    }
    pub fn child(&self, name: &str) -> Result<Self> {
        Self::create(&self.root.join(name), self.budget.clone())
    }
    pub fn writer(&self, name: &str) -> Result<LimitedWriter> {
        LimitedWriter::create(&self.root.join(name), self.budget.clone())
    }
    pub fn bytes(&self, name: &str, data: &[u8]) -> Result<()> {
        self.budget.ensure(data.len() as u64)?;
        let mut out = self.writer(name)?;
        out.write_all(data)?;
        out.finish()
    }
    pub fn json(&self, name: &str, value: &impl Serialize) -> Result<()> {
        let mut bytes = serde_json::to_vec_pretty(value)?;
        bytes.push(b'\n');
        self.bytes(name, &bytes)
    }
    pub fn complete(&self) -> Result<()> {
        fs::remove_file(self.root.join(".incomplete.json"))?;
        Ok(())
    }
}

pub fn pcm_bytes(frames: u64, channels: u32) -> Result<u64> {
    frames
        .checked_mul(u64::from(channels))
        .and_then(|v| v.checked_mul(4))
        .ok_or_else(|| Error::new("PCM size", "size overflow"))
}
pub fn pcm_to_le(samples: &[f32]) -> Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(samples.len() * 4);
    for &s in samples {
        if !s.is_finite() {
            return Err(Error::new("PCM data", "NaN or infinity returned/generated"));
        }
        bytes.extend_from_slice(&s.to_le_bytes());
    }
    Ok(bytes)
}
