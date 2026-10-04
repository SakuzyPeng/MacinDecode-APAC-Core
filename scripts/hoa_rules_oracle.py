#!/usr/bin/env python3
"""Independent rules behind HOA format constants that were first recorded as observations.

The committed data files stay byte-identical (their digests are published identities); this
oracle re-derives what follows from a rule and checks it against them:

- subband grids: methods 0/1/2, 1..16 bands, in hoa-dynamic-format-v1/v2 and
  hoa-salient-subbands-format-v1/v2 (exact; one listed exception);
- salient coefficient groups and mode structure for orders 1..10 (exact);
- static ambient sign tables: row orders of the Sylvester-Hadamard matrix (exact);
- source-layout matrices: (order + 1) * pinv(Y), Y the N3D spherical harmonics at the speaker
  directions, for every full-rank layout (within Float32 rounding of the reference values).

Rank-deficient layouts (horizontal speakers with height or higher-order coefficients) have no
unique pseudo-inverse; their stored matrices remain observations.
"""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import struct

DATA = Path(__file__).resolve().parents[1] / 'data'
# Zwicker critical-band upper edges (Hz) up to 15.5 kHz, then the 24 kHz reference scale.
CRITICAL_BAND_EDGES = [100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320,
                       2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500, 24000]
REFERENCE_SCALE = 24000
LONG_LINES = 1024
SHORT_RATIO = 8
# (method, subbands, boundary index) -> short-window end that differs from the rule.
SUBBAND_EXCEPTIONS = {(0, 9, 0): 2}
SYLVESTER = [[(-1) ** bin(i & j).count('1') for j in range(4)] for i in range(4)]
STATIC_AMBIENT_ROW_ORDERS = [[0, 3, 2, 1], [0, 2, 3, 1], [0, 1, 2, 3]]
# Speaker (azimuth, elevation) in degrees per layout tag, LFE channels omitted, in channel order.
# Inferred from the recorded matrices; every angle is a round value.
SPEAKERS = {
    6553601: [(0, 0)],
    6619138: [(30, 0), (-30, 0)],
    7077892: [(30, 0), (-30, 0), (110, 0), (-110, 0)],
    7405571: [(30, 0), (-30, 0), (0, 0)],
    7471107: [(0, 0), (30, 0), (-30, 0)],
    7536644: [(30, 0), (-30, 0), (0, 0), (180, 0)],
    7602180: [(0, 0), (30, 0), (-30, 0), (180, 0)],
    7667717: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0)],
    7864325: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0)],
    7929862: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0)],
    8060934: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0)],
    8126470: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0)],
    8192007: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (180, 0)],
    8323080: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (60, 0), (-60, 0)],
    8388616: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (135, 0), (-135, 0)],
    8585219: [(30, 0), (-30, 0), (180, 0)],
    8650756: [(30, 0), (-30, 0), (110, 0), (-110, 0)],
    9240582: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (180, 0)],
    9306119: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (180, 0)],
    9371655: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (135, 0), (-135, 0)],
    9437192: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (135, 0), (-135, 0), (180, 0)],
    11993096: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (135, 0), (-135, 0)],
    12058632: [(0, 0), (30, 0), (-30, 0), (110, 0), (-110, 0), (45, 35), (-45, 35)],
    12582924: [(30, 0), (-30, 0), (0, 0), (90, 0), (-90, 0), (135, 0), (-135, 0), (30, 35), (-30, 35), (135, 35), (-135, 35)],
    12648464: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (135, 0), (-135, 0), (60, 0), (-60, 0), (45, 35), (-45, 35), (90, 35), (-90, 35), (135, 35), (-135, 35)],
    12713992: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (90, 35), (-90, 35)],
    12779530: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (45, 35), (-45, 35), (135, 35), (-135, 35)],
    12845066: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (135, 0), (-135, 0), (90, 35), (-90, 35)],
    13369368: [(60, 0), (-60, 0), (0, 0), (135, 0), (-135, 0), (30, 0), (-30, 0), (180, 0), (90, 0), (-90, 0), (45, 35), (-45, 35), (0, 35), (0, 90), (135, 35), (-135, 35), (90, 35), (-90, 35), (0, 35), (0, -15), (45, -15), (-45, -15)],
    13434888: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (30, 35), (-30, 35)],
    13500428: [(30, 0), (-30, 0), (0, 0), (135, 0), (-135, 0), (90, 0), (-90, 0), (45, 35), (-45, 35), (0, 35)],
    13565962: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (30, 35), (-30, 35), (110, 35), (-110, 35)],
    13631500: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (30, 35), (-30, 35), (0, 35), (110, 35), (-110, 35), (0, 90)],
    13697038: [(30, 0), (-30, 0), (0, 0), (110, 0), (-110, 0), (150, 0), (-150, 0), (30, 35), (-30, 35), (0, 35), (110, 35), (-110, 35), (0, 90)],
    13762572: [(30, 0), (-30, 0), (0, 0), (135, 0), (-135, 0), (90, 0), (-90, 0), (30, 35), (-30, 35), (135, 35), (-135, 35)],
    13828110: [(30, 0), (-30, 0), (0, 0), (135, 0), (-135, 0), (90, 0), (-90, 0), (45, 35), (-45, 35), (135, 35), (-135, 35), (60, 0), (-60, 0)],
}
RELATIVE_TOLERANCE = 1e-6
# Layouts whose matrix follows the formula; the others are rank-deficient.
FULL_RANK_LAYOUTS = 18


def half_up(value):
    return math.floor(value + Fraction(1, 2))


def interpolate(points, position):
    index = math.floor(position)
    if index >= len(points) - 1:
        return Fraction(points[-1])
    return points[index] + (position - index) * (points[index + 1] - points[index])


def subband_ends(method, subbands, aac_long_offsets):
    """Short-window ends; long-window ends are SHORT_RATIO times these."""
    ends = []
    for k in range(1, subbands + 1):
        if method == 0:
            points = [0] + CRITICAL_BAND_EDGES
            line = interpolate(points, Fraction(len(points) - 1) * k / subbands) * LONG_LINES / REFERENCE_SCALE
            end = half_up(line / SHORT_RATIO)
        elif method == 1:
            points = aac_long_offsets
            end = half_up(interpolate(points, Fraction(len(points) - 1) * k / subbands) / SHORT_RATIO)
        else:
            end = half_up(Fraction(half_up(Fraction(LONG_LINES * k, subbands)), SHORT_RATIO))
        ends.append(SUBBAND_EXCEPTIONS.get((method, subbands, k - 1), end))
    return ends


def recorded_grids():
    """(method, subbands) -> (long_ends, short_ends) from the four format files, which must agree."""
    load = lambda name: json.loads((DATA / name).read_text())
    v1, v2 = load('hoa-dynamic-format-v1.json'), load('hoa-dynamic-format-v2.json')
    s1, s2 = load('hoa-salient-subbands-format-v1.json'), load('hoa-salient-subbands-format-v2.json')
    grids = {}
    def add(key, long_ends, short_ends):
        if grids.setdefault(key, (long_ends, short_ends)) != (long_ends, short_ends):
            raise AssertionError(f'subband grid {key} differs between format files')
    for method in range(3):
        add((method, v1['subbands']), v1['long_ends'][method], v1['short_ends'][method])
        for table in v2['tables']:
            add((method, table['subbands']), table['long_ends'][method], table['short_ends'][method])
    for table in s1['tables']:
        add((s1['method'], table['subbands']), table['long_ends'], table['short_ends'])
    for table in s2['tables']:
        add((table['method'], table['subbands']), table['long_ends'], table['short_ends'])
    if v1['perceptual_anchors_hz'] != CRITICAL_BAND_EDGES or v1['reference_scale'] != REFERENCE_SCALE:
        raise AssertionError('perceptual anchors are not the critical-band edges')
    return grids, v1['aac_long_offsets']


def check_subbands():
    grids, aac = recorded_grids()
    sq = json.loads((DATA / 'sq-codebooks.json').read_text())['long_offsets']
    if aac != sq:
        raise AssertionError('method 1 anchors are not the 44.1/48 kHz AAC long-window offsets')
    if sorted(grids) != [(m, n) for m in range(3) for n in range(1, 17)]:
        raise AssertionError('expected methods 0..2 with 1..16 subbands')
    for (method, subbands), (long_ends, short_ends) in grids.items():
        expected = subband_ends(method, subbands, aac)
        if short_ends != expected or long_ends != [SHORT_RATIO * end for end in expected]:
            raise AssertionError(f'method {method}, {subbands} subbands: {short_ends} != rule {expected}')
    return dict(grids=len(grids), exceptions=len(SUBBAND_EXCEPTIONS))


def check_salient_groups():
    first = None
    for order in range(1, 11):
        shared = json.loads((DATA / f'hoa-salient-order{order}-shared-v1.json').read_text())
        acn = [(l, m) for l in range(order + 1) for m in range(-l, l + 1)]
        odd = [i for i, (l, m) in enumerate(acn) if (l + abs(m)) % 2]
        even = [i for i, (l, m) in enumerate(acn) if not (l + abs(m)) % 2]
        if shared['groups'] != [list(range(len(acn))), odd, even]:
            raise AssertionError(f'order {order}: groups are not all / odd l+|m| / even l+|m|')
        structure = [(m['mode'], m['signs'], m['group_indices'], len(m['matrix_indices'])) for m in shared['modes']]
        if first is None:
            first = structure
        elif structure != first:
            raise AssertionError(f'order {order}: mode structure differs from order 1')
    return dict(orders=10)


def check_static_ambient():
    tables = json.loads((DATA / 'hoa-static-ambient-tables-v1.json').read_text())
    expected = [[SYLVESTER[row] for row in order] for order in STATIC_AMBIENT_ROW_ORDERS]
    if tables['encoder_signs'] != expected or tables['divisor'] != 2 or tables['decoder_operation'] != 'transpose':
        raise AssertionError('static ambient tables are not halved Sylvester-Hadamard row orders')
    return dict(tables=len(expected))


def legendre(l, m, x):
    """Associated Legendre function without the Condon-Shortley phase."""
    root = math.sqrt(max(0.0, 1 - x * x))
    previous = 1.0
    for i in range(1, m + 1):
        previous *= (2 * i - 1) * root
    if l == m:
        return previous
    current = x * (2 * m + 1) * previous
    for degree in range(m + 2, l + 1):
        previous, current = current, ((2 * degree - 1) * x * current - (degree + m - 1) * previous) / (degree - m)
    return current


def harmonics(order, azimuth, elevation):
    """Real N3D spherical harmonics in ACN order."""
    a, e = math.radians(azimuth), math.radians(elevation)
    values = []
    for l in range(order + 1):
        for m in range(-l, l + 1):
            k = abs(m)
            norm = math.sqrt((2 * l + 1) * (2 if k else 1) * math.factorial(l - k) / math.factorial(l + k))
            values.append(norm * legendre(l, k, math.sin(e)) * (math.cos(k * a) if m >= 0 else math.sin(k * a)))
    return values


def inverse(matrix):
    """Gauss-Jordan inverse, or None when a pivot vanishes relative to the largest entry."""
    n = len(matrix)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    scale = max(abs(v) for row in matrix for v in row)
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(a[r][column]))
        if abs(a[pivot][column]) <= 1e-9 * scale:
            return None
        a[column], a[pivot] = a[pivot], a[column]
        p = a[column][column]
        a[column] = [v / p for v in a[column]]
        for r in range(n):
            if r != column and a[r][column]:
                f = a[r][column]
                a[r] = [v - f * w for v, w in zip(a[r], a[column])]
    return [row[n:] for row in a]


def layout_matrix(order, speakers):
    """(order + 1) * pinv(Y) for full-column-rank Y (coefficients x speakers), else None."""
    columns = [harmonics(order, a, e) for a, e in speakers]
    gram = [[sum(x * y for x, y in zip(u, v)) for v in columns] for u in columns]
    g = inverse(gram)
    if g is None:
        return None
    coefficients = len(columns[0])
    return [[(order + 1) * sum(g[i][j] * columns[j][k] for j in range(len(columns))) for k in range(coefficients)]
            for i in range(len(columns))]


def check_source_layouts():
    data = json.loads((DATA / 'hoa-source-layout-format-v1.json').read_text())
    if sorted(SPEAKERS) != sorted(layout['tag'] for layout in data['layouts']):
        raise AssertionError('speaker table does not cover the recorded layouts')
    full, deficient, worst = 0, 0, 0.0
    for layout in data['layouts']:
        speakers = SPEAKERS[layout['tag']]
        rows, columns = layout['matrix_rows'], layout['matrix_columns']
        if rows != len(speakers) or rows != len(layout['channel_labels']) - len(layout['lfe_indices']):
            raise AssertionError(f'layout {layout["tag"]}: speaker count')
        if columns <= 0 or math.isqrt(columns) ** 2 != columns:
            raise AssertionError(f'layout {layout["tag"]}: coefficient count must be a positive square')
        words = data['matrices'][layout['matrix_id']]
        if len(words) != rows * columns:
            raise AssertionError(f'layout {layout["tag"]}: matrix length {len(words)}, expected {rows * columns}')
        recorded = [struct.unpack('<f', struct.pack('<I', bits))[0] for bits in words]
        if not all(math.isfinite(value) for value in recorded):
            raise AssertionError(f'layout {layout["tag"]}: nonfinite matrix coefficient')
        derived = layout_matrix(math.isqrt(columns) - 1, speakers)
        if derived is None:
            deficient += 1
            continue
        largest = max(abs(v) for v in recorded)
        if largest == 0:
            raise AssertionError(f'layout {layout["tag"]}: matrix has zero magnitude')
        error = max(abs(d - r) for d, r in zip((v for row in derived for v in row), recorded)) / largest
        if not math.isfinite(error) or error > RELATIVE_TOLERANCE:
            raise AssertionError(f'layout {layout["tag"]}: relative error {error:.3g} exceeds {RELATIVE_TOLERANCE}')
        full += 1
        worst = max(worst, error)
    if full != FULL_RANK_LAYOUTS:
        raise AssertionError(f'{full} full-rank layouts, expected {FULL_RANK_LAYOUTS}')
    return dict(full_rank=full, rank_deficient=deficient, max_relative_error=float(f'{worst:.3g}'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='verify the committed data (the only mode)')
    parser.parse_args()
    print(json.dumps(dict(subbands=check_subbands(), salient_groups=check_salient_groups(),
                          static_ambient=check_static_ambient(), source_layouts=check_source_layouts())))


if __name__ == '__main__':
    main()
