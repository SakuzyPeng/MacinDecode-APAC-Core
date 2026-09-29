#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from hoa_vectors import manifest,state_fixture
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args();root=Path(__file__).resolve().parents[1]/'data'
for name,value in [('hoa-ambient-vectors-v1.json',manifest()),('hoa-ambient-state-v1.json',state_fixture())]:
 path=root/name
 if a.check:
  if json.loads(path.read_text())!=value:raise SystemExit(name+' differs')
 else:path.write_text(json.dumps(value,indent=2)+'\n')
 print(name,len(value.get('cases',[])),value.get('sha256',''))
