#!/usr/bin/env python3
"""Generate shared order-1/2/3 mode-4 matrices from qualified frozen candidates.

Only pinned, validated weighted candidates are accepted by --candidate.
The public source contains exact Float32 words and provenance, without PCM,
estimated values or local evidence paths. --write regenerates the packed
matrices and the four dictionary provenance records; --check verifies them.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from hoa_packed_tables import pack_matrix
from hoa_measured_batch_sources import OUTPUTS, LOWER_MATRICES, source as batch_source, lower_source
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

MEASURED_FILES = {c: f'hoa-salient-order3-mode4-cluster{c}-matrix-measured-v1.json' for c in range(4)}
PROFILE = 'apac-hoa-salient-measured-matrix-v1'
CANDIDATE_SHA256 = {0: '3e1fa1a640d1fa68c270ed4b9aadd59d544a153cdccef4b74bb0fba6bb722899'}
MATRIX_SHA256 = {0: '87a5fbe1a977b1312d8d1093425ee3217d87ad4dfc77a7855b0290e8b9861c19'}
SOURCES = {0: dict(
    method='public AudioConverter PCM reconstruction with calibration-weighted reanalysis',
    experiment='hoa-blackbox-order3-q6-mode4-cluster0-v1',
    measurement_code_commit='f4110ca0eb9383c534479026f8f2b6f9656ca646',
    analysis_code_commit='cabcb5523c9175cdb363081f212a7712fa18fb44',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    measurement_quantization_bits=6,
    candidate_sha256=CANDIDATE_SHA256[0],
    policy_sha256='143a3deeacda5577c88cf52d1788c0b0f5613644abacb8cfa7406b085091d325',
    validation_sha256='73a8a9864aae304f12a41d842758b05c0aa51eebc08d674df33cf6891325aa69',
    comparison_sha256='4cf995645d994b078123949eef08aa9fc833c76f48c20c497b39327979d13172',
    max_empirical_half_width=2.3251493876046068e-7,
    empirical_half_width_limit=2.5e-7,
    held_out_checks=254,
    candidate_frozen_before_comparison=True,
    new_native_acquisition_in_reanalysis=False,
)}
for cluster, record in OUTPUTS.items():
    CANDIDATE_SHA256[cluster] = record['matrix_candidate']
    MATRIX_SHA256[cluster] = record['matrix_values']
    SOURCES[cluster] = batch_source(cluster, 'matrix')


MEASURED_FILES = {(3, cluster): value for cluster, value in MEASURED_FILES.items()}
CANDIDATE_SHA256 = {(3, cluster): value for cluster, value in CANDIDATE_SHA256.items()}
MATRIX_SHA256 = {(3, cluster): value for cluster, value in MATRIX_SHA256.items()}
SOURCES = {(3, cluster): value for cluster, value in SOURCES.items()}
for key, record in LOWER_MATRICES.items():
    order, cluster = key
    MEASURED_FILES[key] = f'hoa-salient-order{order}-mode4-cluster{cluster}-matrix-measured-v1.json'
    CANDIDATE_SHA256[key] = record['candidate']
    MATRIX_SHA256[key] = record['matrix_values']
    SOURCES[key] = lower_source(key, 'matrix')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measured_matrix(value):
    require(value.get('schema_version') == 1 and value.get('profile') == PROFILE,
            'incompatible measured matrix schema')
    order, cluster = value.get('order'), value.get('cluster')
    key = (order, cluster)
    require(type(order) is int and type(cluster) is int and key in MEASURED_FILES, 'measured matrix scope differs')
    n = (order+1)**2
    require(type(cluster) is int and key in MEASURED_FILES
            and tuple(value.get(k) for k in ('order', 'mode', 'rows', 'columns')) == (order, 4, n, n)
            and value.get('storage') == 'row-major', 'measured matrix scope differs')
    require(value.get('source') == SOURCES[key], 'measured matrix source differs')
    words = value.get('matrix_f32')
    require(isinstance(words, list) and len(words) == n*n, f'measured matrix needs {n*n} words')
    pack_matrix(words)  # Checks unsigned words, finiteness and exact micro21 storage.
    digest = hashlib.sha256(json.dumps(words, separators=(',', ':')).encode()).hexdigest()
    require(digest == value.get('matrix_sha256') == MATRIX_SHA256[key], 'measured matrix digest differs')
    return words


def from_candidate(raw):
    digest = hashlib.sha256(raw).hexdigest()
    key = next((key for key, expected in CANDIDATE_SHA256.items() if digest == expected), None)
    require(key is not None, 'unverified qualified matrix candidate')
    order, cluster = key
    n = (order+1)**2
    candidate = json.loads(raw)
    require((candidate['order'], candidate['mode'], candidate['cluster']) == (order, 4, cluster),
            'candidate scope differs')
    require(candidate['old_matrix_consulted'] is False, 'candidate used an old matrix')
    if key == (3, 0):
        require(candidate['quantization_bits'] == 6 and candidate['original_matrix_candidate_used'] is False,
                'candidate reanalysis scope differs')
    else:
        require(candidate['profile'] == 'hoa-blackbox-matrix-v1'
                and (candidate['rows'], candidate['columns']) == (n, n)
                and candidate['codebook_sha256'] == (OUTPUTS[cluster] if order == 3 else LOWER_MATRICES[key])['codebook_candidate'],
                'candidate codebook or dimensions differ')
    source = SOURCES[key]
    require(candidate['policy_sha256'] == source['policy_sha256'], 'candidate calibration policy differs')
    require(len(candidate['entries']) == n*n, 'candidate matrix dimensions differ')
    words = []
    for index, entry in enumerate(candidate['entries']):
        require((entry['row'], entry['column']) == divmod(index, n), 'candidate row order differs')
        word = entry['float32_bits']
        require(word is not None and entry['candidate_bits'] == [word]
                and 5e-8 <= entry['empirical_half_width'] < source['empirical_half_width_limit'],
                'candidate coefficient remains unqualified')
        words.append(word)
    result = dict(schema_version=1, profile=PROFILE, order=order, mode=4, cluster=cluster, rows=n, columns=n,
                  storage='row-major', source=copy.deepcopy(source), matrix_sha256=MATRIX_SHA256[key], matrix_f32=words)
    measured_matrix(result)
    return result


def load_measurement(cluster=0, order=3):
    key = (order, cluster)
    value = json.loads((DATA / MEASURED_FILES[key]).read_text())
    measured_matrix(value)
    require((value['order'], value['cluster']) == (order, cluster), 'measurement file scope differs')
    return value


def load_measurements(order=3):
    return [load_measurement(cluster, selected_order) for selected_order, cluster in MEASURED_FILES if selected_order == order]


def replace_matrix(value, measurement):
    require(value['order'] == measurement['order'] and value['quantization_bits'] in range(6, 10), 'matrix replacement scope differs')
    result = copy.deepcopy(value)
    cluster = measurement['cluster']
    key = (measurement['order'], cluster)
    result['modes'][4]['matrices_f32'][cluster] = measured_matrix(measurement)
    source = result['source']
    if 'original_observation' not in source:
        source = dict(method='', original_observation=source, remaining_tables='original_observation')
    replacements = [item for item in source.get('matrix_replacements', [])
                    if (item['mode'], item['cluster']) != (4, cluster)]
    replacements.append(dict(mode=4, cluster=cluster, source_file=MEASURED_FILES[key],
                             source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(), method=SOURCES[key]['method']))
    source.update(method='mixed sources with per-table replacements',
                  matrix_replacements=sorted(replacements, key=lambda item: (item['mode'], item['cluster'])))
    result['source'] = source
    check_digest(result)
    return result


def regenerate(stored_variants, shared, measurements):
    require(set(stored_variants) == set(range(6, 10)), 'all four shared-matrix variants are required')
    order = shared['order']
    require(shared['schema_version'] == 2 and order in (1,2,3), 'shared matrix scope differs')
    shared = copy.deepcopy(shared)
    if isinstance(measurements, dict):
        measurements = [measurements]
    for measurement in measurements:
        require(measurement['order'] == order, 'matrix replacement order differs')
        cluster = measurement['cluster']
        words = measured_matrix(measurement)
        target = shared['modes'][4]['matrix_indices'][cluster]
        require(type(target) is int and 0 <= target < len(shared['matrices_f32']), 'invalid target matrix index')
        users = [(mode['mode'], c) for mode in shared['modes']
                 for c, index in enumerate(mode['matrix_indices']) if index == target]
        require(users == [(4, cluster)], 'target matrix is aliased by another cluster or mode')
        # Repair all selected packed copies before checking any dictionary.
        # Remaining data must satisfy every published semantic digest.
        shared['matrices_f32'][target] = pack_matrix(words)
    outputs = {}; common = None
    for precision, stored in sorted(stored_variants.items()):
        require((stored['order'], stored['quantization_bits']) == (order, precision), 'dictionary scope differs')
        expanded = expand_format(stored, shared)
        for measurement in measurements:
            expanded = replace_matrix(expanded, measurement)
        packed, rebuilt = split_format(expanded)
        if common is not None:
            require(rebuilt == common, 'quantization-dependent shared matrix data')
        common = rebuilt
        outputs[precision] = packed
    return outputs, common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', type=int, choices=(1,2,3), help='restrict to one order; default all registered orders')
    parser.add_argument('--candidate', type=Path, action='append', default=[],
                        help='qualified frozen matrix candidate; repeat for distinct orders/clusters')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    supplied = {}
    for path in args.candidate:
        measurement = from_candidate(path.read_bytes())
        key = (measurement['order'], measurement['cluster'])
        require(args.order is None or key[0] == args.order, 'candidate order was not selected')
        require(key not in supplied, 'duplicate candidate matrix')
        supplied[key] = measurement
    keys = [key for key in MEASURED_FILES if args.order is None or key[0] == args.order]
    require(keys, 'no registered matrices for requested order')
    measurements = [supplied[key] if key in supplied else load_measurement(key[1], key[0]) for key in keys]
    outputs = {DATA / MEASURED_FILES[m['order'], m['cluster']]: json_bytes(m) for m in measurements}
    for order in sorted({key[0] for key in keys}):
        stored = {q: json.loads((DATA / format_name(order, q)).read_text()) for q in range(6, 10)}
        shared_path = DATA / shared_name(order)
        selected = [m for m in measurements if m['order'] == order]
        packed, shared = regenerate(stored, json.loads(shared_path.read_text()), selected)
        outputs[shared_path] = json_bytes(shared)
        outputs.update({DATA / format_name(order, q): json_bytes(value) for q, value in packed.items()})
    for path, raw in outputs.items():
        if args.check:
            require(path.read_bytes() == raw, 'generated matrix or provenance differs: ' + path.name)
        else:
            path.write_bytes(raw)
    print(json.dumps(dict(matrix_sha256={f'{o}:{c}': MATRIX_SHA256[o,c] for o,c in keys},
                          coefficients=sum((o+1)**4 for o,c in keys), shared_quantization_bits=list(range(6,10)))))


if __name__ == '__main__':
    main()
