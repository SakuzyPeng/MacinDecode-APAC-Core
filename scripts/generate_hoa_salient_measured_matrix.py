#!/usr/bin/env python3
"""Generate the shared order-3 mode-4 cluster-0 matrix from a qualified candidate.

Only the frozen, validated weighted reanalysis is accepted by --candidate.
The public source contains exact Float32 words and provenance, without PCM,
estimated values or local evidence paths. --write regenerates the packed
matrix and the four dictionary provenance records; --check verifies them.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from hoa_packed_tables import pack_matrix
from hoa_salient_format import DATA, check_digest, expand_format, format_name, json_bytes, shared_name, split_format

MEASURED_FILE = 'hoa-salient-order3-mode4-cluster0-matrix-measured-v1.json'
PROFILE = 'apac-hoa-salient-measured-matrix-v1'
CANDIDATE_SHA256 = '3e1fa1a640d1fa68c270ed4b9aadd59d544a153cdccef4b74bb0fba6bb722899'
MATRIX_SHA256 = '87a5fbe1a977b1312d8d1093425ee3217d87ad4dfc77a7855b0290e8b9861c19'
SOURCE = dict(
    method='public AudioConverter PCM reconstruction with calibration-weighted reanalysis',
    experiment='hoa-blackbox-order3-q6-mode4-cluster0-v1',
    measurement_code_commit='f4110ca0eb9383c534479026f8f2b6f9656ca646',
    analysis_code_commit='cabcb5523c9175cdb363081f212a7712fa18fb44',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    measurement_quantization_bits=6,
    candidate_sha256=CANDIDATE_SHA256,
    policy_sha256='143a3deeacda5577c88cf52d1788c0b0f5613644abacb8cfa7406b085091d325',
    validation_sha256='73a8a9864aae304f12a41d842758b05c0aa51eebc08d674df33cf6891325aa69',
    comparison_sha256='4cf995645d994b078123949eef08aa9fc833c76f48c20c497b39327979d13172',
    max_empirical_half_width=2.3251493876046068e-7,
    empirical_half_width_limit=2.5e-7,
    held_out_checks=254,
    candidate_frozen_before_comparison=True,
    new_native_acquisition_in_reanalysis=False,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measured_matrix(value):
    require(value.get('schema_version') == 1 and value.get('profile') == PROFILE,
            'incompatible measured matrix schema')
    require(tuple(value.get(k) for k in ('order', 'mode', 'cluster', 'rows', 'columns')) == (3, 4, 0, 16, 16)
            and value.get('storage') == 'row-major', 'measured matrix scope differs')
    require(value.get('source') == SOURCE, 'measured matrix source differs')
    words = value.get('matrix_f32')
    require(isinstance(words, list) and len(words) == 256, 'measured matrix needs 256 words')
    pack_matrix(words)  # Checks unsigned words, finiteness and exact micro21 storage.
    digest = hashlib.sha256(json.dumps(words, separators=(',', ':')).encode()).hexdigest()
    require(digest == value.get('matrix_sha256') == MATRIX_SHA256, 'measured matrix digest differs')
    return words


def from_candidate(raw):
    require(hashlib.sha256(raw).hexdigest() == CANDIDATE_SHA256, 'unverified qualified matrix candidate')
    candidate = json.loads(raw)
    require((candidate['order'], candidate['quantization_bits'], candidate['mode'], candidate['cluster']) == (3, 6, 4, 0),
            'candidate scope differs')
    require(candidate['old_matrix_consulted'] is False and candidate['original_matrix_candidate_used'] is False,
            'candidate used an old matrix')
    require(candidate['policy_sha256'] == SOURCE['policy_sha256'], 'candidate calibration policy differs')
    require(len(candidate['entries']) == 256, 'candidate matrix dimensions differ')
    words = []
    for index, entry in enumerate(candidate['entries']):
        require((entry['row'], entry['column']) == divmod(index, 16), 'candidate row order differs')
        word = entry['float32_bits']
        require(word is not None and entry['candidate_bits'] == [word]
                and entry['empirical_half_width'] < SOURCE['empirical_half_width_limit'],
                'candidate coefficient remains unqualified')
        words.append(word)
    result = dict(schema_version=1, profile=PROFILE, order=3, mode=4, cluster=0, rows=16, columns=16,
                  storage='row-major', source=copy.deepcopy(SOURCE), matrix_sha256=MATRIX_SHA256, matrix_f32=words)
    measured_matrix(result)
    return result


def load_measurement():
    value = json.loads((DATA / MEASURED_FILE).read_text())
    measured_matrix(value)
    return value


def replace_matrix(value, measurement):
    require(value['order'] == 3 and value['quantization_bits'] in range(6, 10), 'matrix replacement scope differs')
    result = copy.deepcopy(value)
    result['modes'][4]['matrices_f32'][0] = measured_matrix(measurement)
    source = result['source']
    if 'original_observation' not in source:
        source = dict(method='', original_observation=source, remaining_tables='original_observation')
    replacements = [item for item in source.get('matrix_replacements', [])
                    if (item['mode'], item['cluster']) != (4, 0)]
    replacements.append(dict(mode=4, cluster=0, source_file=MEASURED_FILE,
                             source_sha256=hashlib.sha256(json_bytes(measurement)).hexdigest(), method=SOURCE['method']))
    source.update(method='mixed sources with per-table replacements',
                  matrix_replacements=sorted(replacements, key=lambda item: (item['mode'], item['cluster'])))
    result['source'] = source
    check_digest(result)
    return result


def regenerate(stored_variants, shared, measurement):
    require(set(stored_variants) == set(range(6, 10)), 'all four shared-matrix variants are required')
    require(shared['schema_version'] == 2 and shared['order'] == 3, 'shared matrix scope differs')
    shared = copy.deepcopy(shared)
    target = shared['modes'][4]['matrix_indices'][0]
    require(type(target) is int and 0 <= target < len(shared['matrices_f32']), 'invalid target matrix index')
    users = [(mode['mode'], cluster) for mode in shared['modes']
             for cluster, index in enumerate(mode['matrix_indices']) if index == target]
    require(users == [(4, 0)], 'target matrix is aliased by another cluster or mode')
    # The measured source can repair its own packed copy. All other words must
    # still satisfy every published expanded dictionary digest.
    shared['matrices_f32'][target] = pack_matrix(measured_matrix(measurement))
    outputs = {}; common = None
    for precision, stored in sorted(stored_variants.items()):
        require((stored['order'], stored['quantization_bits']) == (3, precision), 'dictionary scope differs')
        expanded = replace_matrix(expand_format(stored, shared), measurement)
        packed, rebuilt = split_format(expanded)
        if common is not None:
            require(rebuilt == common, 'quantization-dependent shared matrix data')
        common = rebuilt
        outputs[precision] = packed
    return outputs, common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, help='qualified frozen matrix candidate from weighted reanalysis')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    measurement = from_candidate(args.candidate.read_bytes()) if args.candidate else load_measurement()
    stored = {q: json.loads((DATA / format_name(3, q)).read_text()) for q in range(6, 10)}
    shared_path = DATA / shared_name(3)
    packed, shared = regenerate(stored, json.loads(shared_path.read_text()), measurement)
    outputs = {DATA / MEASURED_FILE: json_bytes(measurement), shared_path: json_bytes(shared)}
    outputs.update({DATA / format_name(3, q): json_bytes(value) for q, value in packed.items()})
    for path, raw in outputs.items():
        if args.check:
            require(path.read_bytes() == raw, 'generated matrix or provenance differs: ' + path.name)
        else:
            path.write_bytes(raw)
    print(json.dumps(dict(matrix_sha256=MATRIX_SHA256, coefficients=256, shared_quantization_bits=list(packed))))


if __name__ == '__main__':
    main()
