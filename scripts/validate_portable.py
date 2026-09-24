#!/usr/bin/env python3
"""Independent SQ mathematics and exact cross-platform stage fingerprints.

Without a reference, every PCM sample is checked against a Decimal direct-sum
oracle. --reference-report requires the same code and source fingerprint;
--regression-report allows an older revision with the same numerical profile
and constants. Both execute the complete matrix and require identical input,
integer, spectral, structural and PCM fingerprints from a successful math report.
No native reference, optional test skip, gain adjustment or implicit flush.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from generate_sq_math import PROFILE
from spectrum_vectors import band_cases, bundle, cookie, frame, matrix_cases
from sq_oracle import Decoder, scaled_channel
from validate import require, write_json
from validate_replay import command, sha256_file
from validate_spectra import check_expected, inspect, ulp
from validate_synthesis import batch_cases, sequences

ROOT = Path(__file__).resolve().parents[1]
COUNTS = dict(spectra=17800, pcm=9948)
LIMIT = 128 * 1024 * 1024


def source_digest():
    paths = [ROOT / p for p in ('Cargo.toml', 'Cargo.lock', 'build.rs')]
    paths += list((ROOT / 'src').rglob('*.rs')) + list((ROOT / 'scripts').glob('*.py'))
    paths += list((ROOT / 'data').glob('*.json'))
    digest = hashlib.sha256()
    # WindowsPath ordering folds case; sort portable relative strings instead.
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def bytes_of(values, kind):
    return struct.pack('<' + str(len(values)) + kind, *values)


def float32(values):
    data = bytes_of(values, 'f')
    result = struct.unpack('<' + str(len(values)) + 'f', data)
    require(all(math.isfinite(v) for v in result), 'nonfinite Float32 values')
    require(all(v != 0 or word == 0 for v, (word,) in zip(result, struct.iter_unpack('<I', data))),
            'negative zero in canonical output')
    return result


def spectra_fingerprints(channels):
    integers = [q for channel in channels for q in channel['quantized']]
    scaled = float32([x for channel in channels for x in channel['scaled']])
    structure = [{k: v for k, v in channel.items() if k not in ('quantized', 'scaled')}
                 for channel in channels]
    return dict(quantized_sha256=hashlib.sha256(bytes_of(integers, 'i')).hexdigest(),
                scaled_sha256=hashlib.sha256(bytes_of(scaled, 'f')).hexdigest(),
                structure_sha256=hashlib.sha256(json.dumps(structure, sort_keys=True,
                                                          separators=(',', ':')).encode()).hexdigest())


def metrics():
    return dict(max_absolute_error=0.0, max_ulp=0, coefficients=0)


def compare_pcm(actual, expected):
    require(len(actual) == len(expected) and len(actual) > 0, 'PCM length mismatch')
    maximum, maximum_ulp, failed, first = 0.0, 0, 0, None
    for i, (a, b) in enumerate(zip(actual, expected)):
        require(math.isfinite(a) and math.isfinite(b), 'nonfinite PCM')
        error = abs(a-b)
        maximum = max(maximum, error)
        if error:
            maximum_ulp = max(maximum_ulp, ulp(a, b))
        if error > 1e-6 + 1e-5 * abs(b):
            failed += 1
            if first is None:
                first = dict(sample=i, channel=i % 2, reference=b, candidate=a)
    return dict(passed=failed == 0, max_absolute_error=maximum, max_ulp=maximum_ulp,
                failed_samples=failed, first_failure=first)


def validate_reference(reference, current, regression=False):
    require(reference.get('passed') is True and reference.get('mode') == 'independent_math',
            'requires a successful independent mathematical reference report')
    require(reference.get('errors') == [] and reference.get('counts') == COUNTS
            and reference.get('pcm_metrics', {}).get('failed_samples') == 0,
            'reference completeness or mathematical metrics disagree')
    for key in ('schema_version', 'numeric_profile', 'code_commit', 'source_sha256', 'tables_sha256',
                'atol', 'rtol'):
        if regression and key in ('code_commit','source_sha256'):
            continue
        require(reference.get(key) == current[key], 'reference identity mismatch: ' + key)
    for stage, count in COUNTS.items():
        records = reference.get(stage, [])
        require(len(records) == count, 'incomplete reference ' + stage)
        require([(r['rate'], r['index']) for r in records]
                == [(rate, i) for rate in (48000, 44100) for i in range(count // 2)],
                'duplicate, reordered or missing reference cases: ' + stage)
        require(all(r.get('passed') is True for r in records), 'failed reference case')


def match_record(record, reference):
    for key in ('rate', 'index', 'kind', 'input_sha256', 'quantized_sha256', 'scaled_sha256',
                'structure_sha256', 'pcm_sha256', 'frames'):
        if key in record or key in reference:
            require(record.get(key) == reference.get(key), 'exact fingerprint mismatch: ' + key)


def check_spectra(binary, report, reference=None):
    for rate in (48000, 44100):
        first = 0
        for cases in batch_cases(itertools.chain(matrix_cases(), band_cases()), 96):
            try:
                with tempfile.TemporaryDirectory(prefix='sq-math-spectra-') as tmp:
                    root = Path(tmp)
                    generated = [frame(case) for case in cases]
                    bundle(root / 'packets', [p for p, _ in generated], rate)
                    _, rows = inspect(binary, root / 'packets', root, len(cases))
                    for i, (case, row, (payload, expected)) in enumerate(zip(cases, rows, generated)):
                        for channel in expected:
                            channel['scaled'] = scaled_channel(channel)
                        actual = row['report']
                        require(actual.get('numeric_profile') == PROFILE, 'spectrum numerical profile mismatch')
                        check_expected(actual, expected, report['spectral_metrics'])
                        require(bytes_of([v for c in actual['channels'] for v in c['scaled']], 'f')
                                == bytes_of([v for c in expected for v in c['scaled']], 'f'),
                                'spectrum differs from separately rounded mathematical value')
                        record = dict(rate=rate, index=first+i, kind=case['kind'], passed=True,
                                      input_sha256=hashlib.sha256(cookie(rate)+payload).hexdigest(),
                                      **spectra_fingerprints(actual['channels']))
                        if reference:
                            match_record(record, reference['spectra'][len(report['spectra'])])
                        report['spectra'].append(record)
            except Exception as error:
                report['errors'].append(dict(stage='spectra', rate=rate, first_case=first, error=str(error)))
            first += len(cases)
            if first % 960 == 0:
                print('portable spectra', rate, first, flush=True, file=sys.stderr)


def check_pcm(binary, report, reference=None):
    for rate in (48000, 44100):
        first = 0
        for batch in batch_cases(sequences(), 48):
            try:
                with tempfile.TemporaryDirectory(prefix='sq-math-pcm-') as tmp:
                    root = Path(tmp)
                    generated = [frame(case) for _, sequence in batch for case in sequence]
                    payloads = [p for p, _ in generated]
                    bundle(root / 'packets', payloads, rate)
                    _, rows = inspect(binary, root / 'packets', root, len(payloads))
                    for row, (_, truth) in zip(rows, generated):
                        require(row['report'].get('numeric_profile') == PROFILE, 'PCM spectrum profile mismatch')
                        for channel in truth:
                            channel['scaled'] = scaled_channel(channel)
                        check_expected(row['report'], truth, report['spectral_metrics'])
                    result = command(binary, 'decode-sq', root / 'packets', '--out', root / 'pcm')
                    require(result['complete'] and result['numeric_profile'] == PROFILE
                            and result['experimental'] and not result['native_apis_used'], 'candidate identity mismatch')
                    require(result['numerical_qualification'] == 'independent_math_reference', 'wrong qualification')
                    metadata = json.loads((root / 'pcm/pcm.json').read_text(encoding="utf-8"))
                    implementation = metadata['decoder_settings']['implementation']['value']
                    require(implementation['tables_sha256'] == report['constant_model_sha256'],
                            'candidate constant fingerprint mismatch')
                    if report['implementation'] is None:
                        report['implementation'] = implementation
                    require(report['implementation'] == implementation, 'candidate implementation changed')
                    require(metadata['frames'] == len(payloads)*1024 and metadata['channels'] == 2
                            and metadata['sample_rate'] == rate and metadata['all_finite'], 'PCM shape mismatch')
                    data = (root / 'pcm/pcm.f32le').read_bytes()
                    require(len(data) == len(payloads)*8192 and hashlib.sha256(data).hexdigest() == metadata['sha256'],
                            'PCM bytes or checksum mismatch')
                    cursor = 0
                    for i, (kind, sequence) in enumerate(batch):
                        length = len(sequence)
                        channels = [c for row in rows[cursor:cursor+length] for c in row['report']['channels']]
                        raw = data[cursor*8192:(cursor+length)*8192]
                        actual = float32(struct.unpack('<'+str(len(raw)//4)+'f', raw))
                        identity = hashlib.sha256(cookie(rate))
                        for payload in payloads[cursor:cursor+length]:
                            identity.update(len(payload).to_bytes(8, 'little'))
                            identity.update(payload)
                        record = dict(rate=rate, index=first+i, kind=kind, frames=length*1024, passed=True,
                                      input_sha256=identity.hexdigest(), pcm_sha256=hashlib.sha256(raw).hexdigest(),
                                      **spectra_fingerprints(channels))
                        if reference:
                            match_record(record, reference['pcm'][len(report['pcm'])])
                        else:
                            decoder = Decoder()
                            expected = [v for _, truth in generated[cursor:cursor+length] for v in decoder.decode(truth)]
                            record.update(compare_pcm(actual, expected))
                        report['pcm'].append(record)
                        cursor += length
            except Exception as error:
                report['errors'].append(dict(stage='pcm', rate=rate, first_case=first, error=str(error)))
            first += len(batch)
            if first % 480 == 0:
                print('portable PCM', rate, first, flush=True, file=sys.stderr)


def finalize(report):
    for stage, count in COUNTS.items():
        if len(report[stage]) != count:
            report['errors'].append(dict(stage=stage, error='required case count mismatch',
                                         required=count, actual=len(report[stage])))
    report['passed'] = not report['errors'] and all(r['passed'] for stage in COUNTS for r in report[stage])
    report['counts'] = {stage: len(report[stage]) for stage in COUNTS}
    report['pcm_metrics'] = dict(max_absolute_error=max((r.get('max_absolute_error', 0.) for r in report['pcm']), default=0.),
                                 max_ulp=max((r.get('max_ulp', 0) for r in report['pcm']), default=0),
                                 failed_samples=sum(r.get('failed_samples', 0) for r in report['pcm'])) if report['mode'] == 'independent_math' else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    references=parser.add_mutually_exclusive_group()
    references.add_argument('--reference-report', type=Path)
    references.add_argument('--regression-report', type=Path,
                            help='Compare every stage with an older successful math report of the same numerical profile/constants.')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('refusing to overwrite report')
    binary = args.binary.resolve(strict=True)
    reference_path=args.reference_report or args.regression_report
    report = dict(schema_version=1, numeric_profile=PROFILE, mode='bit_exact_regression' if args.regression_report else 'bit_exact_replay' if args.reference_report else 'independent_math',
                  code_commit=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, encoding="utf-8").strip(),
                  tested_worktree_dirty=bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True, encoding="utf-8").strip()),
                  source_sha256=source_digest(), tables_sha256=sha256_file(ROOT / 'data/sq-math-v1.json'),
                  constant_model_sha256=json.loads((ROOT / 'data/sq-math-v1.json').read_text(encoding="utf-8"))['tables_sha256'],
                  tool_sha256=sha256_file(binary), platform=platform.platform(), architecture=platform.machine(),
                  python=sys.version, started_utc=datetime.now(timezone.utc).isoformat(), atol=1e-6, rtol=1e-5,
                  implementation=None, spectra=[], pcm=[], errors=[], spectral_metrics=metrics())
    reference = None
    try:
        if reference_path:
            require(reference_path.stat().st_size <= LIMIT, 'reference report exceeds 128 MiB')
            report['reference_report_sha256'] = sha256_file(reference_path)
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
            report['reference_code_commit']=reference['code_commit']
            validate_reference(reference, report, regression=bool(args.regression_report))
        check_spectra(binary, report, reference)
        check_pcm(binary, report, reference)
        require(sha256_file(binary) == report['tool_sha256'], 'candidate executable changed during validation')
        require(source_digest() == report['source_sha256'], 'sources changed during validation')
        if reference_path:
            require(sha256_file(reference_path) == report['reference_report_sha256'], 'reference report changed')
    except Exception as error:
        report['errors'].append(dict(stage='validation', error=str(error)))
    finalize(report)
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode()) < LIMIT, 'report exceeds 128 MiB')
    write_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('passed', 'counts', 'pcm_metrics', 'errors')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
