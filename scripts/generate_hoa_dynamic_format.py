#!/usr/bin/env python3
"""Exact integer/rational eight-band HOA selection boundaries."""
import argparse,hashlib,json
from fractions import Fraction as F
from pathlib import Path
from spectrum_vectors import TABLES
from generate_hoa_spatial_partitions_measured import boundaries, source_metadata

PROFILE='apac-hoa-dynamic-selection-math-v1'
FORMAT_PROFILE='apac-hoa-dynamic-selection-format-v1'


def interpolate(anchors):
    count=len(anchors)-1; ends=[]
    for b in range(1,8):
        position=F(count*b,8); i=int(position); fraction=position-i
        line=int(anchors[i]*(1-fraction)+anchors[i+1]*fraction)
        line=max(line,ends[-1]+1 if ends else 0)
        ends.append(line)
    ends.append(1024); aligned=[]
    for end in ends: aligned.append(max(8*((end+4)//8),aligned[-1]+8 if aligned else 0))
    assert aligned[-1]==1024
    return aligned


def generate():
    long=[boundaries(8, method) for method in range(3)]
    values=dict(internal_slots=9,output_coefficients=16,subbands=8,sample_rates=[44100,48000],reference_scale=24000,
                aac_long_offsets=TABLES['long_offsets'],long_ends=long,short_ends=[[v//8 for v in row] for row in long])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,source=source_metadata(),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__); action=p.add_mutually_exclusive_group(); action.add_argument('--check',action='store_true'); action.add_argument('--write',action='store_true'); a=p.parse_args()
    value=generate(); path=Path(__file__).resolve().parents[1]/'data/hoa-dynamic-format-v1.json'
    if a.check: assert json.loads(path.read_text())==value,'dynamic format changed'
    else:
        with path.open('w' if a.write else 'x') as f: f.write(json.dumps(value,indent=2)+'\n')
    print(value['format_sha256'])


if __name__=='__main__': main()
