#!/usr/bin/env python3
"""Independent salient HOA constants, rounded at 100 and 200 decimal digits."""
import argparse
from decimal import Decimal as D, localcontext
import hashlib
import json
from pathlib import Path
from sq_math import sin_pi, cos_pi, ieee_bits

PROFILE = 'apac-hoa-salient-math-v1'


def generate(precision):
    with localcontext() as ctx:
        ctx.prec = precision
        return dict(
            azimuth_f64=[[ieee_bits(cos_pi(q,180),64),ieee_bits(sin_pi(q,180),64)] for q in range(512)],
            elevation_f64=[[ieee_bits(abs(cos_pi(q-90,180)),64),ieee_bits(sin_pi(q-90,180),64)] for q in range(256)],
            roots_f64=[ieee_bits(v.sqrt(),64) for v in (D(3),D(5),D(15),D(35)/8,D(105),D(21)/8,D(7))])


def document():
    values=generate(100)
    assert values==generate(200),'100/200-digit HOA constants differ'
    digest=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,numeric_profile=PROFILE,decimal_precisions=[100,200],
                rounding='nearest_ties_even',zero='positive',generator='generate_hoa_salient_math.py',
                tables_sha256=digest,**values)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/hoa-salient-math-v1.json')
    p.add_argument('--check',action='store_true');a=p.parse_args();result=document();raw=(json.dumps(result,indent=2)+'\n').encode()
    if a.check:assert a.output.read_bytes()==raw
    else:
        with a.output.open('xb') as f:f.write(raw)
    print(json.dumps(dict(numeric_profile=PROFILE,tables_sha256=result['tables_sha256'])))


if __name__=='__main__':main()
