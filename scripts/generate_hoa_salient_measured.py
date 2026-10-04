#!/usr/bin/env python3
"""Generate the order-3 q6 mode-1 book from its frozen black-box measurement.

--candidate accepts the original hash-pinned local candidate and exports only
format values and public provenance. Without it, the committed measurement is
the source. --check verifies both the source and its packed compatibility copy;
--write regenerates that copy and the per-codebook provenance.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from hoa_packed_tables import pack_codebook
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

MEASURED_FILE = 'hoa-salient-order3-q6-mode1-measured-v1.json'
PROFILE = 'apac-hoa-salient-measured-v1'
CANDIDATE_SHA256 = '85abb44d580e5002cb56fca1eff950d554025b444759d485b243ef70299b9622'
BOOK_SHA256 = '296d730714d97de653c45cc487fa4fa94aebce9a49559da78e81215591e600ee'
SOURCE = dict(
    method='public AudioConverter PCM black-box reconstruction',
    experiment='hoa-blackbox-order3-q6-mode1-v1',
    code_commit='880640456ad3119f098cec1a08592699fcd74be6',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    candidate_sha256=CANDIDATE_SHA256,
    validation_sha256='c388ca5819738a8eced644f619f60949dc7b1a47b8e526a53acd317855df6a5a',
    candidate_frozen_before_comparison=True,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measured_book(value):
    require(value.get('schema_version') == 1 and value.get('profile') == PROFILE,
            'incompatible measurement schema')
    require(tuple(value.get(k) for k in ('order', 'quantization_bits', 'mode', 'book')) == (3, 6, 1, 0),
            'measurement scope differs')
    require(value.get('source') == SOURCE, 'measurement source differs')
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
    require(digest == value.get('book_sha256') == BOOK_SHA256, 'measured codebook digest differs')
    return book


def from_candidate(raw):
    require(hashlib.sha256(raw).hexdigest() == CANDIDATE_SHA256, 'unverified frozen candidate')
    candidate = json.loads(raw)
    require(candidate['old_dictionary_consulted'] is False, 'candidate consulted the old dictionary')
    result = dict(schema_version=1, profile=PROFILE, order=candidate['order'],
                  quantization_bits=candidate['quantization_bits'], mode=candidate['mode'], book=candidate['book'],
                  source=copy.deepcopy(SOURCE), book_sha256=BOOK_SHA256,
                  entries=[{key: entry[key] for key in ('symbol', 'codeword', 'bit_length')}
                           for entry in candidate['entries']])
    measured_book(result)
    return result


def load_measurement():
    value = json.loads((DATA / MEASURED_FILE).read_text())
    measured_book(value)
    return value


def replace_book(value, measurement):
    require((value['order'], value['quantization_bits']) == (3, 6), 'replacement scope differs')
    result = copy.deepcopy(value)
    result['modes'][1]['codebooks'][0] = measured_book(measurement)
    original = result['source'].get('original_observation', result['source'])
    result['source'] = dict(
        method='mixed sources with per-codebook replacements',
        original_observation=original,
        remaining_tables='original_observation',
        codebook_replacements=[dict(
            mode=1, book=0, source_file=MEASURED_FILE,
            source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(),
            method=SOURCE['method'],
        )],
    )
    # The independently recovered words exactly preserve the published identity.
    check_digest(result)
    return result


def regenerate(stored, shared, measurement):
    require((stored['schema_version'], stored['order'], stored['quantization_bits']) == (3, 3, 6),
            'replacement storage scope differs')
    stored = copy.deepcopy(stored)
    # Replace before expanding: regeneration must not depend on the old packed
    # copy being valid. The remaining tables still pass their original digest.
    stored['modes'][1]['codebooks'][0] = pack_codebook(measured_book(measurement), 6)
    return split_format(replace_book(expand_format(stored, shared), measurement))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, help='original frozen local candidate')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    measurement = from_candidate(args.candidate.read_bytes()) if args.candidate else load_measurement()
    path = DATA / format_name(3, 6)
    stored, shared = regenerate(json.loads(path.read_text()),
                                json.loads((DATA / shared_name(3)).read_text()), measurement)
    require(json_bytes(shared) == (DATA / shared_name(3)).read_bytes(), 'shared tables would change')
    outputs = {DATA / MEASURED_FILE: json_bytes(measurement), path: json_bytes(stored)}
    for destination, raw in outputs.items():
        if args.check:
            require(destination.read_bytes() == raw, 'generated measurement or packed copy differs: ' + destination.name)
        else:
            destination.write_bytes(raw)
    print(json.dumps(dict(symbols=64, book_sha256=BOOK_SHA256, tables_sha256=stored['tables_sha256'])))


if __name__ == '__main__':
    main()
