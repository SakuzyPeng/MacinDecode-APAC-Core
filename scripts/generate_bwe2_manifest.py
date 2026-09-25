#!/usr/bin/env python3
"""Freeze BWE2 input identities before running a candidate decoder."""
import argparse
import hashlib
import json
from pathlib import Path
from bwe2_vectors import cases, sequences, packet
from spectrum_vectors import cookie

DESTINATION=Path(__file__).resolve().parents[1]/'data/bwe2-vectors-v2.json'


def document():
    stages={}
    for stage in ('spectra','pcm'):
        records=[]
        for rate in (48000,44100):
            groups=((c['kind'],[c]) for c in cases()) if stage=='spectra' else sequences()
            for index,(kind,sequence) in enumerate(groups):
                identity=hashlib.sha256(cookie(rate))
                for case in sequence:
                    data,_=packet(case,rate);identity.update(len(data).to_bytes(8,'little'));identity.update(data)
                records.append(dict(rate=rate,index=index,kind=kind,packets=len(sequence),input_sha256=identity.hexdigest()))
        stages[stage]=records
    digest=hashlib.sha256(json.dumps(stages,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,vector_profile='apac-bwe2-vectors-v2',generator='generate_bwe2_manifest.py',
                counts={k:len(v) for k,v in stages.items()},inputs_sha256=digest,**stages)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=DESTINATION)
    parser.add_argument('--check',action='store_true');args=parser.parse_args();result=document()
    data=(json.dumps(result,indent=2)+'\n').encode()
    if args.check:
        if args.output.read_bytes()!=data:raise AssertionError('frozen BWE2 inputs differ')
    else:
        with args.output.open('xb') as output:output.write(data)
    print(json.dumps({k:result[k] for k in ('counts','inputs_sha256')}))


if __name__=='__main__':main()
