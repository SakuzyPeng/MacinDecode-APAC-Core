use clap::{Parser, Subcommand};
#[cfg(target_os = "macos")]
use macindecode_apac_tools::research;
use macindecode_apac_tools::{
    compare,
    error::{Error, Result},
    model::DrcConfiguration,
    signal::{LayoutPreset, Signal},
};
use serde_json::{Value, json};
use std::{io::Write, path::PathBuf};

#[derive(Parser)]
#[command(
    name = "apac-tool",
    version,
    about = "Apple APAC research tools; native codec operations require macOS"
)]
struct Cli {
    /// Maximum cumulative bytes written by an export command (MiB).
    #[arg(long, global = true, default_value_t = 128)]
    max_output_mib: u64,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Experimental portable PCM for the restricted SQ/CAC/TNS/BWE2 subset.
    DecodeSq {
        directory: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Parse a standalone APAC cookie with the platform-independent Rust parser.
    ParseCookie { file: PathBuf },
    /// Inspect SQ prefixes, raw spectra, CAC, TNS or BWE2 from a validated packet bundle.
    ParsePackets {
        directory: PathBuf,
        #[arg(long, value_enum, default_value_t = macindecode_apac_tools::frame::ParseDepth::Prefix)]
        depth: macindecode_apac_tools::frame::ParseDepth,
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
        let result = macindecode_apac_tools::config::parse_file(file)?;
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
        return macindecode_apac_tools::frame::parse_packets_with_depth(
            directory,
            output,
            *start_packet,
            *packets,
            limit,
            *depth,
        );
    }
    if let Command::DecodeSq { directory, out } = &cli.command {
        return Ok((
            macindecode_apac_tools::synthesis::decode_sq(directory, out, limit)?,
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
                return macindecode_apac_tools::collect::collect_configs(&manifest, &out, limit)
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
            } => macindecode_apac_tools::replay::replay(
                &directory,
                &out,
                start_frame,
                frames,
                input_batch_packets,
                limit,
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
