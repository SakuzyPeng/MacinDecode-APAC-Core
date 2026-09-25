#!/usr/bin/env python3
"""APAC signed reflection coefficients, independently rounded at 100/200 digits."""
import argparse
from decimal import localcontext
import hashlib
import json
from pathlib import Path

from sq_math import ieee_bits, sin_pi

PROFILE = 'apac-tns-math-v1'
DESTINATION = Path(__file__).resolve().parents[1] / 'data/tns-math-v1.json'


def generate(precision):
    with localcontext() as ctx:
        ctx.prec = precision
        return {str(r): [ieee_bits(sin_pi(q, (1 << r) + (1 if q < 0 else -1)), 64)
                         for q in range(-(1 << (r-1)), 1 << (r-1))] for r in (3, 4)}


def document():
    values = generate(100)
    if values != generate(200):
        raise AssertionError('TNS 100/200-digit rounding differs')
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema_version=1, numeric_profile=PROFILE, decimal_precisions=[100, 200],
                rounding='nearest_ties_even', generator='generate_tns_math.py',
                tables_sha256=digest, reflection_f64=values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DESTINATION)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = document()
    data = (json.dumps(result, indent=2) + '\n').encode()
    if args.check:
        if args.output.read_bytes() != data:
            raise AssertionError('TNS constants differ from formula generation')
    else:
        with args.output.open('xb') as output:
            output.write(data)
    print(json.dumps(dict(numeric_profile=PROFILE, tables_sha256=result['tables_sha256'])))


if __name__ == '__main__':
    main()
