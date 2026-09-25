"""Independent high-precision LPC/direct-form oracle, never the production lattice.

Reflection coefficients are evaluated from the APAC sine definition, then rounded
to binary64 independently. LPC conversion and direct recursion use 200 digits;
only the completed spectrum is rounded to binary32. No production tables or
candidate values enter this reference. Synthesis uses the Decimal direct IMDCT.
"""
from decimal import Decimal as D, localcontext
from functools import lru_cache

from cac_oracle import coupled
from sq_math import from_bits, ieee_bits, round_f32, sin_pi
from sq_oracle import Channel


@lru_cache(maxsize=24)
def reflection(q, resolution):
    with localcontext() as ctx:
        ctx.prec = 200
        denominator = 2**resolution-1 if q >= 0 else 2**resolution+1
        return D.from_float(from_bits(ieee_bits(sin_pi(q, denominator), 64), 64))


@lru_cache(maxsize=256)
def direct_filter(values, quantized, resolution):
    if not quantized or not any(values):
        return values
    with localcontext() as ctx:
        ctx.prec = 200
        a = [D(1)]
        for q in quantized:
            k = reflection(q, resolution)
            a = [D(1)] + [a[i]+k*a[len(a)-i] for i in range(1,len(a))] + [k]
        y = []
        for value in values:
            y.append(D.from_float(value)-sum((a[j]*y[-j] for j in range(1,min(len(a),len(y)+1))), D(0)))
        return tuple(round_f32(value) for value in y)


def filtered(truth, inputs=None):
    output = coupled(truth) if inputs is None else [list(c) for c in inputs]
    for channel, parameters in zip(output, truth['tns']):
        for window in parameters['windows']:
            for f in window['filters']:
                indices = list(range(f['start_line'], f['end_line']))
                if f['direction']: indices.reverse()
                values = tuple(channel[i] for i in indices)
                for i,value in zip(indices, direct_filter(values, tuple(f['quantized']), window['resolution'])):
                    channel[i] = value
    return output


class Decoder:
    def __init__(self):
        self.channels = [Channel(), Channel()]

    def decode(self, truth):
        sides = [state.render(spectrum, channel['ics']['block_type']) for state, spectrum, channel
                 in zip(self.channels, filtered(truth), truth['channels'])]
        return [v for pair in zip(*sides) for v in pair]
