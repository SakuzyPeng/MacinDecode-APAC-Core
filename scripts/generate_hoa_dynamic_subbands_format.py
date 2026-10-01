#!/usr/bin/env python3
"""Exact rational generation of the new one-to-seven effective HOA subband tables."""
import argparse,hashlib,json
from fractions import Fraction as F
from pathlib import Path
from generate_hoa_dynamic_format import ANCHORS,generate as legacy
from spectrum_vectors import TABLES

PROFILE='apac-hoa-dynamic-subbands-v1'
FORMAT_PROFILE='apac-hoa-dynamic-selection-format-v2'


def boundaries(count,method):
    assert 1<=count<=8 and 0<=method<=2
    anchors=[1]+([int(F(hz*1024,24000)+F(1,2)) for hz in ANCHORS] if method==0 else TABLES['long_offsets'][1:])
    ends=[]
    for b in range(1,count):
        if method==2: line=int(F(1024*b,count)+F(1,2))
        else:
            position=F((len(anchors)-1)*b,count); i=int(position); fraction=position-i
            line=int(anchors[i]*(1-fraction)+anchors[i+1]*fraction)
        ends.append(max(line,ends[-1]+1 if ends else 0))
    ends.append(1024); aligned=[]
    for line in ends: aligned.append(max(8*((line+4)//8),aligned[-1]+8 if aligned else 0))
    assert len(aligned)==count and aligned[-1]==1024 and all(a<b for a,b in zip([0]+aligned,aligned))
    return aligned


def generate():
    old=legacy(); assert [boundaries(8,m) for m in range(3)]==old['long_ends']
    values=dict(internal_slots=9,output_coefficients=16,wire_mapping_groups=8,sample_rates=[44100,48000],base_format_sha256=old['format_sha256'],
                tables=[dict(subbands=n,long_ends=[boundaries(n,m) for m in range(3)],short_ends=[[v//8 for v in boundaries(n,m)] for m in range(3)]) for n in range(1,8)])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,source=dict(component='AudioCodecs 7.0',component_sha256=old['source']['component_sha256'],
                evidence='SplitSubbands/SBinterpolation; fixed eight-row Serialize/Deserialize; unchanged v1 anchors'),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    value=generate();path=Path(__file__).resolve().parents[1]/'data/hoa-dynamic-format-v2.json'
    if a.check: assert json.loads(path.read_text())==value,'dynamic v2 format differs'
    else:
        with path.open('x') as f: json.dump(value,f,indent=2);f.write('\n')
    print(value['format_sha256'])


if __name__=='__main__':main()
