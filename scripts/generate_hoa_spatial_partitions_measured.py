#!/usr/bin/env python3
"""Load qualified integer partitions measured across spatial recovery contexts."""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEASURED = ROOT / 'data/hoa-spatial-partitions-measured-v1.json'
SOURCE_SHA256 = 'ca3fd1e94754c50c5dac00a0271da2432748d658bc82274161dddd545de58063'


def measured_partitions(value):
    raw = (json.dumps(value, indent=2) + '\n').encode()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('spatial partition measurement or qualification differs')
    if (value['schema_version'], value['profile'], value['sample_rates']) != (
            1, 'apac-hoa-spatial-partitions-measured-v1', [44100, 48000]):
        raise ValueError('spatial partition measurement schema differs')
    source = value['source']
    qualification = source['qualification']
    if (source['old_dictionary_consulted_during_discovery'] is not False
            or not source['candidate_frozen_before_heldout'] or not source['heldout_passed_before_comparison']
            or not qualification['passed'] or not qualification['full_spectral_coverage']
            or not qualification['strict_all_compared_integer_entries_match']
            or (qualification['grids'], qualification['discovery_observations'],
                qualification['heldout_observations'], qualification['dynamic_alias_checks']) != (96, 192, 576, 192)
            or not 0 <= qualification['subband_maximum_membership_error'] <= .001):
        raise ValueError('spatial partition source is unqualified')
    indexed = {(row['method'], row['subbands'], row['rounded']): row for row in value['tables']}
    expected = {(method, count, rounded) for method in range(3) for count in range(1, 17)
                for rounded in (False, True)}
    if len(value['tables']) != 96 or indexed.keys() != expected:
        raise ValueError('measured spatial partition coverage differs')
    for (_, count, rounded), row in indexed.items():
        keys = [('long_ends', 1024), ('short_ends', 128)] if rounded else [
            ('long_ends', 1024), ('short_frequency_major_ends', 1024)]
        for key, maximum in keys:
            ends = row[key]
            if (len(ends) != count or ends[-1] != maximum or ends != sorted(set(ends))
                    or any(type(end) is not int or not 1 <= end <= maximum for end in ends)):
                raise ValueError('measured spatial interval endpoints differ')
        if rounded and [v*8 for v in row['short_ends']] != row['long_ends']:
            raise ValueError('measured rounded window coordinates differ')
        if not rounded and row['short_frequency_major_ends'] != row['long_ends']:
            raise ValueError('measured frequency-major coordinates differ')
    return indexed


@lru_cache(maxsize=1)
def load_measurement():
    value = json.loads(MEASURED.read_bytes())
    measured_partitions(value)
    return value


def boundaries(count, method, aligned=True):
    if type(count) is not int or not 1 <= count <= 16 or method not in (0, 1, 2):
        raise ValueError('spatial partition selection exceeds qualified coverage')
    row = next(row for row in load_measurement()['tables'] if
               (row['method'], row['subbands'], row['rounded']) == (method, count, aligned))
    return list(row['long_ends'])


def source_metadata():
    identity = load_measurement()['source']['native_identity']
    return dict(component='AudioCodecs 7.0', component_sha256=identity['component_sha256'],
                method='qualified public AudioConverter spectral membership measurements',
                measurement_file=MEASURED.name, measurement_sha256=SOURCE_SHA256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', required=True)
    parser.parse_args()
    value = load_measurement()
    print(json.dumps(dict(source_sha256=SOURCE_SHA256, grids=len(value['tables']))))


if __name__ == '__main__':
    main()
