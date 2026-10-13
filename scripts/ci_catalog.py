"""Versioned CI coverage and identity rules. No candidate output is used here."""
import argparse
from collections import defaultdict
import hashlib
import importlib
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/ci-acceptance-v1.json'
TARGETS = ('x86_64-unknown-linux-gnu', 'x86_64-pc-windows-msvc', 'aarch64-apple-darwin')
CELLS = tuple((target, profile) for target in TARGETS for profile in ('debug', 'release'))
REFERENCE = (TARGETS[0], 'release')
CORE = ('validate_portable', 'validate_cac', 'validate_tns', 'validate_bwe2')
SHARED = ('rate-tools-48000', 'rate-tools-44100', 'rate-spatial-96000',
          'rate-spatial-7350', 'stream-hoa-channel', 'additional-2')
QUICK = (*CORE, 'validate_hoa_orders', 'validate_hoa_asp', 'validate_hoa_shared')
# Historical inputs remain immutable. These particular inputs must now reject.
REJECTIONS = {
    'validate_hoa_expanded_orders': [1, 2, 3, 4, 5],
    'validate_hoa_partial': [3, 6],
    'validate_hoa_dynamic_domains': [1, 2, 6, 9, 11],
    'validate_hoa_remapping': [6, 7, 8, 9, 10, 11, 15],
    'validate_hoa_source_layouts': [26, 50],
    'validate_hoa_shared': [53, 54],
}
FILES = {
    'validate_packets': 'packet', 'validate_access': 'access', 'validate_caf': 'caf',
    'validate_mp4': 'mp4', 'validate_channels': 'channel', 'validate_drc': 'drc',
    'validate_drc_pcm': 'drc-pcm', 'validate_hoa': 'hoa-ambient',
    'validate_hoa_access': 'hoa-access', 'validate_hoa_asp': 'hoa-asp',
    'validate_hoa_additive': 'hoa-additive', 'validate_hoa_ambient_counts': 'hoa-ambient-counts',
    'validate_hoa_component_orders': 'hoa-component-orders',
    'validate_hoa_controls': 'hoa-controls-vectors-v2.json',
    'validate_hoa_dynamic': 'hoa-dynamic', 'validate_hoa_dynamic_domains': 'hoa-dynamic-domains',
    'validate_hoa_dynamic_subbands': 'hoa-subbands', 'validate_hoa_expanded_orders': 'hoa-expanded-orders',
    'validate_hoa_mixed': 'hoa-mixed', 'validate_hoa_order1': 'hoa-order1',
    'validate_hoa_orders': 'hoa-orders', 'validate_hoa_partial': 'hoa-partial',
    'validate_hoa_quantization': 'hoa-quantization', 'validate_hoa_remapping': 'hoa-remapping',
    'validate_hoa_salient': 'hoa-salient', 'validate_hoa_salient_counts': 'hoa-salient-counts',
    'validate_hoa_salient_partition': 'hoa-partition', 'validate_hoa_salient_subbands': 'hoa-spatial-subbands',
    'validate_hoa_shared': 'hoa-shared', 'validate_hoa_source_layouts': 'hoa-source-layout',
    'validate_hoa_static_ambient': 'hoa-static-ambient', 'validate_hoa_transports': 'hoa-transports',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def group(rows, keys):
    identities = [{key: row[key] for key in keys} for row in rows]
    ordered = sorted(identities, key=lambda row: json.dumps(row, sort_keys=True))
    if len({digest(row) for row in ordered}) != len(ordered):
        raise ValueError('duplicate coverage identity')
    return dict(keys=keys, count=len(rows), identities_sha256=digest(ordered))


def read_vectors(name):
    file = name if name.endswith('.json') else name + '-vectors-v1.json'
    return json.loads((ROOT / 'data' / file).read_text(encoding='utf-8'))


def core_items(name, stage):
    if name == 'validate_portable':
        from spectrum_vectors import matrix_cases, band_cases
        from validate_synthesis import sequences
        return list(itertools.chain(matrix_cases(), band_cases())) if stage == 'spectra' else list(sequences())
    vectors = importlib.import_module(name.removeprefix('validate_') + '_vectors')
    return list(vectors.cases() if stage == 'spectra' else vectors.sequences())


def sample_indices(items, stage):
    buckets = defaultdict(list)
    for index, item in enumerate(items):
        kind, sequence = (item['kind'], [item]) if stage == 'spectra' else item
        windows = tuple(sorted({c.get('block', 0) for c in sequence} |
                               {c.get('right_block', c.get('block', 0)) for c in sequence}))
        buckets[kind, windows].append(index)
    return sorted({indices[pos] for indices in buckets.values()
                   for pos in (0, len(indices) // 2, len(indices) - 1)})


def core_identity(name, stage, rate, index, item):
    from spectrum_vectors import cookie, frame
    if name == 'validate_portable':
        writer = frame
    else:
        vectors = importlib.import_module(name.removeprefix('validate_') + '_vectors')
        writer = (lambda c: vectors.packet(c, rate)) if name in ('validate_tns', 'validate_bwe2') else vectors.packet
    kind, sequence = (item['kind'], [item]) if stage == 'spectra' else item
    sha = hashlib.sha256(cookie(rate))
    for case in sequence:
        raw, _ = writer(case)
        if not (name == 'validate_portable' and stage == 'spectra'):
            sha.update(len(raw).to_bytes(8, 'little'))
        sha.update(raw)
    return dict(rate=rate, index=index, kind=kind, input_sha256=sha.hexdigest())


def shards(names):
    groups = [[CORE[0], 'validate_spectra'], [CORE[1]], [CORE[2]], [CORE[3]],
              ['validate_packets', 'validate_drc', 'validate_drc_pcm'],
              ['validate_access', 'validate_caf', 'validate_mp4'],
              ['validate_channels', 'validate_layouts', 'validate_surround916']]
    hoa = sorted(n for n in names if n.startswith('validate_hoa'))
    groups += [hoa[::2], hoa[1::2]]
    assert sorted(n for g in groups for n in g) == sorted(names)
    return groups


def generate():
    from run_portable_suite import SUITE
    full, fast = {}, {}
    for name in CORE:
        full_groups, fast_groups, selection = {}, {}, {}
        for stage in ('spectra', 'pcm'):
            items = core_items(name, stage)
            selection[stage] = sample_indices(items, stage)
            all_rows = [core_identity(name, stage, rate, i, item)
                        for rate in (48000, 44100) for i, item in enumerate(items)]
            full_groups[stage] = group(all_rows, ['rate', 'index', 'kind', 'input_sha256'])
            fast_groups[stage] = group([r for r in all_rows if r['index'] in selection[stage]],
                                       ['rate', 'index', 'kind', 'input_sha256'])
        full[name] = dict(groups=full_groups, rejections=[])
        fast[name] = dict(groups=fast_groups, selection=selection, rejections=[])
        print('catalog', name, flush=True)
    for name, file in FILES.items():
        frozen = read_vectors(file)
        groups = {}
        if name == 'validate_hoa_access':
            groups['cases'] = group(frozen['fixtures'], ['name'])
        elif name == 'validate_hoa_asp':
            import hoa_asp_vectors
            rows = [dict(name=name, variant=variant) for name, _, _, nodes, _ in hoa_asp_vectors.generated() for variant in nodes]
            groups['cases'] = group(rows, ['name', 'variant'])
            groups['rejections'] = group([dict(name=f['name'], kind=kind) for f in frozen['fixtures'] for kind in f['bad']], ['name', 'kind'])
        else:
            for key in ('cases', 'sequences', 'boundary_cases'):
                if key not in frozen:
                    continue
                rows = frozen[key]
                keys = [k for k in ('channels', 'rate', 'index', 'name') if k in rows[0]]
                groups[key] = group(rows, keys)
        full[name] = dict(groups=groups, rejections=REJECTIONS.get(name, []), input_manifest_sha256=digest(frozen))
        if name in QUICK:
            fast[name] = dict(full[name])
            if name == 'validate_hoa_shared':
                fast[name]['cases'] = list(SHARED)
                fast[name]['rejections'] = []
                fast[name]['groups'] = {'cases': group([r for r in frozen['cases'] if r['name'] in SHARED], ['index', 'name'])}
    for name, prefix in (('validate_layouts', 'layout'), ('validate_surround916', 'surround916')):
        groups = {key: group(read_vectors(file)[key], ['channels', 'rate', 'index'])
                  for key, file in (('sequences', prefix), ('cases', prefix + '-access'))}
        presence = json.loads((ROOT / 'data' / (prefix + '-presence-v1.json')).read_text())
        full[name] = dict(groups=groups, rejections=[], presence={k: presence[k] for k in ('cases', 'sha256')})
    from spectrum_vectors import matrix_cases, band_cases
    count = sum(1 for _ in itertools.chain(matrix_cases(), band_cases()))
    rows = [dict(rate=rate, batch=i, cases=min(96, count - 96 * i)) for rate in (48000, 44100) for i in range((count + 95) // 96)]
    full['validate_spectra'] = dict(groups={'artificial': group(rows, ['rate', 'batch', 'cases'])}, rejections=[])
    assert set(full) == set(SUITE)
    return dict(schema='apac-ci-acceptance-v1', profiles={'ci-fast': fast, 'ci-full': full}, shards=shards(SUITE))


def load():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    document = generate()
    if args.check:
        if document != load():
            raise SystemExit('CI acceptance catalog differs; regenerate and review coverage changes')
    else:
        CATALOG.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
