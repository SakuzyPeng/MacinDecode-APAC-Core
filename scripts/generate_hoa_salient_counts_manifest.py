#!/usr/bin/env python3
"""Freeze/check bounded salient-count vectors and state boundaries."""
import argparse,json
from pathlib import Path
from hoa_salient_counts_vectors import manifest,state_fixtures
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
root=Path(__file__).resolve().parents[1]/'data'
for name,value in [('hoa-salient-counts-vectors-v1.json',manifest()),('hoa-salient-counts-state-v1.json',state_fixtures())]:
    path=root/name
    if a.check:assert json.loads(path.read_text())==value,name+' differs'
    else:
        with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(name,len(value.get('cases',[])),value.get('sha256',''))
