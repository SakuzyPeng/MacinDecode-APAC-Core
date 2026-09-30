#!/usr/bin/env python3
"""Exact rational FOA transform dictionaries and separately rounded math constants."""
import argparse, hashlib, json, struct
from decimal import Decimal, localcontext
from pathlib import Path
from sq_math import ieee_bits

PROFILE='apac-hoa-static-ambient-math-v1'
FORMAT_PROFILE='apac-hoa-ambient-transform-format-v1'
# Encoder rows; the decoder applies the transpose. Entries are format signs.
SIGNS=[
    [[1,1,1,1],[1,-1,-1,1],[1,1,-1,-1],[1,-1,1,-1]],
    [[1,1,1,1],[1,1,-1,-1],[1,-1,-1,1],[1,-1,1,-1]],
    [[1,1,1,1],[1,-1,1,-1],[1,1,-1,-1],[1,-1,-1,1]],
]


def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def generate():
    form=dict(encoder_signs=SIGNS,divisor=2,decoder_operation='transpose',identity_index=3)
    def rounded(precision):
        with localcontext() as ctx:
            ctx.prec=precision
            return [[ieee_bits(Decimal(m[j][i])/2,64) for i in range(4) for j in range(4)] for m in SIGNS]
    values=rounded(100); assert values==rounded(200)
    for m in SIGNS:
        assert all(sum(m[k][i]*m[k][j] for k in range(4))==(4 if i==j else 0) for i in range(4) for j in range(4))
    math=dict(decoder_matrices_f64=values,summation='neumaier-input-slots-0-through-3',output='one-f32-rounding-positive-zero')
    return dict(schema_version=1,format_profile=FORMAT_PROFILE,numeric_profile=PROFILE,
                source=dict(component='AudioCodecs 7.0',component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
                            evidence='matching encoder/decoder FOA sign dictionaries, x86_64'),
                format_sha256=digest(form),tables_sha256=digest(math),**form,**math)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--check',action='store_true'); p.add_argument('--component',type=Path); a=p.parse_args()
    value=generate(); path=Path(__file__).resolve().parents[1]/'data/hoa-static-ambient-tables-v1.json'
    if a.component:
        from verify_hoa_salient_format import reader
        read=reader(a.component)
        expected=b''.join(struct.pack('<f',x/2) for m in SIGNS for row in m for x in row)
        for address in (0xa3054c,0xa3b3e0): assert read(address,len(expected))==expected,'native FOA dictionary differs'
    if a.check: assert json.loads(path.read_text())==value,'FOA constants differ'
    else:
        with path.open('x') as f: f.write(json.dumps(value,indent=2)+'\n')
    print(json.dumps({k:value[k] for k in ('format_sha256','tables_sha256')}))


if __name__=='__main__': main()
