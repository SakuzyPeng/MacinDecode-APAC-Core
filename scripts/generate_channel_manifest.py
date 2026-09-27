#!/usr/bin/env python3
"""Freeze/check independent channel vectors and small library state fixtures."""
import argparse,json
from pathlib import Path
from channel_vectors import manifest,state_fixtures
from channel_native_vectors import manifest as native_manifest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args();root=Path(__file__).resolve().parents[1]/'data'
    for name,value in [('channel-vectors-v1.json',manifest()),('channel-state-fixtures-v1.json',state_fixtures()),('channel-native-vectors-v1.json',native_manifest())]:
        path=root/name
        if args.check:
            if json.loads(path.read_text(encoding='utf-8'))!=value:raise SystemExit(name+' differs')
        else:path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
        if 'sequences' in value:print(json.dumps(dict(sequences=len(value['sequences']),sha256=value['sha256'])))
if __name__=='__main__':main()
