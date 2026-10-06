#!/usr/bin/env python3
"""Package qualified HOA means and reproduce their format copy and provenance."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct

DATA = Path(__file__).resolve().parents[1]/'data'
MEASURED_FILE = 'hoa-spatial-means-measured-v1.json'
FORMAT_FILE = 'hoa-spatial-controls-format-v1.json'
PROFILE = 'apac-hoa-spatial-means-measured-v1'
WORDS_SHA256 = '299576ce0a06ba6165b2a7ee1485d7211f9bed94b7e5936ce7ec51c5b4f30c42'
CANDIDATE_SHA256 = '57c0e3e9e07aed26370b7f3448c2387937830cf7896658853a85aafe36c03645'
VALIDATION_SHA256 = 'dfe70996f8a5fd0704e361edfcd1b6a36a78ca82873f017c2da777aa894bdc2f'
COMPARISON_SHA256 = '4048a63f3509691d9f4a2253fe5f2fbe4ae92707d78e196114db0b0cb2eea053'
CALIBRATION_SHA256 = '9c8c955a60dd749cfda5ea888624ac7549f0046eeba5e88f369545c2f7f418b7'
SOURCE = dict(
    method='public AudioConverter PCM black-box reconstruction by exact dyadic cancellation',
    experiment='hoa-spatial-means-blackbox-v1',
    measurement_code_commit='8de23429ad2d45a07814b09805623ddec5c887dc',
    measurement_used_uncommitted_tool_extension=True,
    tool_fingerprint='eabb7e77099da23ea7660da46864b1ab58d13a73d9dca708ff7461a608ca4337',
    policy='hoa-spatial-means-dyadic-cancellation-v1',
    binary_sha256='8878d0c03c0889a70c7d352ce4a084e0f495478239f6c40c58cb3663ee302500',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428', architecture='arm64',
    sample_rate=48000, measurement_order=10, measurement_quantization_bits=9,
    calibration_sha256=CALIBRATION_SHA256, candidate_sha256=CANDIDATE_SHA256,
    validation_sha256=VALIDATION_SHA256, comparison_sha256=COMPARISON_SHA256,
    candidate_frozen_before_validation=True, comparison_after_validation=True,
    old_values_used_in_discovery=False, decimal_grid_assumed=False,
    independent_validation_channel_checks=963, native_calls=24, unresolved_entries=0,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode()


def measured_words(measurement):
    require(measurement.get('schema_version') == 1 and measurement.get('profile') == PROFILE,
            'incompatible measured means schema')
    require(measurement.get('count') == 121 and measurement.get('storage') == 'acn-order-float32-bits',
            'measured means scope differs')
    require(measurement.get('source') == SOURCE, 'measured means source differs')
    words = measurement.get('mean_coefficients_f32')
    require(isinstance(words, list) and len(words) == 121, 'measured means need 121 words')
    require(all(type(w) is int and 0 <= w <= 0xffffffff for w in words), 'invalid Float32 word')
    require(all(math.isfinite(struct.unpack('<f', struct.pack('<I', w))[0]) for w in words), 'nonfinite mean')
    require(sha(struct.pack('<121I', *words)) == measurement.get('mean_sha256') == WORDS_SHA256,
            'measured means digest differs')
    return words


def from_evidence(candidate_raw, validation_raw, comparison_raw):
    require(sha(candidate_raw) == CANDIDATE_SHA256, 'unverified frozen mean candidate')
    require(sha(validation_raw) == VALIDATION_SHA256, 'unverified mean validation')
    require(sha(comparison_raw) == COMPARISON_SHA256, 'unverified mean comparison')
    candidate, validation, comparison = map(json.loads, (candidate_raw, validation_raw, comparison_raw))
    require(candidate['profile'] == SOURCE['policy'] and candidate['old_values_consulted'] is False
            and candidate['grid_assumed'] is False and candidate['calibration_sha256'] == CALIBRATION_SHA256,
            'candidate provenance differs')
    require(validation['candidate_sha256'] == CANDIDATE_SHA256 and validation['status'] == 'passed'
            and validation['qualified'] == 121 and validation['old_values_consulted'] is False,
            'candidate validation is incomplete')
    require(comparison['candidate_sha256'] == CANDIDATE_SHA256
            and comparison['validation_sha256'] == VALIDATION_SHA256
            and comparison['eligible'] is True and comparison['matched'] == 121
            and comparison['differences'] == comparison['unresolved'] == [], 'candidate comparison is incomplete')
    entries = candidate['entries']
    require(len(entries) == 121, 'candidate dimensions differ')
    for j, entry in enumerate(entries):
        require(entry['channel'] == j and entry['float32_bits'] is not None
                and entry['candidate_bits'] == [entry['float32_bits']]
                and entry['zero_sign_ambiguous'] is False, 'candidate mean remains ambiguous')
    measurement = dict(schema_version=1, profile=PROFILE, count=121, storage='acn-order-float32-bits',
        source=copy.deepcopy(SOURCE), mean_sha256=WORDS_SHA256,
        mean_coefficients_f32=[e['float32_bits'] for e in entries])
    measured_words(measurement)
    return measurement


def load_measurement():
    result = json.loads((DATA/MEASURED_FILE).read_bytes())
    measured_words(result)
    return result


def regenerate(stored, measurement):
    require(stored.get('schema_version') == 1
            and stored.get('format_profile') == 'apac-hoa-spatial-controls-format-v1', 'format scope differs')
    result = copy.deepcopy(stored)
    result['mean_coefficients_f32'] = list(measured_words(measurement))
    result['mean_sha256'] = WORDS_SHA256
    semantic = {key:result[key] for key in ('mean_coefficients_f32', 'mean_sha256', 'tables')}
    require(sha(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode()) == result['format_sha256'],
            'format semantic digest differs outside measured means')
    source = result['source']
    if 'original_observation' not in source:
        source = dict(original_observation=source)
    source.update(method='mixed sources with measured mean replacement', remaining_tables='original_observation',
        mean_replacement=dict(source_file=MEASURED_FILE, source_sha256=sha(json_bytes(measurement)),
                              mean_sha256=WORDS_SHA256, method=SOURCE['method']))
    result['source'] = source
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--validation', type=Path)
    parser.add_argument('--comparison', type=Path)
    args = parser.parse_args()
    inputs = (args.candidate, args.validation, args.comparison)
    if any(p is not None for p in inputs) and not all(p is not None for p in inputs):
        parser.error('--candidate, --validation and --comparison must be supplied together')
    measurement = from_evidence(*(p.read_bytes() for p in inputs)) if args.candidate else load_measurement()
    stored = json.loads((DATA/FORMAT_FILE).read_bytes())
    for name, value in ((MEASURED_FILE, measurement), (FORMAT_FILE, regenerate(stored, measurement))):
        raw = json_bytes(value)
        if args.check:
            require((DATA/name).read_bytes() == raw, name+' differs from the measured source')
        else:
            (DATA/name).write_bytes(raw)
        print(name, sha(raw))


if __name__ == '__main__':
    main()
