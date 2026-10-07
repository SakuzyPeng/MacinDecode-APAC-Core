#[cfg(target_os = "macos")]
use apac_native::research;
use apac_research::{
    compare,
    error::{Error, Result},
    model::DrcConfiguration,
    signal::{LayoutPreset, Signal},
};
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use std::{io::Write, path::PathBuf};

#[derive(Parser)]
#[command(
    name = "mapac",
    version,
    about = "Decode and inspect Apple Positional Audio Codec (APAC) streams; native reference commands require macOS"
)]
struct Cli {
    /// Output limit in MiB (default: unlimited for -o files, 128 for directory exports).
    #[arg(long, global = true)]
    max_output_mib: Option<u64>,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Decode APAC to a WAV/RF64/CAF file or a raw PCM research directory.
    DecodeSq {
        /// Complete packet directory, CAF v1 or single-audio-track MP4/M4A (recognized by content).
        input: PathBuf,
        /// New research directory containing raw Float32 PCM and JSON reports.
        #[arg(long, required_unless_present = "output", conflicts_with = "output")]
        out: Option<PathBuf>,
        /// New WAV/RF64/CAF file; existing paths are never overwritten.
        #[arg(
            short = 'o',
            long,
            required_unless_present = "out",
            conflicts_with = "out"
        )]
        output: Option<PathBuf>,
        /// Output container; otherwise inferred from the file extension. WAV auto-selects RF64 for large files.
        #[arg(
            long,
            requires = "output",
            conflicts_with = "out",
            value_name = "wav|rf64|caf"
        )]
        format: Option<apac_research::decode::PcmFormat>,
        /// Correct CAF layout tags only; must match APAC configuration. Names: mono, stereo, 5.1, 7.1, 7.1.4, 9.1.6, 22.2, hoa0..hoa3 (optional -n3d).
        #[arg(long, value_parser = apac_research::decode::parse_input_layout, value_name = "LAYOUT")]
        input_layout: Option<apac_research::model::ChannelLayout>,
        /// Absolute valid-audio frame; container inputs warm up sequentially from packet zero.
        #[arg(long)]
        start_frame: Option<u64>,
        /// Omitted exports the remaining target window, or all valid container audio.
        #[arg(long)]
        frames: Option<u64>,
        /// Container access: sequential (default), or fast prefix scanning with full input verification.
        #[arg(long, value_enum)]
        access: Option<apac_research::decode::SqAccessMode>,
    },
    /// Parse a standalone APAC cookie with the platform-independent Rust parser.
    ParseCookie { file: PathBuf },
    /// Inspect SQ stages or complete channel/HOA packets from a validated packet bundle.
    ParsePackets {
        directory: PathBuf,
        #[arg(long, value_enum, default_value_t = apac_research::parse_packets::ParseDepth::Prefix)]
        depth: apac_research::parse_packets::ParseDepth,
        #[arg(long)]
        output: PathBuf,
        /// Source packet index; omitted starts at the first stored packet, including preroll.
        #[arg(long)]
        start_packet: Option<u64>,
        #[arg(long, default_value_t = 150)]
        packets: u64,
    },
    /// Collect deduplicated, hash-verified APAC cookies from an existing scan JSONL.
    CollectConfigs {
        manifest: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Inspect native format, layout, packet table and magic cookie fingerprint.
    Inspect { file: PathBuf },
    /// Recursively index CAF/M4A files without decoding their audio.
    Scan {
        directory: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Export a raw packet range, cookie and dependency metadata.
    Dump {
        file: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 0)]
        start_packet: u64,
        #[arg(long, default_value_t = 150)]
        packets: u64,
        /// Include prerequisite packets and a replayable target window.
        #[arg(long)]
        with_preroll: bool,
    },
    /// Replay an exported packet directory through Apple's reference converter.
    Replay {
        directory: PathBuf,
        #[arg(long)]
        out: PathBuf,
        /// Absolute valid-audio frame; omitted starts at the exported target window.
        #[arg(long)]
        start_frame: Option<u64>,
        #[arg(long, default_value_t = 8192)]
        frames: u64,
        #[arg(long, default_value_t = 1, value_parser = clap::value_parser!(u32).range(1..=64))]
        input_batch_packets: u32,
        /// Explicit native reference policy; default preserves host behavior.
        #[arg(long, value_enum, default_value_t = apac_research::model::NativeProcessingPolicy::Default)]
        processing_policy: apac_research::model::NativeProcessingPolicy,
    },
    /// Decode a short valid-frame range to interleaved Float32 + JSON.
    Decode {
        file: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 0)]
        start_frame: u64,
        #[arg(long, default_value_t = 8192)]
        frames: u64,
    },
    /// Generate deterministic source PCM, Apple-encoded APAC and reference PCM.
    Fixture {
        #[arg(long)]
        out: PathBuf,
        #[arg(long, value_enum, default_value_t = LayoutPreset::Stereo)]
        layout: LayoutPreset,
        #[arg(long, default_value_t = 48000)]
        sample_rate: u32,
        #[arg(long, default_value_t = 2.)]
        duration: f64,
        #[arg(long, default_value_t = 1)]
        seed: u64,
        /// Comma-separated signals; omitted means all six.
        #[arg(long, value_enum, value_delimiter = ',')]
        signals: Vec<Signal>,
        #[arg(long)]
        bitrate: Option<u32>,
        #[arg(long)]
        quality: Option<u32>,
        /// Encoder DRC configuration; omitted preserves the system default.
        #[arg(long, value_enum)]
        drc_configuration: Option<DrcConfiguration>,
    },
    /// Compare bundles without alignment, gain correction, resampling or padding.
    Compare {
        reference: PathBuf,
        candidate: PathBuf,
        #[arg(long, default_value_t = 1e-6)]
        atol: f64,
        #[arg(long, default_value_t = 1e-5)]
        rtol: f64,
    },
}

fn run(cli: Cli) -> Result<(Value, u8)> {
    let limit = cli
        .max_output_mib
        .unwrap_or(128)
        .checked_mul(1024 * 1024)
        .ok_or_else(|| Error::new("output limit", "MiB value overflow"))?;
    if let Command::Compare {
        reference,
        candidate,
        atol,
        rtol,
    } = &cli.command
    {
        let result = compare::compare(reference, candidate, *atol, *rtol)?;
        let passed = result.passed;
        return Ok((serde_json::to_value(result)?, if passed { 0 } else { 2 }));
    }
    if let Command::ParseCookie { file } = &cli.command {
        let result = apac_research::config::parse_file(file)?;
        let complete = result.is_complete();
        return Ok((serde_json::to_value(result)?, if complete { 0 } else { 2 }));
    }
    if let Command::ParsePackets {
        depth,
        directory,
        output,
        start_packet,
        packets,
    } = &cli.command
    {
        return apac_research::parse_packets::parse_packets_with_depth(
            directory,
            output,
            *start_packet,
            *packets,
            limit,
            *depth,
        );
    }
    if let Command::DecodeSq {
        input,
        out,
        output,
        format,
        input_layout,
        start_frame,
        frames,
        access,
    } = &cli.command
    {
        return Ok((
            {
                let options = apac_research::decode::SqDecodeOptions {
                    start_frame: *start_frame,
                    frames: *frames,
                };
                if let Some(path) = output {
                    let format = match format {
                        Some(format) => *format,
                        None => match path
                            .extension()
                            .and_then(|s| s.to_str())
                            .map(str::to_ascii_lowercase)
                            .as_deref()
                        {
                            Some("wav" | "wave") => apac_research::decode::PcmFormat::Wav,
                            Some("rf64") => apac_research::decode::PcmFormat::Rf64,
                            Some("caf") => apac_research::decode::PcmFormat::Caf,
                            _ => {
                                return Err(Error::new(
                                    "PCM output",
                                    "unknown output extension; specify --format wav, rf64 or caf",
                                ));
                            }
                        },
                    };
                    if let Some(layout) = input_layout {
                        apac_research::decode::decode_sq_with_input_layout(
                            input,
                            path,
                            options,
                            *access,
                            Some(format),
                            layout,
                            cli.max_output_mib.map(|_| limit),
                        )?
                    } else {
                        apac_research::decode::decode_sq_to_file(
                            input,
                            path,
                            options,
                            *access,
                            format,
                            cli.max_output_mib.map(|_| limit),
                        )?
                    }
                } else if let Some(layout) = input_layout {
                    let out = out.as_ref().expect("clap requires a destination");
                    apac_research::decode::decode_sq_with_input_layout(
                        input,
                        out,
                        options,
                        *access,
                        None,
                        layout,
                        Some(limit),
                    )?
                } else if let Some(mode) = access {
                    let out = out.as_ref().expect("clap requires a destination");
                    apac_research::decode::decode_sq_with_access(input, out, options, *mode, limit)?
                } else {
                    let out = out.as_ref().expect("clap requires a destination");
                    apac_research::decode::decode_sq_with_options(input, out, options, limit)?
                }
            },
            0,
        ));
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = limit;
        Err(Error::new(
            "platform",
            "this command requires macOS AudioToolbox; compare, parse-cookie, parse-packets and decode-sq are portable",
        ))
    }
    #[cfg(target_os = "macos")]
    {
        let result = match cli.command {
            Command::CollectConfigs { manifest, out } => {
                return apac_native::collect::collect_configs(&manifest, &out, limit)
                    .map(|(value, passed)| (value, if passed { 0 } else { 2 }));
            }
            Command::Inspect { file } => serde_json::to_value(research::inspect(&file)?)?,
            Command::Scan { directory, output } => {
                return research::scan(&directory, &output, limit)
                    .map(|(value, passed)| (value, if passed { 0 } else { 2 }));
            }
            Command::Dump {
                file,
                out,
                start_packet,
                packets,
                with_preroll,
            } => research::dump_with_options(
                &file,
                &out,
                start_packet,
                packets,
                with_preroll,
                limit,
            )?,
            Command::Replay {
                directory,
                out,
                start_frame,
                frames,
                input_batch_packets,
                processing_policy,
            } => apac_native::replay::replay_with_policy(
                &directory,
                &out,
                start_frame,
                frames,
                input_batch_packets,
                limit,
                processing_policy,
            )?,
            Command::Decode {
                file,
                out,
                start_frame,
                frames,
            } => serde_json::to_value(research::decode(&file, &out, start_frame, frames, limit)?)?,
            Command::Fixture {
                out,
                layout,
                sample_rate,
                duration,
                seed,
                mut signals,
                bitrate,
                quality,
                drc_configuration,
            } => {
                if signals.is_empty() {
                    signals = Signal::ALL.to_vec();
                }
                research::fixture(
                    &out,
                    &research::FixtureOptions {
                        layout,
                        sample_rate,
                        duration,
                        seed,
                        signals,
                        bitrate,
                        quality,
                        drc_configuration,
                    },
                    limit,
                )?
            }
            Command::Compare { .. }
            | Command::ParseCookie { .. }
            | Command::ParsePackets { .. }
            | Command::DecodeSq { .. } => unreachable!(),
        };
        Ok((result, 0))
    }
}
fn main() {
    match run(Cli::parse()) {
        Ok((value, exit_code)) => {
            let mut stdout = std::io::stdout().lock();
            if let Err(e) = serde_json::to_writer_pretty(&mut stdout, &value)
                .map_err(Error::from)
                .and_then(|_| stdout.write_all(b"\n").map_err(Error::from))
            {
                eprintln!("{e}");
                std::process::exit(1);
            }
            if exit_code != 0 {
                std::process::exit(i32::from(exit_code));
            }
        }
        Err(error) => {
            eprintln!(
                "{}",
                json!({"schema_version":1,"status":"error","error":error})
            );
            std::process::exit(1);
        }
    }
}
