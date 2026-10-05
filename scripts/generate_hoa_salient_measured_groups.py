#!/usr/bin/env python3
"""Regenerate shared order-3 coefficient groups from measured q6 codebooks.

The three groups retain their existing aliases and sharing across q6-q9.
Codewords and matrices are verified, never repaired by this generator.
"""
import argparse
import copy
import hashlib
import json

from generate_hoa_salient_measured import MEASURED_FILES, load_measurement, measured_group, require
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

# These are storage users, not measured coefficient indices. Preserve the
# established aliases instead of claiming separate measurements of every mode.
GROUP_USERS = {
    (2, 0): ((2, 0),),
    (2, 1): ((2, 1),),
    (3, 0): ((0, 0), (1, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (5, 0)),
}


def load_measurements():
    return [load_measurement(*key) for key in GROUP_USERS]


def replace_group(value, measurement):
    require(value['order'] == 3 and value['quantization_bits'] in range(6, 10), 'group replacement scope differs')
    group = measured_group(measurement)
    key = (measurement['mode'], measurement['book'])
    result = copy.deepcopy(value)
    for mode, index in GROUP_USERS[key]:
        result['modes'][mode]['groups'][index] = list(group)
    source = result['source']
    if 'original_observation' not in source:
        source = dict(method='', original_observation=source, remaining_tables='original_observation')
    replacements = [entry for entry in source.get('group_replacements', [])
                    if (entry['measured_mode'], entry['measured_book']) != key]
    replacements.append(dict(measured_mode=key[0], measured_book=key[1],
        shared_uses=[list(user) for user in GROUP_USERS[key]], source_file=MEASURED_FILES[key],
        source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(),
        group_sha256=measurement['group_sha256'], method=measurement['source']['method']))
    source.update(method='mixed sources with per-table replacements',
                  group_replacements=sorted(replacements, key=lambda e: (e['measured_mode'], e['measured_book'])))
    result['source'] = source
    check_digest(result)
    return result


def regenerate(stored_variants, shared, measurements):
    require(set(stored_variants) == set(range(6, 10)), 'all four shared-group variants are required')
    require(shared['schema_version'] == 2 and shared['order'] == 3, 'shared group scope differs')
    shared = copy.deepcopy(shared)
    selected = set()
    for measurement in measurements:
        group = measured_group(measurement)
        key = (measurement['mode'], measurement['book'])
        require(key not in selected, 'duplicate group measurement')
        selected.add(key)
        target = shared['modes'][key[0]]['group_indices'][key[1]]
        require(type(target) is int and 0 <= target < len(shared['groups']), 'invalid target group index')
        users = tuple((m['mode'], i) for m in shared['modes'] for i, index in enumerate(m['group_indices']) if index == target)
        require(users == GROUP_USERS[key], 'shared group aliases differ')
        shared['groups'][target] = list(group)
    outputs, common = {}, None
    for precision, stored in sorted(stored_variants.items()):
        require((stored['order'], stored['quantization_bits']) == (3, precision), 'dictionary scope differs')
        expanded = expand_format(stored, shared)
        for measurement in measurements:
            expanded = replace_group(expanded, measurement)
        packed, rebuilt = split_format(expanded)
        require(common is None or rebuilt == common, 'quantization-dependent shared group data')
        common, outputs[precision] = rebuilt, packed
    return outputs, common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    stored = {q: json.loads((DATA / format_name(3, q)).read_text()) for q in range(6, 10)}
    shared_path = DATA / shared_name(3)
    packed, common = regenerate(stored, json.loads(shared_path.read_text()), load_measurements())
    outputs = {DATA / format_name(3, q): json_bytes(value) for q, value in packed.items()}
    outputs[shared_path] = json_bytes(common)
    for path, raw in outputs.items():
        if args.check:
            require(path.read_bytes() == raw, 'generated group or provenance differs: ' + path.name)
        else:
            path.write_bytes(raw)
    print(json.dumps(dict(groups=3, shared_quantization_bits=list(packed))))


if __name__ == '__main__':
    main()
