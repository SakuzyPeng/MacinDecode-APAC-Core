//! Decode a CAF or MP4 file to raw interleaved little-endian `f32` PCM.
//!
//! ```sh
//! cargo run -p apac-container --example decode_file -- input.caf output.f32
//! cargo run -p apac-container --example decode_file -- input.mp4 output.f32 --fast --start 48000 --frames 96000
//! ```
//!
//! `--start` and `--frames` select valid-audio frames (priming excluded);
//! `--fast` scans the prefix with state-only access. The output file must not
//! exist yet. The PCM equals what `mapac decode-sq` writes for the same
//! range.
use apac_container::{Access, CafReader, Error, Mp4Reader, PacketSource, ReadError, Reader};
use std::{
    fs::File,
    io::{BufWriter, Read, Write},
    process::ExitCode,
};

struct Options {
    input: String,
    output: String,
    access: Access,
    start: Option<u64>,
    frames: Option<u64>,
}

fn options() -> Result<Options, String> {
    let mut args = std::env::args().skip(1);
    let usage =
        "usage: decode_file <input.caf|input.mp4> <output.f32> [--fast] [--start N] [--frames N]";
    let input = args.next().ok_or(usage)?;
    let output = args.next().ok_or(usage)?;
    let mut options = Options {
        input,
        output,
        access: Access::Sequential,
        start: None,
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
            "--fast" => options.access = Access::Fast,
            "--start" => options.start = Some(number()?),
            "--frames" => options.frames = Some(number()?),
            _ => return Err(usage.into()),
        }
    }
    Ok(options)
}

fn decode<S: PacketSource<Error = Error>>(
    source: S,
    options: &Options,
) -> Result<(), ReadError<Error>> {
    let mut reader = Reader::open(source, options.start, options.frames, options.access)?;
    let info = reader.decoder().info();
    let channels = info.channel_count as usize;
    let range = *reader.range();
    eprintln!(
        "{} Hz, {channels} channels, {:?}: frames {}..{}",
        info.sample_rate_hz,
        info.kind,
        range.start_frame,
        range.start_frame + range.frames
    );
    let file = File::create_new(&options.output).map_err(|e| ReadError::Source(e.into()))?;
    let mut output = BufWriter::new(file);
    let mut pcm = vec![0f32; 1024 * channels];
    loop {
        let frames = reader.read(&mut pcm)?;
        if frames == 0 {
            break;
        }
        for sample in &pcm[..frames * channels] {
            output
                .write_all(&sample.to_le_bytes())
                .map_err(|e| ReadError::Source(e.into()))?;
        }
    }
    output.flush().map_err(|e| ReadError::Source(e.into()))?;
    let (_, _, stats) = reader.finish()?;
    eprintln!(
        "wrote {} frames; decoded {} packets, scanned {}, verified the whole file",
        stats.saved_frames, stats.decoded_packets, stats.prefix_packets
    );
    Ok(())
}

fn run(options: &Options) -> Result<(), ReadError<Error>> {
    let source = |e: std::io::Error| ReadError::Source(Error::from(e));
    let mut file = File::open(&options.input).map_err(source)?;
    let mut magic = [0u8; 4];
    let caf = file.read_exact(&mut magic).is_ok() && &magic == b"caff";
    let file = File::open(&options.input).map_err(source)?;
    if caf {
        decode(CafReader::new(file).map_err(ReadError::Source)?, options)
    } else {
        decode(Mp4Reader::new(file).map_err(ReadError::Source)?, options)
    }
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
