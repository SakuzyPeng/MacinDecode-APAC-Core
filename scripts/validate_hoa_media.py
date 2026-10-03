#!/usr/bin/env python3
"""Stream one DRC and one non-DRC HOA source; retain digests, not full-song PCM.

Build the release library test executable with cargo test -p apac-research --lib --release
--no-run --message-format=json, then pass its compiler-artifact executable.
The script runs only the explicitly ignored HOA streaming test.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from validate import require, write_json
from validate_drc import workspace
from validate_portable import ROOT, source_digest
from validate_replay import sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-binary', type=Path, required=True)
    parser.add_argument('--with-drc', type=Path, required=True)
    parser.add_argument('--without-drc', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binary = args.test_binary.resolve()
    require(binary.is_file() and not args.report.exists(), 'missing binary or report exists')
    require(args.with_drc.resolve() != args.without_drc.resolve(), 'two distinct classes required')
    report = dict(
        passed=False, profile='apac-hoa-media-validation-v1',
        created_at=datetime.now(timezone.utc).isoformat(),
        code_commit=subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_sha256=source_digest(), binary_sha256=sha256_file(binary),
        media=[], errors=[], failure_directory=str(args.report.with_suffix('.failures')),
    )
    try:
        for kind, source in [('with_drc', args.with_drc), ('without_drc', args.without_drc)]:
            source = source.resolve()
            fingerprint = sha256_file(source)
            with workspace(report, kind) as root:
                result = root / 'digest.json'
                env = dict(os.environ, APAC_HOA_MEDIA_INPUT=str(source),
                           APAC_HOA_MEDIA_REPORT=str(result))
                begin = time.monotonic()
                proc = subprocess.run(
                    [str(binary), 'decode::hoa_media_tests::hoa_media_stream_digest',
                     '--exact', '--ignored', '--nocapture'],
                    env=env, capture_output=True, text=True, encoding='utf-8',
                )
                (root / 'test.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
                require(proc.returncode == 0, kind + ': streaming decoder failed')
                require('1 passed' in proc.stdout, 'required streaming test did not run')
                data = json.loads(result.read_text(encoding='utf-8'))
                require(data['passed'] and data['channels'] == 16, 'incomplete HOA stream')
                require(data['input']['consistency_verified'], 'input was not fully verified')
                require(not data['debug_assertions'], 'real-media check requires release build')
                require((data['drc_payload_frames'] > 0) == (kind == 'with_drc'),
                        'source does not belong to the requested DRC class')
                require(sha256_file(source) == fingerprint, 'source file changed')
                report['media'].append(dict(kind=kind, source=str(source),
                                          input_sha256=fingerprint,
                                          seconds=time.monotonic() - begin, **data))
            print(kind, data['packets'], data['pcm_sha256'], flush=True)
        require(source_digest() == report['source_sha256'] and
                sha256_file(binary) == report['binary_sha256'], 'source/binary changed')
        report['passed'] = True
    except Exception as error:
        report['errors'].append(str(error))
    write_json(args.report, report)
    print(json.dumps({key: report[key] for key in ('passed', 'errors')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
