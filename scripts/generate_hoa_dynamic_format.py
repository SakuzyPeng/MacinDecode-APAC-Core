#!/usr/bin/env python3
"""Exact integer/rational eight-band HOA selection boundaries."""
import argparse,hashlib,json,struct
from fractions import Fraction as F
from pathlib import Path
from spectrum_vectors import TABLES

PROFILE='apac-hoa-dynamic-selection-math-v1'
FORMAT_PROFILE='apac-hoa-dynamic-selection-format-v1'
ANCHORS=[100,200,300,400,510,630,770,920,1080,1270,1480,1720,2000,2320,2700,3150,3700,4400,5300,6400,7700,9500,12000,15500,24000]


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
    long=[interpolate([1]+[int(F(hz*1024,24000)+F(1,2)) for hz in ANCHORS]),
          interpolate([1]+TABLES['long_offsets'][1:]),[128*b for b in range(1,9)]]
    assert long==[[16,32,48,80,128,216,392,1024],[24,56,112,208,376,568,768,1024],[128,256,384,512,640,768,896,1024]]
    values=dict(internal_slots=9,output_coefficients=16,subbands=8,sample_rates=[44100,48000],reference_scale=24000,
                perceptual_anchors_hz=ANCHORS,aac_long_offsets=TABLES['long_offsets'],long_ends=long,short_ends=[[v//8 for v in row] for row in long])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,source=dict(component='AudioCodecs 7.0',component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
                evidence='SplitSubbands and SBinterpolation; exact format anchors and existing AAC bands'),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--check',action='store_true'); p.add_argument('--component',type=Path); a=p.parse_args()
    value=generate(); path=Path(__file__).resolve().parents[1]/'data/hoa-dynamic-format-v1.json'
    if a.component:
        from verify_hoa_salient_format import reader
        read=reader(a.component); assert read(0x88bee0,100)==struct.pack('<25f',*ANCHORS),'native anchors changed'
    if a.check: assert json.loads(path.read_text())==value,'dynamic format changed'
    else:
        with path.open('x') as f: f.write(json.dumps(value,indent=2)+'\n')
    print(value['format_sha256'])


if __name__=='__main__': main()
