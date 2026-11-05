#!/usr/bin/env python3
"""Small 26B123 HOA acceptance and native error-explanation regression."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import caf_vectors
import hoa_dynamic_domains_vectors as vectors
from native_component import CURRENT_SHA256, identity, sha256_file, unchanged
from native_expectations import COOKIE_OPERATIONS
from validate import require, write_json

FIXTURES = Path(__file__).resolve().parents[1] / 'crates/apac-native/tests/data/hoa-native-boundary.json'


def controls():
    for row in json.loads(FIXTURES.read_text())['cases']:
        opts = vectors.options(row['internal_coefficients'], row['output_channels'],
                               dynamic=row['dynamic_selection'], profile=0, level=0)
        cookie = vectors.cookie(**opts)
        packets = [vectors.packet(c, **opts)[0] for c in [vectors.basis(opts, 0), {}, {}]]
        require(cookie.hex() == row['cookie_hex'], 'frozen cookie differs from independent writer')
        require([p.hex() for p in packets] == row['packets_hex'], 'frozen packet sequence differs')
        yield row, opts, cookie, packets


def validate_result(row, mode, returncode, result, output):
    rejected = row['dynamic_selection'] and row['internal_coefficients'] > 36
    if rejected:
        require(returncode == 1, 'expected native rejection')
        error = result['error']
        require(error['os_status'] == 560226676 and error['os_status_fourcc'] == '!dat',
                'native rejection status changed')
        if mode != 'file':
            require(error['operation'] == COOKIE_OPERATIONS[mode], 'native rejection phase changed')
        else:
            require(error['operation'].startswith(('ExtAudioFileSetProperty(', 'ExtAudioFileRead (')),
                    'native file rejection phase changed')
        require(f"{row['internal_coefficients']} internal coefficients" in error['message']
                and 'limit of 36' in error['message'] and '26B123' in error['message'],
                'qualified native explanation missing')
        require((output/'.incomplete.json').exists() and not (output/'pcm.json').exists(),
                'rejection did not preserve incomplete output')
    else:
        require(returncode == 0 and result.get('complete'), 'valid boundary control rejected')
        meta = json.loads((output/'pcm.json').read_text())
        raw = (output/'pcm.f32le').read_bytes()
        require(meta['frames'] == 3072 and meta['channels'] == row['output_channels']
                and meta['sample_rate'] == 48000 and meta['all_finite'], 'decoded PCM shape differs')
        require(len(raw) == 3072 * row['output_channels'] * 4
                and hashlib.sha256(raw).hexdigest() == meta['sha256'], 'decoded PCM integrity differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('refusing to overwrite report')
    component = identity()
    if component['component_sha256'] != CURRENT_SHA256:
        parser.error('this regression requires the qualified 26B123 component')
    binary = args.binary.resolve()
    report = dict(**component, binary_sha256=sha256_file(binary),
                  fixtures_sha256=sha256_file(FIXTURES), script_sha256=sha256_file(__file__),
                  code_baseline=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                  cases=[], errors=[], passed=False)
    for row, opts, cookie, packets in controls():
        for mode in ('default', 'drc-off', 'file'):
            record = dict(name=row['name'], mode=mode, passed=False,
                          cookie_sha256=hashlib.sha256(cookie).hexdigest(),
                          packets_sha256=hashlib.sha256(b''.join(packets)).hexdigest())
            report['cases'].append(record)
            try:
                with tempfile.TemporaryDirectory(prefix='apac-hoa-boundary-') as temp:
                    root = Path(temp)
                    output = root/'pcm'
                    if mode == 'file':
                        source = root/'control.caf'
                        source.write_bytes(caf_vectors.encode(cookie, packets,
                            channels=row['output_channels'], layout_tag=(190<<16)|row['output_channels'])[0])
                        command = [str(binary), 'decode', str(source), '--out', str(output), '--frames', '3072']
                    else:
                        vectors.bundle(root/'packets', packets, **opts)
                        command = [str(binary), 'replay', str(root/'packets'), '--out', str(output),
                                   '--frames', '3072', '--processing-policy', mode]
                    process = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    result = json.loads(process.stdout or process.stderr)
                    record.update(returncode=process.returncode, result=result)
                    validate_result(row, mode, process.returncode, result, output)
                    record['passed'] = True
            except Exception as error:
                record['error'] = str(error)
                report['errors'].append(dict(name=row['name'], mode=mode, error=str(error)))
    try:
        unchanged(component)
        require(sha256_file(binary) == report['binary_sha256'], 'executable changed during regression')
        require(sha256_file(FIXTURES) == report['fixtures_sha256'], 'fixtures changed during regression')
    except Exception as error:
        report['errors'].append(dict(error=str(error)))
    report['passed'] = len(report['cases']) == 18 and not report['errors']
    write_json(args.report, report)
    print(json.dumps(dict(passed=report['passed'], cases=len(report['cases']), errors=report['errors'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
