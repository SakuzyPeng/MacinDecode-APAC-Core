"""Independent Decimal SQ oracle using the direct IMDCT definition.

Only public frequency-band boundaries are shared with the packet generator.
No production numerical tables, FFT, native codec, or candidate values are read.
"""
from decimal import Decimal as D, localcontext
from functools import lru_cache

from spectrum_vectors import TABLES
from sq_math import cos_pi, inverse_magnitude, round_f32, scale_gain

PRECISION = 100


@lru_cache(maxsize=16384)
def coefficient(q, sf):
    if not -8191 <= q <= 8191 or not -256 <= sf <= 255:
        raise ValueError('outside the verified SQ domain')
    if not q:
        return 0.0
    with localcontext() as ctx:
        ctx.prec = PRECISION
        magnitude = D.from_float(round_f32(inverse_magnitude(abs(q))))
        gain = D.from_float(round_f32(scale_gain(sf)))
        return round_f32((-magnitude if q < 0 else magnitude) * gain)


def scaled_channel(channel):
    short = channel['ics']['block_type'] == 2
    n = 128 if short else 1024
    offsets = TABLES['short_offsets' if short else 'long_offsets']
    scaled = [0.0] * 1024
    first_window = 0
    for length, factors in zip(channel['ics']['window_groups'], channel['scale_factors']):
        for sfb, sf in enumerate(factors):
            if sf is None:
                continue
            for w in range(first_window, first_window + length):
                for line in range(offsets[sfb], offsets[sfb + 1]):
                    i = w * n + line
                    scaled[i] = coefficient(channel['quantized'][i], sf)
        first_window += length
    return scaled


@lru_cache(maxsize=2)
def cosine_grid(n):
    with localcontext() as ctx:
        ctx.prec = PRECISION
        quarter = [cos_pi(k, 4*n) for k in range(2*n + 1)]
        half = quarter + [-v for v in reversed(quarter[:-1])]
        return tuple(half + list(reversed(half[1:-1])))


@lru_cache(maxsize=2)
def window(n):
    grid = cosine_grid(n)
    return tuple(grid[(2*n - (2*i+1)) % (8*n)] for i in range(2*n))


# BWE2 produces dense spectra; retain both 64-gain long/short sweeps.
# Immutable Decimal results only: cache size does not change the oracle arithmetic.
@lru_cache(maxsize=128)
def direct_imdct(n, nonzero):
    if not nonzero:
        return (D(0),) * (2*n)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        grid = cosine_grid(n)
        coefficients = [(2*k+1, D.from_float(v)) for k, v in nonzero]
        normalization = D(1) / (n * 32768)
        return tuple(sum((value * grid[((2*i+1+n)*frequency) % (8*n)]
                          for frequency, value in coefficients), D(0)) * normalization
                     for i in range(2*n))


class Channel:
    def __init__(self, strict_transitions=True):
        self.overlap = [D(0)] * 1024
        self.previous = 0
        self.strict_transitions = strict_transitions

    def render(self, spectrum, block):
        if self.strict_transitions and (self.previous in (0, 3) and block not in (0, 1)
                or self.previous in (1, 2) and block not in (2, 3)):
            raise ValueError('invalid oracle window transition')
        with localcontext() as ctx:
            ctx.prec = PRECISION
            time = [D(0)] * 2048
            if block == 2:
                for w in range(8):
                    terms = tuple((k, v) for k, v in enumerate(spectrum[w*128:(w+1)*128]) if v)
                    if not terms:
                        continue
                    for i, sample in enumerate(direct_imdct(128, terms)):
                        time[448+w*128+i] += sample * window(128)[i]
            else:
                terms = tuple((k, v) for k, v in enumerate(spectrum) if v)
                if terms:
                    for i, sample in enumerate(direct_imdct(1024, terms)):
                        if block == 1 and i >= 1024:
                            gain = D(1) if i < 1472 else window(128)[128+i-1472] if i < 1600 else D(0)
                        elif block == 3 and i < 1024:
                            gain = D(0) if i < 448 else window(128)[i-448] if i < 576 else D(1)
                        else:
                            gain = window(1024)[i]
                        time[i] = sample * gain
            output = [round_f32(time[i] + self.overlap[i]) for i in range(1024)]
            self.overlap, self.previous = time[1024:], block
            return output


class Decoder:
    def __init__(self):
        self.channels = [Channel(), Channel()]

    def decode(self, expected):
        sides = [state.render(scaled_channel(channel), channel['ics']['block_type'])
                 for state, channel in zip(self.channels, expected)]
        return [value for pair in zip(*sides) for value in pair]
