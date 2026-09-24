use super::{FrameContext, parse_cac, parse_frame, parse_spectrum};
use serde::Serialize;

use crate::{
    config::ParseStatus,
    error::{Error, Result},
    model::SCHEMA_VERSION,
    output::{Budget, LimitedWriter},
    packets::{PacketBundle, add},
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, io::Write, path::Path};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum ParseDepth {
    Prefix,
    Spectrum,
    Cac,
}

/// Stream per-packet reports; the integer is the CLI exit code (0/1/2).
pub fn parse_packets(
    directory: &Path,
    output: &Path,
    start: Option<u64>,
    requested: u64,
    limit: u64,
) -> Result<(Value, u8)> {
    parse_packets_with_depth(
        directory,
        output,
        start,
        requested,
        limit,
        ParseDepth::Prefix,
    )
}

pub fn parse_packets_with_depth(
    directory: &Path,
    output: &Path,
    start: Option<u64>,
    requested: u64,
    limit: u64,
    depth: ParseDepth,
) -> Result<(Value, u8)> {
    let (mut spectra, mut left, mut right, mut absent) = (0u64, 0u64, 0u64, 0u64);
    let (mut cac_complete, mut shared_ics) = (0u64, 0u64);
    if requested == 0 {
        return Err(Error::new("parse-packets", "packet count must be positive"));
    }
    let mut bundle = PacketBundle::open(directory)?;
    let context = FrameContext::from_cookie(bundle.cookie()).map_err(|e| {
        let mut error = Error::new("frame context (cookie)", e.to_string());
        error.bit_offset = Some(e.bit_offset);
        error
    })?;
    let first = bundle.manifest().start_packet;
    let bundle_end = add(first, bundle.manifest().actual_packets)?;
    let start = start.unwrap_or(first);
    if start < first || start >= bundle_end {
        return Err(Error::new(
            "parse-packets",
            "start packet is outside the stored range",
        ));
    }
    let end = add(start, requested)?.min(bundle_end);
    let filename = output
        .file_name()
        .ok_or_else(|| Error::new("parse-packets", "invalid output path"))?;
    let mut marker_name = filename.to_os_string();
    marker_name.push(".incomplete");
    let marker = output.with_file_name(marker_name);
    let budget = Budget::new(limit);
    budget.ensure(128)?;
    if let Some(parent) = output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    // Reserve the report first so an existing report is never touched, including
    // when its old incomplete marker is still present.
    let mut writer = LimitedWriter::create(output, budget.clone())?;
    let mut mark = match LimitedWriter::create(&marker, budget) {
        Ok(mark) => mark,
        Err(mut error) => {
            // Close the new, still-empty report before removing it on all platforms.
            drop(writer);
            if let Err(cleanup) = fs::remove_file(output) {
                error.message.push_str(&format!(
                    "; failed to remove new report {}: {cleanup}",
                    output.display()
                ));
            }
            return Err(error);
        }
    };
    mark.write_all(
        b"Packet parsing did not finish successfully; inspect errors before using this report.\n",
    )?;
    mark.finish()?;
    let (mut parsed, mut prefixes, mut errors, mut whole) = (0u64, 0u64, 0u64, 0u64);
    let mut stops: BTreeMap<String, u64> = BTreeMap::new();
    while bundle.consumed_packets() < end - first {
        let (packet, bytes) = bundle
            .next_packet()?
            .ok_or_else(|| Error::new("parse-packets", "unexpected packet EOF"))?;
        if packet.packet_index < start {
            continue;
        }
        eprintln!("parse packet {}", packet.packet_index);
        let result = match depth {
            ParseDepth::Prefix => parse_frame(&context, &bytes).map(|frame| (frame, None)),
            ParseDepth::Spectrum => parse_spectrum(&context, &bytes).map(|spectrum| {
                spectra += u64::from(spectrum.spectrum_complete);
                left += u64::from(!spectrum.channels.is_empty());
                right += u64::from(spectrum.channels.len() == 2);
                absent += u64::from(spectrum.frame.stop_reason == "cpe_absent");
                (
                    spectrum.frame.clone(),
                    Some(serde_json::to_value(spectrum).expect("finite spectrum report")),
                )
            }),
            ParseDepth::Cac => parse_cac(&context, &bytes).map(|cac| {
                spectra += u64::from(cac.spectrum.spectrum_complete);
                left += u64::from(!cac.spectrum.channels.is_empty());
                right += u64::from(cac.spectrum.channels.len() == 2);
                absent += u64::from(cac.spectrum.frame.stop_reason == "cpe_absent");
                cac_complete += u64::from(cac.cac_complete);
                shared_ics += u64::from(cac.shared_ics);
                (
                    cac.spectrum.frame.clone(),
                    Some(serde_json::to_value(cac).expect("finite CAC report")),
                )
            }),
        };
        let row = match result {
            Ok((report, spectrum)) => {
                prefixes += u64::from(report.prefix_complete);
                whole += u64::from(report.status == ParseStatus::Complete);
                *stops.entry(report.stop_reason.clone()).or_default() += 1;
                let report = if let Some(spectrum) = spectrum {
                    spectrum
                } else {
                    serde_json::to_value(&report)?
                };
                json!({"schema_version": SCHEMA_VERSION, "packet_index":packet.packet_index,
                    "export_offset":packet.export_offset, "frames":packet.frames,
                    "raw_frame_position":packet.raw_frame_position, "status":report["status"],
                    "report":report})
            }
            Err(error) => {
                errors += 1;
                json!({"schema_version":SCHEMA_VERSION, "packet_index":packet.packet_index,
                    "export_offset":packet.export_offset, "cookie_sha256":context.cookie_sha256(),
                    "packet_sha256":packet.sha256, "packet_bytes":bytes.len(), "status":"error", "error":error})
            }
        };
        serde_json::to_writer(&mut writer, &row)
            .map_err(Error::from)
            .and_then(|_| writer.write_all(b"\n").map_err(Error::from))
            .map_err(|mut error| {
                error.packet_index = Some(packet.packet_index);
                error
            })?;
        parsed += 1;
    }
    // Validate even the unused tail a second time before removing the marker.
    bundle.verify_remaining()?;
    writer.finish()?;
    if errors == 0 {
        fs::remove_file(marker)?;
    }
    let exit_code = if errors > 0 {
        1
    } else if whole == parsed {
        0
    } else {
        2
    };
    let mut summary = json!({"schema_version":SCHEMA_VERSION, "complete":errors == 0,
        "bundle":directory, "output":output, "context":context,
        "start_packet":start, "requested_packets":requested, "actual_packets":parsed,
        "clipped_at_bundle_end":end - start < requested,
        "prefix_complete_packets":prefixes, "all_prefixes_complete":prefixes == parsed,
        "whole_frame_complete_packets":whole, "errors":errors, "stops":stops,
        "exit_code":exit_code});
    if depth != ParseDepth::Prefix {
        summary["depth"] = json!(depth);
        summary["spectrum_complete_packets"] = json!(spectra);
        summary["left_spectrum_packets"] = json!(left);
        summary["right_spectrum_packets"] = json!(right);
        summary["cpe_absent_packets"] = json!(absent);
    }
    if depth == ParseDepth::Cac {
        summary["cac_complete_packets"] = json!(cac_complete);
        summary["shared_ics_packets"] = json!(shared_ics);
    }
    Ok((summary, exit_code))
}
