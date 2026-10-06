#!/usr/bin/env python3
"""Generate registered measured order-1–10 q6–q9 Huffman books from frozen candidates.

--candidate accepts a hash-pinned local codebook candidate and exports only
format values and public provenance. It may be repeated for any measured books. Other
books use their committed measurements. --check verifies the measured sources
and their packed copies; --write regenerates the copies and their provenance.
Mode-4 matrices are never imported or replaced by this generator.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from hoa_packed_tables import pack_codebook
from hoa_measured_high_order_sources import HIGHER_OUTPUTS, higher_source
from hoa_measured_batch_sources import (
    OUTPUTS, MODE23_OUTPUTS, Q7_OUTPUTS, Q89_OUTPUTS, LOWER_OUTPUTS,
    source as batch_source, mode23_source, q7_source, q89_source, lower_source,
)
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

MEASURED_FILES = {
    (6, 1, 0): 'hoa-salient-order3-q6-mode1-measured-v1.json',
    (6, 4, 0): 'hoa-salient-order3-q6-mode4-cluster0-measured-v1.json',
}
PROFILE = 'apac-hoa-salient-measured-v1'
CANDIDATE_SHA256 = {
    (6, 1, 0): '85abb44d580e5002cb56fca1eff950d554025b444759d485b243ef70299b9622',
    (6, 4, 0): '12fc04895e48037d2d5919caf0de31ee61299f75943733fa71b7fbd2db455293',
}
BOOK_SHA256 = {
    (6, 1, 0): '296d730714d97de653c45cc487fa4fa94aebce9a49559da78e81215591e600ee',
    (6, 4, 0): '08fa83f508549126a68be673c7f6065c15e8ea5c2279385e00ac6f180c943322',
}
SOURCES = {(6, 1, 0): dict(
    method='public AudioConverter PCM black-box reconstruction',
    experiment='hoa-blackbox-order3-q6-mode1-v1',
    code_commit='880640456ad3119f098cec1a08592699fcd74be6',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    candidate_sha256=CANDIDATE_SHA256[6, 1, 0],
    validation_sha256='c388ca5819738a8eced644f619f60949dc7b1a47b8e526a53acd317855df6a5a',
    candidate_frozen_before_comparison=True,
)}
SOURCES[6, 4, 0] = dict(
    SOURCES[6, 1, 0],
    experiment='hoa-blackbox-order3-q6-mode4-cluster0-v1',
    code_commit='f4110ca0eb9383c534479026f8f2b6f9656ca646',
    candidate_sha256=CANDIDATE_SHA256[6, 4, 0],
    validation_sha256='861d3f9147a60e6a3cfff81627221c154f852444dd46716c921de7056cec66df',
    comparison_sha256='efba38ce4ce7737cc4ef4b292ab3e74bb063240ff6fd6ef1ca2207c84c93d7e8',
    validation_scope='Huffman codewords and lengths',
)
for cluster, record in OUTPUTS.items():
    key = (6, 4, cluster)
    MEASURED_FILES[key] = f'hoa-salient-order3-q6-mode4-cluster{cluster}-measured-v1.json'
    CANDIDATE_SHA256[key] = record['codebook_candidate']
    BOOK_SHA256[key] = record['codebook_values']
    SOURCES[key] = batch_source(cluster, 'codebook')
for (mode, book), record in MODE23_OUTPUTS.items():
    key = (6, mode, book)
    suffix = f'-book{book}' if mode == 2 else ''
    MEASURED_FILES[key] = f'hoa-salient-order3-q6-mode{mode}{suffix}-measured-v1.json'
    CANDIDATE_SHA256[key] = record['candidate']
    BOOK_SHA256[key] = record['book_values']
    SOURCES[key] = mode23_source((mode, book))
for (mode, book), record in Q7_OUTPUTS.items():
    key = (7, mode, book)
    suffix = f'-book{book}' if mode == 2 else f'-cluster{book}' if mode == 4 else ''
    MEASURED_FILES[key] = f'hoa-salient-order3-q7-mode{mode}{suffix}-measured-v1.json'
    CANDIDATE_SHA256[key] = record['candidate']
    BOOK_SHA256[key] = record['book_values']
    SOURCES[key] = q7_source((mode, book))
for (precision, mode, book), record in Q89_OUTPUTS.items():
    key = (precision, mode, book)
    suffix = f'-book{book}' if mode == 2 else f'-cluster{book}' if mode == 4 else ''
    MEASURED_FILES[key] = f'hoa-salient-order3-q{precision}-mode{mode}{suffix}-measured-v1.json'
    CANDIDATE_SHA256[key] = record['candidate']
    BOOK_SHA256[key] = record['book_values']
    SOURCES[key] = q89_source(key)


# Keep historical source records unchanged while keying all registered orders.
MEASURED_FILES = {(3, *key): value for key, value in MEASURED_FILES.items()}
CANDIDATE_SHA256 = {(3, *key): value for key, value in CANDIDATE_SHA256.items()}
BOOK_SHA256 = {(3, *key): value for key, value in BOOK_SHA256.items()}
SOURCES = {(3, *key): value for key, value in SOURCES.items()}
GROUP_SHA256 = {(3, 6, mode, book): record['group_values'] for (mode, book), record in MODE23_OUTPUTS.items()}
for key, record in {**LOWER_OUTPUTS, **HIGHER_OUTPUTS}.items():
    order, precision, mode, book = key
    suffix = f'-book{book}' if mode == 2 else f'-cluster{book}' if mode == 4 else ''
    MEASURED_FILES[key] = f'hoa-salient-order{order}-q{precision}-mode{mode}{suffix}-measured-v1.json'
    CANDIDATE_SHA256[key] = record['candidate']
    BOOK_SHA256[key] = record['book_values']
    SOURCES[key] = lower_source(key) if order <= 2 else higher_source(key)
    if precision == 6 and mode in (2,3):
        GROUP_SHA256[key] = record['group_values']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measured_book(value):
    require(value.get('schema_version') == 1 and value.get('profile') == PROFILE,
            'incompatible measurement schema')
    precision, mode, index = value.get('quantization_bits'), value.get('mode'), value.get('book')
    order = value.get('order')
    key = (order, precision, mode, index)
    require(type(order) is int and type(precision) is int and type(mode) is int and type(index) is int and key in MEASURED_FILES
            and order in range(1, 11),
            'measurement scope differs')
    require(value.get('source') == SOURCES[key], 'measurement source differs')
    entries = value.get('entries')
    require(isinstance(entries, list) and len(entries) == 1 << precision,
            f'measurement must contain {1 << precision} symbols')
    book = []
    for symbol, entry in enumerate(entries):
        require(type(entry.get('symbol')) is int and entry['symbol'] == symbol,
                'measurement symbols must be ordered and unique')
        word, length = entry.get('codeword'), entry.get('bit_length')
        require(isinstance(word, str) and type(length) is int and 1 <= length <= 32
                and len(word) == length and set(word) <= {'0', '1'}, 'invalid measured codeword')
        book.append([length, int(word, 2)])
    pack_codebook(book, precision)  # Requires a complete tree and distinct leaves.
    digest = hashlib.sha256(json.dumps(book, separators=(',', ':')).encode()).hexdigest()
    require(digest == value.get('book_sha256') == BOOK_SHA256[key], 'measured codebook digest differs')
    if key in GROUP_SHA256:
        group = value.get('coefficient_group')
        require(isinstance(group, list) and group and all(type(i) is int and 0 <= i < (order+1)**2 for i in group)
                and len(set(group)) == len(group), 'invalid measured coefficient group')
        group_digest = hashlib.sha256(json.dumps(group, separators=(',', ':')).encode()).hexdigest()
        require(group_digest == value.get('group_sha256') == GROUP_SHA256[key],
                'measured coefficient group digest differs')
    if precision > 6:
        require('coefficient_group' not in value and 'group_sha256' not in value,
                'wider sources must not replace the measured six-bit groups')
    return book


def measured_group(value):
    measured_book(value)
    require((value['order'], value['quantization_bits'], value['mode'], value['book']) in GROUP_SHA256,
            'measurement has no recovered group')
    return value['coefficient_group']


def from_candidate(raw):
    digest = hashlib.sha256(raw).hexdigest()
    key = next((key for key, expected in CANDIDATE_SHA256.items() if digest == expected), None)
    require(key is not None, 'unverified frozen candidate')
    order, precision, mode, index = key
    candidate = json.loads(raw)
    require(candidate['old_dictionary_consulted'] is False, 'candidate consulted the old dictionary')
    require(candidate['order'] == order and candidate['mode'] == mode and candidate['quantization_bits'] == precision, 'candidate scope differs')
    candidate_index = candidate.get('book', candidate.get('cluster'))
    require(candidate_index == index, 'candidate book differs')
    if order == 3 and precision == 6 and mode == 4 and index in OUTPUTS:
        require(candidate['profile'] == 'hoa-blackbox-codebook-v1'
                and candidate['policy_sha256'] == SOURCES[key]['policy_sha256'], 'candidate policy differs')
    if key in GROUP_SHA256:
        require(candidate['profile'] == 'hoa-blackbox-codebook-v1'
                and candidate['policy_sha256'] == SOURCES[key]['policy_sha256']
                and candidate['layout_sha256'] == SOURCES[key]['layout_sha256'], 'candidate policy or layout differs')
    if order != 3:
        require(candidate['profile'] == 'hoa-blackbox-codebook-v1'
                and candidate['policy_sha256'] == SOURCES[key]['policy_sha256'], 'candidate policy differs')
    if precision > 6:
        require(candidate['profile'] == 'hoa-blackbox-codebook-v1'
                and candidate['policy_sha256'] == SOURCES[key]['policy_sha256']
                and candidate.get('prior_sha256') == SOURCES[key].get('prior_sha256')
                and candidate.get('matrix_reused', False) == SOURCES[key]['matrix_reused']
                and candidate.get('group_reused', False) == SOURCES[key]['group_reused'], 'candidate prior or policy differs')
    result = dict(schema_version=1, profile=PROFILE, order=candidate['order'],
                  quantization_bits=candidate['quantization_bits'], mode=mode,
                  book=index,
                  source=copy.deepcopy(SOURCES[key]), book_sha256=BOOK_SHA256[key],
                  entries=[{key: entry[key] for key in ('symbol', 'codeword', 'bit_length')}
                           for entry in candidate['entries']])
    if key in GROUP_SHA256:
        result.update(coefficient_group=candidate['coefficient_group'], group_sha256=GROUP_SHA256[key])
    measured_book(result)
    return result


def load_measurement(mode=1, book=0, quantization_bits=6, order=3):
    value = json.loads((DATA / MEASURED_FILES[order, quantization_bits, mode, book]).read_text())
    measured_book(value)
    require((value['order'], value['quantization_bits'], value['mode'], value['book']) == (order, quantization_bits, mode, book),
            'measurement file scope differs')
    return value


def load_measurements(quantization_bits=6, order=3):
    return [load_measurement(mode, book, precision, selected_order)
            for selected_order, precision, mode, book in MEASURED_FILES
            if (selected_order, precision) == (order, quantization_bits)]


def replace_book(value, measurement):
    require((value['order'], value['quantization_bits']) == (measurement['order'], measurement['quantization_bits']), 'replacement scope differs')
    result = copy.deepcopy(value)
    book = measured_book(measurement)
    mode, index = measurement['mode'], measurement['book']
    key = (measurement['order'], measurement['quantization_bits'], mode, index)
    result['modes'][mode]['codebooks'][index] = book
    original = result['source'].get('original_observation', result['source'])
    replacements = [entry for entry in result['source'].get('codebook_replacements', [])
                    if (entry['mode'], entry['book']) != (mode, index)]
    replacements.append(dict(
        mode=mode, book=index, source_file=MEASURED_FILES[key],
        source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(),
        method=SOURCES[key]['method'],
    ))
    source = result['source'] if 'original_observation' in result['source'] else {}
    source.update(
        method=('mixed sources with per-table replacements' if source.get('matrix_replacements') or source.get('group_replacements')
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
    require(stored['schema_version'] == 3 and stored['order'] in range(1, 11) and stored['quantization_bits'] in (6, 7, 8, 9),
            'replacement storage scope differs')
    stored = copy.deepcopy(stored)
    if isinstance(measurements, dict):
        measurements = [measurements]
    # Replace before expanding: regeneration must not depend on the old packed
    # copy being valid. The remaining tables still pass their original digest.
    for measurement in measurements:
        book = measured_book(measurement)
        require((measurement['order'], measurement['quantization_bits']) == (stored['order'], stored['quantization_bits']), 'replacement order or precision scope differs')
        stored['modes'][measurement['mode']]['codebooks'][measurement['book']] = pack_codebook(book, stored['quantization_bits'])
    expanded = expand_format(stored, shared)
    for measurement in measurements:
        expanded = replace_book(expanded, measurement)
    return split_format(expanded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', type=int, choices=range(1, 11), help='restrict to one order; default all registered orders')
    parser.add_argument('--candidate', type=Path, action='append', default=[],
                        help='verified frozen codebook candidate; repeat for distinct measured books')
    parser.add_argument('--quantization-bits', type=int, choices=(6, 7, 8, 9),
                        help='restrict to one width; by default check/write all measured widths')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    supplied = {}
    for candidate in args.candidate:
        measurement = from_candidate(candidate.read_bytes())
        key = (measurement['order'], measurement['quantization_bits'], measurement['mode'], measurement['book'])
        require(args.quantization_bits is None or key[1] == args.quantization_bits, 'candidate precision was not selected')
        require(args.order is None or key[0] == args.order, 'candidate order was not selected')
        require(key not in supplied, 'duplicate candidate book')
        supplied[key] = measurement
    keys = [key for key in MEASURED_FILES if (args.quantization_bits is None or key[1] == args.quantization_bits)
            and (args.order is None or key[0] == args.order)]
    require(keys, 'no registered measurements for requested scope')
    measurements = [supplied[key] if key in supplied else load_measurement(key[2], key[3], key[1], key[0]) for key in keys]
    outputs = {DATA / MEASURED_FILES[v['order'], v['quantization_bits'], v['mode'], v['book']]: json_bytes(v) for v in measurements}
    table_hashes = {}
    for order, precision in sorted({key[:2] for key in keys}):
        path = DATA / format_name(order, precision)
        selected = [v for v in measurements if (v['order'], v['quantization_bits']) == (order, precision)]
        stored, shared = regenerate(json.loads(path.read_text()), json.loads((DATA / shared_name(order)).read_text()), selected)
        require(json_bytes(shared) == (DATA / shared_name(order)).read_bytes(), 'shared tables would change')
        outputs[path] = json_bytes(stored)
        table_hashes[f'{order}:{precision}'] = stored['tables_sha256']
    for destination, raw in outputs.items():
        if args.check:
            require(destination.read_bytes() == raw, 'generated measurement or packed copy differs: ' + destination.name)
        else:
            if not destination.exists() or destination.read_bytes() != raw:
                destination.write_bytes(raw)
    print(json.dumps(dict(books=len(measurements), symbols=sum(1 << v['quantization_bits'] for v in measurements),
                          book_sha256={f'{o}:{p}:{m}:{b}': BOOK_SHA256[o,p,m,b] for o,p,m,b in keys},
                          tables_sha256=table_hashes)))


if __name__ == '__main__':
    main()
