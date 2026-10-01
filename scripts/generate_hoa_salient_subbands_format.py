#!/usr/bin/env python3
"""Per-component salient perceptual boundaries; reuse lower-count dynamic format tables."""
import argparse,hashlib,json
from pathlib import Path
from generate_hoa_dynamic_subbands_format import boundaries,generate as lower
from generate_hoa_dynamic_format import generate as eight
PROFILE='apac-hoa-salient-subbands-v1'
FORMAT_PROFILE='apac-hoa-salient-subbands-format-v1'


def generate():
    assert boundaries(4,0)==[32,80,216,1024]
    values=dict(method=0,sample_rates=[44100,48000],reference_scale=24000,components=5,maximum_subbands=16,
                dynamic_format_v1_sha256=eight()['format_sha256'],dynamic_format_v2_sha256=lower()['format_sha256'],
                tables=[dict(subbands=n,long_ends=boundaries(n,0),short_ends=[v//8 for v in boundaries(n,0)]) for n in range(9,17)])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,source=dict(component='AudioCodecs 7.0',component_sha256=eight()['source']['component_sha256'],evidence='per-component Serialize/Deserialize; SplitSubbands method 0, native cached grids 1..16'),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args();v=generate();path=Path(__file__).resolve().parents[1]/'data/hoa-salient-subbands-format-v1.json'
    if a.check:assert json.loads(path.read_text())==v,'salient subband format differs'
    else:
        with path.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    print(v['format_sha256'])
if __name__=='__main__':main()
