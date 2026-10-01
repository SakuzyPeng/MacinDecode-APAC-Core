#!/usr/bin/env python3
"""Freeze spatial controls and independently verify immutable dictionary/grid data."""
import argparse,hashlib,json,struct
from pathlib import Path
from hoa_controls_vectors import manifest,state_fixtures
from generate_hoa_dynamic_subbands_format import boundaries

def check_format():
    p=Path(__file__).resolve().parents[1]/'data/hoa-spatial-controls-format-v1.json';v=json.loads(p.read_text())
    assert len(v['mean_coefficients_f32'])==121
    assert hashlib.sha256(struct.pack('<121I',*v['mean_coefficients_f32'])).hexdigest()==v['mean_sha256']=='299576ce0a06ba6165b2a7ee1485d7211f9bed94b7e5936ce7ec51c5b4f30c42'
    assert v['tables']==[dict(method=m,subbands=n,long_ends=boundaries(n,m,False)) for m in range(3) for n in range(1,17)]
    values={k:v[k] for k in ('mean_coefficients_f32','mean_sha256','tables')}
    assert hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()==v['format_sha256']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args();check_format()
    for name,generate in [('hoa-controls-vectors-v2.json',manifest),('hoa-controls-state-v2.json',state_fixtures)]:
        p=Path(__file__).resolve().parents[1]/'data'/name;value=generate()
        if args.check:assert json.loads(p.read_text())==value,name+' changed'
        else:p.write_text(json.dumps(value,indent=2)+'\n')
        print(name,len(value.get('cases',value.get('fixtures',[]))),value.get('sha256',''))
