#!/usr/bin/env python3
"""Freeze independent HOA transport identities and focused state fixtures."""
import argparse
import json
from pathlib import Path
import hoa_transport_vectors as vectors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    root=Path(__file__).resolve().parents[1]/'data'
    for name,value in [('hoa-transports-vectors-v1.json',vectors.manifest()),('hoa-transports-state-v1.json',vectors.state_fixtures())]:
        path=root/name
        if a.check:
            if json.loads(path.read_text())!=value:raise RuntimeError('frozen fixture differs: '+name)
        else:path.write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps(dict(cases=len(vectors.manifest()['cases']),sha256=vectors.manifest()['sha256'])))


if __name__=='__main__':main()
