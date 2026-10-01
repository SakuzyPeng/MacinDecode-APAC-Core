"""Lossless storage of HOA dictionaries with per-order shared matrices and groups.

Consumers receive the original expanded schema and semantic table digest.
Repository storage uses schema 3 with packed trees and shared micro21 matrices.
The wire format is unchanged; archived schemas 1 and 2 remain readable.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from hoa_packed_tables import (
    CODEBOOK_ENCODING, MATRIX_ENCODING, pack_codebook, unpack_codebook, pack_matrix, unpack_matrix,
)

DATA = Path(__file__).resolve().parents[1] / 'data'
SHARED_PROFILE = 'apac-hoa-salient-shared-v1'


def format_name(order, precision=6):
    if order not in range(1, 4) or precision not in range(6, 10):
        raise ValueError('dictionary key outside order 1..3 / precision 6..9')
    suffix = f'-q{precision}' if precision != 6 else ''
    stem = '' if (order, precision) == (3, 6) else f'-order{order}{suffix}'
    return f'hoa-salient{stem}-format-v1.json'


def shared_name(order):
    return f'hoa-salient-order{order}-shared-v1.json'


def check_digest(value):
    content = {key: value[key] for key in ('order', 'quantization_bits', 'modes')}
    raw = json.dumps(content, sort_keys=True, separators=(',', ':')).encode()
    if hashlib.sha256(raw).hexdigest() != value['tables_sha256']:
        raise ValueError('HOA dictionary digest differs')


def split_format(value):
    """Return compact dictionary and shared data, without changing any table word."""
    if value['schema_version'] != 1:
        raise ValueError('expected an expanded dictionary')
    check_digest(value)
    shared = dict(schema_version=2, format_profile=SHARED_PROFILE,
                  order=value['order'], modes=[], groups=[], matrices_f32=[])

    def intern(key, block):
        pool = shared[key]
        if block not in pool:
            pool.append(block)
        return pool.index(block)

    modes = []
    for mode in value['modes']:
        shared['modes'].append(dict(
            mode=mode['mode'], signs=mode['signs'],
            group_indices=[intern('groups', group) for group in mode['groups']],
            matrix_indices=[intern('matrices_f32', matrix) for matrix in mode['matrices_f32']]))
        modes.append(dict(mode=mode['mode'], codebooks=[
            pack_codebook(book, value['quantization_bits']) for book in mode['codebooks']]))
    shared['matrices_f32'] = [pack_matrix(matrix) for matrix in shared['matrices_f32']]
    shared['matrix_encoding'] = MATRIX_ENCODING
    stored = dict(value, schema_version=3, shared_file=shared_name(value['order']),
                  modes=modes, codebook_encoding=CODEBOOK_ENCODING)
    return stored, shared


def expand_shared(shared):
    if shared['schema_version'] == 1:
        return shared
    if (shared['schema_version'] != 2 or shared.get('matrix_encoding') != MATRIX_ENCODING
            or shared['order'] not in range(1, 4)):
        raise ValueError('incompatible packed HOA matrices')
    expanded = {key: value for key, value in shared.items() if key != 'matrix_encoding'}
    expanded.update(schema_version=1, matrices_f32=[
        unpack_matrix(matrix, (shared['order'] + 1) ** 4) for matrix in shared['matrices_f32']])
    return expanded


def expand_format(stored, shared):
    """Resolve checked references, preserving the historical schema and digest."""
    packed = stored['schema_version'] == 3
    if (stored['schema_version'] not in (2, 3) or shared['schema_version'] not in (1, 2)
            or (packed and stored.get('codebook_encoding') != CODEBOOK_ENCODING)
            or shared['format_profile'] != SHARED_PROFILE
            or shared['order'] != stored['order']
            or stored['shared_file'] != shared_name(stored['order'])
            or len(stored['modes']) != 6 or len(shared['modes']) != 6):
        raise ValueError('incompatible shared HOA dictionary')
    shared = expand_shared(shared)

    def resolve(key, indices):
        pool = shared[key]
        if any(type(i) is not int or not 0 <= i < len(pool) for i in indices):
            raise ValueError('invalid shared HOA table index')
        return [pool[i] for i in indices]

    modes = []
    for index, (mode, common) in enumerate(zip(stored['modes'], shared['modes'])):
        if mode['mode'] != index or common['mode'] != index:
            raise ValueError('shared HOA coding mode differs')
        books = ([unpack_codebook(book, stored['quantization_bits']) for book in mode['codebooks']]
                 if packed else mode['codebooks'])
        modes.append(dict(mode=index, groups=resolve('groups', common['group_indices']),
                          codebooks=books, signs=common['signs'],
                          matrices_f32=resolve('matrices_f32', common['matrix_indices'])))
    expanded = {key: value for key, value in stored.items() if key not in ('shared_file', 'codebook_encoding')}
    expanded.update(schema_version=1, modes=modes)
    check_digest(expanded)
    return expanded


@lru_cache(maxsize=3)
def _shared(path):
    return expand_shared(json.loads(path.read_text()))


def load_format(path):
    path = Path(path)
    stored = json.loads(path.read_text())
    if stored['schema_version'] == 1:
        check_digest(stored)
        return stored
    # Resolve only the expected sibling; external --table inputs cannot redirect it.
    if stored.get('shared_file') != shared_name(stored['order']):
        raise ValueError('invalid shared HOA dictionary filename')
    return expand_format(stored, _shared(path.with_name(stored['shared_file']).resolve()))


@lru_cache(maxsize=12)
def format_for(order, quantization_bits=6):
    return load_format(DATA / format_name(order, quantization_bits))


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode()
