# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`apac-tool` is a research toolkit for Apple Positional Audio Codec (APAC), organized as a Cargo workspace (single `target/`, default member `apac-tool`):

- `crates/apac-core` — portable pure Rust: cookie/config parsing, packet/frame parsing (SQ, CAC, TNS, BWE2, DRC, HOA, ASP, scene graph) and the experimental independent decoder (`Decoder`, in `synthesis`). Never calls Apple APIs. It is `#![no_std]` + `alloc` (only unit tests link `std`, always by explicit `std::` paths). Without features it depends only on `sha2` (no default features) and `libm` (`libm::sqrt` is correctly rounded, so bit-identical to `f64::sqrt`); the optional `serde` feature (enabled by apac-research, and by core's own tests through a self dev-dependency) gates every `Serialize`/`Deserialize` derive as `cfg_attr(feature = "serde", …)` and pulls in alloc-only `serde_json`, used just for the key-sorted rendering of structured `FieldValue`s. New core types follow the same `cfg_attr` pattern.
- `crates/apac-container` — std CAF/MP4 readers (`CafReader<R>`, `Mp4Reader<R>`) over any `Source` (`Read + Seek` plus length and an optional revision stamp; implemented for `File` and `Cursor`). They validate structure and cookie, expose a typed `Track` and a summary for reports, and verify integrity per pass: opening reads the whole file once, and every pass from packet zero (`rewind()`) is compared with it at the end, together with a rescan. Errors carry operation, message, bit offset, file position and packet index; `apac_research::error::Error` converts them field by field. `PacketSource` unifies containers and packet bundles (implemented in research for `PacketBundle` and the `Input` dispatcher); `Range` holds the frame-window arithmetic. `Reader<S: PacketSource>` is the decode-sq loop: `open` (same check order and texts), `read`/`read_with` (cropped frames per packet, sequential or fast access, a callback before the output-start packet), bidirectional `seek` (continues forward when the remaining packets match a fresh reader, else rewinds, resets and replays; tests prove equality with fresh readers), `stats`, `finish` (completes verification). Reader tests build in-memory CAFs from the state fixtures.
- `crates/apac-research` — std report layer: the `parse-cookie` `CookieReport`, `parse_packets` / `decode` drivers behind `parse-packets` / `decode-sq`, input dispatch (`input.rs` opens files, builds `FileInfo` and the input report JSON from the container's typed values), packet-directory bundles, output budget, PCM compare, test signals. Optional `clap` feature derives `ValueEnum` for CLI enums.
- `crates/apac-native` — macOS-only AudioToolbox reference code (`native`, `collect`, `replay`, `research`); the crate body is `#![cfg(target_os = "macos")]`. Its `build.rs` compiles the root-level `native/audio_toolbox.c`.
- `crates/apac-tool` — the `apac-tool` binary plus the integration tests that run it (`CARGO_BIN_EXE_apac-tool` only exists in this package).
- Examples: `crates/apac-container/examples/decode_file.rs` (CAF/MP4 → raw f32 PCM through `Reader`, byte-identical to `decode-sq`) and `crates/apac-no-std-example` (an unpublished `#![no_std]` library decoding into a caller buffer with feature-less `apac-core`; host tests compare it with `Decoder::decode_vec`). `crates/apac-research/examples/layout_presence.rs` is a suite helper, not an API example.

The core's public surface has three tiers: the crate root is the decoding API (`Config`, `Decoder`, `ParsedPacket`, `StreamInfo`, `DecodeError`, `ChannelLayout`, `MAX_PACKET_BUFFER`); `apac_core::inspect` is the report layer (packet/frame report parsers, contexts, stateful `*_with_state` parsing, report and state types); `apac_core::identity` holds the frozen profile strings and format/math digests. `frame` and `synthesis` are private and nothing is `#[doc(hidden)]`: when research needs a core item, export it from `inspect` (or `identity`), never by making an internal module public.

`README.md` (Chinese, ~140 KB) is the user-facing reference for every command, report schema, exit code and support boundary; read the relevant section before changing behavior. The README sections "实现边界", "共享配置与组合 HOA 流" and "ASP 与帧长支持边界" define what is deliberately *not* supported (LRVQ, active DRC/loudness/EQ processing, outer ASP reconfiguration, frame-length index ≠ 0, spatial rendering) — unsupported paths must reject explicitly, never guess.

## Commands

Use Rust 1.98.0 (edition 2024) for numeric acceptance and clippy. Dependencies are cached on the primary dev machine; on a fresh machine drop `--offline` for the first build.

```sh
cargo +1.98.0 build --offline                       # apac-tool only (default member)
cargo +1.98.0 build --offline --workspace --bins --examples
cargo +1.98.0 test --offline --workspace
cargo +1.98.0 clippy --offline --workspace --all-targets -- -D warnings
cargo +1.98.0 check --offline -p apac-core          # core without the serde feature
# no_std builds (both targets, with and without --features serde); core code may not use std::
# or float methods missing from core (sqrt etc.): use libm
cargo +1.98.0 build --offline -p apac-core --target thumbv7em-none-eabihf
cargo +1.98.0 build --offline -p apac-core --target wasm32v1-none --features serde
cargo +1.98.0 build --offline -p apac-no-std-example --target thumbv7em-none-eabihf
# rustdoc must stay warning-free (missing_docs is denied by clippy -D warnings)
cargo +1.98.0 doc --offline --no-deps -p apac-core -p apac-container
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
- `apac-core/src/config/` — cookie (magic cookie / ASC) syntax parser: DRC, HOA, scenes, passive renderer/metadata. It builds the typed `Config` (`config/model.rs`): only the values decoding reads, each wire field as `Field<T> = Option<Located<T>>` so rejection texts keep their `name=value at cookie bit N` / `=missing … bit unknown` forms. Field events (`ConfigField` with a `record::FieldValue`) are recorded only on request: `Config::parse` records nothing, `parse_recorded` also returns the `Recording`; `apac_research::config::{parse_cookie, CookieReport}` assemble the report `parse-cookie` prints (`ParseStatus` complete/partial/unsupported). Field names are passed as `format_args!` and formatted only when recorded; the 16384-field limit counts every event. Syntax the decoder carries is collected by zone either way: the DRC declaration's fields (`DrcDeclaration::fields` / `loudness_metadata`) and the scene graph declaration as `trace_sha256`, the digest of its fields' JSON array (`record` has the serde_json-identical writer). Decoding code reads `Config`, never field names; when a consumer needs a new cookie value, add it to `Config`, fill it where the parser reads it, and extend the `from_report` oracle in `config/legacy_tests.rs` (`model_tests` compares it, the recorded and the unrecorded parse over the corpus, truncations and bit flips). In-band scene, DRC header and graph updates take a `record` flag and return an `InBand` outcome.
- `apac-core/src/frame/` — bit-level packet parsing. `FrameContext` / `ChannelFrameContext` / `HoaFrameContext` / `StreamFrameContext` are built `from_config(&Config)` (`from_cookie(&[u8])` parses then delegates; `DecodedFrameContext` dispatches). Composite streams (multiple ASCs, shared configuration, HOA + SQ components) reuse the single-ASC checks through `Config::component_view`. Packet parsers record fields and derived values only when `capture` is set; decisions read typed state (`FrameReport::preroll`, `FrameReport::cpe_absent`), never recorded fields. Many `hoa_*` modules each own one syntax area and expose a `PROFILE` string and `format_sha256()`.
- `apac-core/src/synthesis/` — independent Float64 decoder math: IMDCT/FFT, overlap, HOA reconstruction, DRC-off path. `Decoder` holds one `Engine` (Stereo, Channels, Hoa, Composite) and offers `new(&Config)`, `info()`, `decode(packet, &mut out) -> FrameInfo`, `decode_vec`, `advance` (state-only fast access, rechecked through the full path on failure) and `reset`. `decode` is the public two-stage `parse` (against copies of the state) then `synthesize` (render and commit together); a generation counter rejects a parsed packet after any other commit or reset. Research times the two stages; the core has no clock. Frame parsers take a `ParseMode`: `Report` records syntax, `Decode` evaluates spectra without recording, `Scan` advances state only. Only report-filling sites may test `record()`; anything synthesis reads stays on `spectra()`, and `synthesis::mode_tests` proves both modes decode every fixture identically. Report identifiers (backend, support scope, state/numeric/layout profiles, access profiles) live in `apac_research::implementation`. The decoder exposes its committed state as a borrowed `MetadataState`; `apac-research` digests it as `metadata_after_processing_sha256`. Final PCM is the only Float32 cast. Errors are `error::DecodeError` (stage, message, bit offset); `apac_research::error::Error` adds OSStatus, file position and packet index for the CLI. `apac-research/src/decode.rs` drives `decode-sq` through `apac_container::Reader` (sequential vs. fast access via `SqAccessMode`) and assembles the report from the reader's `Stats` and decoder.
- `apac-container/src/{caf.rs,mp4/}` — container readers (tests use the in-memory `test_source::Shared`, which can change between passes); `apac-research/src/packets.rs` — packet-directory bundles; `apac-core/src/{numeric.rs,bwe2_math.rs}` — fixed numeric tables.
- `data/*.json` — frozen tables, format descriptions and state vectors. Runtime tables are compiled in by `apac-core/build/` (one generator per area: `sq.rs`, `formats.rs`, `salient.rs`): it validates each file with the checks the old runtime loaders ran, applies their derivations (bit patterns → floats, Huffman tries, packed salient dictionaries) and writes `$OUT_DIR/tables.rs`, included by `apac-core/src/tables/mod.rs`. Floats are emitted from exact bit patterns; core has no runtime JSON parsing or lazy table init. `src/tables/{trie_build,packed}.rs` are shared with the build script via `#[path]` (alloc-only). `tables::legacy_tests` keeps the former loaders and checks every generated table bit for bit — extend it when adding a table. Format/math identifiers (`format_sha256()` etc.) are build-time constants (embedded JSON fields or file-byte SHA-256). JSON state/vector fixtures are still read with `include_str!` in tests only.
- `scripts/` — Python (stdlib only) independent oracles and acceptance harnesses, grouped by naming convention:
  - `generate_*.py` — (re)generate a `data/` file; `--check` verifies the committed file and its embedded hash without writing.
  - `*_vectors.py` / `*_oracle.py` — independent input vectors and reference math.
  - `validate_*.py` — acceptance runs against a built binary (`--binary target/debug/apac-tool --report reports/...`); `*_native.py` / `native_*_trace.py` additionally use macOS AudioToolbox/LLDB and pinned component hashes.
  - `test_*.py` — unittest suites.

## Invariants when changing code

- **Numeric identity is frozen.** Profile strings (e.g. `apac-sq-math-v1`, `apac-hoa-shared-configuration-v1`), `BACKEND`, data-file hashes and the order of floating-point operations are part of published results. Don't alter existing arithmetic order or data files; introduce new behavior under a new/optional profile or report field and keep existing reports byte-compatible.
- **Restructuring guard.** While the no_std decode API refactor is in progress, library APIs may break but CLI output must stay byte-identical (PCM, `parse-cookie`/`parse-packets` JSON, error text, exit codes). Never regenerate `crates/apac-research/tests/golden/refactor-v1.json` to make a change pass; the Python unittest failures recorded at the baseline (6 HOA/TNS cases) must stay exactly the same set.
- After touching anything in `data/`, the corresponding `generate_*.py --check` must still pass.
- **Public items are documented.** `apac-core` and `apac-container` set `#![warn(missing_docs)]`. Document every new public type, function, method, constant and variant; serializable report types document the type and put `#[allow(missing_docs)]` on it (their fields are the JSON keys). Research-only core items go into `inspect`, frozen identifiers into `identity`.
- State changes are atomic per outer packet: a failed packet must not commit component descriptions, dynamic maps, DRC history, or overlap; `reset()` restores the initial state.
- Exit codes: `0` success/complete; `1` runtime/input/integrity error; `2` PCM out of tolerance, unresolved collection errors, or `partial`/`unsupported` parse (`parse-packets` usually returns 2 because payloads beyond the prefix are unparsed).
- Export commands require `--out` to be a **new, non-existent directory** and cap cumulative output at 128 MiB (`--max-output-mib`). JSON/JSONL records use `schema_version: 1`; optional system properties are `{"value": ..., "error": null}`.

## Repository rules (from AGENTS.md)

- `docs/` is a **separate Git repository** (research notes, phase summaries) ignored by this repo — never force-add it, make it a submodule, or a gitlink. Check it with `git -C docs status`. Usage and public interface docs go in `README.md`; research/analysis goes in `docs/`, and experimental conclusions there should cite the code commit.
- `reports/`, `artifacts/`, `target/` and local Xcode workspaces stay local (they contain absolute paths, sample names, audio). Never commit them.
- Committed examples use generic paths or explicit arguments — no local usernames, music-library listings, credentials, Apple binaries or decompiled output.
- Don't rewrite local raw experiment records for redaction; produce a separate redacted copy when sharing.
