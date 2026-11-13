#!/usr/bin/env python3
"""Independent high-order normalization constants; existing angle tables are reused."""
import argparse,hashlib,json
from decimal import Decimal as D,localcontext
from math import factorial
from pathlib import Path
from sq_math import ieee_bits

def generate(precision):
    with localcontext() as ctx:
        ctx.prec=precision;rows=[]
        for order in range(11):
            row=[0]*((order+1)**2)
            for degree in range(order+1):
                for m in range(degree+1):
                    value=(D(2*degree+1)*D(2 if m else 1)*D(factorial(degree-m))/D(factorial(degree+m))).sqrt()/D(order+1)
                    row[degree*(degree+1)+m]=row[degree*(degree+1)-m]=ieee_bits(value,64)
            rows.append(row)
        return rows

def document():
    rows=generate(100);assert rows==generate(200),'normalizations do not round consistently'
    return dict(numeric_profile='apac-hoa-expanded-orders-math-v1',decimal_precisions=[100,200],normalizations_f64=rows,
                tables_sha256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    path=Path(__file__).resolve().parents[1]/'data/hoa-expanded-orders-math-v1.json';value=document()
    if a.check:assert json.loads(path.read_text())==value
    else:
        with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(value['tables_sha256'])
