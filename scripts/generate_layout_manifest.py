#!/usr/bin/env python3
"""Freeze independent math, access, exhaustive presence and transaction inputs."""
import argparse,json
from pathlib import Path
from layout_vectors import math_manifest,access_manifest,presence_manifest,state_fixtures,LAYOUTS
from validate_layouts_native import probe_manifest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args();root=Path(__file__).resolve().parents[1]/'data'
    for name,value in [('layout-vectors-v1.json',math_manifest()),('layout-access-vectors-v1.json',access_manifest()),('layout-presence-v1.json',presence_manifest()),('layout-state-fixtures-v1.json',state_fixtures(LAYOUTS)),('layout-native-vectors-v1.json',probe_manifest())]:
        path=root/name
        if a.check:
            if json.loads(path.read_text(encoding='utf-8'))!=value:raise SystemExit(name+' differs')
        else:path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
        print(name,len(value.get('sequences',value.get('cases',[]))) if isinstance(value.get('sequences',value.get('cases',[])),list) else value['cases'],value.get('sha256',''),flush=True)
if __name__=='__main__':main()
