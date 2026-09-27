#!/usr/bin/env python3
"""Freeze/check independently generated CAF input vector identities."""
import argparse,json
from pathlib import Path
from caf_vectors import manifest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args()
    path=Path(__file__).resolve().parents[1]/'data/caf-vectors-v1.json';value=manifest()
    if args.check:
        if json.loads(path.read_text(encoding='utf-8'))!=value:raise SystemExit('CAF manifest differs')
    else:path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=len(value['cases']),sha256=value['sha256'])))
if __name__=='__main__':main()
