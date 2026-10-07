//! Decode a CAF or MP4 file the way a player does: open it reading metadata
//! only, optionally build the whole seek index first, seek, then write raw
//! interleaved little-endian `f32` PCM.
//!
//! ```sh
//! cargo run -p apac-container --example playback -- input.m4a output.f32
//! cargo run -p apac-container --example playback -- input.caf output.f32 --index --start 480000 --frames 8192
//! cargo run -p apac-container --example playback -- input.m4a output.f32 --background
//! ```
//!
//! `--start` and `--frames` select valid-audio frames (priming excluded);
//! `--index` builds the whole seek index before seeking; `--background`
//! builds it on a second thread with an `Indexer` while decoding, merging
//! its batches between reads. The output file must
//! not exist yet. The PCM equals what `mapac decode-sq` and the
//! `decode_file` example write for the same range. Timings and counts go to
//! stderr.
use apac_container::{Error, IndexBatch, Media, Playback, ReadError};
use std::{
    fs::File,
    io::{BufWriter, Write},
    process::ExitCode,
    sync::mpsc,
    thread,
    time::Instant,
};

struct Options {
    input: String,
    output: String,
    index: bool,
    background: bool,
    start: u64,
    frames: Option<u64>,
}

fn options() -> Result<Options, String> {
    let mut args = std::env::args().skip(1);
    let usage = "usage: playback <input.caf|input.mp4> <output.f32> [--index] [--background] [--start N] [--frames N]";
    let input = args.next().ok_or(usage)?;
    let output = args.next().ok_or(usage)?;
    let mut options = Options {
        input,
        output,
        index: false,
        background: false,
        start: 0,
        frames: None,
    };
    while let Some(arg) = args.next() {
        let mut number = || -> Result<u64, String> {
            let value = args.next().ok_or(usage)?;
            value
                .parse()
                .map_err(|_| format!("{arg}: not a frame count: {value}"))
        };
        match arg.as_str() {
            "--index" => options.index = true,
            "--background" => options.background = true,
            "--start" => options.start = number()?,
            "--frames" => options.frames = Some(number()?),
            _ => return Err(usage.into()),
        }
    }
    Ok(options)
}

fn run(options: &Options) -> Result<(), ReadError<Error>> {
    let source = |e: std::io::Error| ReadError::Source(Error::from(e));
    let timer = Instant::now();
    let media =
        Media::open(File::open(&options.input).map_err(source)?).map_err(ReadError::Source)?;
    let mut playback = Playback::open(media)?;
    let info = playback.decoder().info();
    let channels = info.channel_count as usize;
    eprintln!(
        "{}: {} Hz, {channels} channels, {:?}, {} frames; opened in {:.1} ms",
        playback.media().container(),
        info.sample_rate_hz,
        info.kind,
        playback.frames(),
        timer.elapsed().as_secs_f64() * 1e3
    );
    if options.index {
        let timer = Instant::now();
        playback.extend_index(u64::MAX)?;
        eprintln!(
            "indexed {} packets in {:.1} ms: {} checkpoints every {} packets",
            playback.stats().indexed_packets,
            timer.elapsed().as_secs_f64() * 1e3,
            playback.checkpoints(),
            playback.checkpoint_interval()
        );
    }
    // The indexer thread scans a second handle to the input and sends what
    // it collected every 256 packets; the batches are merged between reads.
    let mut background = None;
    if options.background {
        let mut indexer = playback.indexer(File::open(&options.input).map_err(source)?)?;
        let (batches, received) = mpsc::channel::<IndexBatch>();
        let timer = Instant::now();
        let worker = thread::spawn(move || {
            loop {
                let result = indexer.run(256);
                if batches.send(indexer.take_batch()).is_err() || !matches!(result, Ok(false)) {
                    return result.map(|_| (indexer.scanned_packets(), timer.elapsed()));
                }
            }
        });
        background = Some((worker, received));
    }
    let merge = |playback: &mut Playback<File>, received: &mpsc::Receiver<IndexBatch>| {
        received
            .try_iter()
            .try_for_each(|batch| playback.merge_index(batch).map(|_| ()))
    };
    let file = File::create_new(&options.output).map_err(source)?;
    let mut output = BufWriter::new(file);
    let mut pcm = vec![0f32; 1024 * channels];
    let timer = Instant::now();
    playback.seek(options.start)?;
    let mut left = options.frames.unwrap_or(u64::MAX);
    let mut written = 0;
    let mut first = true;
    while left > 0 {
        if let Some((_, received)) = &background {
            merge(&mut playback, received)?;
        }
        let frames = playback.read(&mut pcm)?;
        if first {
            let stats = playback.stats();
            eprintln!(
                "seek to {} and first read in {:.1} ms: advanced {}, decoded {} packets",
                options.start,
                timer.elapsed().as_secs_f64() * 1e3,
                stats.advanced_packets,
                stats.decoded_packets
            );
            first = false;
        }
        if frames == 0 {
            break;
        }
        let frames = frames.min(usize::try_from(left).unwrap_or(usize::MAX));
        for sample in &pcm[..frames * channels] {
            output.write_all(&sample.to_le_bytes()).map_err(source)?;
        }
        left -= frames as u64;
        written += frames as u64;
    }
    output.flush().map_err(source)?;
    let wrote = timer.elapsed();
    if let Some((worker, received)) = background {
        let (packets, elapsed) = worker.join().expect("indexer thread")?;
        merge(&mut playback, &received)?;
        eprintln!(
            "background indexer scanned {packets} packets in {:.1} ms; index complete: {}, {} checkpoints every {} packets",
            elapsed.as_secs_f64() * 1e3,
            playback.index_complete(),
            playback.checkpoints(),
            playback.checkpoint_interval()
        );
    }
    let stats = playback.stats();
    eprintln!(
        "wrote frames {}..{} in {:.1} ms; advanced {}, decoded {} packets ({} warm-up)",
        options.start,
        options.start + written,
        wrote.as_secs_f64() * 1e3,
        stats.advanced_packets,
        stats.decoded_packets,
        stats.warmup_packets
    );
    Ok(())
}

fn main() -> ExitCode {
    let options = match options() {
        Ok(options) => options,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };
    match run(&options) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            match &error {
                ReadError::Decode {
                    packet_index: Some(index),
                    ..
                } => eprintln!("{error} (packet {index})"),
                _ => eprintln!("{error}"),
            }
            ExitCode::FAILURE
        }
    }
}
