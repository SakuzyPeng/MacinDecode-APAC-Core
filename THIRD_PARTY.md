# Third-party format tables

The project's own code is licensed under the MIT License (`LICENSE`). The data below keeps its own terms.

The full license and copyright notices for every external Rust dependency in `Cargo.lock` are included in `LICENSES/Rust-dependencies.txt` and in the CLI archives. Regenerate this file with `python3 -B scripts/generate_dependency_licenses.py` after changing the lockfile. CI checks it against the locked crates' bundled license files.

`data/sq-codebooks.json` contains the AAC spectral and scale-factor Huffman codewords and code lengths and the 44.1/48 kHz scale-factor band offsets, taken from vo-aacenc `aacenc/src/aac_rom.c`, commit `a277487e051e92e99a532294eed3c673f4d879f2` (https://github.com/mstorsjo/vo-aacenc).

`data/aac-sfb-tns-v1.json` contains the SFB offsets and Main/LC TNS band limits for all 12 upstream sampling rates, independently regenerated from the same commit's `aac_rom.c` and `tns.c`. Its source record pins both files by SHA-256 and records the copyright, license and JSON conversion. `data/hoa-shared-config-format-v2.json` combines these licensed constants with the qualified shared-profile and layout measurements. The 7350 Hz configuration uses the 8000 Hz table bucket.

Copyright 2003-2010, VisualOn, Inc. Licensed under the Apache License, Version 2.0; the license is included in `LICENSES/Apache-2.0.txt`. The vo-aacenc NOTICE file reads:

> Additional Codecs
> These files are Copyright 2003-2010 VisualOn, but released under
> the Apache2 License.

The JSON records its source URL, SHA-256, copyright, license and changes. Changes are limited to selecting these format constants, splitting the packed book-pair length tables and converting their representation to JSON. `scripts/generate_sq_codebooks.py --source <aac_rom.c>` reproduces the file byte for byte from the hash-pinned source; `--check` alone verifies that every book is a complete prefix code and that the offsets are well formed. The Rust trie, APAC parser and vector generator are newly written; Apple lookup nodes and disassembly are not included.


## Historical reference sine-window observations

Historical Float32 sine-window observations and their native comparison tool are retained in the private research archive. The current build obtains its windows from the independent `apac-sq-math-v2` formula generator.

## Independent mathematical profile

`data/sq-math-v2.json` (the default `apac-sq-math-v2`) and the preserved `data/sq-math-v1.json` are independently generated from mathematical formulas by `scripts/generate_sq_math.py` and `scripts/sq_math.py`, using Python's standard-library Decimal arithmetic at 100 and 200 decimal digits. They record exact IEEE bit patterns for inverse quantization, gains, sine windows, modulation and FFT rotations (v2 stores the inverse quantizer and gains as Float64). These constants are not copied from an Apple component or the historical window observations. The formula generator is included for offline reproduction.

## CAC wire constants and independent mathematics

`data/cac-codebooks-measured-v1.json` supplies all 35 gain-index and 44 repeat-code mappings. Controlled public AudioConverter PCM observations recovered complete prefix trees on macOS 27.2 / 26B123. The candidate was frozen before 411 independent held-out checks covering amplitudes, channel mixtures, run lengths, field boundaries and frame overlap. A subsequent isolated comparison matched all 79 mappings exactly. Candidate, validation, comparison, producer and component hashes are retained in the measured source. The Research generator reproduces the compatibility copy `data/cac-codebooks.json`. The Rust build reads the measured source and checks its complete file identity and compatibility copy.

`data/cac-math-v1.json` is independently generated from the dB grid and normalized matrix formulas by `scripts/generate_cac_math.py`, checked at 100 and 200 Decimal digits. It does not copy the native Float32 rotation table or depend on a system exp/sqrt implementation. The Rust build consumes the measured wire source and the independently generated rotation tables.

## Independently reconstructed BWE2 excitation gains

`data/bwe2-gains-measured-v1.json` is the canonical source for all 64 BWE2 excitation gains, stored as exact Float32 bit patterns in index order. Controlled bitstreams and PCM from the public AudioConverter API supplied the observations on macOS 27.0 / 26A428, arm64. Discovery reused known syntax and public-source AAC tables without reading the previous gain values or internal decoder state.

The reconstruction used monic spectral factorization and relative PCM calibration. A `0.00001` decimal grid was inferred from discovery measurements and tested with independent held-out inputs; this hypothesis is explicitly retained in the measured source. This is not an unconditional uniqueness claim over arbitrary Float32 values. The frozen candidate passed 512 held-out gain checks and eight absolute-scale controls. A subsequent independent comparison matched all 64 Float32 words exactly.

The experiment used code baseline `8de23429ad2d45a07814b09805623ddec5c887dc` with an uncommitted tool extension, later packaged in `9468a2e246d1728a9924be690d9b4719772ec18d`. Candidate and validation producer fingerprints, binary and component identities, and frozen evidence hashes are recorded in the measured source. The gain word digest is `b4089e46f670df5bb84923eba409db45b7b117f5f910c816b48de197cdc5fe5d`.

The measured gains and formula-defined BWE2 mathematics are retained. Neither 512-by-16 original LSF table is distributed. BWE2 is disabled by default; the optional numerical crate requires an explicit external input and verifies its frozen identity. Private acquisition tools, PCM, source snapshots and Apple binaries are not included.

## HOA source-layout format constants

`data/hoa-source-matrices-measured-v1.json` supplies 34 reachable source-layout matrices with 3,038 Float32 coordinates recovered from controlled public AudioCodec PCM. The frozen candidates passed 136 mixed or single-channel checks and 413 held-out acquisitions. Matrix zero signs use a qualified decoding-equivalence argument bound to three consumer source files.

`data/hoa-source-layout-acceptance-measured-v1.json` supplies the 40 accepted layout identifiers and public CoreAudio channel labels. The identifier domain comes from the qualified public `aocl` measurement. HOA type-2 controls independently verify its consumer behavior through 176 discovery cases and 176 frozen-candidate held-out cases. They use 48/44.1 kHz, distinct frequencies and amplitudes, variable-layout cardinalities 1/3, and four rejected layout controls. Packaging revalidates 434 property observations, 228 complete PCM acquisitions and 124 initialization rejection controls, including their original inputs, processing properties, positional bases, mixtures and overlap.

The format generator builds `data/hoa-source-layout-format-v1.json` directly from both qualified sources. The Rust build reads the canonical measurements, verifies complete file hashes, and checks every compatibility copy. The two unavailable or excluded matrix entries retain SDK labels and empty numerical tables. Source recovery uses Float64 compensated sums and a separate Decimal direct-sum oracle. Native observations retain the CICP_7 LFE discrepancy and the first-two-slot permutation of tag 8060934. Production interprets source positions and LFE channels using the declared public SDK labels.

## Rules behind recorded HOA format constants

Several HOA format files were first recorded from the AudioCodecs reference. The following values have independent rule checks in the source research records. Shared rate tables retain the pinned public upstream and regeneration provenance.

| Constants | File | Rule | Agreement |
| --- | --- | --- | --- |
| Band offsets for all 13 sampling rates; TNS band limits | `aac-sfb-tns-v1.json`, `hoa-shared-config-format-v2.json` | AAC tables, rebuilt from vo-aacenc `aac_rom.c` and `tns.c` (Apache-2.0) at the commit pinned above, with 7350 Hz using the 8000 Hz bucket | identical runtime tables |
| Subband grids, methods 0–2, 1–16 subbands | `hoa-dynamic-format-v1/v2.json`, `hoa-salient-subbands-format-v1/v2.json` | Method 0 interpolates linearly over the Zwicker critical-band edges (with 24 kHz as the last point, mapped onto 1024 lines), method 1 over the 49 AAC long-window bands; both round half up once onto the 128-line short-window grid. Method 2 takes equal widths, rounds half up to a long-window line, then half up onto the short grid. Long-window ends are eight times the short ones. | exact, except method 0 with 9 subbands: first end 2 where the rule gives 1, kept as recorded |
| Salient coefficient groups and mode structure, orders 1–10 | `hoa-salient-orderN-shared-v1.json` | all coefficients / odd l+\|m\| / even l+\|m\| (symmetry about the horizontal plane); the same mode structure for every order | exact |
| Static ambient sign tables | `hoa-static-ambient-tables-v1.json` | row orders of the 4×4 Sylvester–Hadamard matrix, divisor 2, inverse by transpose | exact |
| Source-layout matrices of the 16 reachable full-rank layouts | `hoa-source-layout-format-v1.json` | (order + 1) · pinv(Y), Y the real N3D spherical harmonics (ACN order, no Condon–Shortley phase) at the speaker directions, LFE excluded | geometric diagnostic within 4.1e-7 relative to the largest entry; production coordinates come from the qualified public PCM measurements above |

The legacy shared configuration and its original internal profile-31 bounds are preserved in the private research archive. The supported shared configuration has a qualified-derived v2 data identity. Its syntax profile is `apac-hoa-shared-configuration-v1`. The build verifies the complete derived file and every parent input, including the licensed AAC tables and the canonical profile/layout measurements. The original LSF tables and four private protocol/state descriptions are excluded from this repository. Frozen compatibility identifiers are retained without those description-file dependencies.

## Independently reconstructed HOA salient codebooks

The **320 independently measured active Huffman codebooks for orders 1–10, six- through nine-bit quantization** are sourced from independent measurements of controlled bitstreams and PCM returned by Apple's public AudioConverter API. Each order and precision has eight books: mode 1, two mode-2 books, mode 3, and four mode-4 clusters. Together they contain 76,800 symbol mappings. The measured files are named `data/hoa-salient-orderN-qQ-modeM[-bookB|-clusterC]-measured-v1.json`; their candidate, validation, comparison and producer identities are pinned in the source registries and file metadata.

The first two six-bit pilots were:

| Codebook | Measured source |
| --- | --- |
| Mode 1, book 0 | `data/hoa-salient-order3-q6-mode1-measured-v1.json` |
| Mode 4, cluster 0 / book 0 | `data/hoa-salient-order3-q6-mode4-cluster0-measured-v1.json` |

The measurements used the arm64 reference on macOS 27.0 / 26A428, with AudioCodecs component SHA-256 `826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd`. They reused known bitstream syntax and public-source AAC tables for the carrier, calibrated with mode 0's fixed-width coding, and recovered prefix trees from observable PCM. Discovery did not consult the recorded salient dictionaries, debugger snapshots or internal decoder state; it used the existing tool's native replay path. Candidates were frozen and independently validated before the final reference comparison.

The mode-1 experiment used code commit `880640456ad3119f098cec1a08592699fcd74be6`. Its candidate was frozen and validated before comparison: all **64 codewords and lengths matched exactly**, and 152 validation cases across two carrier amplitudes produced PCM bit-identical to their mode 0 controls. Its frozen candidate SHA-256 is `85abb44d580e5002cb56fca1eff950d554025b444759d485b243ef70299b9622`.

The mode-4 cluster-0 experiment used code commit `f4110ca0eb9383c534479026f8f2b6f9656ca646`. Single-bit perturbations identified a scaled transform from which the first decoded symbol could be isolated, without reading the recorded matrix. The frozen codebook passed normal-length bitstream validation and all **64 codewords and lengths matched exactly**. Its frozen codebook SHA-256 is `12fc04895e48037d2d5919caf0de31ee61299f75943733fa71b7fbd2db455293`. Later batches completed the remaining order-3 books and all order-1/2 books; their per-batch identities are recorded in Research: `scripts/hoa_measured_batch_sources.py`.

The order-10 campaign used baseline `8de23429ad2d45a07814b09805623ddec5c887dc` with the frozen v12 producer `30209667c3f449742e88d22d15526c98b61ee6bcd64a7f89d2308eff13bd7acf`. All 32 books, four matrices and three coefficient groups completed qualification before registration. Their identities are pinned in Research: `scripts/hoa_measured_high_order_sources.py` and the build loads these qualified sources. Orders 4–9 add 192 qualified codebooks and 24 matrices using the frozen campaigns, coordinate supplements and independent mode-1 held-out carriers. Their nine unresolved matrix zero signs have a separate, consumer-bound zero-equivalence qualification. The source retains both signed-zero candidates and the unknown original signs.

Each measurement file contains symbol/codeword/length mappings, provenance metadata and, for the six-bit mode-2/3 sources, independently measured coefficient groups. Higher-precision source records identify their six-bit group and matrix priors. Coordinate supplements identify their coordinate priors and unresolved original matrix signs. Research: `scripts/generate_hoa_salient_measured.py --candidate FILE --check` reproduces a measurement from its hash-pinned frozen codebook candidate; repeat `--candidate` for multiple candidates. Other inputs use their committed measurements. Without `--candidate`, `--check` defaults to all active order-1–3 measured sources and their packed copies in the corresponding order/precision dictionaries; `--write` regenerates the copies and their per-codebook source metadata. Matrix candidates are not accepted by this generator.

The Rust build reads active measured files for orders 1–3 and 10 directly, verifies their mapping digests, and requires the packed copies to agree. A missing active measurement fails the build instead of falling back to the packed copy. The format JSON retains its initial x86_64 extraction record under `source.original_observation`, identifies measured replacements separately, and keeps the remaining tables under their original provenance. All table values and published semantic digests are unchanged.

The first pilots are identified as `hoa-blackbox-order3-q6-mode1-v1` and `hoa-blackbox-order3-q6-mode4-cluster0-v1`; later experiment names are recorded in each measured file. Prototypes and raw evidence remain local and are not distributed or required for builds. These source replacements do not change the project's licensing declarations. The reproducible batch tool and its acceptance rules are documented in `guide/hoa-blackbox.md`.

## Independently reconstructed HOA mode-4 matrices

The **40 independently measured active mode-4 matrices for orders 1–10** are sourced from `data/hoa-salient-orderN-mode4-clusterC-matrix-measured-v1.json`. They contain 159,892 Float32 words in row-major order and their provenance. Nine zero coordinates carry explicitly qualified canonical positive zeros and retain unknown original signs. Each order contributes four matrices of size `(order + 1)² × (order + 1)²`. The decoder shares each matrix across six- through nine-bit quantization. The observations used six-bit input descriptors.

The first order-3 cluster-0 source came from a weighted reanalysis of the experiment's existing public AudioConverter PCM. Its calibration policy was frozen from known mode-0 inputs before matrix estimation; no recorded matrix or previous matrix candidate was used. The maximum empirical half-width was `2.3251493876046068e-7`, below the unchanged `2.5e-7` limit with the eightfold safety factor retained. All 254 held-out checks passed, and all 256 frozen Float32 words matched the recorded matrix exactly. The reanalysis ran at code commit `cabcb5523c9175cdb363081f212a7712fa18fb44`; it required no fresh native acquisition. Its qualified candidate SHA-256 is `3e1fa1a640d1fa68c270ed4b9aadd59d544a153cdccef4b74bb0fba6bb722899`. Later matrices used the same weighted calibration method and qualification thresholds, with their own frozen evidence and held-out checks.

Research: `scripts/generate_hoa_salient_measured_matrix.py --candidate FILE --check` reproduces a measured source from its hash-pinned qualified candidate; unqualified candidates are not accepted. Without `--candidate`, `--check` defaults to the order-1–3 measured matrices, their packed copies and provenance records in all four precisions. `--write` regenerates those copies without changing other matrices or codebooks.

For the order-9/10 experiments, the approved v12 policy permitted an infinity-norm condition number up to 128 and additionally required a normalized Gram residual no greater than `1e-3`. The inverse residual limit `1e-10`, eightfold uncertainty safety factor, `5e-8` half-width floor and strict `<2.5e-7` precision limit remained unchanged. Order 10's 58,564 frozen matrix words passed independent validation and matched the reference exactly, with no unresolved entries. The decoder loads these qualified order-10 sources.

The Rust build reads measured matrices for orders 1–3 and 10 directly, verifies their word digests and requires the packed copies in the corresponding `data/hoa-salient-orderN-shared-v1.json` to agree. Their packed words, sharing arrangement and published table digests are unchanged. The source generators default to orders 1–3; explicit higher-order `--check` is available for audits. Independent-source `--write` and campaign registration retain their separate order-1–3 write qualification limit. Raw PCM, continuous estimates and local evidence paths are not included in measured production sources; unresolved positive/negative zero bits are never filled from the reference.

## Independently reconstructed HOA spatial-control means

`data/hoa-spatial-means-measured-v1.json` is the canonical source for all 121 HOA spatial-control mean coefficients, stored as exact Float32 bit patterns in ACN order. They were independently reconstructed from controlled mode-0 bitstreams and PCM returned through the public AudioConverter API on macOS 27.0 / 26A428, arm64. Known bitstream syntax and public-source AAC tables supplied the carriers; discovery did not read the previous mean table or native decoder state.

Full-band calibration gave initial estimates. Exact dyadic mode-0 carriers then cancelled each candidate, and neighboring Float32 values produced distinguishable nonzero residuals. The frozen candidate passed 963 channel checks from 12 independent validation acquisitions, including repeated cancellation, padding, held-out spectral lines and cross-order prefixes. Only then did a separate comparison process confirm all 121 words matched the prior observations. The complete experiment used 24 native acquisitions and had no unresolved coefficients.

The experiment used code baseline `8de23429ad2d45a07814b09805623ddec5c887dc` plus an uncommitted measurement extension. The measured source records the exact producer fingerprint, component and binary hashes, and frozen calibration, candidate, validation and comparison identities. Its word digest is `299576ce0a06ba6165b2a7ee1485d7211f9bed94b7e5936ce7ec51c5b4f30c42`. Reproducible tooling and evidence handling are described in `guide/hoa-mean-blackbox.md`.

Research: `scripts/generate_hoa_spatial_means_measured.py --check` verifies the canonical measurement, its format copy and provenance offline. Supplying the registered frozen candidate, validation and comparison files reproduces the measurement. The Rust build reads the canonical source directly, verifies its pinned file and word hashes, and requires the copy in `data/hoa-spatial-controls-format-v1.json` to agree. Missing measured data fails the build. The format file preserves its original observation record for the remaining subband tables. All mean values and the published format semantic digest remain unchanged.

Raw PCM, local evidence paths and continuous estimates stay outside the production source. This source replacement does not change the project's licensing declarations.

## Higher-order decoding and unresolved original zero signs

Orders 4–9 load registered independent measurement sources. Nine original signed-zero bits in seven matrices remain ambiguous, with `{+0, -0}` candidates retained in the source records. The separate zero-equivalence qualification binds each coordinate to the reviewed decoder function. Canonical positive zeros preserve the current finite-input decoding behavior and descriptor history. Strict original sign recovery remains open.


## Measured ambient row orders, HOA bounds and spatial partitions

`data/hoa-ambient-row-orders-measured-v1.json` supplies three independently measured Sylvester-Hadamard row permutations. Discovery used identity carriers from the public AudioConverter interface. The frozen candidate passed 48 basis-word checks and 36 independent mixed-carrier checks, followed by an exact comparison of all 48 integer signs. Signed-zero PCM differences have a separate consumer-bound qualification with their coordinates retained in the private validation evidence. The generator derives every nonzero matrix value from the exact rational magnitude one half. The build validates both the complete measured source and the reviewed zero consumers.

`data/hoa-profile-levels-measured-v1.json` supplies HOA ASC type-2 profile bounds. Public property observations enumerated all 64 profile IDs and 16 levels, then every output cardinality from 1 through 255 for the supported combinations. Independent validation comprises 1,052 property observations using another layout family and 12 complete scalar native decodes. The standalone profile file and the type-2 entries in shared configuration are checked copies. The build emits type-2 limits from the canonical measurements.

`data/shared-channel-profile-levels-measured-v1.json` supplies four type-0 bounds for shared profiles 0 and 5. The independent public AudioCodec campaign enumerated 1,024 profile/level combinations and measured all 255 channel counts for each of the four qualified combinations. The frozen bounds passed 1,020 observations with paired transport elements and a second layout family, followed by 12 complete AudioConverter decodes exercising the last channel and overlap. Loaded component metadata was checked for every property observation. An isolated comparison then matched all four bounds. The build reads these canonical measurements and checks the existing shared-format copy.

`data/shared-layout-tags-measured-v1.json` supplies layout allowlists for profiles 0, 5 and 31, containing 40, 40 and 35 tags. Public AudioCodec properties supplied the 40-tag domain. Cookie observations covered all 16 levels for each profile and every public tag at the 11 accepted levels. After freezing the candidate, 488 property checks used paired transport elements, 44.1 kHz and a different cardinality for variable layouts. These checks comprise 477 distinct cookie observations. Another 22 complete AudioConverter decodes verified channel placement and overlap across all accepted levels. The 967 distinct property receipts, including the two domain queries, were reread and checked against the shared evidence ledger before isolated comparison. All 115 allowlist entries matched the compatibility copy in public-property order. The build reads the canonical measured allowlists.

`data/shared-profile31-layout-levels-measured-v1.json` supplies the per-level fixed-layout domains from that frozen campaign. All 585 profile-31 property receipts were reread and verified. The generator derives effective cardinalities of 2, 6, 8, 12, 24, 24 and 24 within those domains, and the build checks the derived runtime copy against the canonical measurement. Complete admission equivalence was verified before archiving the legacy file. Parsed type-0 tags derive their cardinality from the transport count, and public configuration construction preserves this relation. The measurement retains the unknown status of the original internal bounds.

`data/shared-type1-profile-levels-measured-v1.json` supplies independently measured transport-channel bounds of 1024 and 7/32/128 for profiles 0 and 5. Discovery comprises 2,237 distinct public AudioCodec observations. After freezing, 1,243 property checks and 16 boundary checks used 44.1 kHz and paired transport elements. Twelve boundary cases produced complete two-frame PCM, including 1,024 channels, with isolated last-channel signal and overlap. All 3,492 receipts and PCM were reread before isolated comparison matched the four compatibility values. Qualification covers the frozen SQ transport configuration. Complete type-1 syntax remains open. The runtime static table includes the parser's supported ASC types 0 and 2.

`data/hoa-spatial-partitions-measured-v1.json` supplies 96 integer grids for methods 0–2, subband counts 1–16 and both rounding modes. Discovery measured complete spectral membership in 192 acquisitions. Another 576 observations verified independent amplitudes, signed carriers, another HOA order, 44.1/48 kHz, long and short windows, and dynamic selection contexts. The maximum membership error was below one millionth against a binary marker separation of one. Every compared integer endpoint matched exactly, including the method-0 nine-band first endpoint. Unrounded short-window coordinates retain their 1024-position frequency-major representation.

Spatial generators read the qualified grid source directly. The unused historical perceptual-anchor field and native-address comparison branches are retained in the private archive. Source, producer and evidence hashes are pinned in each canonical measurement file. These measurements preserve the previous numerical matrices and partitions. Their new provenance and dependency identities are reflected in regression metadata.

## Shared DRC wire mappings

`data/hoa-shared-drc-codebooks-measured-v1.json` supplies 25 normal gain-delta mappings, 49 clipping mappings and 15 slope-index mappings. The licensed UniDRC prior comes from libxaac commit `2fbadd57bca46693b7a077bcf1513131106d960c`, including `decoder/drc_src/impd_drc_rom.c`. Copyright 2018 The Android Open Source Project. The Apache-2.0 license is preserved in `LICENSES/libxaac-LICENSE.txt`. Source file hashes and the upstream commit are recorded in the measurement. [libxaac source](https://github.com/ittiam-systems/libxaac/tree/2fbadd57bca46693b7a077bcf1513131106d960c)

Explicit public decoder properties made the controlled gains observable on macOS 27.2 / 26B123. The frozen candidate follows 94 discovery observations. Independent validation comprises 134 observations with 129 mapping checks and 91 boundary observations with 89 checks. It covers different amplitudes, carrier bands, normal profiles 0/1, clipping profile 2, cubic slope indices, subsequent codewords and nonterminal nodes across frames. Every observation records the loaded component UUID and property readbacks. A subsequent isolated comparison matched all 89 integer mappings.

Gain labels are uniquely identified integers. Slope labels use the licensed ordered derivative semantics and independently observed cubic gain curves. The measurement records this scope and its classification tolerances. The build reads the qualified measurement directly, checks the compatibility copy and its format digest, and generates the normal gain codewords alongside the clipping and slope maps. Raw synthetic PCM and producer snapshots remain in the private research evidence.
