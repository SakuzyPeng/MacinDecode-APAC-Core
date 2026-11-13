#!/usr/bin/env python3
"""Deterministic wire-defined integer subband boundaries, separate from audio math."""
import argparse,hashlib,json
from pathlib import Path
from generate_hoa_dynamic_format import generate as legacy
from generate_hoa_spatial_partitions_measured import boundaries, source_metadata

PROFILE='apac-hoa-dynamic-subbands-v1'
FORMAT_PROFILE='apac-hoa-dynamic-selection-format-v2'


def generate():
    old=legacy(); assert [boundaries(8,m) for m in range(3)]==old['long_ends']
    values=dict(internal_slots=9,output_coefficients=16,wire_mapping_groups=8,sample_rates=[44100,48000],base_format_sha256=old['format_sha256'],
                tables=[dict(subbands=n,long_ends=[boundaries(n,m) for m in range(3)],short_ends=[[v//8 for v in boundaries(n,m)] for m in range(3)]) for n in range(1,8)])
    sha=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,format_sha256=sha,source=source_metadata(),**values)


def main():
    p=argparse.ArgumentParser(description=__doc__);action=p.add_mutually_exclusive_group();action.add_argument('--check',action='store_true');action.add_argument('--write',action='store_true');a=p.parse_args()
    value=generate();path=Path(__file__).resolve().parents[1]/'data/hoa-dynamic-format-v2.json'
    if a.check: assert json.loads(path.read_text())==value,'dynamic v2 format differs'
    else:
        with path.open('w' if a.write else 'x') as f: json.dump(value,f,indent=2);f.write('\n')
    print(value['format_sha256'])


if __name__=='__main__':main()
