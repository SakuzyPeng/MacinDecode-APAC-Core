"""Decimal matrix oracle, independent of production rotations and CAC parser."""
from decimal import Decimal as D, localcontext
from functools import lru_cache

from sq_math import ieee_bits, from_bits, round_f32
from sq_oracle import Channel, scaled_channel
from spectrum_vectors import TABLES


def binary64(value):
    return D.from_float(from_bits(ieee_bits(value, 64), 64))


@lru_cache(maxsize=35)
def rotation(index):
    with localcontext() as ctx:
        ctx.prec = 100
        step = index-1 if index <= 17 else index-18
        ratio = D(10) ** ((D('1.5')*step-12)/20)
        small = ratio if ratio < 1 else 1/ratio
        norm = (1+small*small).sqrt()
        return binary64(1/norm), binary64((-small if index > 17 else small)/norm), ratio >= 1


def coupled(expected,rate=48000):
    left, right = (scaled_channel(c,rate) for c in expected['channels'])
    if not expected['shared_ics']:
        return [left, right]
    ics = expected['channels'][0]['ics']
    short = ics['block_type'] == 2
    n = 128 if short else 1024
    from shared_config_tables import offsets as band_offsets
    offsets=band_offsets(rate,short,TABLES)
    first_window = 0
    with localcontext() as ctx:
        ctx.prec = 100
        for group, count in enumerate(ics['window_groups']):
            for sfb, index in enumerate(expected['cac']['gain_indices'][group]):
                if index == 0:
                    continue
                a, b, swap = rotation(index)
                for window in range(first_window, first_window+count):
                    for line in range(offsets[sfb], offsets[sfb+1]):
                        i = window*n+line
                        if left[i] == right[i] == 0:
                            continue
                        x, y = D.from_float(left[i]), D.from_float(right[i])
                        sum_ = round_f32(binary64(binary64(a*x)+binary64(b*y)))
                        difference = round_f32(binary64(binary64(b*x)-binary64(a*y)))
                        left[i], right[i] = (difference, sum_) if swap else (sum_, difference)
            first_window += count
    return [left, right]


class Decoder:
    def __init__(self):
        self.channels = [Channel(), Channel()]

    def decode(self, expected):
        output = [state.render(spectrum, channel['ics']['block_type']) for state, spectrum, channel
                  in zip(self.channels, coupled(expected), expected['channels'])]
        return [v for pair in zip(*output) for v in pair]
