#!/usr/bin/env python3
"""Package bounded source-layout constants from explicit, local observations."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'data/hoa-source-layout-format-v1.json'
PROFILE = 'apac-hoa-source-layout-format-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def generate(matrices, layouts, profiles):
    observed = {row['symbol'].split('HOA2CHmtx_')[1][:-1]: bytes.fromhex(row['raw_hex'])
                for row in matrices}
    tables = {}
    entries = []
    for row in layouts:
        assert row['status'] == 0 and len(row['labels']) == row['channels']
        lfe = [i for i, label in enumerate(row['labels']) if label in (4, 37, 62)]
        rows = row['channels'] - len(lfe)
        columns = (math.isqrt(rows - 1) + 1) ** 2
        name = row['matrix'].removeprefix('HOA2CHmtx_')
        raw = observed[name]
        size = rows * columns * 4
        # Observations include linker alignment up to the following named table.
        assert size <= len(raw) < size + 16 and not any(raw[size:])
        raw = raw[:size]
        identity = hashlib.sha256(raw).hexdigest()
        tables[identity] = [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, size, 4)]
        entries.append(dict(tag=row['tag'], channel_labels=row['labels'], lfe_indices=lfe,
                            matrix_id=identity, matrix_rows=rows, matrix_columns=columns,
                            matrix_available=row.get('matrix_available', True)))
    supported = [p['layout_tags'] for p in profiles if p['profile'] in (0, 5)]
    assert len(supported) == 2 and supported[0] == supported[1]
    content = dict(format_profile=PROFILE, numeric_profile='apac-hoa-source-layout-math-v1',
                   accepted_layout_tags=supported[0],
                   layouts=entries, matrices=tables,
                   source=dict(component='AudioCodecs 7.0',
                               component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
                               method='layout selection pseudocode, bounded constants, public SDK labels and native basis controls'))
    content['format_sha256'] = hashlib.sha256(canonical(content)).hexdigest()
    return content


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix-observations', type=Path)
    p.add_argument('--layout-observations', type=Path)
    p.add_argument('--profile-observations', type=Path)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    if args.matrix_observations is None or args.layout_observations is None:
        if not args.check or args.matrix_observations is not None or args.layout_observations is not None or args.profile_observations is not None:
            p.error('provide both observation paths, or use --check for the bundled format')
        result = json.loads(DESTINATION.read_text())
        expected = result.pop('format_sha256')
        assert result['format_profile'] == PROFILE
        assert hashlib.sha256(canonical(result)).hexdigest() == expected
    else:
        if args.profile_observations is None: p.error('provide --profile-observations')
        result = generate(json.loads(args.matrix_observations.read_text()),
                          json.loads(args.layout_observations.read_text()),
                          json.loads(args.profile_observations.read_text()))
        if args.check:
            assert json.loads(DESTINATION.read_text()) == result
        else:
            DESTINATION.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
