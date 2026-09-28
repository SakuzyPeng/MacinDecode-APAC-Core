#!/usr/bin/env python3
"""Freeze/check independent MP4 vector identities."""
import argparse,json
from pathlib import Path
from mp4_vectors import manifest,encode,cookie,sha

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args()
    data=Path(__file__).resolve().parents[1]/'data';path=data/'mp4-vectors-v1.json';value=manifest()
    raw,_,_=encode(cookie(2),[bytes([i])*4 for i in range(4)],variant=5)
    fixture=dict(schema_version=1,sha256=sha(raw),hex=raw.hex());fixture_path=data/'mp4-reader-fixture.json'
    if args.check:
        if json.loads(fixture_path.read_text(encoding='utf-8'))!=fixture:raise SystemExit('MP4 reader fixture differs')
        if json.loads(path.read_text(encoding='utf-8'))!=value:raise SystemExit('MP4 manifest differs')
    else:
        path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
        fixture_path.write_text(json.dumps(fixture,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=len(value['cases']),sha256=value['sha256'])))
if __name__=='__main__':main()
