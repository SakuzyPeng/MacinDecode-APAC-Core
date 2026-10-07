# MacinDecode-APAC-Core

English | [中文](README.md)

[![Build](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml/badge.svg)](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml)

Independent Apple Positional Audio Codec decoding core · Rust 2024 · Rust 1.98.0 · `no_std` core · `unsafe` forbidden

## What is this?

APAC (Apple Positional Audio Codec) is Apple's codec for spatial audio, carrying channel-based audio and Higher-Order Ambisonics (HOA). MacinDecode-APAC-Core decodes APAC bitstreams to **interleaved Float32 PCM** on any platform without calling Apple APIs: channel layouts come out as channels, HOA as ACN/SN3D coefficients or the source channels the bitstream declares. It also reports the configuration and per-packet syntax as structured data.

This project does **not** perform spatial rendering, DRC/loudness/EQ processing, or real-time playback.

The decoder follows its own numeric model, defined by formulas and a fixed order of operations, and deliberately does not reproduce the numeric details of Apple's native implementation. By design it is therefore not bit-identical to the AudioToolbox reference; the two are compared within a tolerance (see [numeric relationship to Apple's reference](guide/support.md#与苹果参考的数值关系), in Chinese).

This is an independent implementation written for interoperability and research. It is not affiliated with, sponsored by, or endorsed by Apple Inc. Apple and AudioToolbox are trademarks of Apple Inc.; APAC and related names are used only to describe compatibility.

## Features

- **Containers & input**: CAF v1, unfragmented single-track MP4/M4A, and exported packet directories; files are read and verified in full on open, and every later pass is checked against the first
- **Configuration parsing**: field-by-field magic cookie/ASC syntax with cookie bit positions, including DRC, HOA, scene graph and passive renderer metadata
- **Channel decoding**: spectral Huffman, dequantization, CAC, TNS, BWE2, IMDCT and overlap, from mono up to 22.2
- **HOA decoding**: orders zero to three, salient/ambient, dynamic selection, spatial controls, source-layout recovery, and composite streams with channels
- **Range access**: sequential decoding or fast prefix scanning, bidirectional `seek`, bit-identical to a decoder opened fresh at that point
- **Independent numeric model**: constants generated from formulas at high precision, a fixed order of operations, deterministic output; SQ dequantization (`apac-sq-math-v2`) multiplies in Float64 and rounds once to Float32
- **Per-packet syntax reports**: `parse-packets` emits fields, bit offsets and intermediate spectra stage by stage
- **`#![no_std]` core**: the decoding library depends only on `alloc`, `sha2` and `libm`; the CAC inverse mixing is an optional crate that builds can leave out
- **Apple reference comparison (macOS)**: AudioToolbox encoding/decoding, test signals and PCM comparison for acceptance

## Quick Start

### Download prebuilt binaries

Download the `apac-tool` CLI from [Releases](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/releases) for Windows x64, Linux x64, or macOS arm64. macOS builds are Apple Silicon only. See the [v0.1.0 release notes](guide/releases/v0.1.0.md#english) for features and requirements.

Download the portable archive and its adjacent `.sha256` checksum, verify it, then extract it. No Rust installation is required. Documentation, licenses, and build revision metadata are included. From the extracted directory, run:

```sh
./apac-tool --version
./apac-tool decode-sq input.m4a --out decoded
```

Use `.\apac-tool.exe` in Windows PowerShell. Output is raw Float32 PCM with JSON metadata, not a WAV file. `decoded` must not already exist; raise the default 128 MiB output limit with `--max-output-mib` for longer files. Development builds remain available under **Artifacts** in the [Build workflow](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml). See [CI and build artifacts](guide/development.md#ci-与构建产物) (Chinese) for platform requirements, checksum verification, and release steps.

### Building from source: prerequisites

- Rust 1.98.0 ([install](https://rustup.rs/); numeric acceptance is pinned to this version)
- The macOS reference tools additionally need the Xcode Command Line Tools

### Build & Test

```bash
cargo build --release
cargo test --workspace
```

### Decode

```bash
# All valid audio: writes pcm.f32le (interleaved little-endian Float32), pcm.json and decode-sq.json
# Raise the default 128 MiB output cap with --max-output-mib for long or multichannel files
target/release/apac-tool decode-sq input.m4a --out decoded

# A window: 8192 frames from frame 480000, with fast access
target/release/apac-tool decode-sq input.caf --out window \
  --start-frame 480000 --frames 8192 --access fast
```

### As a library

```rust
use apac_core::{Config, Decoder};

let config = Config::parse(&cookie)?;          // magic cookie
let mut decoder = Decoder::new(&config)?;      // unsupported configurations fail here, with the reason
let info = decoder.info();                     // sample rate, channels, 1024 frames per packet, layout
for packet in packets {
    let pcm: Vec<f32> = decoder.decode_vec(packet)?;  // 1024 × channels interleaved samples
}
```

Streams that use CAC (channel pairs with a shared header) need the `cac` feature: `apac-core = { ..., features = ["cac"] }`. `apac_core::CAC_ENABLED` tells whether the core, after Cargo's feature unification, includes the CAC inverse mixing; without it, full decoding and fast prefix scanning both reject nonzero CAC gains.

For files, `apac_container::Reader` wraps CAF/MP4 reading, range cropping and bidirectional `seek`; see `crates/apac-container/examples/decode_file.rs`, and `crates/apac-no-std-example` for `no_std` use. Players use `apac_container::Playback`: it opens a file reading metadata only and seeks frame-exactly at a cost bounded by decoder checkpoints, which an `Indexer` can build on a background thread; see `crates/apac-container/examples/playback.rs` and [the playback section](guide/decoding.md#播放media-与-playback) (Chinese). API docs:

```bash
cargo doc --no-deps -p apac-core -p apac-container --open
```

## Basic Usage

The most common commands follow; every subcommand is described in the [command reference](guide/commands.md) (in Chinese).

**Decode to PCM** — channels or HOA coefficients, interleaved Float32:

```bash
target/release/apac-tool decode-sq input.m4a --out decoded
```

**Inspect the configuration** — the magic cookie field by field, with bit positions:

```bash
target/release/apac-tool parse-cookie cookie.bin
```

**Parse packets** — syntax and intermediate spectra by stage, here up to CAC:

```bash
target/release/apac-tool parse-packets packets/ --depth cac --output cac.jsonl
```

**Compare PCM** — two outputs within `1e-6 + 1e-5·|reference|`:

```bash
target/release/apac-tool compare reference/pcm.json decoded/pcm.json
```

Results go to stdout, progress and errors to stderr. `--out` must be a directory that does not exist yet. Exit codes: `0` success; `1` runtime, input or integrity error; `2` PCM out of tolerance, or a `partial`/`unsupported` parse.

## Project Structure

```text
apac-tool ──→ research / core / native (macOS only)
apac-native ──→ research / core
apac-research ──→ container / core
apac-container ──→ core
apac-core ──→ cac (optional `cac` feature)
apac-no-std-example ──→ core
```

| Crate | Responsibility | `no_std` |
|---|---|---|
| [`apac-core`](crates/apac-core) | cookie/ASC configuration, packet and frame parsing (SQ, CAC syntax, TNS, BWE2, DRC, HOA, ASP, scene graph), independent Float64 synthesis | ✅ |
| [`apac-cac`](crates/apac-cac) | CAC inverse mixing and the `apac-cac-math-v1` rotation table, pulled in by `apac-core`'s `cac` feature | ✅ |
| [`apac-container`](crates/apac-container) | CAF/MP4 reading, integrity checks, frame-range arithmetic and the `Reader` decode loop; `Media`/`Playback` for players | — |
| [`apac-research`](crates/apac-research) | report assembly for `parse-cookie`, `parse-packets` and `decode-sq`; packet directories, output budget, PCM comparison, test signals | — |
| [`apac-native`](crates/apac-native) | macOS AudioToolbox reference tools: collection, export, replay, reference decoding and test-signal encoding | — |
| [`apac-tool`](crates/apac-tool) | the `apac-tool` command line and its integration tests | — |
| [`apac-no-std-example`](crates/apac-no-std-example) | unpublished example decoding packets into a caller buffer from a `no_std` library | ✅ |

The `cac` feature is an isolation boundary: without it `apac-core` still reads and reports the CAC syntax, decodes frames whose CAC gains are all 0, and rejects any other frame with `cac-unavailable`. `cargo build -p apac-tool --no-default-features` builds the tool without `apac-cac`.

### Platforms

| Commands | Platforms |
| --- | --- |
| `decode-sq`, `parse-cookie`, `parse-packets`, `compare` | Linux, Windows, macOS (pure Rust) |
| `inspect`, `scan`, `collect-configs`, `dump`, `replay`, `decode`, `fixture` | macOS only (AudioToolbox) |

## Data Flow

```text
CAF / MP4 / packet directory
    → container reading and integrity checks      (apac-container)
    → magic cookie → Config                       (apac-core::config)
    → packet parsing: SQ spectra → CAC → TNS → BWE2 (apac-core::frame)
    → HOA recovery, dynamic selection, source-layout recovery
    → IMDCT, windowing and overlap (Float64)      (apac-core::synthesis)
    → interleaved Float32 PCM
```

DRC, loudness, scene graph and renderer metadata are parsed but not applied to the audio. State commits per outer packet; a failed packet leaves no partial state behind.

## Support

| Capability | Status | Notes |
|---|---|---|
| Configuration syntax | ✅ | cookie/ASC, DRC, HOA, scene graph, passive renderer metadata |
| Channel decoding | ✅ | Mono/Stereo/5.1/7.1/7.1.4/22.2; SQ, CAC, TNS, BWE2 |
| HOA decoding | ✅ | orders 0–3; salient/ambient, dynamic selection, spatial controls, source-layout recovery; order 4 and above explicitly rejected |
| Composite streams & shared configuration | ✅ | multiple ASCs, HOA combined with channels, up to 255 output channels |
| Containers & access | ✅ | CAF, unfragmented single-track MP4/M4A, packet directories; sequential or fast range decoding |
| Sample rates | ✅ | indices 0–12 (96 kHz–7.35 kHz); some HOA configurations limited to 44.1/48 kHz |
| DRC/loudness/EQ | read-only | syntax parsed and reported, audio not processed |
| LRVQ | ⏸ | deferred; rejected when encountered |
| Outer ASP reconfiguration, frame-length index ≠ 0 | ✗ | not implemented by the reference; rejected |
| Spatial rendering, real-time playback | — | out of scope |

Unsupported input is always rejected with the reason (field name, value and cookie bit position), never guessed or silently degraded. The full boundaries are in [support boundaries](guide/support.md) (in Chinese).

## Design Principles

1. Unsupported paths are rejected explicitly — no guessing, no silent degradation.
2. Input is untrusted by default: containers are verified as a whole, cookies are capped at 8 MiB and packets at 16 MiB, and resource limits fail with an error.
3. Numeric identity is frozen: profile strings, constant bit patterns and the order of floating-point operations are part of published results; new behavior gets a new profile.
4. Constants are generated independently from formulas or come from license-compatible public sources; comparisons with Apple's reference use a tolerance, and bit-for-bit checks apply to the project's own platforms and builds.
5. State commits atomically per outer packet; a failure commits no descriptions, maps, DRC history or overlap, and `reset()` restores the initial state.
6. The portable crates do not call Apple APIs, and the workspace lint `unsafe_code = "forbid"` rules out `unsafe` in them; AudioToolbox reference code lives only in the macOS-only `apac-native`, the one crate that does not inherit that lint.
7. The repository holds no Apple binaries, decompiled output, SDK files, source media or raw reports with local paths.

## Documentation

The guides are written in Chinese.

| Document | Contents |
|---|---|
| [Decoding & library API](guide/decoding.md) | `decode-sq`, CAF/MP4 input, fast range decoding, `Decoder` and `Reader` |
| [Command reference](guide/commands.md) | every subcommand, export files and report fields, exit codes |
| [Bitstream parsing](guide/bitstream.md) | `parse-packets` depths, channel and HOA syntax and numeric identifiers |
| [Support boundaries](guide/support.md) | implementation boundaries, numeric relationship to Apple's reference, shared configuration, ASP and frame length |
| [Validation & regression](guide/validation.md) | tests, independent math acceptance, refactor regression and Apple reference diagnostics |
| [HOA black-box workflows](guide/hoa-blackbox.md) | third-order production limit, resumable campaigns and historical measurements |
| [HOA spatial-control means](guide/hoa-mean-blackbox.md) | exact cancellation measurements, independent validation and external-volume evidence |
| [BWE2 black-box reconstruction](guide/bwe2-blackbox.md) | gain reconstruction, LSF observability experiments and frozen validation |
| [Development](guide/development.md) | workspace layout, `no_std` builds, API tiers, the CAC feature, repository rules |
| [Third-party data](THIRD_PARTY.md) | sources, licenses and independent derivation of format constants (in English) |

## License

The project code is released under [MIT](LICENSE). The AAC Huffman codebooks and band offsets in `data/sq-codebooks.json` come from vo-aacenc under Apache-2.0 (see [THIRD_PARTY.md](THIRD_PARTY.md) and [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)); they are compiled into `apac-core`, which therefore declares `MIT AND Apache-2.0`, while the other crates are `MIT`.

### Disclaimer

- The repository contains no Apple binaries, decompiled code, SDK files or source media. Format constants that were observed or transcribed name their source inside each data file; third-party data is listed in [THIRD_PARTY.md](THIRD_PARTY.md).
- The decoder outputs its own numeric model and is by design not bit-identical to Apple's reference. The software is provided "as is", without warranty of any kind, express or implied (see [LICENSE](LICENSE)).
- APAC and related audio technologies may be covered by third-party patents. The project's license grants no patent rights; evaluate the patent and legal requirements of your jurisdiction before using or distributing it in a product.
- When processing audio with this project, respect the copyright and license terms of that content.
