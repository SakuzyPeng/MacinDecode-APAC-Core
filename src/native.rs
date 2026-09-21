use crate::{
    error::{Error, Result},
    model::*,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::{CStr, CString, c_char, c_void},
    marker::PhantomData,
    os::unix::ffi::OsStrExt,
    path::Path,
    ptr::{self, NonNull},
    rc::Rc,
};

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct RawPacket {
    pub buffer_offset: i64,
    pub frame: i64,
    pub bytes: u32,
    pub frames: u32,
    pub frame_status: i32,
    pub dependency_status: i32,
    pub roll_status: i32,
    pub independently_decodable: u32,
    pub preroll_packet_count: u32,
    pub roll_distance: i64,
}

unsafe extern "C" {
    fn apac_last_operation() -> *const c_char;
    fn apac_free(p: *mut c_void);
    fn apac_open(path: *const u8, length: u32, out: *mut *mut c_void) -> i32;
    fn apac_close(h: *mut c_void) -> i32;
    fn apac_format(h: *mut c_void, rate: *mut f64, fields: *mut u32) -> i32;
    fn apac_property(h: *mut c_void, prop: u32, out: *mut *mut u8, length: *mut u32) -> i32;
    fn apac_layout_name(data: *const u8, length: u32, out: *mut c_char, capacity: u32) -> i32;
    fn apac_read_packets(
        h: *mut c_void,
        start: i64,
        wanted: u32,
        buffer: *mut u8,
        capacity: u32,
        out: *mut RawPacket,
        count: *mut u32,
        bytes: *mut u32,
        eof: *mut u32,
    ) -> i32;
    fn apac_prepare_decode(h: *mut c_void, start: i64, length: *mut i64) -> i32;
    fn apac_read_pcm(h: *mut c_void, buffer: *mut f32, frames: *mut u32) -> i32;
    fn apac_converter_property(h: *mut c_void, prop: u32, value: *mut u32) -> i32;
    fn apac_create_encoder(
        path: *const c_char,
        rate: f64,
        channels: u32,
        tag: u32,
        bitrate: u32,
        quality: u32,
        limit: u64,
        out: *mut *mut c_void,
    ) -> i32;
    fn apac_write_pcm(h: *mut c_void, buffer: *const f32, frames: u32) -> i32;
}

fn id(code: &[u8; 4]) -> u32 {
    u32::from_be_bytes(*code)
}
fn checked(status: i32, detail: &str) -> Result<()> {
    if status == 0 {
        return Ok(());
    }
    // The bridge returns a thread-local static string, valid until the next call.
    let operation = unsafe { CStr::from_ptr(apac_last_operation()) }.to_string_lossy();
    Err(Error::native(format!("{operation} ({detail})"), status))
}
fn word(bytes: &[u8]) -> Result<u32> {
    Ok(u32::from_ne_bytes(bytes.try_into().map_err(|_| {
        Error::new("property", "expected four bytes")
    })?))
}

/// A native file and its converter are confined to their owning thread.
pub struct NativeFile {
    handle: Option<NonNull<c_void>>,
    format: AudioFormat,
    _not_send_sync: PhantomData<Rc<()>>,
}
impl Drop for NativeFile {
    fn drop(&mut self) {
        if let Some(h) = self.handle.take() {
            unsafe {
                apac_close(h.as_ptr());
            }
        }
    }
}
impl NativeFile {
    fn raw(&self) -> *mut c_void {
        self.handle.expect("open native handle").as_ptr()
    }
    fn wrap(raw: *mut c_void) -> Result<Self> {
        let handle = NonNull::new(raw)
            .ok_or_else(|| Error::new("AudioToolbox", "returned null file handle"))?;
        let mut rate = 0.;
        let mut fields = [0; 7];
        let status = unsafe { apac_format(handle.as_ptr(), &mut rate, fields.as_mut_ptr()) };
        if status != 0 {
            unsafe {
                apac_close(handle.as_ptr());
            }
            return checked(status, "format").and_then(|_| unreachable!());
        }
        let format = AudioFormat {
            sample_rate: rate,
            format_id: fields[0],
            format_fourcc: fourcc(fields[0]),
            flags: fields[1],
            bytes_per_packet: fields[2],
            frames_per_packet: fields[3],
            bytes_per_frame: fields[4],
            channels: fields[5],
            bits_per_channel: fields[6],
        };
        let file = Self {
            handle: Some(handle),
            format,
            _not_send_sync: PhantomData,
        };
        if !rate.is_finite() || rate <= 0. || fields[5] == 0 || fields[5] > 1024 {
            return Err(Error::new(
                "audio format",
                "invalid sample rate or channel count",
            ));
        }
        Ok(file)
    }
    pub fn open(path: &Path) -> Result<Self> {
        let bytes = path.as_os_str().as_bytes();
        let len = u32::try_from(bytes.len()).map_err(|_| Error::new("path", "path too long"))?;
        let mut raw = ptr::null_mut();
        checked(
            unsafe { apac_open(bytes.as_ptr(), len, &mut raw) },
            &path.display().to_string(),
        )?;
        Self::wrap(raw)
    }
    pub fn create_encoder(
        path: &Path,
        rate: f64,
        channels: u32,
        tag: u32,
        bitrate: Option<u32>,
        quality: Option<u32>,
        limit: u64,
    ) -> Result<Self> {
        let path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| Error::new("path", "NUL in path"))?;
        let mut raw = ptr::null_mut();
        checked(
            unsafe {
                apac_create_encoder(
                    path.as_ptr(),
                    rate,
                    channels,
                    tag,
                    bitrate.unwrap_or(0),
                    quality.unwrap_or(u32::MAX),
                    limit,
                    &mut raw,
                )
            },
            "create encoder",
        )?;
        Self::wrap(raw)
    }
    /// Explicit close is essential for detecting encoder flush/header write failures.
    pub fn finish(mut self) -> Result<()> {
        let handle = self.handle.take().expect("open native handle");
        let status = unsafe { apac_close(handle.as_ptr()) };
        if status != 0 {
            return Err(Error::native(
                "ExtAudioFileDispose / AudioFileClose",
                status,
            ));
        }
        Ok(())
    }
    pub fn format(&self) -> &AudioFormat {
        &self.format
    }
    pub fn property(&self, code: &[u8; 4]) -> Result<Vec<u8>> {
        let mut data = ptr::null_mut();
        let mut len = 0;
        checked(
            unsafe { apac_property(self.raw(), id(code), &mut data, &mut len) },
            &String::from_utf8_lossy(code),
        )?;
        if data.is_null() {
            return Err(Error::new("AudioFileGetProperty", "null property buffer"));
        }
        let bytes = unsafe { std::slice::from_raw_parts(data, len as usize) }.to_vec();
        unsafe {
            apac_free(data.cast());
        }
        Ok(bytes)
    }
    pub fn u32_property(&self, code: &[u8; 4]) -> Result<u32> {
        word(&self.property(code)?)
    }
    pub fn u64_property(&self, code: &[u8; 4]) -> Result<u64> {
        let bytes = self.property(code)?;
        Ok(u64::from_ne_bytes(bytes.as_slice().try_into().map_err(
            |_| Error::new("property", "expected eight bytes"),
        )?))
    }
    pub fn cookie(&self) -> Result<Vec<u8>> {
        self.property(b"mgic")
    }
    pub fn layout(&self) -> Result<ChannelLayout> {
        let bytes = self.property(b"cmap")?;
        if bytes.len() < 12 {
            return Err(Error::new("channel layout", "truncated layout header"));
        }
        let tag = word(&bytes[0..4])?;
        let bitmap = word(&bytes[4..8])?;
        let count = word(&bytes[8..12])? as usize;
        if count > (bytes.len() - 12) / 20 {
            return Err(Error::new(
                "channel layout",
                "truncated channel descriptions",
            ));
        }
        let mut name = [0i8; 1024];
        let status = unsafe {
            apac_layout_name(
                bytes.as_ptr(),
                bytes.len() as u32,
                name.as_mut_ptr(),
                name.len() as u32,
            )
        };
        let name = (status == 0).then(|| {
            unsafe { CStr::from_ptr(name.as_ptr()) }
                .to_string_lossy()
                .into_owned()
        });
        let mut layout = ChannelLayout::tagged(tag, self.format.channels, name);
        layout.bitmap = bitmap;
        for raw in bytes[12..12 + count * 20].chunks_exact(20) {
            let coordinates = [
                f32::from_bits(word(&raw[8..12])?),
                f32::from_bits(word(&raw[12..16])?),
                f32::from_bits(word(&raw[16..20])?),
            ];
            if !coordinates.iter().all(|v| v.is_finite()) {
                return Err(Error::new("channel layout", "non-finite coordinates"));
            }
            layout.descriptions.push(ChannelDescription {
                label: word(&raw[..4])?,
                flags: word(&raw[4..8])?,
                coordinates,
            });
        }
        Ok(layout)
    }
    pub fn packet_table(&self) -> Result<PacketTable> {
        let b = self.property(b"pnfo")?;
        if b.len() != 16 {
            return Err(Error::new("packet table", "invalid structure length"));
        }
        let table = PacketTable {
            valid_frames: i64::from_ne_bytes(b[..8].try_into().unwrap()),
            priming_frames: i32::from_ne_bytes(b[8..12].try_into().unwrap()),
            remainder_frames: i32::from_ne_bytes(b[12..16].try_into().unwrap()),
        };
        if table.valid_frames < 0 || table.priming_frames < 0 || table.remainder_frames < 0 {
            return Err(Error::new("packet table", "negative frame count"));
        }
        Ok(table)
    }
    pub fn inspect(&self, path: &Path, environment: &Environment) -> Result<FileInfo> {
        let metadata = std::fs::metadata(path)?;
        Ok(FileInfo {
            schema_version: SCHEMA_VERSION,
            source: std::fs::canonicalize(path)?,
            file_bytes: metadata.len(),
            modified_unix_seconds: metadata
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs()),
            environment: environment.clone(),
            container: Property::from_result(self.u32_property(b"ffmt").map(fourcc)),
            format: self.format.clone(),
            layout: Property::from_result(self.layout()),
            packet_count: Property::from_result(self.u64_property(b"pcnt")),
            max_packet_bytes: Property::from_result(self.u32_property(b"pkub")),
            packet_table: Property::from_result(self.packet_table()),
            cookie: Property::from_result(self.cookie().map(|b| CookieInfo {
                bytes: b.len(),
                sha256: sha256(&b),
            })),
            restricts_random_access: Property::from_result(
                self.u32_property(b"rrap").map(|x| x != 0),
            ),
        })
    }
    pub fn read_packets(
        &mut self,
        start: u64,
        wanted: u32,
    ) -> Result<(Vec<u8>, Vec<RawPacket>, bool)> {
        let max = self.u32_property(b"pkub")?;
        if max == 0 || max > 16 * 1024 * 1024 {
            return Err(Error::new(
                "packet buffer",
                "invalid/excessive packet size upper bound",
            ));
        }
        let wanted = wanted.min(64).min((16 * 1024 * 1024 / max).max(1));
        if wanted == 0 {
            return Err(Error::new("packet range", "zero packet request"));
        }
        let start =
            i64::try_from(start).map_err(|_| Error::new("packet range", "index exceeds i64"))?;
        let mut buffer = vec![0; max as usize * wanted as usize];
        let mut packets = vec![RawPacket::default(); wanted as usize];
        let (mut count, mut bytes, mut eof) = (0, 0, 0);
        checked(
            unsafe {
                apac_read_packets(
                    self.raw(),
                    start,
                    wanted,
                    buffer.as_mut_ptr(),
                    buffer.len() as u32,
                    packets.as_mut_ptr(),
                    &mut count,
                    &mut bytes,
                    &mut eof,
                )
            },
            "read packets",
        )?;
        if count > wanted || bytes as usize > buffer.len() {
            return Err(Error::new("packet buffer", "invalid native read count"));
        }
        buffer.truncate(bytes as usize);
        packets.truncate(count as usize);
        Ok((buffer, packets, eof != 0))
    }
    pub fn prepare_decode(&mut self, start: u64) -> Result<u64> {
        let start =
            i64::try_from(start).map_err(|_| Error::new("frame range", "index exceeds i64"))?;
        let mut length = 0;
        checked(
            unsafe { apac_prepare_decode(self.raw(), start, &mut length) },
            "prepare reference decode",
        )?;
        u64::try_from(length).map_err(|_| Error::new("frame range", "negative file length"))
    }
    pub fn read_pcm(&mut self, frames: u32) -> Result<Vec<f32>> {
        if frames > 16384 {
            return Err(Error::new("PCM buffer", "read block exceeds 16384 frames"));
        }
        let mut samples = vec![0.; frames as usize * self.format.channels as usize];
        let mut actual = frames;
        checked(
            unsafe { apac_read_pcm(self.raw(), samples.as_mut_ptr(), &mut actual) },
            "reference decode",
        )?;
        if actual > frames {
            return Err(Error::new(
                "PCM buffer",
                "native decoder returned too many frames",
            ));
        }
        samples.truncate(actual as usize * self.format.channels as usize);
        Ok(samples)
    }
    pub fn write_pcm(&mut self, samples: &[f32]) -> Result<()> {
        let channels = self.format.channels as usize;
        if !samples.len().is_multiple_of(channels) || samples.len() / channels > 16384 {
            return Err(Error::new("PCM buffer", "invalid encoding block size"));
        }
        checked(
            unsafe {
                apac_write_pcm(
                    self.raw(),
                    samples.as_ptr(),
                    (samples.len() / channels) as u32,
                )
            },
            "encode APAC",
        )
    }
    pub fn converter_settings(&self, encoder: bool) -> BTreeMap<String, Property<Value>> {
        let mut result = BTreeMap::new();
        let props: &[(&[u8; 4], bool)] = if encoder {
            &[(b"brat", false), (b"cdqu", false), (b"cdrc", false)]
        } else {
            &[
                (b"mdrc", false),
                (b"^pro", false),
                (b"ptlc", false),
                (b"pptl", true),
            ]
        };
        for &(prop, float) in props {
            let value = (|| {
                let mut raw = 0;
                checked(
                    unsafe { apac_converter_property(self.raw(), id(prop), &mut raw) },
                    &String::from_utf8_lossy(prop),
                )?;
                if float {
                    let v = f32::from_bits(raw);
                    if !v.is_finite() {
                        return Err(Error::new(
                            "converter property",
                            "non-finite property value",
                        ));
                    }
                    Ok(json!(v))
                } else {
                    Ok(json!(raw))
                }
            })();
            result.insert(
                String::from_utf8_lossy(prop).into_owned(),
                Property::from_result(value),
            );
        }
        result
    }
}
