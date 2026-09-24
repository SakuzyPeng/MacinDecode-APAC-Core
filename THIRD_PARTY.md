# Third-party format tables

`data/sq-codebooks.json` contains AAC Huffman codewords, code lengths and frequency-band boundaries transcribed from FFmpeg `libavcodec/aactab.c`, commit `a9cbcc2bbb9f64256834af2a22e31dd465a81f9d`.

Copyright (c) 2005–2006 Oded Shimon; Copyright (c) 2006–2007 Maxim Gavrilov.
The source is licensed under LGPL 2.1 or later; the license is included in `COPYING.LGPLv2.1`. The JSON records its source URL and SHA-256. Changes are limited to selecting these format constants and converting their representation to JSON. The Rust trie, APAC parser and vector generator are newly written; Apple lookup nodes and disassembly are not included.
