#!/usr/bin/env python3
"""Freeze DRC-off PCM sequences independently of candidate execution."""
import argparse,json
from pathlib import Path
from drc_pcm_vectors import manifest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/drc-pcm-vectors-v1.json')
    args=p.parse_args();document=manifest();raw=(json.dumps(document,indent=2)+'\n').encode()
    if args.check:
        if args.output.read_bytes()!=raw:raise AssertionError('DRC PCM frozen inputs differ')
    else:
        with args.output.open('xb') as out:out.write(raw)
    print(json.dumps(dict(sequences=len(document['sequences']),sha256=document['sha256'])))

if __name__=='__main__':main()
