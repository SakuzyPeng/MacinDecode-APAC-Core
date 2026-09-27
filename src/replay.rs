//! macOS reference replay. Native decoding owns no AudioFile and knows no source path.
use crate::{
    error::{Error, Result},
    model::*,
    native::checked,
    output::{Budget, OutputDir, pcm_bytes, pcm_to_le},
    packets::{self, InputPacket, PacketBatch, PacketBundle},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    ffi::c_void,
    io::Write,
    marker::PhantomData,
    panic::{AssertUnwindSafe, catch_unwind},
    path::Path,
    ptr::{self, NonNull},
    rc::Rc,
};

#[repr(C)]
struct LayoutDescription {
    label: u32,
    flags: u32,
    coordinates: [f32; 3],
}
#[repr(C)]
struct ReplayConfig {
    sample_rate: f64,
    fields: [u32; 7],
    has_layout: u32,
    layout_tag: u32,
    layout_bitmap: u32,
    description_count: u32,
    descriptions: *const LayoutDescription,
    cookie: *const u8,
    cookie_bytes: u32,
    defer_cookie: u32,
}
type InputProc = unsafe extern "C" fn(
    *mut c_void,
    u32,
    *mut *const u8,
    *mut u32,
    *mut *const InputPacket,
    *mut u32,
) -> i32;
unsafe extern "C" {
    fn apac_replay_create(
        config: *const ReplayConfig,
        input: InputProc,
        context: *mut c_void,
        out: *mut *mut c_void,
    ) -> i32;
    fn apac_replay_set_cookie(handle: *mut c_void, cookie: *const u8, bytes: u32) -> i32;
    fn apac_replay_close(handle: *mut c_void) -> i32;
    fn apac_replay_read(handle: *mut c_void, samples: *mut f32, frames: *mut u32) -> i32;
    fn apac_replay_property(handle: *mut c_void, property: u32, words: *mut u32, count: u32)
    -> i32;
    fn apac_replay_set_off_property(handle: *mut c_void, property: u32) -> i32;
}

struct Feed {
    bundle: PacketBundle,
    batch_size: u32,
    batch: PacketBatch,
    callback_calls: u64,
    eof_sent: bool,
    error: Option<Error>,
}

unsafe extern "C" fn input_callback(
    context: *mut c_void,
    wanted: u32,
    data: *mut *const u8,
    bytes: *mut u32,
    packets: *mut *const InputPacket,
    count: *mut u32,
) -> i32 {
    // The synchronous converter callback owns this exclusive borrow. The Box and
    // its current batch survive until the next callback (or converter disposal).
    let feed = unsafe { &mut *context.cast::<Feed>() };
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<()> {
        if let Some(error) = &feed.error {
            return Err(error.clone());
        }
        feed.callback_calls = packets::add(feed.callback_calls, 1)?;
        feed.batch = feed.bundle.next_batch(wanted.min(feed.batch_size))?;
        feed.eof_sent |= feed.batch.packets.is_empty();
        unsafe {
            *data = feed.batch.data.as_ptr();
            *bytes = feed.batch.data.len() as u32;
            *packets = feed.batch.packets.as_ptr();
            *count = feed.batch.packets.len() as u32;
        }
        Ok(())
    }));
    match result {
        Ok(Ok(())) => 0,
        error => {
            feed.error = Some(match error {
                Ok(Err(error)) => error,
                _ => Error::new(
                    "replay input callback",
                    "panic was contained at the FFI boundary",
                ),
            });
            unsafe {
                *count = 0;
                *bytes = 0;
                *data = ptr::null();
                *packets = ptr::null();
            }
            -1
        }
    }
}

struct Converter {
    handle: Option<NonNull<c_void>>,
    feed: Box<Feed>,
    channels: u32,
    _not_send_sync: PhantomData<Rc<()>>,
}
impl Drop for Converter {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            unsafe {
                apac_replay_close(handle.as_ptr());
            }
        }
    }
}
impl Converter {
    fn new(bundle: PacketBundle, batch_size: u32, policy: NativeProcessingPolicy) -> Result<Self> {
        if !(1..=64).contains(&batch_size) {
            return Err(Error::new(
                "replay",
                "input batch must contain 1..64 packets",
            ));
        }
        let mut feed = Box::new(Feed {
            bundle,
            batch_size,
            batch: PacketBatch {
                data: Vec::new(),
                packets: Vec::new(),
            },
            callback_calls: 0,
            eof_sent: false,
            error: None,
        });
        let info = &feed.bundle.manifest().file;
        let f = &info.format;
        let layout = info.layout.value.as_ref();
        let descriptions: Vec<_> = layout
            .map(|l| {
                l.descriptions
                    .iter()
                    .map(|d| LayoutDescription {
                        label: d.label,
                        flags: d.flags,
                        coordinates: d.coordinates,
                    })
                    .collect()
            })
            .unwrap_or_default();
        let config = ReplayConfig {
            sample_rate: f.sample_rate,
            fields: [
                f.format_id,
                f.flags,
                f.bytes_per_packet,
                f.frames_per_packet,
                f.bytes_per_frame,
                f.channels,
                f.bits_per_channel,
            ],
            has_layout: u32::from(layout.is_some()),
            layout_tag: layout.map_or(0, |l| l.tag),
            layout_bitmap: layout.map_or(0, |l| l.bitmap),
            description_count: descriptions.len() as u32,
            descriptions: descriptions.as_ptr(),
            cookie: feed.bundle.cookie().as_ptr(),
            cookie_bytes: feed.bundle.cookie().len() as u32,
            defer_cookie: u32::from(policy == NativeProcessingPolicy::DrcOff),
        };
        let channels = f.channels;
        let mut raw = ptr::null_mut();
        checked(
            unsafe {
                apac_replay_create(
                    &config,
                    input_callback,
                    (&mut *feed as *mut Feed).cast(),
                    &mut raw,
                )
            },
            "create packet decoder",
        )?;
        let handle = NonNull::new(raw).ok_or_else(|| Error::new("replay", "null converter"))?;
        Ok(Self {
            handle: Some(handle),
            feed,
            channels,
            _not_send_sync: PhantomData,
        })
    }
    fn raw(&self) -> *mut c_void {
        self.handle.expect("open converter").as_ptr()
    }
    fn read(&mut self, requested: u32) -> Result<Vec<f32>> {
        let mut samples = vec![0.; requested as usize * self.channels as usize];
        let mut frames = requested;
        let status = unsafe { apac_replay_read(self.raw(), samples.as_mut_ptr(), &mut frames) };
        if let Some(error) = self.feed.error.take() {
            return Err(error);
        }
        checked(status, "packet PCM")?;
        if frames > requested {
            return Err(Error::new("replay", "converter exceeded PCM capacity"));
        }
        samples.truncate(frames as usize * self.channels as usize);
        Ok(samples)
    }
    fn property(&self, code: &[u8; 4], count: u32) -> Result<Vec<u32>> {
        let mut words = vec![0; count as usize];
        checked(
            unsafe {
                apac_replay_property(
                    self.raw(),
                    u32::from_be_bytes(*code),
                    words.as_mut_ptr(),
                    count,
                )
            },
            &String::from_utf8_lossy(code),
        )?;
        Ok(words)
    }
    fn settings(&self) -> BTreeMap<String, Property<Value>> {
        [b"mdrc", b"^pro", b"ptlc", b"pptl"]
            .into_iter()
            .map(|code| {
                let value = self.property(code, 1).and_then(|v| {
                    if code == b"pptl" {
                        let f = f32::from_bits(v[0]);
                        if !f.is_finite() {
                            return Err(Error::new("decoder property", "non-finite target level"));
                        }
                        Ok(json!(f))
                    } else {
                        Ok(json!(v[0]))
                    }
                });
                (
                    String::from_utf8_lossy(code).into_owned(),
                    Property::from_result(value),
                )
            })
            .collect()
    }
    fn request_processing_off(&self) -> (Value, Option<Error>) {
        let mut requests = Vec::new();
        let mut failure = None;
        for property in [b"mdrc", b"^pro", b"^tlc"] {
            let name = String::from_utf8_lossy(property);
            let status =
                unsafe { apac_replay_set_off_property(self.raw(), u32::from_be_bytes(*property)) };
            requests.push(json!({"property":name,"requested":0,"os_status":status}));
            if status != 0 {
                failure = Some(Error::native(
                    format!("AudioConverterSetProperty({name}/replay processing off)"),
                    status,
                ));
                break;
            }
        }
        if failure.is_none() {
            let cookie = self.feed.bundle.cookie();
            failure = checked(
                unsafe { apac_replay_set_cookie(self.raw(), cookie.as_ptr(), cookie.len() as u32) },
                "set cookie after processing policy",
            )
            .err();
        }
        let readback = self.settings();
        if failure.is_none() {
            failure = require_processing_off(&readback).err();
        }
        (
            json!({"policy":"drc-off","verification_scope":"public_property_requests_and_readback_only","request_order":"before_magic_cookie","requests":requests,"readback":readback,"verified":failure.is_none()}),
            failure,
        )
    }
    fn finish(mut self) -> Result<()> {
        let handle = self.handle.take().expect("open converter");
        let status = unsafe { apac_replay_close(handle.as_ptr()) };
        if status != 0 {
            return Err(Error::native("AudioConverterDispose(replay)", status));
        }
        Ok(())
    }
}

fn require_processing_off(settings: &BTreeMap<String, Property<Value>>) -> Result<()> {
    for name in ["mdrc", "^pro", "ptlc"] {
        let property = settings.get(name).expect("queried decoder property");
        if property.error.is_some() || property.value != Some(json!(0)) {
            return Err(Error::new(
                "replay processing policy",
                format!(
                    "{name} did not confirm None: {}",
                    serde_json::to_string(property)?
                ),
            ));
        }
    }
    Ok(())
}

pub fn replay(
    directory: &Path,
    destination: &Path,
    start: Option<u64>,
    frames: u64,
    batch: u32,
    limit: u64,
) -> Result<Value> {
    replay_with_policy(
        directory,
        destination,
        start,
        frames,
        batch,
        limit,
        NativeProcessingPolicy::Default,
    )
}

pub fn replay_with_policy(
    directory: &Path,
    destination: &Path,
    start: Option<u64>,
    frames: u64,
    batch: u32,
    limit: u64,
    policy: NativeProcessingPolicy,
) -> Result<Value> {
    if !(1..=64).contains(&batch) {
        return Err(Error::new(
            "replay",
            "input batch must contain 1..64 packets",
        ));
    }
    let bundle = PacketBundle::open(directory)?;
    let range = bundle.range(start, frames)?;
    let info = bundle.manifest().file.clone();
    let raw_start = bundle.raw_start();
    let raw_end = bundle.raw_end();
    let expected_raw = raw_end - raw_start;
    let stored_start = bundle.manifest().start_packet;
    let stored_count = bundle.manifest().actual_packets;
    let replay_window = bundle.manifest().replay_window.clone();
    let out = OutputDir::create(destination, Budget::new(limit))?;
    let output_bytes = pcm_bytes(range.frames, info.format.channels)?;
    out.budget.ensure(packets::add(output_bytes, 65536)?)?;
    let mut decoder = Converter::new(bundle, batch, policy)?;
    let audit = if policy == NativeProcessingPolicy::DrcOff {
        let (audit, failure) = decoder.request_processing_off();
        out.json("processing-policy.json", &audit)?;
        if let Some(error) = failure {
            return Err(error);
        }
        Some(audit)
    } else {
        None
    };
    let mut settings = decoder.settings();
    let prime_info = Property::from_result(
        decoder
            .property(b"prim", 2)
            .map(|v| json!({"leading_frames":v[0],"trailing_frames":v[1]})),
    );
    let prime_method = Property::from_result(decoder.property(b"prmm", 1).map(|v| v[0]));
    let mut writer = out.writer("pcm.f32le")?;
    let mut hash = Sha256::new();
    let (mut produced, mut saved, mut discarded_before, mut discarded_after) =
        (0u64, 0u64, 0u64, 0u64);
    let mut eof_drained = false;
    loop {
        if !range.drain_to_eof && saved == range.frames {
            break;
        }
        let wanted = if range.drain_to_eof {
            8192
        } else {
            (range.raw_end - raw_start - produced).min(8192) as u32
        };
        let before = decoder.feed.bundle.consumed_packets();
        let samples = decoder.read(wanted)?;
        if samples.is_empty() {
            if decoder.feed.eof_sent {
                if produced != expected_raw
                    || saved != range.frames
                    || decoder.feed.bundle.consumed_packets() != stored_count
                {
                    return Err(Error::new(
                        "replay EOF",
                        "decoder ended before the advertised frame range",
                    ));
                }
                eof_drained = true;
                break;
            }
            if decoder.feed.bundle.consumed_packets() == before {
                return Err(Error::new("replay", "decoder made no progress"));
            }
            continue;
        }
        if samples.iter().any(|v| !v.is_finite()) {
            return Err(Error::new("replay PCM", "NaN or infinity returned"));
        }
        let count = samples.len() as u64 / u64::from(info.format.channels);
        let block_start = packets::add(raw_start, produced)?;
        produced = packets::add(produced, count)?;
        if produced > expected_raw {
            return Err(Error::new(
                "replay frames",
                "decoder produced more frames than the packet index describes",
            ));
        }
        let block_end = packets::add(raw_start, produced)?;
        discarded_before = packets::add(
            discarded_before,
            block_end.min(range.raw_start).saturating_sub(block_start),
        )?;
        discarded_after = packets::add(
            discarded_after,
            block_end.saturating_sub(block_start.max(range.raw_end)),
        )?;
        let keep_start = block_start.max(range.raw_start);
        let keep_end = block_end.min(range.raw_end);
        if keep_start < keep_end {
            let first = (keep_start - block_start) as usize * info.format.channels as usize;
            let last = (keep_end - block_start) as usize * info.format.channels as usize;
            let bytes = pcm_to_le(&samples[first..last])?;
            writer.write_all(&bytes)?;
            hash.update(&bytes);
            saved = packets::add(saved, keep_end - keep_start)?;
        }
    }
    if saved != range.frames {
        return Err(Error::new(
            "replay",
            "requested frame range was not produced",
        ));
    }
    writer.finish()?;
    let consumed_packets = decoder.feed.bundle.consumed_packets();
    let consumed_frames = decoder.feed.bundle.consumed_frames();
    let callback_calls = decoder.feed.callback_calls;
    let eof_sent = decoder.feed.eof_sent;
    decoder.feed.bundle.verify_remaining()?;
    if policy == NativeProcessingPolicy::DrcOff {
        let final_settings = decoder.settings();
        require_processing_off(&final_settings)?;
        settings.insert(
            "processing_policy".into(),
            Property::known(
                json!({"initial":audit,"final_readback":final_settings,"verified":true}),
            ),
        );
    }
    decoder.finish()?;
    let environment = Environment::current();
    let pcm = PcmInfo {
        schema_version: SCHEMA_VERSION,
        complete: true,
        pcm_file: "pcm.f32le".into(),
        encoding: "f32le".into(),
        interleaved: true,
        sample_rate: info.format.sample_rate,
        channels: info.format.channels,
        layout: info.layout,
        start_frame: range.start_frame,
        requested_frames: range.requested_frames,
        frames: saved,
        bytes: output_bytes,
        sha256: format!("{:x}", hash.finalize()),
        source: Some(info.source),
        source_cookie_sha256: info.cookie.value.map(|c| c.sha256),
        source_packet_table: info.packet_table.value,
        environment: environment.clone(),
        decoder_settings: settings,
        all_finite: true,
    };
    let report = json!({"schema_version":SCHEMA_VERSION,"complete":true,"backend":"AudioConverterFillComplexBuffer",
        "bundle":directory,"environment":environment,"original_source_accessed":false,"input_batch_packets":batch,"processing_policy":policy,
        "stored_start_packet":stored_start,"stored_packets":stored_count,"consumed_packets":consumed_packets,
        "consumed_packet_frames":consumed_frames,"produced_raw_frames":produced,"stored_raw_start":raw_start,"stored_raw_end":raw_end,
        "discarded_before_frames":discarded_before,"discarded_after_frames":discarded_after,"saved_frames":saved,
        "callback_calls":callback_calls,"eof_sent":eof_sent,"eof_drained":eof_drained,
        "range":range,"replay_window":replay_window,"converter_prime_info":prime_info,"converter_prime_method":prime_method,
        "decoder_settings":pcm.decoder_settings,"pcm_metadata":"pcm.json"});
    out.json("pcm.json", &pcm)?;
    out.json("replay.json", &report)?;
    out.complete()?;
    Ok(report)
}
