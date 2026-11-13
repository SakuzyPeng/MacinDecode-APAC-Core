"""Run one catalogued validator in a fresh, explicitly qualified CI context."""
import argparse
import json
from pathlib import Path
import runpy
import sys

import ci_support
from ci_catalog import ROOT, load
from ci_evidence import envelope
from validate import require
from validate_portable import source_digest
from validate_replay import sha256_file


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--validator', required=True)
    p.add_argument('--profile', choices=('ci-fast', 'ci-full'), required=True)
    p.add_argument('--mode', choices=('math', 'collect'), required=True)
    p.add_argument('--build-info', type=Path, required=True)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--presence-binary', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    from run_portable_suite import SUITE
    entry = load()['profiles'][a.profile][a.validator]
    build = json.loads(a.build_info.read_text(encoding='utf-8'))
    require(source_digest() == build['source_sha256'] and sha256_file(a.binary) == build['binary_sha256'], 'source/binary differs from build evidence')
    require(not a.out.exists(), 'output already exists')
    a.out.mkdir(parents=True)
    report_path = a.out / 'report.json'
    ci_support.CONTEXT = dict(entry, mode=a.mode, validator=a.validator)
    flag, extra = SUITE[a.validator]
    sys.argv = [a.validator, '--binary', str(a.binary.resolve()), flag, str(report_path.resolve())]
    if 'presence' in extra:
        require(a.presence_binary is not None, 'missing layout_presence executable')
        sys.argv += ['--presence-binary', str(a.presence_binary.resolve())]
    for name in entry.get('cases', []):
        sys.argv += ['--case', name]
    # validate_portable was imported for source_digest above; the executed script
    # receives the configured context while imported numerical helpers are unchanged.
    status = 0
    try:
        runpy.run_path(str(ROOT / 'scripts' / (a.validator + '.py')), run_name='__main__')
    except SystemExit as error:
        status = error.code or 0
    finally:
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['mode'] = 'independent_math' if a.mode == 'math' else 'fingerprint_collection'
            report['ci_profile'] = a.profile
            report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    require(status == 0, 'validator failed with exit ' + str(status))
    require(source_digest() == build['source_sha256'] and sha256_file(a.binary) == build['binary_sha256'], 'source/binary changed during validation')
    evidence = envelope(report, a.profile, a.validator, a.mode, build)
    (a.out / 'evidence.json').write_text(json.dumps(evidence, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
