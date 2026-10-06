"""Export only approved q6 matrix/group measurements for wider-codebook work.

Invoked in a separate process before discovery. No q7–q9 dictionary is opened and
no q6 Huffman word is exported. Full source hashes pin the approved artifacts.
"""
import json
import math
import struct
import argparse
from pathlib import Path

from .common import ROOT, canonical, digest, geometry, require

MATRIX_SOURCES = (
    '410345a9b07544104bfae974c778b14f8a1f61cf8d6b87f09a6e690a88ccc462',
    '9df080e3717a4c4060ed19495a5d0dfa35f06aa0018e51fe12319e34f851b5f6',
    '1b0b394772acdea3038fb436378f27cff4407b232a56f40b15b327cf58c01814',
    '58e551546c7afe705368610dd68119e5dcd11aa5e2d8c498faacb1ee7643b8c9',
)
GROUP_SOURCES = {
    '2:0': ('hoa-salient-order3-q6-mode2-book0-measured-v1.json', '78f0f21314fc565bdaf8d482fc0b5861667cf146626fab843c2e2d59e00d9c61'),
    '2:1': ('hoa-salient-order3-q6-mode2-book1-measured-v1.json', '7095b2eacf59c31cede556e6d812ba85b71315a6b0936c18724c7348a7bae804'),
    '3:0': ('hoa-salient-order3-q6-mode3-measured-v1.json', 'e67feb38d9b8b4284075fc0e55d33f71fda26ea91e54ca6633f690765b283599'),
}
COMPONENT_SHA256 = '826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd'


def read_source(name, sha):
    raw = (ROOT/'data'/name).read_bytes()
    require(digest(raw) == sha, 'qualified prior source changed: '+name)
    value = json.loads(raw)
    require(value['source']['component_sha256'] == COMPONENT_SHA256
            and value['source']['architecture'] == 'arm64', 'prior native identity differs')
    return value


def export():
    matrices, groups = {}, {}
    for cluster, sha in enumerate(MATRIX_SOURCES):
        name = f'hoa-salient-order3-mode4-cluster{cluster}-matrix-measured-v1.json'
        value = read_source(name, sha)
        words = value['matrix_f32']
        require(len(words) == 256 and all(type(v) is int and 0 <= v < 2**32
                and math.isfinite(struct.unpack('<f', struct.pack('<I', v))[0]) for v in words), 'invalid prior matrix')
        radius = value['source']['max_empirical_half_width']
        require(5e-8 <= radius < 2.5e-7, 'prior matrix precision is not qualified')
        matrices[str(cluster)] = dict(matrix_f32=words, empirical_half_width=radius,
                                      source_file=name, source_sha256=sha)
    for key, (name, sha) in GROUP_SOURCES.items():
        value = read_source(name, sha)
        group = value['coefficient_group']
        require(group and all(type(i) is int and 0 <= i < 16 for i in group)
                and len(set(group)) == len(group), 'invalid prior group')
        groups[key] = dict(indices=group, source_file=name, source_sha256=sha)
    require(sorted(groups['2:0']['indices']+groups['2:1']['indices']) == list(range(16)), 'prior partition incomplete')
    require(sorted(groups['3:0']['indices']) == list(range(16)), 'prior coefficient order incomplete')
    return dict(schema_version=1, profile='hoa-blackbox-qualified-order3-priors-v1',
                measurement_quantization_bits=6, component_sha256=COMPONENT_SHA256,
                architecture='arm64', matrices=matrices, groups=groups,
                huffman_words_included=False)


def export_batches(order, paths):
    """Read frozen qualification records in this separate process only."""
    from .store import Store
    from .maths import inverse, validate_words
    n = geometry(order)['channels']
    matrices, groups, identity = {}, {}, None
    for path in paths:
        store = Store(Path(path), readonly=True)
        try:
            store.audit_evidence()
            require(store.config.get('order', 3) == order
                    and store.config.get('quantization_bits', 6) == 6,
                    'prior evidence order or quantization width differs')
            current = {key: store.config['native_identity'][key] for key in ('component_sha256', 'architecture')}
            require(identity is None or identity == current, 'prior evidence native components differ')
            identity = current
            for target in store.config['targets']:
                if target == 'mode1':
                    continue
                book = store.stage(target, 'codebook')
                validation = store.stage(target, 'validation')
                comparison = store.stage(target, 'comparison')
                if not book or not validation or not comparison:
                    continue
                require(book['order'] == order and book['quantization_bits'] == 6
                        and book['old_dictionary_consulted'] is False,
                        'prior candidate scope differs')
                validate_words(book['entries'], 64)
                require(validation['status'] == 'passed'
                        and validation['codebook_sha256'] == comparison['codebook_sha256'] == digest(canonical(book))
                        and comparison['validation_sha256'] == digest(canonical(validation)),
                        'prior qualification binding differs')
                if not comparison['eligible_codebook']:
                    continue
                source = dict(codebook_sha256=digest(canonical(book)),
                              validation_sha256=digest(canonical(validation)),
                              comparison_sha256=digest(canonical(comparison)),
                              tool_fingerprint=store.config['tool_fingerprint'],
                              code_commit=store.config.get('code_commit'))
                if target.startswith('mode4:'):
                    if not comparison['eligible_matrix']:
                        continue
                    matrix = store.stage(target, 'matrix')
                    require(matrix is not None and matrix['old_matrix_consulted'] is False
                            and (matrix['order'], matrix['rows'], matrix['columns']) == (order, n, n)
                            and len(matrix['entries']) == n*n
                            and validation['matrix_qualified'] == n*n
                            and validation['matrix_sha256'] == comparison['matrix_sha256'] == digest(canonical(matrix)),
                            'prior matrix qualification differs')
                    entries = matrix['entries']
                    require([(e['row'], e['column']) for e in entries] == [(r,c) for r in range(n) for c in range(n)],
                            'prior matrix entry order differs')
                    require(all(type(e['float32_bits']) is int and 0 <= e['float32_bits'] < 2**32
                                and 5e-8 <= e['empirical_half_width'] < 2.5e-7 for e in entries),
                            'unqualified prior matrix entry')
                    words = [e['float32_bits'] for e in entries]
                    floats = [struct.unpack('<f', struct.pack('<I', word))[0] for word in words]
                    inversion_policy = {}
                    inverse([floats[i:i+n] for i in range(0,n*n,n)], order=order, diagnostics=inversion_policy)
                    item = dict(matrix_f32=words, empirical_half_width=max(e['empirical_half_width'] for e in entries),
                                matrix_sha256=digest(canonical(matrix)), source=source, inversion_policy=inversion_policy)
                    key = target[-1]
                    require(key not in matrices or matrices[key] == item, 'conflicting prior matrices')
                    matrices[key] = item
                else:
                    key = f'{book["mode"]}:{book["book"]}'
                    layout = store.stage('_mode2' if book['mode'] == 2 else target, 'layout')
                    require(comparison.get('group_exact') is True and layout is not None
                            and validation['layout_sha256'] == book['layout_sha256'] == digest(canonical(layout))
                            and book['coefficient_group'] == layout['groups'][book['book']],
                            'prior group qualification differs')
                    group = book['coefficient_group']
                    require(group and all(type(i) is int and 0 <= i < n for i in group)
                            and len(set(group)) == len(group), 'invalid prior group')
                    item = dict(indices=group, source=source)
                    require(key not in groups or groups[key] == item, 'conflicting prior groups')
                    groups[key] = item
        finally:
            store.close()
    require(identity is not None, 'no prior evidence batches')
    if '2:0' in groups and '2:1' in groups:
        require(sorted(groups['2:0']['indices']+groups['2:1']['indices']) == list(range(n)), 'prior partition incomplete')
    if '3:0' in groups:
        require(sorted(groups['3:0']['indices']) == list(range(n)), 'prior coefficient order incomplete')
    return dict(schema_version=1, profile='hoa-blackbox-qualified-priors-v2', order=order,
                measurement_quantization_bits=6, matrices=matrices, groups=groups,
                huffman_words_included=False, **identity)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', type=int, choices=range(1,11), default=3)
    parser.add_argument('--evidence', type=Path, nargs='+')
    args = parser.parse_args()
    if args.evidence:
        result = export_batches(args.order, args.evidence)
    else:
        require(args.order == 3, 'qualified six-bit geometry evidence is required for this order')
        result = export()
    print(canonical(result).decode(), end='')
