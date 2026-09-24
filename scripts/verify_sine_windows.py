#!/usr/bin/env python3
"""Read-only verification of the versioned Float32 sine-window compatibility data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from native_frame_trace import trace_bundle
from spectrum_vectors import bundle, frame
from validate import require, write_json
from validate_replay import sha256_file


def float_bytes(values):
    require(all(0 < x < 1 for x in values), 'invalid sine-window values')
    return struct.pack('<'+'f'*len(values), *values)


def check_windows(expected, trace):
    require(not trace['errors'] and trace['process_exit_code'] == 0, 'native window trace failed')
    require(trace['component_sha256'] == expected['reference_component_sha256'], 'reference component differs')
    hashes = {}
    for name, count in [('long',1024),('short',128)]:
        actual=trace['windows'][name]
        require(len(actual)==len(expected[name])==count, 'window length differs')
        raw=float_bytes(actual)
        require(raw==float_bytes(expected[name]), 'window coefficient bits differ: '+name)
        hashes[name]=hashlib.sha256(raw).hexdigest()
    return hashes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=Path('target/debug/apac-tool'))
    p.add_argument('--tables',type=Path,default=Path(__file__).resolve().parents[1]/'data/sq-sine-windows.json')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if sys.platform!='darwin':p.error('read-only native snapshots require macOS')
    if args.output.exists():p.error('refusing to overwrite report')
    expected=json.loads(args.tables.read_text())
    report=dict(schema_version=1,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        numeric_profile=expected['numeric_profile'],reference_component_sha256=expected['reference_component_sha256'],
        table_sha256=sha256_file(args.tables),tool_sha256=sha256_file(args.binary),cases=[])
    for rate in [48000,44100]:
        record=dict(rate=rate,passed=False);report['cases'].append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='apac-sine-window-') as tmp:
                root=Path(tmp)
                bundle(root/'packets',[frame({})[0]]*4,rate)
                trace=trace_bundle(args.binary.resolve(),root/'packets',root,windows=True)
                record['coefficient_sha256']=check_windows(expected,trace)
                record['coefficients']=1152;record['passed']=True
        except Exception as e:record['error']=str(e)
    report.update(passed=all(c['passed'] for c in report['cases']),finished_utc=datetime.now(timezone.utc).isoformat())
    write_json(args.output,report)
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
