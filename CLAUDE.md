# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`apac-tool` is a research toolkit for Apple Positional Audio Codec (APAC), organized as a Cargo workspace (single `target/`, default member `apac-tool`):

- `crates/apac-core` — portable pure Rust: cookie/config parsing, packet/frame parsing (SQ, CAC, TNS, BWE2, DRC, HOA, ASP, scene graph) and the experimental independent decoder (`synthesis::SqDecoder`). Never calls Apple APIs. It is being restructured toward a `#![no_std]` + `alloc` decode API.
- `crates/apac-research` — std report layer: `parse_packets` / `decode` drivers behind `parse-packets` / `decode-sq`, input dispatch, CAF/MP4 readers, packet-directory bundles, output budget, PCM compare, test signals. Optional `clap` feature derives `ValueEnum` for CLI enums.
- `crates/apac-native` — macOS-only AudioToolbox reference code (`native`, `collect`, `replay`, `research`); the crate body is `#![cfg(target_os = "macos")]`. Its `build.rs` compiles the root-level `native/audio_toolbox.c`.
- `crates/apac-tool` — the `apac-tool` binary plus the integration tests that run it (`CARGO_BIN_EXE_apac-tool` only exists in this package).

Core internals that the report drivers still need are exposed only through the doc-hidden, transitional `apac_core::research_support`; don't add new users of it outside apac-research.

`README.md` (Chinese, ~140 KB) is the user-facing reference for every command, report schema, exit code and support boundary; read the relevant section before changing behavior. The README sections "实现边界", "共享配置与组合 HOA 流" and "ASP 与帧长支持边界" define what is deliberately *not* supported (LRVQ, active DRC/loudness/EQ processing, outer ASP reconfiguration, frame-length index ≠ 0, spatial rendering) — unsupported paths must reject explicitly, never guess.

## Commands

Use Rust 1.98.0 (edition 2024) for numeric acceptance and clippy. Dependencies are cached on the primary dev machine; on a fresh machine drop `--offline` for the first build.

```sh
cargo +1.98.0 build --offline                       # apac-tool only (default member)
cargo +1.98.0 build --offline --workspace --bins --examples
cargo +1.98.0 test --offline --workspace
cargo +1.98.0 clippy --offline --workspace --all-targets -- -D warnings
cargo +1.98.0 fmt --all
python3 -B -m unittest discover -s scripts -p 'test_*.py'

# single Rust test: unit tests live in crates/*/src (*_tests.rs files are often mounted via
# #[path] as `mod tests`); filters are substring matches on the test path
cargo test --offline -p apac-core --lib frame::hoa_transport::tests
cargo test --offline -p apac-tool --test frame_parser sq_ics_and_grouping_end_before_the_first_channel_stream
# single Python test module (scripts import siblings, so use -s scripts)
python3 -B -m unittest discover -s scripts -p 'test_tns.py'

# refactor regression guards: golden snapshot and portable validators vs. a saved baseline
cargo +1.98.0 test --offline -p apac-research --test golden_decode
python3 -B scripts/run_portable_suite.py --binary target/debug/apac-tool --fast --jobs 4 --out reports/suite-new
python3 -B scripts/compare_reports.py reports/<baseline>/suite reports/suite-new

# cross-target compile checks (not a substitute for running on that OS)
cargo check --offline --workspace --target x86_64-pc-windows-msvc
APAC_NATIVE_RUST_CHECK=1 cargo check --offline --workspace --target aarch64-apple-darwin  # skips the C bridge
```

Some Python tests require macOS (`afconvert`/AudioToolbox) and skip elsewhere. The full portable suite (drop `--fast`, add `--presence-binary target/debug/examples/layout_presence`) takes about 25 minutes on 4 cores, mostly in Python oracles. Dev/test profiles disable debug info and incremental compilation; keep using the single `target/` in the checkout (no extra worktrees or duplicate caches — disk is constrained).

## Architecture

- `apac-tool/src/main.rs` — clap CLI; subcommands `decode-sq`, `parse-cookie`, `parse-packets`, `collect-configs`, `inspect`, `scan`, `dump`, `replay`, `decode`, `fixture`, `compare`. Results go to stdout, progress/errors to stderr.
- `apac-core/src/config/` — cookie (magic cookie / ASC) syntax parser: DRC, HOA, scenes, passive renderer/metadata. Produces `ParseStatus` complete/partial/unsupported.
- `apac-core/src/frame/` — bit-level packet parsing. `FrameContext` / `ChannelFrameContext` / `HoaFrameContext` are built from a parsed cookie; `StreamFrameContext::from_cookie` handles composite streams (multiple ASCs, shared configuration, HOA + SQ components). Many `hoa_*` modules each own one syntax area and expose a `PROFILE` string and `format_sha256()`.
- `apac-core/src/synthesis/` — independent Float64 decoder math (`SqDecoder`): IMDCT/FFT, overlap, HOA reconstruction, DRC-off path. Final PCM is the only Float32 cast. `apac-research/src/decode.rs` drives it for `decode-sq`, including sequential vs. fast container access (`SqAccessMode`).
- `apac-research/src/{caf.rs,mp4/}` — container readers used by `decode-sq --access fast`; `packets.rs` — packet-directory bundles; `apac-core/src/{numeric.rs,bwe2_math.rs}` — fixed numeric tables.
- `data/*.json` — frozen tables, format descriptions and state vectors, embedded via `include_str!`/`include_bytes!`. Their SHA-256 hashes become `format_sha256` / math identifiers reported in output.
- `scripts/` — Python (stdlib only) independent oracles and acceptance harnesses, grouped by naming convention:
  - `generate_*.py` — (re)generate a `data/` file; `--check` verifies the committed file and its embedded hash without writing.
  - `*_vectors.py` / `*_oracle.py` — independent input vectors and reference math.
  - `validate_*.py` — acceptance runs against a built binary (`--binary target/debug/apac-tool --report reports/...`); `*_native.py` / `native_*_trace.py` additionally use macOS AudioToolbox/LLDB and pinned component hashes.
  - `test_*.py` — unittest suites.

## Invariants when changing code

- **Numeric identity is frozen.** Profile strings (e.g. `apac-sq-math-v1`, `apac-hoa-shared-configuration-v1`), `BACKEND`, data-file hashes and the order of floating-point operations are part of published results. Don't alter existing arithmetic order or data files; introduce new behavior under a new/optional profile or report field and keep existing reports byte-compatible.
- **Restructuring guard.** While the no_std decode API refactor is in progress, library APIs may break but CLI output must stay byte-identical (PCM, `parse-cookie`/`parse-packets` JSON, error text, exit codes). Never regenerate `crates/apac-research/tests/golden/refactor-v1.json` to make a change pass; the Python unittest failures recorded at the baseline (6 HOA/TNS cases) must stay exactly the same set.
- After touching anything in `data/`, the corresponding `generate_*.py --check` must still pass.
- State changes are atomic per outer packet: a failed packet must not commit component descriptions, dynamic maps, DRC history, or overlap; `reset()` restores the initial state.
- Exit codes: `0` success/complete; `1` runtime/input/integrity error; `2` PCM out of tolerance, unresolved collection errors, or `partial`/`unsupported` parse (`parse-packets` usually returns 2 because payloads beyond the prefix are unparsed).
- Export commands require `--out` to be a **new, non-existent directory** and cap cumulative output at 128 MiB (`--max-output-mib`). JSON/JSONL records use `schema_version: 1`; optional system properties are `{"value": ..., "error": null}`.

## Repository rules (from AGENTS.md)

- `docs/` is a **separate Git repository** (research notes, phase summaries) ignored by this repo — never force-add it, make it a submodule, or a gitlink. Check it with `git -C docs status`. Usage and public interface docs go in `README.md`; research/analysis goes in `docs/`, and experimental conclusions there should cite the code commit.
- `reports/`, `artifacts/`, `target/` and local Xcode workspaces stay local (they contain absolute paths, sample names, audio). Never commit them.
- Committed examples use generic paths or explicit arguments — no local usernames, music-library listings, credentials, Apple binaries or decompiled output.
- Don't rewrite local raw experiment records for redaction; produce a separate redacted copy when sharing.
