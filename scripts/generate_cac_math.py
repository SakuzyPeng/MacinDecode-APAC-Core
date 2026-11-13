#!/usr/bin/env python3
"""Formula-defined CAC rotations, independently rounded at 100/200 digits."""
import argparse
from decimal import Decimal as D, localcontext
import hashlib
import json
from pathlib import Path

from sq_math import ieee_bits

PROFILE = 'apac-cac-math-v1'
DESTINATION = Path(__file__).resolve().parents[1] / 'data/cac-math-v1.json'


def coefficients(index):
    if not 0 <= index <= 34:
        raise ValueError('CAC gain index outside 0..34')
    if index == 0:
        return D(1), D(0), False
    k = (index-1) % 17
    gain = D(10) ** ((D(3)*k - 24) / 40)
    magnitude = min(gain, 1/gain)
    a = 1 / (1+magnitude*magnitude).sqrt()
    b = magnitude*a * (-1 if index > 17 else 1)
    return a, b, k >= 8


def generate(precision):
    with localcontext() as ctx:
        ctx.prec = precision
        return [dict(a_f64=ieee_bits(a, 64), b_f64=ieee_bits(b, 64), swap=swap)
                for a, b, swap in (coefficients(i) for i in range(35))]


def document():
    values = generate(100)
    if values != generate(200):
        raise AssertionError('CAC 100/200-digit rounding differs')
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema_version=1, numeric_profile=PROFILE, decimal_precisions=[100, 200],
                rounding='nearest_ties_even', generator='generate_cac_math.py',
                tables_sha256=digest, rotations=values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DESTINATION)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = document()
    data = (json.dumps(result, indent=2) + '\n').encode()
    if args.check:
        if args.output.read_bytes() != data:
            raise AssertionError('CAC constants differ from formula generation')
    else:
        with args.output.open('xb') as output:
            output.write(data)
    print(json.dumps(dict(numeric_profile=PROFILE, tables_sha256=result['tables_sha256'])))


if __name__ == '__main__':
    main()
