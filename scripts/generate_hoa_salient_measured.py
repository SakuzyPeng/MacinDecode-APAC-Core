#!/usr/bin/env python3
"""Generate the measured order-3 q6 Huffman books from frozen candidates.

--candidate accepts a hash-pinned local codebook candidate and exports only
format values and public provenance. It may be repeated for both books. Other
books use their committed measurements. --check verifies the measured sources
and their packed copies; --write regenerates both copies and their provenance.
Mode-4 matrices are never imported or replaced by this generator.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from hoa_packed_tables import pack_codebook
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

MEASURED_FILES = {
    1: 'hoa-salient-order3-q6-mode1-measured-v1.json',
    4: 'hoa-salient-order3-q6-mode4-cluster0-measured-v1.json',
}
PROFILE = 'apac-hoa-salient-measured-v1'
CANDIDATE_SHA256 = {
    1: '85abb44d580e5002cb56fca1eff950d554025b444759d485b243ef70299b9622',
    4: '12fc04895e48037d2d5919caf0de31ee61299f75943733fa71b7fbd2db455293',
}
BOOK_SHA256 = {
    1: '296d730714d97de653c45cc487fa4fa94aebce9a49559da78e81215591e600ee',
    4: '08fa83f508549126a68be673c7f6065c15e8ea5c2279385e00ac6f180c943322',
}
SOURCES = {1: dict(
    method='public AudioConverter PCM black-box reconstruction',
    experiment='hoa-blackbox-order3-q6-mode1-v1',
    code_commit='880640456ad3119f098cec1a08592699fcd74be6',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    candidate_sha256=CANDIDATE_SHA256[1],
    validation_sha256='c388ca5819738a8eced644f619f60949dc7b1a47b8e526a53acd317855df6a5a',
    candidate_frozen_before_comparison=True,
)}
SOURCES[4] = dict(
    SOURCES[1],
    experiment='hoa-blackbox-order3-q6-mode4-cluster0-v1',
    code_commit='f4110ca0eb9383c534479026f8f2b6f9656ca646',
    candidate_sha256=CANDIDATE_SHA256[4],
    validation_sha256='861d3f9147a60e6a3cfff81627221c154f852444dd46716c921de7056cec66df',
    comparison_sha256='efba38ce4ce7737cc4ef4b292ab3e74bb063240ff6fd6ef1ca2207c84c93d7e8',
    validation_scope='Huffman codewords and lengths',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measured_book(value):
    require(value.get('schema_version') == 1 and value.get('profile') == PROFILE,
            'incompatible measurement schema')
    mode = value.get('mode')
    require(type(mode) is int and mode in MEASURED_FILES
            and tuple(value.get(k) for k in ('order', 'quantization_bits', 'book')) == (3, 6, 0),
            'measurement scope differs')
    require(value.get('source') == SOURCES[mode], 'measurement source differs')
    entries = value.get('entries')
    require(isinstance(entries, list) and len(entries) == 64, 'measurement must contain 64 symbols')
    book = []
    for symbol, entry in enumerate(entries):
        require(type(entry.get('symbol')) is int and entry['symbol'] == symbol,
                'measurement symbols must be ordered and unique')
        word, length = entry.get('codeword'), entry.get('bit_length')
        require(isinstance(word, str) and type(length) is int and 1 <= length <= 32
                and len(word) == length and set(word) <= {'0', '1'}, 'invalid measured codeword')
        book.append([length, int(word, 2)])
    pack_codebook(book, 6)  # Requires a complete prefix tree with 64 distinct leaves.
    digest = hashlib.sha256(json.dumps(book, separators=(',', ':')).encode()).hexdigest()
    require(digest == value.get('book_sha256') == BOOK_SHA256[mode], 'measured codebook digest differs')
    return book


def from_candidate(raw):
    digest = hashlib.sha256(raw).hexdigest()
    mode = next((mode for mode, expected in CANDIDATE_SHA256.items() if digest == expected), None)
    require(mode is not None, 'unverified frozen candidate')
    candidate = json.loads(raw)
    require(candidate['old_dictionary_consulted'] is False, 'candidate consulted the old dictionary')
    require(candidate['mode'] == mode, 'candidate mode differs')
    result = dict(schema_version=1, profile=PROFILE, order=candidate['order'],
                  quantization_bits=candidate['quantization_bits'], mode=mode,
                  book=candidate['cluster'] if mode == 4 else candidate['book'],
                  source=copy.deepcopy(SOURCES[mode]), book_sha256=BOOK_SHA256[mode],
                  entries=[{key: entry[key] for key in ('symbol', 'codeword', 'bit_length')}
                           for entry in candidate['entries']])
    measured_book(result)
    return result


def load_measurement(mode=1):
    value = json.loads((DATA / MEASURED_FILES[mode]).read_text())
    measured_book(value)
    require(value['mode'] == mode, 'measurement file scope differs')
    return value


def load_measurements():
    return [load_measurement(mode) for mode in MEASURED_FILES]


def replace_book(value, measurement):
    require((value['order'], value['quantization_bits']) == (3, 6), 'replacement scope differs')
    result = copy.deepcopy(value)
    book = measured_book(measurement)
    mode = measurement['mode']
    result['modes'][mode]['codebooks'][0] = book
    original = result['source'].get('original_observation', result['source'])
    replacements = [entry for entry in result['source'].get('codebook_replacements', [])
                    if (entry['mode'], entry['book']) != (mode, 0)]
    replacements.append(dict(
        mode=mode, book=0, source_file=MEASURED_FILES[mode],
        source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(),
        method=SOURCES[mode]['method'],
    ))
    source = result['source'] if 'original_observation' in result['source'] else {}
    source.update(
        method=('mixed sources with per-table replacements' if source.get('matrix_replacements')
                else 'mixed sources with per-codebook replacements'),
        original_observation=original,
        remaining_tables='original_observation',
        codebook_replacements=sorted(replacements, key=lambda entry: (entry['mode'], entry['book'])),
    )
    result['source'] = source
    # The independently recovered words exactly preserve the published identity.
    check_digest(result)
    return result


def regenerate(stored, shared, measurements):
    require((stored['schema_version'], stored['order'], stored['quantization_bits']) == (3, 3, 6),
            'replacement storage scope differs')
    stored = copy.deepcopy(stored)
    if isinstance(measurements, dict):
        measurements = [measurements]
    # Replace before expanding: regeneration must not depend on the old packed
    # copy being valid. The remaining tables still pass their original digest.
    for measurement in measurements:
        book = measured_book(measurement)
        stored['modes'][measurement['mode']]['codebooks'][0] = pack_codebook(book, 6)
    expanded = expand_format(stored, shared)
    for measurement in measurements:
        expanded = replace_book(expanded, measurement)
    return split_format(expanded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, action='append', default=[],
                        help='original frozen codebook candidate; repeat for both measured books')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    supplied = {}
    for candidate in args.candidate:
        measurement = from_candidate(candidate.read_bytes())
        require(measurement['mode'] not in supplied, 'duplicate candidate mode')
        supplied[measurement['mode']] = measurement
    measurements = [supplied[mode] if mode in supplied else load_measurement(mode) for mode in MEASURED_FILES]
    path = DATA / format_name(3, 6)
    stored, shared = regenerate(json.loads(path.read_text()),
                                json.loads((DATA / shared_name(3)).read_text()), measurements)
    require(json_bytes(shared) == (DATA / shared_name(3)).read_bytes(), 'shared tables would change')
    outputs = {DATA / MEASURED_FILES[value['mode']]: json_bytes(value) for value in measurements}
    outputs[path] = json_bytes(stored)
    for destination, raw in outputs.items():
        if args.check:
            require(destination.read_bytes() == raw, 'generated measurement or packed copy differs: ' + destination.name)
        else:
            destination.write_bytes(raw)
    print(json.dumps(dict(books=len(measurements), symbols=64*len(measurements),
                          book_sha256=BOOK_SHA256, tables_sha256=stored['tables_sha256'])))


if __name__ == '__main__':
    main()
