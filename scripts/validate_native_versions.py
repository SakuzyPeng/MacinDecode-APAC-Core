#!/usr/bin/env python3
"""Check the exact versioned acceptance of frozen synthetic HOA inputs."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from hoa_dynamic_domains_vectors import bundle
from native_expectations import cases, collect, PACKET_SHA256
from validate import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('refusing to overwrite report')
    report = dict(code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  cases=[], errors=[], passed=False)
    for name, options, payloads in cases():
        try:
            with tempfile.TemporaryDirectory(prefix='apac-native-version-') as temporary:
                path = Path(temporary) / 'packets'
                bundle(path, payloads, **options)
                result = collect(args.binary.resolve(), path)
                if result['packet_data_sha256'] != PACKET_SHA256[name]:
                    raise ValueError('generated versioned input identity changed')
                report['cases'].append(dict(name=name, **result))
        except Exception as error:
            report['errors'].append(dict(name=name, error=str(error)))
    report['passed'] = len(report['cases']) == 2 and not report['errors']
    write_json(args.report, report)
    print(json.dumps(dict(passed=report['passed'], cases=len(report['cases']), errors=report['errors'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
