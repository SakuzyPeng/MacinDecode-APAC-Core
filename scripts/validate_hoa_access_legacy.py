#!/usr/bin/env python3
"""Frozen discrete access representatives after extending the private HOA scanner."""
import argparse,json,subprocess
from itertools import islice
from pathlib import Path
from validate_access import validate
from access_vectors import cases
from validate import require,write_json
from validate_portable import source_digest
from validate_replay import sha256_file
from caf_vectors import digest
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--reference-report',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
require(a.binary.is_file() and not a.report.exists(),'binary missing/report exists');old=json.loads(a.reference_report.read_text());require(old['passed'],'failed frozen reference')
report=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),reference_sha256=sha256_file(a.reference_report),implementations={},cases=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
try:
    validate(a.binary.resolve(),report,old,layouts=(2,8),cases_fn=lambda n:list(islice(cases(n),3)))
    require(len(report['cases'])==12,'missing discrete representatives')
    for key,value in report['implementations'].items():require(value==old['implementations'][key],'legacy implementation changed')
    require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
except Exception as e:report['errors'].append(str(e))
report['stage_sha256']=digest(report['cases']);write_json(a.report,report);print(json.dumps(dict(passed=report['passed'],errors=report['errors'])));raise SystemExit(0 if report['passed'] else 1)
