#!/usr/bin/env python3
"""Reproduce SQ constants and qualify single-rounding dequantization offline."""
import argparse
from decimal import localcontext
import hashlib
import json
from pathlib import Path
import sys
import struct

from sq_math import cos_pi, ieee_bits, inverse_magnitude, scale_gain, sin_pi

PROFILE = 'apac-sq-math-v2'
DESTINATION = Path(__file__).resolve().parents[1] / 'data/sq-math-v2.json'


def generate(precision, profile=PROFILE):
    with localcontext() as ctx:
        ctx.prec = precision
        width = 32 if profile == 'apac-sq-math-v1' else 64
        values = {f'inverse_quantizer_f{width}': [ieee_bits(inverse_magnitude(q), width) for q in range(8192)],
                  f'gains_f{width}': [ieee_bits(scale_gain(sf), width) for sf in range(-256, 256)], 'transforms': {}}
        if width == 64:
            # Every legal gain differs from one of these four by an exact
            # power of two. All nonzero inputs/products/outputs stay normal,
            # so binary scaling commutes with both IEEE rounding operations.
            from decimal import Decimal as D
            def binary64(word):
                return struct.unpack('<d', struct.pack('<Q', word))[0]
            for sf, word in zip(range(-256, 256), values['gains_f64']):
                exponent, residue = divmod(sf - 100, 4)
                assert binary64(word) == binary64(values['gains_f64'][356 + residue]) * 2.**exponent
            for q, word in enumerate(values['inverse_quantizer_f64']):
                magnitude = inverse_magnitude(q)
                for residue in range(4):
                    product = binary64(word) * binary64(values['gains_f64'][356 + residue])
                    if ieee_bits(D.from_float(product), 32) != ieee_bits(magnitude * scale_gain(100 + residue), 32):
                        raise AssertionError(f'SQ Float64 product is not correctly rounded: q={q}, residue={residue}')
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


def document(profile=PROFILE):
    first = generate(100, profile)
    if first != generate(200, profile):
        raise AssertionError('100/200-digit IEEE rounding differs')
    return dict(numeric_profile=profile, schema_version=1, generator='generate_sq_math.py',
                decimal_precisions=[100, 200], rounding='nearest_ties_even',
                zero='positive', tables_sha256=digest(first), **first)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profile', choices=('apac-sq-math-v1', PROFILE), default=PROFILE)
    parser.add_argument('--check', action='store_true', help='Verify existing bytes without writing.')
    args = parser.parse_args()
    if args.output is None:
        args.output = DESTINATION.with_name(args.profile.removeprefix('apac-') + '.json')
    result = document(args.profile)
    data = (json.dumps(result, indent=2) + '\n').encode()
    if args.check:
        if args.output.read_bytes() != data:
            raise AssertionError('committed numerical constants differ from formula generation')
    else:
        with args.output.open('xb') as output:
            output.write(data)
    print(json.dumps(dict(numeric_profile=args.profile, tables_sha256=result['tables_sha256'], bytes=len(data))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
