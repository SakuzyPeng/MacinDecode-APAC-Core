#!/usr/bin/env python3
"""AAC spectral/scale-factor Huffman books and 48 kHz SFB offsets from vo-aacenc (Apache-2.0).

Regeneration reads `aacenc/src/aac_rom.c` of the pinned vo-aacenc commit (`--source`).
`--check` verifies the committed file on its own: every book is a complete prefix code with the
expected size and the offsets are the strictly increasing 49/14-band 48 kHz tables. With
`--source`, `--check` also requires the committed bytes to equal the regeneration.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

DESTINATION = Path(__file__).resolve().parents[1] / 'data/sq-codebooks.json'
COMMIT = 'a277487e051e92e99a532294eed3c673f4d879f2'
SOURCE = f'https://github.com/mstorsjo/vo-aacenc/blob/{COMMIT}/aacenc/src/aac_rom.c'
SOURCE_SHA256 = 'bebae3b53f4de70055c6328a24e3fc3172f3b15d40eb9e4556d6d8c2d91cb8b6'
COPYRIGHT = 'Copyright 2003-2010, VisualOn, Inc.'
LICENSE = 'Apache-2.0; see LICENSES/Apache-2.0.txt and THIRD_PARTY.md'
CHANGES = ('Selected the spectral (1-11) and scale-factor Huffman codewords and lengths and the '
           '44.1/48 kHz long/short scale-factor band offsets; split the packed book-pair length '
           'tables (high byte: odd book, low byte: even book); flattened in table-index order; '
           'converted to JSON.')
# Book sizes: 4-tuples over 3 values, pairs over 9, 8, 13 and 17 values.
SIZES = [81, 81, 81, 81, 81, 81, 64, 64, 169, 169, 289]
SCALEFACTOR_SIZE = 121
# sfBandTabLongOffset / sfBandTabShortOffset entries for 48 kHz (sampling index 3).
LONG = slice(90, 140)
SHORT = slice(13, 28)


def array(source, name):
    """Integer values of the C array `name`, which must be defined exactly once."""
    matches = list(re.finditer(r'\b' + re.escape(name) + r'\s*(?:\[[^\]=]*\])+\s*=\s*\{', source))
    if len(matches) != 1:
        raise ValueError(f'{name}: expected one definition, found {len(matches)}')
    start = end = matches[0].end()
    depth = 1
    while depth:
        if end == len(source):
            raise ValueError(f'{name}: unterminated initializer')
        depth += {'{': 1, '}': -1}.get(source[end], 0)
        end += 1
    body = re.sub(r'/\*.*?\*/', '', source[start:end - 1], flags=re.S)
    return [int(token, 0) for token in re.findall(r'0[xX][0-9a-fA-F]+|\d+', body)]


def lengths(source, book):
    if book == 11:
        return array(source, 'huff_ltab11')
    first = book - (book + 1) % 2
    shift = 8 if book == first else 0
    return [(value >> shift) & 0xff for value in array(source, f'huff_ltab{first}_{first + 1}')]


def document(source):
    spectral = [dict(codes=array(source, f'huff_ctab{book}'), bits=lengths(source, book)) for book in range(1, 12)]
    return dict(source=SOURCE, source_sha256=SOURCE_SHA256, copyright=COPYRIGHT, license=LICENSE, changes=CHANGES,
                spectral=spectral,
                scalefactor=dict(codes=array(source, 'huff_ctabscf'), bits=array(source, 'huff_ltabscf')),
                long_offsets=array(source, 'sfBandTabLong')[LONG], short_offsets=array(source, 'sfBandTabShort')[SHORT])


def check_book(name, book, size):
    codes, bits = book['codes'], book['bits']
    if len(codes) != size or len(bits) != size:
        raise AssertionError(f'{name}: expected {size} entries')
    if any(not 1 <= length <= 32 or not 0 <= code < 1 << length for code, length in zip(codes, bits)):
        raise AssertionError(f'{name}: codeword outside its length')
    if sum(Fraction(1, 1 << length) for length in bits) != 1:
        raise AssertionError(f'{name}: not a complete code')
    words = sorted(format(code, f'0{length}b') for code, length in zip(codes, bits))
    if any(b.startswith(a) for a, b in zip(words, words[1:])):
        raise AssertionError(f'{name}: not prefix-free')


def check(tables):
    if len(tables['spectral']) != len(SIZES):
        raise AssertionError('expected 11 spectral books')
    for book, (value, size) in enumerate(zip(tables['spectral'], SIZES), 1):
        check_book(f'spectral book {book}', value, size)
    check_book('scale-factor book', tables['scalefactor'], SCALEFACTOR_SIZE)
    for name, count, end in (('long_offsets', 50, 1024), ('short_offsets', 15, 128)):
        offsets = tables[name]
        if len(offsets) != count or offsets[0] != 0 or offsets[-1] != end or offsets != sorted(set(offsets)):
            raise AssertionError(f'{name}: not a strictly increasing 0..{end} table of {count} entries')


def encode(tables):
    return (json.dumps(tables, indent=2) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help=f'vo-aacenc aacenc/src/aac_rom.c at commit {COMMIT}')
    parser.add_argument('--output', type=Path, default=DESTINATION)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.source is None and not args.check:
        parser.error('generation requires --source')
    expected = None
    if args.source is not None:
        raw = args.source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
            raise AssertionError(f'{args.source} is not aac_rom.c at vo-aacenc {COMMIT}')
        expected = document(raw.decode())
        check(expected)
    if args.check:
        data = args.output.read_bytes()
        check(json.loads(data))
        if expected is not None and data != encode(expected):
            raise AssertionError('SQ codebooks differ from the vo-aacenc regeneration')
    else:
        with args.output.open('xb') as output:
            output.write(encode(expected))
    print(json.dumps(dict(source=SOURCE, source_sha256=SOURCE_SHA256, regenerated=expected is not None)))


if __name__ == '__main__':
    main()
