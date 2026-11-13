#!/usr/bin/env python3
"""Independent BWE2 transform and bounded trigonometric constants, 100/200 digits."""
import argparse
from decimal import Decimal as D, localcontext
import hashlib
import json
import math
from pathlib import Path

from sq_math import cos_pi, sin_pi, pi, ieee_bits

PROFILE='apac-bwe2-math-v2'
DESTINATION=Path(__file__).resolve().parents[1]/'data/bwe2-math-v2.json'
SIZES=(32,64,96,128,256,512,768,1024)  # 32/256 are the radix-3 subtransforms.


def generate(precision):
    with localcontext() as ctx:
        ctx.prec=precision
        return dict(twiddles_f64={str(n):[[ieee_bits(cos_pi(2*k,n),64),ieee_bits(-sin_pi(2*k,n),64)]
                                        for k in range(n)] for n in SIZES},
                    cosine_f64=[ieee_bits(D((-1)**i)/math.factorial(2*i),64) for i in range(13)],
                    sine_f64=[ieee_bits(D((-1)**i)/math.factorial(2*i+1),64) for i in range(13)],
                    lsf_angle_scale_f64=ieee_bits(pi(precision)/12000,64),
                    autocorrelation_loading_f64=ieee_bits(D('1.0001'),64))


def document():
    result=generate(100)
    if result!=generate(200):raise AssertionError('BWE2 100/200-digit rounding differs')
    digest=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,numeric_profile=PROFILE,decimal_precisions=[100,200],rounding='nearest_ties_even',
                generator='generate_bwe2_math.py',tables_sha256=digest,**result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DESTINATION);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();result=document();data=(json.dumps(result,indent=2)+'\n').encode()
    if args.check:
        if args.output.read_bytes()!=data:raise AssertionError('BWE2 constants differ from formulas')
    else:
        with args.output.open('xb') as output:output.write(data)
    print(json.dumps(dict(numeric_profile=PROFILE,tables_sha256=result['tables_sha256'])))


if __name__=='__main__':main()
