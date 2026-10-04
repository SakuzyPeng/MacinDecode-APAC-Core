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
