#!/usr/bin/env python3
"""Freeze actual-domain inputs, mapping widths and transactional fixtures."""
import argparse,json
from pathlib import Path
from hoa_dynamic_domains_vectors import manifest,state_fixtures
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    for name,fn in [('hoa-dynamic-domains-vectors-v1.json',manifest),('hoa-dynamic-domains-state-v1.json',state_fixtures)]:
        path=Path(__file__).resolve().parents[1]/'data'/name;v=fn()
        if a.check:assert json.loads(path.read_text())==v,name+' changed'
        else:path.write_text(json.dumps(v,indent=2)+'\n')
        print(name,len(v.get('cases',v.get('mapping',[]))),v.get('sha256',''))
