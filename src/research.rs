use crate::{
    error::{Error, Result},
    model::*,
    native::NativeFile,
    output::{Budget, LimitedWriter, OutputDir, pcm_bytes, pcm_to_le},
    signal::{self, LayoutPreset, Signal},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
};

pub fn inspect(path: &Path) -> Result<FileInfo> {
    NativeFile::open(path)?.inspect(path, &Environment::current())
}

fn discover(
    root: &Path,
    files: &mut Vec<PathBuf>,
    errors: &mut Vec<(PathBuf, Error)>,
    skipped: &mut u64,
) {
    let entries = match fs::read_dir(root) {
        Ok(v) => v,
        Err(e) => {
            errors.push((root.to_owned(), Error::io("scan directory", e)));
            return;
        }
    };
    for entry in entries {
        let entry = match entry {
            Ok(v) => v,
            Err(e) => {
                errors.push((root.to_owned(), Error::io("scan entry", e)));
                continue;
            }
        };
        let path = entry.path();
        let kind = match entry.file_type() {
            Ok(v) => v,
            Err(e) => {
                errors.push((path, Error::io("file type", e)));
                continue;
            }
        };
        if kind.is_symlink() {
            *skipped += 1;
        } else if kind.is_dir() {
            discover(&path, files, errors, skipped);
        } else if kind.is_file()
            && path
                .extension()
                .and_then(|x| x.to_str())
                .is_some_and(|e| e.eq_ignore_ascii_case("caf") || e.eq_ignore_ascii_case("m4a"))
        {
            files.push(path);
        }
    }
}

pub fn scan(root: &Path, output: &Path, limit: u64) -> Result<(Value, bool)> {
    if !root.is_dir() {
        return Err(Error::new("scan", "input must be a directory"));
    }
    let root = fs::canonicalize(root)?;
    let mut paths = vec![];
    let mut discovery_errors = vec![];
    let mut skipped = 0;
    discover(&root, &mut paths, &mut discovery_errors, &mut skipped);
    paths.sort();
    let budget = Budget::new(limit);
    let marker = output.with_file_name(format!(
        "{}.incomplete",
        output
            .file_name()
            .ok_or_else(|| Error::new("scan", "invalid output path"))?
            .to_string_lossy()
    ));
    // Reserve the destination before the marker: an existing destination is never modified.
    if let Some(parent) = output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let mut out = LimitedWriter::create(output, budget.clone())?;
    let mut mark = LimitedWriter::create(&marker, budget.clone())?;
    mark.write_all(
        b"Scan did not finish. Partial JSONL must not be treated as a complete corpus index.\n",
    )?;
    mark.finish()?;
    let env = Environment::current();
    let mut successes = 0u64;
    let mut failures = 0u64;
    let mut groups: BTreeMap<String, u64> = BTreeMap::new();
    for (i, path) in paths.iter().enumerate() {
        eprintln!("scan {}/{}: {}", i + 1, paths.len(), path.display());
        let record = match NativeFile::open(path).and_then(|f| f.inspect(path, &env)) {
            Ok(info) => {
                successes += 1;
                let group = json!({"format":info.format.format_fourcc,"sample_rate":info.format.sample_rate,"channels":info.format.channels,"layout":info.layout.value,"cookie_sha256":info.cookie.value.as_ref().map(|v| &v.sha256)}).to_string();
                *groups.entry(group).or_default() += 1;
                json!({"schema_version":SCHEMA_VERSION,"status":"ok","file":info})
            }
            Err(error) => {
                failures += 1;
                json!({"schema_version":SCHEMA_VERSION,"status":"error","source":path,"error":error})
            }
        };
        serde_json::to_writer(&mut out, &record)?;
        out.write_all(b"\n")?;
    }
    for (path, error) in discovery_errors {
        failures += 1;
        serde_json::to_writer(
            &mut out,
            &json!({"schema_version":SCHEMA_VERSION,"status":"discovery-error","source":path,"error":error}),
        )?;
        out.write_all(b"\n")?;
    }
    out.finish()?;
    fs::remove_file(marker)?;
    let grouped: Vec<Value> = groups.into_iter().map(|(key, count)| json!({"configuration":serde_json::from_str::<Value>(&key).expect("serialized group"),"files":count})).collect();
    Ok((
        json!({"schema_version":SCHEMA_VERSION,"complete":true,"root":root,"manifest":output,"files_discovered":paths.len(),"successful":successes,"errors":failures,"skipped_symlinks":skipped,"groups":grouped}),
        failures == 0,
    ))
}

pub fn dump(
    path: &Path,
    destination: &Path,
    start: u64,
    requested: u64,
    limit: u64,
) -> Result<Value> {
    if requested == 0 {
        return Err(Error::new("packet range", "packet count must be positive"));
    }
    let mut file = NativeFile::open(path)?;
    if file.format().format_fourcc != "apac" {
        return Err(Error::new("dump", "input is not Apple APAC"));
    }
    let info = file.inspect(path, &Environment::current())?;
    let total = file.u64_property(b"pcnt")?;
    if start > total {
        return Err(Error::new("packet range", "start exceeds packet count"));
    }
    let end = start
        .checked_add(requested)
        .ok_or_else(|| Error::new("packet range", "range overflow"))?
        .min(total);
    let cookie = file.cookie()?;
    let out = OutputDir::create(destination, Budget::new(limit))?;
    out.bytes("cookie.bin", &cookie)?;
    let mut data = out.writer("packets.bin")?;
    let mut index = out.writer("packets.jsonl")?;
    let mut position = start;
    let mut offset = 0u64;
    let mut hash = Sha256::new();
    while position < end {
        let (buffer, packets, _eof) =
            file.read_packets(position, (end - position).min(64) as u32)?;
        if packets.is_empty() {
            return Err(Error::new(
                "dump",
                "unexpected EOF before advertised packet count",
            ));
        }
        for packet in packets {
            let begin = usize::try_from(packet.buffer_offset)
                .map_err(|_| Error::new("packet", "negative buffer offset"))?;
            let bytes = buffer
                .get(begin..begin + packet.bytes as usize)
                .ok_or_else(|| Error::new("packet", "out-of-bounds packet description"))?;
            data.write_all(bytes)?;
            hash.update(bytes);
            let prop = |status, operation, value: Value| {
                Property::from_result(if status == 0 {
                    Ok(value)
                } else {
                    Err(Error::native(operation, status))
                })
            };
            let record = json!({"schema_version":SCHEMA_VERSION,"packet_index":position,"export_offset":offset,"bytes":packet.bytes,"frames":packet.frames,"sha256":sha256(bytes),
                "raw_frame_position":prop(packet.frame_status,"AudioFileGetProperty(PacketToFrame)",json!(packet.frame)),
                "dependency":prop(packet.dependency_status,"AudioFileGetProperty(PacketToDependencyInfo)",json!({"independently_decodable":packet.independently_decodable != 0,"preroll_packet_count":packet.preroll_packet_count})),
                "roll_distance":prop(packet.roll_status,"AudioFileGetProperty(PacketToRollDistance)",json!(packet.roll_distance))});
            serde_json::to_writer(&mut index, &record)?;
            index.write_all(b"\n")?;
            offset += u64::from(packet.bytes);
            position += 1;
        }
    }
    data.finish()?;
    index.finish()?;
    let manifest = json!({"schema_version":SCHEMA_VERSION,"complete":true,"file":info,"start_packet":start,"requested_packets":requested,"actual_packets":position-start,"packet_data_file":"packets.bin","packet_index_file":"packets.jsonl","cookie_file":"cookie.bin","packet_data_bytes":offset,"packet_data_sha256":format!("{:x}",hash.finalize()),"offset_origin":"exported packets.bin; frame positions are untrimmed packet timeline","dependency_note":"raw range only; preroll dependencies are annotated, not automatically added"});
    out.json("manifest.json", &manifest)?;
    out.complete()?;
    Ok(manifest)
}

fn decode_into(
    path: &Path,
    out: &OutputDir,
    start: u64,
    requested: u64,
    env: &Environment,
) -> Result<PcmInfo> {
    if requested == 0 {
        return Err(Error::new("frame range", "frame count must be positive"));
    }
    start
        .checked_add(requested)
        .ok_or_else(|| Error::new("frame range", "range overflow"))?;
    let mut file = NativeFile::open(path)?;
    let info = file.inspect(path, env)?;
    let length = file.prepare_decode(start)?;
    let frames = requested.min(length - start);
    let bytes = pcm_bytes(frames, info.format.channels)?;
    out.budget.ensure(
        bytes
            .checked_add(65536)
            .ok_or_else(|| Error::new("PCM size", "size overflow"))?,
    )?;
    let settings = file.converter_settings(false);
    let mut writer = out.writer("pcm.f32le")?;
    let mut hasher = Sha256::new();
    let mut actual = 0;
    while actual < frames {
        let samples = file.read_pcm((frames - actual).min(8192) as u32)?;
        if samples.is_empty() {
            return Err(Error::new(
                "decode",
                "unexpected EOF before the reported valid frame count",
            ));
        }
        let data = pcm_to_le(&samples)?;
        writer.write_all(&data)?;
        hasher.update(&data);
        actual += samples.len() as u64 / u64::from(info.format.channels);
    }
    writer.finish()?;
    file.finish()?;
    let pcm = PcmInfo {
        schema_version: SCHEMA_VERSION,
        complete: true,
        pcm_file: "pcm.f32le".into(),
        encoding: "f32le".into(),
        interleaved: true,
        sample_rate: info.format.sample_rate,
        channels: info.format.channels,
        layout: info.layout,
        start_frame: start,
        requested_frames: requested,
        frames: actual,
        bytes,
        sha256: format!("{:x}", hasher.finalize()),
        source: Some(info.source),
        source_cookie_sha256: info.cookie.value.map(|c| c.sha256),
        source_packet_table: info.packet_table.value,
        environment: env.clone(),
        decoder_settings: settings,
        all_finite: true,
    };
    out.json("pcm.json", &pcm)?;
    Ok(pcm)
}

pub fn decode(
    path: &Path,
    destination: &Path,
    start: u64,
    requested: u64,
    limit: u64,
) -> Result<PcmInfo> {
    // Fail missing inputs before creating the destination.
    fs::metadata(path)?;
    let out = OutputDir::create(destination, Budget::new(limit))?;
    let pcm = decode_into(path, &out, start, requested, &Environment::current())?;
    out.complete()?;
    Ok(pcm)
}

pub struct FixtureOptions {
    pub layout: LayoutPreset,
    pub sample_rate: u32,
    pub duration: f64,
    pub seed: u64,
    pub signals: Vec<Signal>,
    pub bitrate: Option<u32>,
    pub quality: Option<u32>,
}

pub fn fixture(destination: &Path, options: &FixtureOptions, limit: u64) -> Result<Value> {
    if options.sample_rate == 0
        || !options.duration.is_finite()
        || options.duration <= 0.
        || options.duration * f64::from(options.sample_rate) >= i64::MAX as f64
    {
        return Err(Error::new("fixture", "invalid duration/sample rate"));
    }
    if options.quality.is_some_and(|q| q > 127) || options.bitrate == Some(0) {
        return Err(Error::new(
            "fixture",
            "quality must be 0..127 and bitrate must be positive",
        ));
    }
    let frames = (options.duration * f64::from(options.sample_rate)).round() as u64;
    if frames == 0 || options.signals.is_empty() {
        return Err(Error::new(
            "fixture",
            "requires at least one frame and signal",
        ));
    }
    let mut names: Vec<_> = options.signals.iter().map(|s| s.name()).collect();
    names.sort_unstable();
    names.dedup();
    if names.len() != options.signals.len() {
        return Err(Error::new("fixture", "duplicate signal selection"));
    }
    let channels = options.layout.channels();
    let source_bytes = pcm_bytes(frames, channels)?;
    let minimum = source_bytes
        .checked_mul(2)
        .and_then(|n| n.checked_mul(options.signals.len() as u64))
        .and_then(|n| n.checked_add(131072))
        .ok_or_else(|| Error::new("fixture", "output size overflow"))?;
    let budget = Budget::new(limit);
    budget.ensure(minimum)?;
    let out = OutputDir::create(destination, budget)?;
    let env = Environment::current();
    let mut cases = vec![];
    for &kind in &options.signals {
        eprintln!("fixture: {} / {:?}", kind.name(), options.layout);
        let case = out.child(kind.name())?;
        let encoded_path = case.root.join("encoded.caf");
        // Reserve source + reference PCM and metadata before assigning the encoder quota.
        let reserve = source_bytes
            .checked_mul(2)
            .and_then(|n| n.checked_add(65536))
            .ok_or_else(|| Error::new("fixture", "size overflow"))?;
        case.budget.ensure(reserve)?;
        let mut encoder = NativeFile::create_encoder(
            &encoded_path,
            f64::from(options.sample_rate),
            channels,
            options.layout.tag(),
            options.bitrate,
            options.quality,
            case.budget.remaining() - reserve,
        )?;
        let mut writer = case.writer("source.f32le")?;
        let mut hash = Sha256::new();
        let mut position = 0;
        while position < frames {
            let count = (frames - position).min(8192) as u32;
            let samples = signal::generate(
                kind,
                position,
                count,
                frames,
                options.sample_rate,
                channels,
                options.seed,
            );
            let bytes = pcm_to_le(&samples)?;
            writer.write_all(&bytes)?;
            hash.update(&bytes);
            encoder.write_pcm(&samples)?;
            position += u64::from(count);
        }
        writer.finish()?;
        let encoder_settings = encoder.converter_settings(true);
        encoder.finish()?;
        case.budget.charge(fs::metadata(&encoded_path)?.len())?;
        let encoded_info = NativeFile::open(&encoded_path)?.inspect(&encoded_path, &env)?;
        let expected_layout = options.layout.layout();
        if encoded_info.format.sample_rate != f64::from(options.sample_rate)
            || encoded_info.format.channels != channels
            || !encoded_info
                .layout
                .value
                .as_ref()
                .is_some_and(|l| l.equivalent(&expected_layout))
        {
            return Err(Error::new(
                "fixture",
                "encoder changed the requested sample rate/channel layout",
            ));
        }
        let source = PcmInfo {
            schema_version: SCHEMA_VERSION,
            complete: true,
            pcm_file: "source.f32le".into(),
            encoding: "f32le".into(),
            interleaved: true,
            sample_rate: f64::from(options.sample_rate),
            channels,
            layout: Property::known(expected_layout),
            start_frame: 0,
            requested_frames: frames,
            frames,
            bytes: source_bytes,
            sha256: format!("{:x}", hash.finalize()),
            source: None,
            source_cookie_sha256: None,
            source_packet_table: None,
            environment: env.clone(),
            decoder_settings: BTreeMap::new(),
            all_finite: true,
        };
        case.json("source.json", &source)?;
        let reference_out = case.child("reference")?;
        let reference = decode_into(&encoded_path, &reference_out, 0, frames, &env)?;
        if reference.frames != frames {
            return Err(Error::new(
                "fixture",
                "round trip did not preserve the valid frame count",
            ));
        }
        reference_out.complete()?;
        let case_manifest = json!({"schema_version":SCHEMA_VERSION,"complete":true,"signal":kind,"seed":options.seed,"amplitudes":"impulse=0.5, sine/sweep/channel-solo=0.25, noise=0.2","requested":{"sample_rate":options.sample_rate,"layout":options.layout,"frames":frames,"bitrate":options.bitrate,"quality":options.quality},"actual_encoder_settings":encoder_settings,"encoded":encoded_info,"source_pcm":"source.json","reference_pcm":"reference/pcm.json","note":"APAC is lossy: source PCM is not an equality reference for decoded PCM"});
        case.json("manifest.json", &case_manifest)?;
        case.complete()?;
        cases.push(json!({"signal":kind,"manifest":format!("{}/manifest.json",kind.name()),"reference_sha256":reference.sha256}));
    }
    let manifest =
        json!({"schema_version":SCHEMA_VERSION,"complete":true,"environment":env,"cases":cases});
    out.json("manifest.json", &manifest)?;
    out.complete()?;
    Ok(manifest)
}
