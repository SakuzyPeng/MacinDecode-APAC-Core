# MacinDecode APAC Core

[简体中文](README.md) · **English**

[![Build](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml/badge.svg)](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml)

A command-line tool and Rust library for **decoding Apple Positional Audio Codec (APAC) spatial audio
and inspecting its bitstream**. The CLI is called `mapac`. It brings its own decoder, runs on
Windows, Linux and macOS, and exports **WAV/RF64 or CAF files** containing Float32 PCM, with a JSON decoding report on stdout.

It accepts APAC `.m4a`, `.mp4` and `.caf` files, plus exported packet bundles. Output preserves the
channels or HOA coefficients for further analysis, processing or integration into a player. The CLI itself does not play audio.

This page covers downloading, getting started and building. **See the [command reference](guide/commands.md)
for all commands and options, and the [decoding guide](guide/decoding.md) for decoding modes and Rust APIs.**
The detailed guides are in Chinese; this quickstart is available in English.

## Contents

- [What you can do with it](#what-you-can-do-with-it)
- [Getting the CLI](#getting-the-cli)
- [Your first decode](#your-first-decode)
- [Inspecting and comparing](#inspecting-and-comparing)
- [Platform support](#platform-support)
- [Before you start](#before-you-start)
- [Building from source](#building-from-source)
- [Using the Rust libraries](#using-the-rust-libraries)
- [Documentation](#documentation)
- [License](#license)

## What you can do with it

- **Decode APAC files to audio files:** choose WAV/RF64 or CAF for mono, stereo, 5.1, 7.1, 7.1.4, 9.1.6 and 22.2, preserving the input sample rate and channel layout.
- **Export HOA audio:** Higher-Order Ambisonics from order zero to ten, as ACN/SN3D coefficients or the source channels declared by the stream.
- **Take just the part you need:** specify a start frame and length, with optional fast range access for short excerpts from long files.
- **Inspect the bitstream:** parse standalone configuration data and exported audio packets into fields, bit positions, metadata and intermediate spectra.
- **Compare two decodes:** verify PCM format, layout and hashes, then report per-channel errors without automatic alignment or gain adjustment.
- **Use the decoder in your own program:** the core supports `no_std` + `alloc`; file APIs provide sequential decoding, frame-exact seeking and playback checkpoints.

## Getting the CLI

Versioned CLI packages and release notes are distributed through GitHub Releases. Open the version's **Assets** section and choose the archive for your computer. [GitHub Releases](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/releases)

| Your computer | v0.2.0 archive |
| --- | --- |
| Windows 10/11, 64-bit Intel / AMD PC | `mapac-v0.2.0-x86_64-pc-windows-msvc.zip` |
| Linux, 64-bit Intel / AMD PC, glibc 2.35 or later (such as Ubuntu 22.04) | `mapac-v0.2.0-x86_64-unknown-linux-gnu.tar.gz` |
| Apple silicon Mac (M1 or newer, tested on macOS 26) | `mapac-v0.2.0-aarch64-apple-darwin.tar.gz` |

Extract the archive and run the executable from a terminal. Packages run without installing Rust or an additional decoder. Documentation, licenses and `build-info.json` are included, with an adjacent `.sha256` checksum file.
v0.2.0 supports HOA orders zero through ten. Distributed binaries return `bwe2-unavailable` for active BWE2 frames. See the release notes for features and limits, and development for checksum commands. [v0.2.0 release notes](guide/releases/v0.2.0.md) · [Download verification](guide/development.md#ci-与构建产物)

Development packages are available under **Artifacts** in successful full Build runs as `mapac-TARGET`, containing a `mapac-TARGET-SHA12` archive. You can also build the current `main` branch. [Build workflow](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml) · [Source build](#building-from-source)

## Your first decode

1. Extract the download or follow the [source build steps](#building-from-source), then open a terminal in the executable directory.
2. Run `--version` or `--help` to check that the program starts.
3. Replace `input.m4a` below with the path to your APAC file and export a WAV.

**Windows PowerShell:**

```powershell
.\mapac.exe --version
.\mapac.exe decode-sq "input.m4a" -o output.wav
```

**macOS / Linux:**

```sh
./mapac --version
./mapac decode-sq "input.m4a" -o output.wav
```

A successful export creates `output.wav` for audio tools that support floating-point WAV. The destination
must not already exist. A JSON report goes to stdout; no additional JSON files are created.

| Output file | Format and supported layouts |
| --- | --- |
| `.wav` / `.wave` | Float32 WAV; mono, stereo, 5.1, 7.1 and 7.1.4; automatically uses RF64 beyond the RIFF size limit |
| `.rf64` | Forced RF64, including files larger than 4 GiB; the same layouts as WAV |
| `.caf` | Float32 LPCM CAF; all supported decoded layouts, including 9.1.6, 22.2, HOA and composite streams |

**Use CAF for 9.1.6, 22.2 or HOA audio:**

```sh
./mapac decode-sq "spatial.m4a" -o output.caf
```

The exported CAF contains PCM audio. Layouts that WAV cannot represent accurately fail with guidance to
use CAF, rather than being exported as anonymous channels. WAV uses its standard speaker order, with rear
surrounds before side surrounds in 7.1/7.1.4; CAF retains the source channel order. Neither resamples,
downmixes or changes sample precision. Use `--format wav|rf64|caf` to select the container explicitly.

For an incorrect CAF layout tag, explicitly supply the expected layout:

```sh
./mapac decode-sq "input.caf" -o output.caf --input-layout 9.1.6
```

The choice must match the APAC configuration. It only corrects container metadata and records the
original declaration in the report. Missing CAF layout chunks already fall back to the APAC configuration.
This option does not relabel output, modify the bitstream or apply to MP4/M4A.

File output has **no default total-size limit** and streams in bounded blocks. Add `--max-output-mib 1024`
when a size limit is wanted. `--start-frame` and `--frames` count audio frames; this example takes one
second starting ten seconds into a 48 kHz file:

```sh
./mapac decode-sq "input.caf" -o window.wav \
  --start-frame 480000 --frames 48000 --access fast
```

For research or PCM comparison, `--out decoded` still creates raw `pcm.f32le`, `pcm.json` and
`decode-sq.json`, with the existing **128 MiB** default limit. `--out` and `-o` are mutually exclusive.
Only raw PCM needs its sample rate and channel count entered manually from the JSON metadata.

Later examples use macOS / Linux syntax. In Windows PowerShell, replace `./mapac` with
`.\mapac.exe` and write multiline commands on one line.

## Inspecting and comparing

Inspect standalone configuration files and exported packet bundles. `compare` takes the raw PCM bundles produced by `--out`:

```sh
./mapac parse-cookie cookie.bin
./mapac parse-packets packets/ --depth cac --output cac.jsonl
./mapac compare reference/pcm.json decoded/pcm.json
```

`parse-cookie` takes a standalone magic cookie; `parse-packets` takes a packet directory.
Use `decode-sq` to decode `.m4a`, `.mp4` or `.caf` directly. macOS also provides `inspect`, `dump`
and other reference tools; see the [command reference](guide/commands.md).

Results go to stdout; progress and errors go to stderr. Exit code `0` means success, `1` means a runtime,
input or integrity error, and `2` means a comparison exceeded tolerance, parsing is incomplete or command-line
arguments are invalid. See the [full exit-code contract](guide/commands.md#概览).

## Platform support

| Feature | Windows x64 | Linux x64 | macOS arm64 |
| --- | --- | --- | --- |
| Independent decoding, configuration and packet parsing, PCM comparison | ✅ | ✅ | ✅ |
| AudioToolbox reference collection, encoding, decoding and replay | — | — | ✅ |

CI builds and runs portable tests on Windows Server 2022, Ubuntu 22.04 and macOS 26. The macOS build
target is Apple Silicon. Use `decode-sq` for independent decoding; `decode` is the macOS AudioToolbox reference command.

The macOS 27.2 reference backend limits some higher-order HOA dynamic-selection configurations. Qualified failures include an explanation; see [native reference limits](guide/support.md#macos-272-原生参考限制). Independent decoding supports orders 0–10, with up to 121 coefficients per HOA domain.

## Before you start

- **Input must contain APAC audio:** ordinary AAC `.m4a`, MP3, FLAC and encrypted/DRM files are unsupported. MP4/M4A is currently limited to unfragmented, single-audio-track files.
- **Output retains its channel meaning:** HOA coefficients need spatial rendering before speaker playback. Use CAF for HOA, 9.1.6 and 22.2. The CLI does not render spatial audio, play sound or encode compressed formats such as AAC or FLAC.
- **DRC, loudness and EQ are not applied:** their metadata is parsed without processing the output audio.
- **Some APAC paths are not implemented:** LRVQ, outer ASP reconfiguration and nonzero frame-length indices fail explicitly. See [support boundaries](guide/support.md) for layout and sample-rate requirements.
- Distributed binaries enable CAC by default. Active BWE2 frames return `bwe2-unavailable`, including during fast-access scans and embedded preroll.
- **Results follow this project's numeric model:** comparisons with Apple's AudioToolbox reference use a tolerance and are not bit-identical by design. See the [numeric relationship](guide/support.md#与苹果参考的数值关系).
- **Existing output is never overwritten:** file exports use an adjacent temporary file until successful, cleaning up on failure. Research-directory output with `.incomplete.json` or `.incomplete` is unfinished and must not be treated as a valid result.

Report problems through [Issues](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/issues), including the
program version, operating system, command and error message. Remove personal paths from commands before
posting, and make sure you have permission to share any audio.

## Building from source

Use **Rust 1.98.0**. macOS builds also need Xcode Command Line Tools. Independent decoding on Windows
and Linux does not require an Apple SDK.

```sh
git clone https://github.com/SakuzyPeng/MacinDecode-APAC-Core.git
cd MacinDecode-APAC-Core
cargo +1.98.0 build --locked --release -p mapac
cargo +1.98.0 test --locked -p mapac --test frame_parser --test config_parser
```

The program is `target/release/mapac`, or `target/release/mapac.exe` on Windows. CAC inverse mixing
is enabled by default. **BWE2 is disabled by default.** Its syntax is parsed, and active BWE2 frames
return `bwe2-unavailable` while preserving the preceding state in both decoding and fast scanning.
The optional `bwe2` feature requires explicit external LSF input. See [development](guide/development.md#显式-bwe2-外部输入) for configuration.

Use `--no-default-features` to disable CAC inverse mixing. Nonzero CAC gains then return `cac-unavailable`.
See [validation](guide/validation.md) for the full core, container, golden-snapshot and source-audit commands.

## Using the Rust libraries

`apac-core` is the `no_std` + `alloc` decoding core. `apac-container` provides CAF/MP4 reading and seeking.
The portable crates forbid `unsafe` through a workspace lint. AudioToolbox reference code lives separately
in the macOS-only `apac-native` crate.

```rust
use apac_core::{Config, Decoder};

let config = Config::parse(&cookie)?;
let mut decoder = Decoder::new(&config)?;
for packet in packets {
    let pcm: Vec<f32> = decoder.decode_vec(packet)?;
    // Interleaved Float32 PCM; decoder.info() gives the sample rate and channel count.
}
```

Enable the `cac` feature on `apac-core` for streams using CAC. Use `Reader` for file decoding and
bidirectional seeking, or `Media` / `Playback` / `Indexer` for fast opening, frame-exact seeking and
background indexing in a player. See [decoding and library APIs](guide/decoding.md) for examples and limits.
Generate API documentation with:

```sh
cargo +1.98.0 doc --no-deps -p apac-core -p apac-container --open
```

## Documentation

Detailed guides are in Chinese.

| Document | Contents |
| --- | --- |
| [CLI version notes](guide/releases/v0.2.0.md) | Downloads, features and distribution scope for v0.2.0 |
| [Command reference](guide/commands.md) | All subcommands, options, output files and exit codes |
| [Decoding and library APIs](guide/decoding.md) | PCM output, range decoding, `Decoder`, `Reader` and `Playback` |
| [Support boundaries](guide/support.md) | Supported paths, rejection conditions and numeric relationship to Apple's reference |
| [Bitstream parsing](guide/bitstream.md) | Channel and HOA syntax, parsing depths and numeric identifiers |
| [Development](guide/development.md) | Workspace, `no_std` and CAC/BWE2 features |
| [Validation and regression](guide/validation.md) | Portable regression, golden snapshots, source and history checks |
| [Numerical sources and repository scope](guide/source-provenance.md) | Measured sources, generated mathematics and distribution manifest |
| [Third-party data](THIRD_PARTY.md) | Sources, licenses and independent derivation of format constants |

## License

The project code is released under [MIT](LICENSE). The AAC Huffman codebooks, band offsets and TNS limits in `data/sq-codebooks.json` and `data/aac-sfb-tns-v1.json` come from vo-aacenc under Apache-2.0 (see [THIRD_PARTY.md](THIRD_PARTY.md) and [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)); they are compiled into `apac-core`, which therefore declares `MIT AND Apache-2.0`, while the other crates are `MIT`.

This is an independent implementation written for interoperability and research. It is not affiliated with, sponsored by, or endorsed by Apple Inc. Apple and AudioToolbox are trademarks of Apple Inc.; APAC and related names are used only to describe compatibility.

### Disclaimer

- The repository contains no Apple binaries, decompiled code, SDK files or source media. Format constants that were observed or transcribed name their source inside each data file; third-party data is listed in [THIRD_PARTY.md](THIRD_PARTY.md).
- The decoder outputs its own numeric model and is by design not bit-identical to Apple's reference. The software is provided "as is", without warranty of any kind, express or implied (see [LICENSE](LICENSE)).
- APAC and related audio technologies may be covered by third-party patents. The project's license grants no patent rights; evaluate the patent and legal requirements of your jurisdiction before using or distributing it in a product.
- When processing audio with this project, respect the copyright and license terms of that content.
