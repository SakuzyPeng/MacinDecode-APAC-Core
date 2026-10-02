# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`apac-tool` (crate `macindecode-apac-tools`) is a research toolkit for Apple Positional Audio Codec (APAC). It has two halves:

- **Portable pure-Rust code** (any OS): cookie/config parsing, packet/frame parsing (SQ, CAC, TNS, BWE2, DRC, HOA, ASP, scene graph), CAF/MP4 container reading, and an experimental independent PCM decoder (`decode-sq`). None of this may call Apple APIs.
- **macOS-only reference code**: `native/audio_toolbox.c` wraps AudioFile/ExtAudioFile/AudioConverter; it is compiled by `build.rs` only when the target OS is macOS. Modules `native`, `collect`, `replay`, `research` and the integration tests `tests/native_cli.rs` / `tests/replay_native.rs` are `#[cfg(target_os = "macos")]`.

`README.md` (Chinese, ~140 KB) is the user-facing reference for every command, report schema, exit code and support boundary; read the relevant section before changing behavior. The README sections "实现边界", "共享配置与组合 HOA 流" and "ASP 与帧长支持边界" define what is deliberately *not* supported (LRVQ, active DRC/loudness/EQ processing, outer ASP reconfiguration, frame-length index ≠ 0, spatial rendering) — unsupported paths must reject explicitly, never guess.

## Commands

Numeric acceptance uses Rust 1.98.0 (edition 2024). Dependencies are cached on the primary dev machine; on a fresh machine drop `--offline` for the first build.

```sh
cargo build --offline
cargo test --offline
cargo clippy --offline --all-targets -- -D warnings
python3 -B -m unittest discover -s scripts -p 'test_*.py'

# single Rust test: unit tests live in src/ (*_tests.rs files are often mounted via #[path] as `mod tests`),
# integration tests in tests/; filters are substring matches on the test path
cargo test --offline --lib frame::hoa_transport::tests
cargo test --offline --test frame_parser sq_ics_and_grouping_end_before_the_first_channel_stream
# single Python test module (scripts import siblings, so use -s scripts)
python3 -B -m unittest discover -s scripts -p 'test_tns.py'

# cross-target compile checks (not a substitute for running on that OS)
cargo check --offline --target x86_64-unknown-linux-gnu
cargo check --offline --target x86_64-pc-windows-msvc
```

Some Python tests require macOS (`afconvert`/AudioToolbox) and skip elsewhere. Dev/test profiles disable debug info and incremental compilation; keep using the single `target/` in the checkout (no extra worktrees or duplicate caches — disk is constrained).

## Architecture

- `src/main.rs` — clap CLI; subcommands `decode-sq`, `parse-cookie`, `parse-packets`, `collect-configs`, `inspect`, `scan`, `dump`, `replay`, `decode`, `fixture`, `compare`. Results go to stdout, progress/errors to stderr.
- `src/config/` — cookie (magic cookie / ASC) syntax parser: DRC, HOA, scenes, passive renderer/metadata. Produces `ParseStatus` complete/partial/unsupported.
- `src/frame/` — bit-level packet parsing. `FrameContext` / `ChannelFrameContext` / `HoaFrameContext` are built from a parsed cookie; `StreamFrameContext::from_cookie` handles composite streams (multiple ASCs, shared configuration, HOA + SQ components). Many `hoa_*` modules each own one syntax area and expose a `PROFILE` string and `format_sha256()`.
- `src/synthesis/` — independent Float64 decoder math (`SqDecoder`, `decode_sq*`): IMDCT/FFT, overlap, HOA reconstruction, DRC-off path, sequential vs. fast container access (`SqAccessMode`). Final PCM is the only Float32 cast.
- `src/caf.rs`, `src/mp4/` — container readers used by `decode-sq --access fast`; `src/packets.rs` — packet-directory bundles; `src/numeric.rs`, `src/bwe2_math.rs` — fixed numeric tables.
- `data/*.json` — frozen tables, format descriptions and state vectors, embedded via `include_str!`/`include_bytes!`. Their SHA-256 hashes become `format_sha256` / math identifiers reported in output.
- `scripts/` — Python (stdlib only) independent oracles and acceptance harnesses, grouped by naming convention:
  - `generate_*.py` — (re)generate a `data/` file; `--check` verifies the committed file and its embedded hash without writing.
  - `*_vectors.py` / `*_oracle.py` — independent input vectors and reference math.
  - `validate_*.py` — acceptance runs against a built binary (`--binary target/debug/apac-tool --report reports/...`); `*_native.py` / `native_*_trace.py` additionally use macOS AudioToolbox/LLDB and pinned component hashes.
  - `test_*.py` — unittest suites.

## Invariants when changing code

- **Numeric identity is frozen.** Profile strings (e.g. `apac-sq-math-v1`, `apac-hoa-shared-configuration-v1`), `BACKEND`, data-file hashes and the order of floating-point operations are part of published results. Don't alter existing arithmetic order or data files; introduce new behavior under a new/optional profile or report field and keep existing reports byte-compatible.
- After touching anything in `data/`, the corresponding `generate_*.py --check` must still pass.
- State changes are atomic per outer packet: a failed packet must not commit component descriptions, dynamic maps, DRC history, or overlap; `reset()` restores the initial state.
- Exit codes: `0` success/complete; `1` runtime/input/integrity error; `2` PCM out of tolerance, unresolved collection errors, or `partial`/`unsupported` parse (`parse-packets` usually returns 2 because payloads beyond the prefix are unparsed).
- Export commands require `--out` to be a **new, non-existent directory** and cap cumulative output at 128 MiB (`--max-output-mib`). JSON/JSONL records use `schema_version: 1`; optional system properties are `{"value": ..., "error": null}`.

## Repository rules (from AGENTS.md)

- `docs/` is a **separate Git repository** (research notes, phase summaries) ignored by this repo — never force-add it, make it a submodule, or a gitlink. Check it with `git -C docs status`. Usage and public interface docs go in `README.md`; research/analysis goes in `docs/`, and experimental conclusions there should cite the code commit.
- `reports/`, `artifacts/`, `target/` and local Xcode workspaces stay local (they contain absolute paths, sample names, audio). Never commit them.
- Committed examples use generic paths or explicit arguments — no local usernames, music-library listings, credentials, Apple binaries or decompiled output.
- Don't rewrite local raw experiment records for redaction; produce a separate redacted copy when sharing.
