#!/usr/bin/env python3
"""Freeze independent explicit-dimension vectors and state fixtures."""
import argparse,json
from pathlib import Path
from hoa_partial_vectors import manifest,state_fixtures

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,generate in [('hoa-partial-vectors-v1.json',manifest),('hoa-partial-state-v1.json',state_fixtures)]:
        p=Path(__file__).resolve().parents[1]/'data'/name;value=generate();text=json.dumps(value,indent=2)+'\n'
        if args.check:assert json.loads(p.read_text())==value,name+' changed'
        else:p.write_text(text)
        print(name,len(value.get('cases',value.get('boundaries',[]))),value.get('sha256',''))
