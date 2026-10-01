#!/usr/bin/env python3
"""Freeze/check only the mixed descriptor-order vectors and bounded state truth."""
import argparse,json
from pathlib import Path
from hoa_component_orders_vectors import manifest,state_fixtures
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
root=Path(__file__).resolve().parents[1]/'data'
for name,value in [('hoa-component-orders-vectors-v1.json',manifest()),('hoa-component-orders-state-v1.json',state_fixtures())]:
    path=root/name
    if a.check:assert json.loads(path.read_text())==value,name+' differs'
    else:
        with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(name,len(value.get('cases',[])),value.get('sha256',''))
