//! Measure how much faster than real time `Playback` decodes CAF or MP4
//! files on one thread, and how long single reads take against the audio
//! they produce.
//!
//! ```sh
//! cargo run --release -p apac-container --example realtime -- a.caf b.m4a
//! cargo run --release -p apac-container --example realtime -- --repeat 5 *.caf
//! ```
//!
//! Each file is opened, decoded from start to end `--repeat` times (default
//! 3) and indexed once on a fresh `Playback`. The speed is the decoded audio
//! duration over the decoding time; the median pass is reported. Read times
//! cover one `Playback::read` call (at most one packet, 1024 frames) over all
//! passes and are compared with the 1024-frame budget at the stream's sample
//! rate, which a decoder feeding an audio callback has to meet. One line per
//! file goes to stdout, errors to stderr.
use apac_container::{Error, Media, Playback, ReadError};
use std::{fs::File, process::ExitCode, time::Instant};

struct Row {
    kind: String,
    channels: usize,
    rate: u64,
    seconds: f64,
    open_ms: f64,
    index_speed: f64,
    speeds: Vec<f64>,
    reads_ms: Vec<f64>,
}

fn open(path: &str) -> Result<Playback<File>, ReadError<Error>> {
    let file = File::open(path).map_err(|e| ReadError::Source(Error::from(e)))?;
    Playback::open(Media::open(file).map_err(ReadError::Source)?)
}

fn measure(path: &str, repeat: usize) -> Result<Row, ReadError<Error>> {
    let timer = Instant::now();
    let mut playback = open(path)?;
    let open_ms = timer.elapsed().as_secs_f64() * 1e3;
    let info = playback.decoder().info();
    let (channels, rate) = (info.channel_count as usize, info.sample_rate_hz);
    let kind = format!("{:?}", info.kind);
    let seconds = playback.frames() as f64 / rate as f64;
    let mut pcm = vec![0f32; 1024 * channels];
    let mut speeds = vec![];
    let mut reads_ms = vec![];
    for _ in 0..repeat {
        playback.seek(0)?;
        let pass = Instant::now();
        loop {
            let read = Instant::now();
            let frames = playback.read(&mut pcm)?;
            if frames == 0 {
                break;
            }
            reads_ms.push(read.elapsed().as_secs_f64() * 1e3);
        }
        speeds.push(seconds / pass.elapsed().as_secs_f64());
    }
    let mut fresh = open(path)?;
    let timer = Instant::now();
    fresh.extend_index(u64::MAX)?;
    let index_speed = seconds / timer.elapsed().as_secs_f64();
    Ok(Row {
        kind,
        channels,
        rate,
        seconds,
        open_ms,
        index_speed,
        speeds,
        reads_ms,
    })
}

/// The value at quantile `q` of sorted `values`.
fn quantile(values: &[f64], q: f64) -> f64 {
    values[((values.len() - 1) as f64 * q).round() as usize]
}

fn main() -> ExitCode {
    let mut repeat = 3;
    let mut paths = vec![];
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--repeat" {
            match args.next().and_then(|v| v.parse().ok()).filter(|&n| n > 0) {
                Some(n) => repeat = n,
                None => {
                    eprintln!("--repeat: expected a positive count");
                    return ExitCode::from(2);
                }
            }
        } else {
            paths.push(arg);
        }
    }
    if paths.is_empty() {
        eprintln!("usage: realtime [--repeat N] <input.caf|input.mp4>...");
        return ExitCode::from(2);
    }
    println!(
        "| file | kind | ch | Hz | audio s | open ms | index ×RT | decode ×RT (min–max) | \
         read p50 / p99 / max ms | budget ms | max / budget |"
    );
    println!("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|");
    let mut failed = false;
    for path in &paths {
        let mut row = match measure(path, repeat) {
            Ok(row) => row,
            Err(error) => {
                eprintln!("{path}: {error}");
                failed = true;
                continue;
            }
        };
        row.speeds.sort_by(f64::total_cmp);
        row.reads_ms.sort_by(f64::total_cmp);
        let budget_ms = 1024e3 / row.rate as f64;
        let max_ms = row.reads_ms.last().copied().unwrap_or(0.0);
        let name = std::path::Path::new(path)
            .file_name()
            .map_or(path.clone(), |n| n.to_string_lossy().into_owned());
        println!(
            "| {name} | {} | {} | {} | {:.1} | {:.2} | {:.0} | {:.1} ({:.1}–{:.1}) | \
             {:.3} / {:.3} / {:.3} | {budget_ms:.2} | {:.1}% |",
            row.kind,
            row.channels,
            row.rate,
            row.seconds,
            row.open_ms,
            row.index_speed,
            quantile(&row.speeds, 0.5),
            row.speeds[0],
            row.speeds[row.speeds.len() - 1],
            quantile(&row.reads_ms, 0.5),
            quantile(&row.reads_ms, 0.99),
            max_ms,
            100.0 * max_ms / budget_ms,
        );
    }
    if failed {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}
