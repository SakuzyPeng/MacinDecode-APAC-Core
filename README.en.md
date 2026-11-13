# MacinDecode APAC Core

An independent Apple Positional Audio Codec parser and decoder, with a `no_std` + `alloc` core, CAF/MP4 input and the `mapac` CLI.

This repository starts a new history from a source-qualified snapshot. Research acquisition tools, private evidence, previous commits and the original LSF tables are not distributed here.

## Build

Use Rust 1.98.0. The macOS AudioToolbox reference commands also require Xcode Command Line Tools.

```sh
cargo +1.98.0 build --locked --release -p mapac
target/release/mapac --help
target/release/mapac decode-sq input.caf -o output.wav
```

CAC is enabled by default. **BWE2 is disabled by default.** Its syntax is parsed, but active BWE2 frames return `bwe2-unavailable` in both decoding and state scanning without committing failed state. No original LSF tables are included. The optional `bwe2` interface requires an explicit external input; see [development](guide/development.md).

Input must be unencrypted APAC in a supported CAF or MP4/M4A container. Ordinary AAC, MP3 and FLAC are not APAC inputs. Supported paths include SQ, CAC, TNS, HOA orders 0–10 and source-channel restoration. HOA coefficients retain their meaning and require external spatial rendering. Use CAF output for HOA, 9.1.6 and 22.2 layouts. DRC, loudness and EQ audio processing are disabled; LRVQ and other unsupported syntax are rejected.

## Validation and sources

```sh
python3 -B scripts/check_sources.py
cargo +1.98.0 test --locked -p apac-core -p apac-container -p apac-research -p apac-cac -p apac-no-std-example -p apac-bwe2
cargo +1.98.0 test --locked -p mapac --test frame_parser --test config_parser
```

See [validation](guide/validation.md), [source provenance](guide/source-provenance.md), [CLI commands](guide/commands.md) and [decoding APIs](guide/decoding.md). Native acquisition tests are opt-in and are not part of the portable validation commands above.

Project code is MIT-licensed; third-party tables retain the terms described in [THIRD_PARTY.md](THIRD_PARTY.md). This repository has not issued a new binary release.

[中文](README.md)
