//! Configuration-only corpus extraction. Original media are never copied.
use crate::{
    error::{Error, Result},
    model::{Environment, FileInfo, SCHEMA_VERSION, sha256},
    native::NativeFile,
    output::{Budget, OutputDir},
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs::File, io::Read, path::Path};

const MAX_MANIFEST_BYTES: u64 = 64 * 1024 * 1024;

pub fn collect_configs(manifest: &Path, destination: &Path, limit: u64) -> Result<(Value, bool)> {
    let marker = manifest.with_file_name(format!(
        "{}.incomplete",
        manifest
            .file_name()
            .ok_or_else(|| Error::new("collect-configs", "invalid manifest path"))?
            .to_string_lossy()
    ));
    if marker.try_exists()? {
        return Err(Error::new(
            "collect-configs",
            format!(
                "scan manifest still has an incomplete marker: {}; finish the scan or rescan before collecting",
                marker.display()
            ),
        ));
    }
    let mut text = String::new();
    File::open(manifest)?
        .take(MAX_MANIFEST_BYTES + 1)
        .read_to_string(&mut text)?;
    if text.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(Error::new("collect-configs", "manifest exceeds 64 MiB"));
    }
    let mut groups: BTreeMap<String, Vec<FileInfo>> = BTreeMap::new();
    let mut input_errors = vec![];
    let mut source_count = 0u64;
    for (line, raw) in text.lines().enumerate() {
        if raw.trim().is_empty() {
            continue;
        }
        let record = (|| -> Result<FileInfo> {
            let value: Value = serde_json::from_str(raw)?;
            if value["schema_version"] != SCHEMA_VERSION || value["status"] != "ok" {
                return Err(Error::new(
                    "manifest record",
                    "requires a successful schema v1 scan record",
                ));
            }
            let info: FileInfo = serde_json::from_value(value["file"].clone())?;
            if info.schema_version != SCHEMA_VERSION || info.format.format_fourcc != "apac" {
                return Err(Error::new(
                    "manifest record",
                    "requires schema v1 Apple APAC metadata",
                ));
            }
            let cookie = info
                .cookie
                .value
                .as_ref()
                .ok_or_else(|| Error::new("manifest record", "cookie metadata unavailable"))?;
            if cookie.sha256.len() != 64
                || !cookie.sha256.bytes().all(|b| b.is_ascii_hexdigit())
                || cookie.bytes > 8 * 1024 * 1024
            {
                return Err(Error::new(
                    "manifest record",
                    "invalid cookie hash or length",
                ));
            }
            Ok(info)
        })();
        match record {
            Ok(info) => {
                source_count += 1;
                groups
                    .entry(
                        info.cookie
                            .value
                            .as_ref()
                            .unwrap()
                            .sha256
                            .to_ascii_lowercase(),
                    )
                    .or_default()
                    .push(info);
            }
            Err(error) => input_errors.push(json!({"line":line+1,"error":error})),
        }
    }
    if source_count == 0 && input_errors.is_empty() {
        return Err(Error::new(
            "collect-configs",
            "manifest contains no records",
        ));
    }
    let out = OutputDir::create(destination, Budget::new(limit))?;
    let cookies_out = out.child("cookies")?;
    let mut records = vec![];
    let mut collected = 0u64;
    for (hash, mut sources) in groups {
        sources.sort_by(|a, b| {
            a.file_bytes
                .cmp(&b.file_bytes)
                .then(a.source.cmp(&b.source))
        });
        let mut attempts = vec![];
        let mut selected = None;
        let mut byte_count = None;
        for source in &sources {
            let result = (|| -> Result<Vec<u8>> {
                let file = NativeFile::open(&source.source)?;
                if file.format().format_fourcc != "apac" {
                    return Err(Error::new(
                        "collect-configs",
                        "source no longer contains Apple APAC",
                    ));
                }
                let bytes = file.cookie()?;
                if bytes.len() != source.cookie.value.as_ref().unwrap().bytes
                    || sha256(&bytes) != hash
                {
                    return Err(Error::new(
                        "collect-configs",
                        "source cookie differs from manifest hash/length; rescan the source",
                    ));
                }
                Ok(bytes)
            })();
            match result {
                Ok(bytes) => {
                    cookies_out.bytes(&format!("{hash}.bin"), &bytes)?;
                    byte_count = Some(bytes.len());
                    selected = Some(source.source.clone());
                    collected += 1;
                    break;
                }
                Err(error) => attempts.push(json!({"source":source.source,"error":error})),
            }
        }
        eprintln!(
            "collect-configs: {hash} {}",
            if selected.is_some() {
                "collected"
            } else {
                "failed"
            }
        );
        records.push(json!({"sha256":hash,"status":if selected.is_some(){"collected"}else{"error"},"cookie_file":selected.as_ref().map(|_|format!("cookies/{hash}.bin")),"cookie_bytes":byte_count,"selected_source":selected,"sources":sources,"failed_attempts":attempts}));
    }
    cookies_out.complete()?;
    let failed = records.len() as u64 - collected;
    let passed = failed == 0 && input_errors.is_empty() && collected > 0;
    let index = json!({"schema_version":SCHEMA_VERSION,"complete":true,"all_collected":passed,"manifest":std::fs::canonicalize(manifest)?,"environment":Environment::current(),"source_records":source_count,"unique_configs":records.len(),"collected":collected,"failed":failed,"input_errors":input_errors,"configs":records});
    out.json("index.json", &index)?;
    out.complete()?;
    Ok((
        json!({"schema_version":SCHEMA_VERSION,"complete":true,"all_collected":passed,"index":destination.join("index.json"),"source_records":source_count,"unique_configs":index["unique_configs"],"collected":collected,"failed":failed,"input_error_count":index["input_errors"].as_array().unwrap().len()}),
        passed,
    ))
}
