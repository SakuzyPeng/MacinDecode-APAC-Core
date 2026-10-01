#!/usr/bin/env python3
"""Exact salient methods 1/2 grids; existing methods and lower-count tables stay frozen."""
import argparse,hashlib,json
from pathlib import Path
from generate_hoa_dynamic_subbands_format import boundaries,generate as lower
from generate_hoa_dynamic_format import generate as eight
from generate_hoa_salient_subbands_format import generate as perceptual

PROFILE='apac-hoa-salient-partition-v1'
FORMAT_PROFILE='apac-hoa-salient-subbands-format-v2'


def generate():
    assert boundaries(4,1)==[56,208,568,1024]
    assert boundaries(4,2)==[256,512,768,1024]
    values=dict(methods=[1,2],sample_rates=[44100,48000],components=5,maximum_subbands=16,
                dynamic_format_v1_sha256=eight()['format_sha256'],dynamic_format_v2_sha256=lower()['format_sha256'],
                salient_format_v1_sha256=perceptual()['format_sha256'],
                tables=[dict(method=m,subbands=n,long_ends=boundaries(n,m),short_ends=[v//8 for v in boundaries(n,m)]) for m in (1,2) for n in range(9,17)])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,
                source=dict(component='AudioCodecs 7.0',component_sha256=eight()['source']['component_sha256'],
                            evidence='encoder/decoder spatial SplitSubbands; method 1 AAC anchors, method 2 equal width; native cached grids 1..16'),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args();value=generate()
    path=Path(__file__).resolve().parents[1]/'data/hoa-salient-subbands-format-v2.json'
    if a.check:assert json.loads(path.read_text())==value,'salient partition format differs'
    else:
        with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(value['format_sha256'])


if __name__=='__main__':main()
