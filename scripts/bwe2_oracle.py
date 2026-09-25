"""Independent Decimal direct-DFT/Toeplitz BWE2 oracle; no production math tables.

Only normative IEEE wire dictionaries and public SFB boundaries are shared.
The reference uses exact high-precision cosine, Gaussian elimination of the
Toeplitz system, and direct polynomial evaluation, not the production FFT,
Levinson recursion or bounded trigonometric polynomial.
"""
from decimal import Decimal as D, localcontext
from functools import lru_cache
import json
from pathlib import Path
import struct

from sq_math import cos_pi, sin_pi, round_f32
from sq_oracle import Channel
from tns_oracle import filtered
from bwe2_vectors import regions

FORMAT=json.loads((Path(__file__).resolve().parents[1]/'data/bwe2-format-v1.json').read_text())
PRECISION=100


def exact_f32(word):return D.from_float(struct.unpack('<f',word.to_bytes(4,'little'))[0])


@lru_cache(maxsize=4096)
def target_lpc(indices):
    with localcontext() as ctx:
        ctx.prec=PRECISION
        lsf=[exact_f32(a)+exact_f32(b) for a,b in zip(FORMAT['lsf_codebooks_f32'][0][indices[0]],FORMAT['lsf_codebooks_f32'][1][indices[1]])]
        for i in range(16):lsf[i]=max(lsf[i],(lsf[i-1] if i else D(0))+50)
        if lsf[-1]>11950:
            for i in range(15,-1,-1):lsf[i]=min(lsf[i],(lsf[i+1] if i<15 else D(12000))-50)
        # Independent polynomial convolution, then (P + z^-1 P + Q - z^-1 Q)/2.
        products=[]
        for parity in (0,1):
            poly=[D(1)]
            for frequency in lsf[parity::2]:
                factor=[D(1),-2*cos_pi(frequency,12000),D(1)]
                poly=[sum((poly[j]*factor[k-j] for j in range(max(0,k-2),min(k+1,len(poly)))),D(0))
                      for k in range(len(poly)+2)]
            products.append(poly)
        p,q=products
        return tuple([D(1)]+[(p[i]+q[i]+p[i-1]-q[i-1])/2 for i in range(1,17)])


@lru_cache(maxsize=6)
def grid(n):
    with localcontext() as ctx:
        ctx.prec=PRECISION
        return tuple(cos_pi(2*k,n) for k in range(n)),tuple(sin_pi(2*k,n) for k in range(n))


def solve(matrix, rhs):
    """Pivoted Gaussian elimination, structurally independent of Levinson."""
    a=[list(row)+[value] for row,value in zip(matrix,rhs)]
    n=len(rhs)
    for k in range(n):
        pivot=max(range(k,n),key=lambda i:abs(a[i][k]))
        if a[pivot][k]==0:raise ArithmeticError('singular reference Toeplitz system')
        a[k],a[pivot]=a[pivot],a[k]
        for i in range(k+1,n):
            factor=a[i][k]/a[k][k]
            for j in range(k,n+1):a[i][j]-=factor*a[k][j]
    x=[D(0)]*n
    for i in range(n-1,-1,-1):x[i]=(a[i][n]-sum((a[i][j]*x[j] for j in range(i+1,n)),D(0)))/a[i][i]
    return x


@lru_cache(maxsize=128)
def source_lpc(values, size, cutoff):
    with localcontext() as ctx:
        ctx.prec=PRECISION
        n=2*cutoff;cosines,_=grid(n)
        terms=[]
        for first in range(0,len(values),size):
            for k,v in enumerate(values[first:first+cutoff]):
                if v:
                    value=D.from_float(v);terms.append((k,value*value*(1 if k==0 else 2)))
        r=[sum((power*cosines[k*j%n] for k,power in terms),D(0))/n for j in range(17)]
        r[0]*=D('1.0001')
        return tuple([D(1)]+solve([[r[abs(i-j)] for j in range(16)] for i in range(16)],[-r[i+1] for i in range(16)]))


@lru_cache(maxsize=128)
def envelope(coefficients,bins):
    with localcontext() as ctx:
        ctx.prec=PRECISION
        n=2*bins;c,s=grid(n)
        result=[]
        for k in range(bins):
            real=sum((a*c[k*j%n] for j,a in enumerate(coefficients)),D(0))
            imaginary=sum((a*s[k*j%n] for j,a in enumerate(coefficients)),D(0))
            result.append((real*real+imaginary*imaginary).sqrt())
        return tuple(result)


@lru_cache(maxsize=128)
def restore(values,short,cutoff,groups,indices,gains):
    size=128 if short else 1024;source_start=16 if short else 128;high_bins=64 if short else 512
    if all(v==0 for first in range(0,1024,size) for v in values[first:first+cutoff]):return values
    with localcontext() as ctx:
        ctx.prec=PRECISION
        source=envelope(source_lpc(values,size,cutoff),cutoff)
        target=envelope(target_lpc(indices),high_bins)
        width=cutoff-source_start;result=list(values);first=0
        for group,length in enumerate(groups):
            gain=exact_f32(FORMAT['excitation_gains_f32'][gains[group]])
            for w in range(first,first+length):
                for k in range(high_bins):
                    i=source_start+k%width
                    result[w*size+cutoff+k]=round_f32(D.from_float(values[w*size+i])*source[i]/target[k]*gain)
            first+=length
        return tuple(result)


def expanded(truth,inputs=None):
    before=filtered(truth) if inputs is None else inputs
    result=[]
    for c,p,values in zip(truth['channels'],truth['bwe2']['channels'],before):
        if not p['active']:result.append(list(values));continue
        ics=c['ics'];spec=p['parameters'];region=regions(ics)[0];cutoff=region['target_start_line']
        if len(spec['gain_indices'])<len(ics['window_groups']):raise ValueError('undefined BWE2 reused gain')
        result.append(list(restore(tuple(values),ics['block_type']==2,cutoff,tuple(ics['window_groups']),
                                   tuple(spec['lsf_indices']),tuple(spec['gain_indices']))))
    return result


def conditioned_transform(values, ics, parameters, source_coefficients, target_coefficients, cutoff):
    """Diagnostic only: independently evaluate a captured native LPC pair.

    This is never used to generate the portable mathematical reference. It
    isolates the downstream transform/copy/gain arithmetic from native LPC
    quantization and polynomial cancellation.
    """
    short=ics['block_type']==2
    size=128 if short else 1024;source_start=16 if short else 128;high_bins=64 if short else 512
    if all(v==0 for first in range(0,1024,size) for v in values[first:first+cutoff]):return list(values)
    with localcontext() as ctx:
        ctx.prec=PRECISION
        source=envelope(tuple(D.from_float(v) for v in source_coefficients),cutoff)
        target=envelope(tuple(D.from_float(v) for v in target_coefficients),high_bins)
        if any(v<=0 for v in target):raise ArithmeticError('singular native LPC envelope')
        result=list(values);first=0;width=cutoff-source_start
        for group,length in enumerate(ics['window_groups']):
            gain=exact_f32(FORMAT['excitation_gains_f32'][parameters['gain_indices'][group]])
            for w in range(first,first+length):
                for k in range(high_bins):
                    j=source_start+k%width
                    result[w*size+cutoff+k]=round_f32(D.from_float(values[w*size+j])*source[j]/target[k]*gain)
            first+=length
        return result


class Decoder:
    def __init__(self):self.channels=[Channel(),Channel()]
    def decode(self,truth):
        sides=[state.render(spectrum,c['ics']['block_type']) for state,spectrum,c in zip(self.channels,expanded(truth),truth['channels'])]
        return [v for pair in zip(*sides) for v in pair]
