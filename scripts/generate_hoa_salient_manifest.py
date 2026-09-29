#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from hoa_salient_vectors import manifest,state_fixture
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args();root=Path(__file__).resolve().parents[1]/'data'
for name,value in [('hoa-salient-vectors-v1.json',manifest()),('hoa-salient-state-v1.json',state_fixture())]:
    path=root/name
    if a.check:assert json.loads(path.read_text())==value,name+' differs'
    else:
        with path.open('x') as f:f.write(json.dumps(value,indent=2)+'\n')
    print(name,len(value.get('cases',[])),value.get('sha256',''))
