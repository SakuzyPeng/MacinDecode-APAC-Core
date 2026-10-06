# Third-party format tables

The project's own code is licensed under the MIT License (`LICENSE`). The data below keeps its own terms.

`data/sq-codebooks.json` contains the AAC spectral and scale-factor Huffman codewords and code lengths and the 44.1/48 kHz scale-factor band offsets, taken from vo-aacenc `aacenc/src/aac_rom.c`, commit `a277487e051e92e99a532294eed3c673f4d879f2` (https://github.com/mstorsjo/vo-aacenc).

Copyright 2003-2010, VisualOn, Inc. Licensed under the Apache License, Version 2.0; the license is included in `LICENSES/Apache-2.0.txt`. The vo-aacenc NOTICE file reads:

> Additional Codecs
> These files are Copyright 2003-2010 VisualOn, but released under
> the Apache2 License.

The JSON records its source URL, SHA-256, copyright, license and changes. Changes are limited to selecting these format constants, splitting the packed book-pair length tables and converting their representation to JSON. `scripts/generate_sq_codebooks.py --source <aac_rom.c>` reproduces the file byte for byte from the hash-pinned source; `--check` alone verifies that every book is a complete prefix code and that the offsets are well formed. The Rust trie, APAC parser and vector generator are newly written; Apple lookup nodes and disassembly are not included.


## Historical reference sine-window observations

`data/sq-sine-windows.json` records Float32 numerical sine-window coefficients observed in the verified macOS 27.0 / 26A428 AudioCodecs reference. Its component SHA-256 and numerical-profile ID are included. These observations are separate from the vo-aacenc Huffman tables above.

The file contains rising-half window values only, with no executable bytes, disassembly, decoder control flow, source-media data or local paths. `scripts/verify_sine_windows.py` verifies every coefficient against a read-only snapshot of the matching reference build. This is a versioned numerical compatibility profile, not a claim that APAC universally requires that implementation's rounding.

The historical window data is used only by diagnostic verification. It is not included in the default SQ synthesis path.

## Independent mathematical profile

`data/sq-math-v1.json` is independently generated from mathematical formulas by `scripts/generate_sq_math.py` and `scripts/sq_math.py`, using Python's standard-library Decimal arithmetic at 100 and 200 decimal digits. It records exact IEEE bit patterns for inverse quantization, gains, sine windows, modulation and FFT rotations. These constants are not copied from an Apple component or the historical window observations. The generator and the independent direct-sum synthesis oracle are included for offline reproduction.

## CAC wire constants and independent mathematics

`data/cac-codebooks.json` records the 35 gain-index and 44 repeat-code wire mappings observed in AudioCodecs 7.0 on macOS 27.0 / 26A428. The encoder and decoder mappings were compared entry by entry under the component SHA-256 recorded in the file. `scripts/verify_cac_codebooks.py` reproduces this read-only, hash-gated check. These are observations of format constants, not a claim that a public AAC standard defines APAC CAC. Only value/codeword/length mappings are included; native decoder nodes, executable bytes and disassembly are not included.

`data/cac-math-v1.json` is independently generated from the dB grid and normalized matrix formulas by `scripts/generate_cac_math.py`, checked at 100 and 200 Decimal digits. It does not copy the native Float32 rotation table or depend on a system exp/sqrt implementation. Both CAC data files are included directly in the Rust build; the diagnostic extractor is not a build step.

## HOA source-layout format constants

`data/hoa-source-layout-format-v1.json` records bounded source-layout matrix coefficients and accepted layout identifiers observed in the hash-bound AudioCodecs 7.0 reference. Matrix entries retain their Float32 bit patterns; shared matrices are stored once. Channel labels follow the public CoreAudio layout property. These are format observations, not executable bytes, decompiled control flow, native decoder objects or source-media content. The included packaging tool accepts explicit observation paths; reference binaries and observations are not build dependencies.

Source recovery uses independently implemented Float64 compensated sums and a separate Decimal direct-sum oracle. A reference defect in the CICP_7 alias's LFE position is reconstructed only by the native diagnostic validator; production uses the declared public channel layout.

## Rules behind recorded HOA format constants

Several HOA format files were first recorded from the AudioCodecs reference. The following values are now re-derived from independent rules by `scripts/hoa_rules_oracle.py --check` and `scripts/generate_hoa_shared_config_format.py --vo-aacenc DIR --check`, preserving their published table identities:

| Constants | File | Rule | Agreement |
| --- | --- | --- | --- |
| Band offsets for all 13 sampling rates; TNS band limits | `hoa-shared-config-format-v1.json` | AAC tables, rebuilt from vo-aacenc `aac_rom.c` and `tns.c` (Apache-2.0) at the commit pinned above | byte-identical file |
| Subband grids, methods 0–2, 1–16 subbands | `hoa-dynamic-format-v1/v2.json`, `hoa-salient-subbands-format-v1/v2.json` | Method 0 interpolates linearly over the Zwicker critical-band edges (with 24 kHz as the last point, mapped onto 1024 lines), method 1 over the 49 AAC long-window bands; both round half up once onto the 128-line short-window grid. Method 2 takes equal widths, rounds half up to a long-window line, then half up onto the short grid. Long-window ends are eight times the short ones. | exact, except method 0 with 9 subbands: first end 2 where the rule gives 1, kept as recorded |
| Salient coefficient groups and mode structure, orders 1–10 | `hoa-salient-orderN-shared-v1.json` | all coefficients / odd l+\|m\| / even l+\|m\| (symmetry about the horizontal plane); the same mode structure for every order | exact |
| Static ambient sign tables | `hoa-static-ambient-tables-v1.json` | row orders of the 4×4 Sylvester–Hadamard matrix, divisor 2, inverse by transpose | exact |
| Source-layout matrices of the 18 full-rank layouts | `hoa-source-layout-format-v1.json` | (order + 1) · pinv(Y), Y the real N3D spherical harmonics (ACN order, no Condon–Shortley phase) at the speaker directions, LFE excluded | at most 4.1e-7 relative to the largest entry, the Float32 rounding of the reference; not bit-identical, so the recorded bits stay |

These retain their original observation provenance: the profile/level table; the speaker directions per layout (inferred from the matrices, all round angles); the three Hadamard row orders; the matrices of the 18 rank-deficient layouts (horizontal speakers with height or higher-order coefficients have no unique pseudo-inverse, and the recorded values come from the reference's near-singular inversion); and the data-trained dictionaries — salient Huffman codebooks except the two measured books below, mode-4 cluster matrices except the measured order-3 cluster-0 matrix below, BWE2 and CAC codebooks.

## Independently reconstructed HOA salient codebooks

Two 64-symbol Huffman codebooks for **order 3, six-bit quantization** are now sourced from independent measurements of controlled bitstreams and PCM returned by Apple's public AudioConverter API:

| Codebook | Measured source |
| --- | --- |
| Mode 1, book 0 | `data/hoa-salient-order3-q6-mode1-measured-v1.json` |
| Mode 4, cluster 0 / book 0 | `data/hoa-salient-order3-q6-mode4-cluster0-measured-v1.json` |

Both experiments used the arm64 reference on macOS 27.0 / 26A428, with AudioCodecs component SHA-256 `826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd`. They reused known bitstream syntax and public-source AAC tables for the carrier, calibrated with mode 0's fixed-width coding, and recovered prefix trees from observable PCM. Discovery did not consult the recorded salient dictionaries, debugger snapshots or internal decoder state; it used the existing tool's native replay path.

The mode-1 experiment used code commit `880640456ad3119f098cec1a08592699fcd74be6`. Its candidate was frozen and validated before comparison: all **64 codewords and lengths matched exactly**, and 152 validation cases across two carrier amplitudes produced PCM bit-identical to their mode 0 controls. Its frozen candidate SHA-256 is `85abb44d580e5002cb56fca1eff950d554025b444759d485b243ef70299b9622`.

The mode-4 cluster-0 experiment used code commit `f4110ca0eb9383c534479026f8f2b6f9656ca646`. Single-bit perturbations identified a scaled transform from which the first decoded symbol could be isolated, without reading the recorded matrix. The frozen codebook passed normal-length bitstream validation and all **64 codewords and lengths matched exactly**. Its frozen codebook SHA-256 is `12fc04895e48037d2d5919caf0de31ee61299f75943733fa71b7fbd2db455293`. Matrix reconstruction is covered separately below; the other three cluster codebooks keep their previous sources.

Each measurement file contains only symbol/codeword/length mappings and provenance metadata. `scripts/generate_hoa_salient_measured.py --candidate FILE --check` reproduces a measurement from its hash-pinned frozen codebook candidate; repeat `--candidate` to supply both candidates. Other inputs use their committed measurements. Without `--candidate`, `--check` verifies both measured sources and their packed copies in `data/hoa-salient-format-v1.json`; `--write` regenerates both copies and their per-codebook source metadata. Matrix candidates are not accepted.

The Rust build reads both measured files directly, verifies their mapping digests, and requires the packed copies to agree. The format JSON retains its initial x86_64 extraction record under `source.original_observation`, identifies the measured replacements separately, and keeps the remaining tables under their original provenance. All table values and the published semantic digest are unchanged.

The experiments are identified as `hoa-blackbox-order3-q6-mode1-v1` and `hoa-blackbox-order3-q6-mode4-cluster0-v1`. Their prototypes and raw evidence remain local under `reports/` and are not distributed or required for builds. These source replacements do not change the project's licensing declarations.

## Independently reconstructed HOA mode-4 matrix

The **order-3, mode-4, cluster-0** 16×16 matrix is now sourced from `data/hoa-salient-order3-mode4-cluster0-matrix-measured-v1.json`. It contains the 256 exact Float32 bit patterns in row-major order and their provenance. The existing decoder shares this matrix across six- through nine-bit quantization; the observations used six-bit input descriptors.

The source comes from a weighted reanalysis of the experiment's existing public AudioConverter PCM. Its calibration policy was frozen from known mode-0 inputs before matrix estimation; no recorded matrix or previous matrix candidate was used. The maximum empirical half-width was `2.3251493876046068e-7`, below the unchanged `2.5e-7` limit with the eightfold safety factor retained. All 254 held-out checks passed, and all 256 frozen Float32 words matched the recorded matrix exactly. The reanalysis ran at code commit `cabcb5523c9175cdb363081f212a7712fa18fb44`; it required no fresh native acquisition. Its qualified candidate SHA-256 is `3e1fa1a640d1fa68c270ed4b9aadd59d544a153cdccef4b74bb0fba6bb722899`.

`scripts/generate_hoa_salient_measured_matrix.py --candidate FILE --check` reproduces the measured source from that hash-pinned qualified candidate; the initial unqualified candidate is not accepted. Without `--candidate`, `--check` verifies the committed matrix, its packed copy and the provenance records in all four order-3 dictionaries. `--write` regenerates them without changing other matrices or codebooks.

The Rust build reads the measured matrix directly, verifies its word digest and requires its packed copy in `data/hoa-salient-order3-shared-v1.json` to agree. The packed words, sharing arrangement and all published table digests are unchanged. The other three order-3 cluster matrices and matrices of other orders retain their original sources. Raw PCM, continuous estimates and local evidence paths are not included in the measured source.

## Independently reconstructed HOA spatial-control means

`data/hoa-spatial-means-measured-v1.json` is the canonical source for all 121 HOA spatial-control mean coefficients, stored as exact Float32 bit patterns in ACN order. They were independently reconstructed from controlled mode-0 bitstreams and PCM returned through the public AudioConverter API on macOS 27.0 / 26A428, arm64. Known bitstream syntax and public-source AAC tables supplied the carriers; discovery did not read the previous mean table or native decoder state.

Full-band calibration gave initial estimates. Exact dyadic mode-0 carriers then cancelled each candidate, and neighboring Float32 values produced distinguishable nonzero residuals. The frozen candidate passed 963 channel checks from 12 independent validation acquisitions, including repeated cancellation, padding, held-out spectral lines and cross-order prefixes. Only then did a separate comparison process confirm all 121 words matched the prior observations. The complete experiment used 24 native acquisitions and had no unresolved coefficients.

The experiment used code baseline `8de23429ad2d45a07814b09805623ddec5c887dc` plus an uncommitted measurement extension. The measured source records the exact producer fingerprint, component and binary hashes, and frozen calibration, candidate, validation and comparison identities. Its word digest is `299576ce0a06ba6165b2a7ee1485d7211f9bed94b7e5936ce7ec51c5b4f30c42`. Reproducible tooling and evidence handling are described in `guide/hoa-mean-blackbox.md`.

`scripts/generate_hoa_spatial_means_measured.py --check` verifies the canonical measurement, its format copy and provenance offline. Supplying the registered frozen candidate, validation and comparison files reproduces the measurement. The Rust build reads the canonical source directly, verifies its pinned file and word hashes, and requires the copy in `data/hoa-spatial-controls-format-v1.json` to agree. Missing measured data fails the build. The format file preserves its original observation record for the remaining subband tables. All mean values and the published format semantic digest remain unchanged.

Raw PCM, local evidence paths and continuous estimates stay outside the production source. This source replacement does not change the project's licensing declarations.
