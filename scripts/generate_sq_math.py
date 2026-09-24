#!/usr/bin/env python3
"""Reproduce apac-sq-math-v1 at 100 and 200 decimal digits, entirely offline."""
import argparse
from decimal import localcontext
import hashlib
import json
from pathlib import Path
import sys

from sq_math import cos_pi, ieee_bits, inverse_magnitude, scale_gain, sin_pi

PROFILE = 'apac-sq-math-v1'
DESTINATION = Path(__file__).resolve().parents[1] / 'data/sq-math-v1.json'


def generate(precision):
    with localcontext() as ctx:
        ctx.prec = precision
        values = dict(inverse_quantizer_f32=[ieee_bits(inverse_magnitude(q), 32) for q in range(8192)],
                      gains_f32=[ieee_bits(scale_gain(sf), 32) for sf in range(-256, 256)], transforms={})
        for n in (128, 1024):
            values['transforms'][str(n)] = dict(
                window_f64=[ieee_bits(sin_pi(2*i+1, 4*n), 64) for i in range(n)],
                modulation_f64=[[ieee_bits(sin_pi(8*k+1, 8*n), 64),
                                 ieee_bits(cos_pi(8*k+1, 8*n), 64)] for k in range(n//2)],
                twiddles_f64=[[ieee_bits(cos_pi(-4*k, n), 64),
                               ieee_bits(sin_pi(-4*k, n), 64)] for k in range(n//4)])
        return values


def digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def document():
    first = generate(100)
    if first != generate(200):
        raise AssertionError('100/200-digit IEEE rounding differs')
    return dict(numeric_profile=PROFILE, schema_version=1, generator='generate_sq_math.py',
                decimal_precisions=[100, 200], rounding='nearest_ties_even',
                zero='positive', tables_sha256=digest(first), **first)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DESTINATION)
    parser.add_argument('--check', action='store_true', help='Verify existing bytes without writing.')
    args = parser.parse_args()
    result = document()
    data = (json.dumps(result, indent=2) + '\n').encode()
    if args.check:
        if args.output.read_bytes() != data:
            raise AssertionError('committed numerical constants differ from formula generation')
    else:
        with args.output.open('xb') as output:
            output.write(data)
    print(json.dumps(dict(numeric_profile=PROFILE, tables_sha256=result['tables_sha256'], bytes=len(data))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
