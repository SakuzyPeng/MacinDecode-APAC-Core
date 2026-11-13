#!/usr/bin/env python3
"""Exact rational FOA transform dictionaries and separately rounded math constants."""
import argparse, hashlib, json
from decimal import Decimal, localcontext
from pathlib import Path
from sq_math import ieee_bits

PROFILE='apac-hoa-static-ambient-math-v1'
FORMAT_PROFILE='apac-hoa-ambient-transform-format-v1'
ROOT = Path(__file__).resolve().parents[1]
MEASURED = ROOT / 'data/hoa-ambient-row-orders-measured-v1.json'
SOURCE_SHA256 = '3e5ebc921acbb41c71e2411e262ab85bf511125ff4b16439701e17411372fa48'


def measured_row_orders(value):
    raw = (json.dumps(value, indent=2) + '\n').encode()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('ambient row-order measurement or qualification differs')
    if value['schema_version'] != 1 or value['profile'] != 'apac-hoa-ambient-row-orders-measured-v1':
        raise ValueError('ambient measurement profile differs')
    orders = value['row_orders']
    if len(orders) != 3 or any(sorted(order) != list(range(4)) for order in orders):
        raise ValueError('ambient measured row permutations are incomplete')
    qualification = value['source']['qualification']
    if not qualification['passed'] or (qualification['basis_checks'], qualification['heldout_checks']) != (48, 36):
        raise ValueError('ambient measurement is unqualified')
    consumers = qualification['zero_equivalence']['consumers_sha256']
    for name, expected in consumers.items():
        actual = hashlib.sha256((ROOT / 'crates/apac-core' / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('ambient zero-equivalence consumer changed')
    return orders


def load_measurement():
    value = json.loads(MEASURED.read_bytes())
    measured_row_orders(value)
    return value


# Existing oracles share these formula-defined signs in the measured row order.
SIGNS = [[[(-1)**((row & column).bit_count()) for column in range(4)]
          for row in order] for order in load_measurement()['row_orders']]


def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def generate():
    measurement = load_measurement()
    signs = SIGNS
    form=dict(encoder_signs=signs,divisor=2,decoder_operation='transpose',identity_index=3)
    def rounded(precision):
        with localcontext() as ctx:
            ctx.prec=precision
            return [[ieee_bits(Decimal(m[j][i])/2,64) for i in range(4) for j in range(4)] for m in signs]
    values=rounded(100); assert values==rounded(200)
    for m in signs:
        assert all(sum(m[k][i]*m[k][j] for k in range(4))==(4 if i==j else 0) for i in range(4) for j in range(4))
    math=dict(decoder_matrices_f64=values,summation='neumaier-input-slots-0-through-3',output='one-f32-rounding-positive-zero')
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,numeric_profile=PROFILE,
                source=dict(method='formula-defined Hadamard signs in independently measured row order',
                            measurement_file=MEASURED.name, measurement_sha256=SOURCE_SHA256),
                format_sha256=digest(form),tables_sha256=digest(math),**form,**math)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--check',action='store_true'); a=p.parse_args()
    value=generate(); path=Path(__file__).resolve().parents[1]/'data/hoa-static-ambient-tables-v1.json'
    if a.check: assert json.loads(path.read_text())==value,'FOA constants differ'
    else:
        path.write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps({k:value[k] for k in ('format_sha256','tables_sha256')}))


if __name__=='__main__': main()
