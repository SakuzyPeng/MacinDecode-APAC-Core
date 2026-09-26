#!/usr/bin/env python3
"""Freeze portable packet/state input identities before candidate execution."""
import argparse
import json
from pathlib import Path
from packet_vectors import manifest

DESTINATION=Path(__file__).resolve().parents[1]/'data/packet-vectors-v1.json'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DESTINATION)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();result=manifest();data=(json.dumps(result,indent=2)+'\n').encode()
    if args.check:
        if args.output.read_bytes()!=data:raise AssertionError('frozen packet vectors differ')
    else:
        with args.output.open('xb') as out:out.write(data)
    print(json.dumps(dict(sequences=len(result['sequences']),sha256=result['sha256'])))


if __name__=='__main__':main()
