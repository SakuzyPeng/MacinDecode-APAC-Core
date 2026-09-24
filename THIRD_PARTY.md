# Third-party format tables

`data/sq-codebooks.json` contains AAC Huffman codewords, code lengths and frequency-band boundaries transcribed from FFmpeg `libavcodec/aactab.c`, commit `a9cbcc2bbb9f64256834af2a22e31dd465a81f9d`.

Copyright (c) 2005–2006 Oded Shimon; Copyright (c) 2006–2007 Maxim Gavrilov.
The source is licensed under LGPL 2.1 or later; the license is included in `COPYING.LGPLv2.1`. The JSON records its source URL and SHA-256. Changes are limited to selecting these format constants and converting their representation to JSON. The Rust trie, APAC parser and vector generator are newly written; Apple lookup nodes and disassembly are not included.


## Historical reference sine-window observations

`data/sq-sine-windows.json` records Float32 numerical sine-window coefficients observed in the verified macOS 27.0 / 26A428 AudioCodecs reference. Its component SHA-256 and numerical-profile ID are included. These observations are separate from the FFmpeg-derived Huffman tables above.

The file contains rising-half window values only, with no executable bytes, disassembly, decoder control flow, source-media data or local paths. `scripts/verify_sine_windows.py` verifies every coefficient against a read-only snapshot of the matching reference build. This is a versioned numerical compatibility profile, not a claim that APAC universally requires that implementation's rounding.

The historical window data is used only by diagnostic verification. It is not included in the default SQ synthesis path.

## Independent mathematical profile

`data/sq-math-v1.json` is independently generated from mathematical formulas by `scripts/generate_sq_math.py` and `scripts/sq_math.py`, using Python's standard-library Decimal arithmetic at 100 and 200 decimal digits. It records exact IEEE bit patterns for inverse quantization, gains, sine windows, modulation and FFT rotations. These constants are not copied from an Apple component or the historical window observations. The generator and the independent direct-sum synthesis oracle are included for offline reproduction.
