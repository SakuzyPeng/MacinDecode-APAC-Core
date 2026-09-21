use clap::{Parser, Subcommand};
#[cfg(target_os = "macos")]
use macindecode_apac_tools::research;
use macindecode_apac_tools::{
    compare,
    error::{Error, Result},
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
    /// Parse a standalone APAC cookie with the platform-independent Rust parser.
    ParseCookie { file: PathBuf },
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

fn run(cli: Cli) -> Result<(Value, bool)> {
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
        return Ok((serde_json::to_value(result)?, passed));
    }
    if let Command::ParseCookie { file } = &cli.command {
        let result = macindecode_apac_tools::config::parse_file(file)?;
        let complete = result.is_complete();
        return Ok((serde_json::to_value(result)?, complete));
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = limit;
        Err(Error::new(
            "platform",
            "this command requires macOS AudioToolbox; compare and parse-cookie are portable",
        ))
    }
    #[cfg(target_os = "macos")]
    {
        let result = match cli.command {
            Command::CollectConfigs { manifest, out } => {
                return macindecode_apac_tools::collect::collect_configs(&manifest, &out, limit);
            }
            Command::Inspect { file } => serde_json::to_value(research::inspect(&file)?)?,
            Command::Scan { directory, output } => {
                return research::scan(&directory, &output, limit);
            }
            Command::Dump {
                file,
                out,
                start_packet,
                packets,
            } => research::dump(&file, &out, start_packet, packets, limit)?,
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
                    },
                    limit,
                )?
            }
            Command::Compare { .. } | Command::ParseCookie { .. } => unreachable!(),
        };
        Ok((result, true))
    }
}
fn main() {
    match run(Cli::parse()) {
        Ok((value, passed)) => {
            let mut stdout = std::io::stdout().lock();
            if let Err(e) = serde_json::to_writer_pretty(&mut stdout, &value)
                .map_err(Error::from)
                .and_then(|_| stdout.write_all(b"\n").map_err(Error::from))
            {
                eprintln!("{e}");
                std::process::exit(1);
            }
            if !passed {
                std::process::exit(2);
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
