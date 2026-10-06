"""Pure identification mathematics; independent of project dictionaries."""
from fractions import Fraction
import math
import statistics
import struct
from .common import require, geometry, MAX_DEPTH, REPEAT_EPS, LEAF_EPS


def dot(a, b): return math.fsum(x * y for x, y in zip(a, b))
def norm(v): return math.sqrt(dot(v, v))
def subtract(a, b): return [x - y for x, y in zip(a, b)]
def vmul(v, matrix): return [math.fsum(v[j] * matrix[j][k] for j in range(len(v))) for k in range(len(matrix[0]))]
def infnorm(matrix): return max(math.fsum(abs(x) for x in row) for row in matrix)


def heldout_rms_error(pcm, control, positive_half, zero, calibration_bound):
    """Mode-0 half-scale carrier gives one quantization step on a held-out line.

    Subtract the measured endpoint coefficient errors before applying the
    original one-eighth classification margin. Float32 bit equality is recorded
    separately; it is not a requirement for this held-out waveform.
    """
    require(len(pcm)==len(control)==len(positive_half) and len(pcm)>0, 'held-out waveform sizes differ')
    step=2*math.sqrt(dot(positive_half,positive_half)/len(positive_half))*(1/zero-2*calibration_bound)
    require(step>0, 'held-out calibration separation is insufficient')
    error=math.sqrt(math.fsum((a-b)**2 for a,b in zip(pcm,control))/len(pcm))
    limit=step/8
    require(error<=limit, 'mode-1 held-out RMS exceeds calibration classification margin')
    return error,limit


def normalized_gram_residual(matrix):
    n = len(matrix)
    scale = math.fsum(dot(row, row) for row in matrix) / n
    require(math.isfinite(scale) and scale > 0, 'invalid matrix Gram scale')
    return max(math.fsum(abs(dot(row, other)/scale - float(i == j))
                         for j, other in enumerate(matrix))
               for i, row in enumerate(matrix))


def inverse(matrix, *, order=None, diagnostics=None):
    n = len(matrix)
    require(n > 0 and all(len(row) == n for row in matrix), 'nonsquare matrix')
    require(all(math.isfinite(x) for row in matrix for x in row), 'nonfinite matrix')
    if order is not None:
        require(n == geometry(order)['channels'], 'matrix order and dimensions differ')
    extended = order in (9, 10)
    condition_limit = 128 if extended else 64
    scale = infnorm(matrix)
    require(scale > 0, 'singular matrix')
    a = [list(row) + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(a[i][k]))
        require(abs(a[p][k]) > 1e-14 * scale, 'singular matrix')
        a[k], a[p] = a[p], a[k]
        value = a[k][k]
        a[k] = [x / value for x in a[k]]
        for i in range(n):
            if i != k:
                value = a[i][k]
                a[i] = [x - value * y for x, y in zip(a[i], a[k])]
    result = [row[n:] for row in a]
    condition = scale * infnorm(result)
    residual = infnorm([[x - float(i == k) for k, x in enumerate(vmul(row, result))] for i, row in enumerate(matrix)])
    require(condition <= condition_limit, f'matrix condition exceeds {condition_limit}')
    require(residual <= 1e-10, 'inverse residual exceeds 1e-10')
    gram = normalized_gram_residual(matrix) if extended else None
    if extended:
        require(gram <= 1e-3, 'normalized matrix Gram residual exceeds 0.001')
    if diagnostics is not None:
        diagnostics.update(policy='order9-10-condition128-gram-v1' if extended else 'original-condition64-v1',
                           order=order, condition_inf_limit=condition_limit,
                           inverse_residual_inf_limit=1e-10,
                           normalized_gram_residual_inf=gram,
                           normalized_gram_residual_inf_limit=1e-3 if extended else None)
    return result, condition, residual


def direction_residual(a, b):
    require(norm(a) > 1e-7 and norm(b) > 1e-7, 'unobservable bit difference')
    projection = dot(a, b) / dot(b, b)
    return norm([x - projection * y for x, y in zip(a, b)]) / norm(a)


def validate_words(entries, symbols=64):
    require(symbols in (64, 128, 256, 512), 'unsupported alphabet size')
    require(len(entries) == symbols and {e['symbol'] for e in entries} == set(range(symbols)), 'incomplete alphabet')
    words = [e['codeword'] for e in entries]
    require(all(w and set(w) <= {'0', '1'} for w in words), 'invalid codeword')
    require(all(e['bit_length'] == len(e['codeword']) <= MAX_DEPTH for e in entries), 'invalid code length')
    require(not any(i != j and b.startswith(a) for i, a in enumerate(words) for j, b in enumerate(words)), 'prefix collision')
    require(sum((Fraction(1, 1 << len(w)) for w in words), Fraction()) == 1, 'incomplete prefix tree')


def infer_tree(query, coordinate=False, symbols=64):
    require(symbols in (64, 128, 256, 512), 'unsupported alphabet size')
    zero = symbols // 2
    leaf_eps, repeat_eps = 1/(8*(symbols-1)), 1/(32*(symbols-1))
    leaves, decisions = [], []
    def visit(prefix):
        # Exactly 32 first-symbol bits: ancestor extrema share actual requests.
        a, aid = query(prefix + '0' * (MAX_DEPTH - len(prefix)))
        b, bid = query(prefix + '1' * (MAX_DEPTH - len(prefix)))
        decisions.append(dict(prefix=prefix, left=a, right=b, evidence=[aid, bid]))
        if abs(a - b) <= leaf_eps if coordinate else a == b:
            require(prefix, 'first symbol not observable')
            entry = dict(codeword=prefix, bit_length=len(prefix), evidence=[aid, bid])
            entry.update(coordinate=(a + b) / 2) if coordinate else entry.update(symbol=int(a))
            leaves.append(entry)
            require(len(leaves) <= symbols, 'too many leaves')
        else:
            require(len(prefix) < MAX_DEPTH, 'search depth exhausted')
            visit(prefix + '0')
            visit(prefix + '1')
    visit('')
    scale = None
    if coordinate:
        require(len(leaves) == symbols, 'incomplete observed alphabet')
        ordered = sorted(leaves, key=lambda e: e['coordinate'])
        step = statistics.median(b['coordinate'] - a['coordinate'] for a, b in zip(ordered, ordered[1:]))
        require(step > leaf_eps, 'symbol coordinates collide')
        magnitude = round(1 / step)
        require(1 <= magnitude < symbols, 'invalid coordinate scale')
        zero_index = min(range(symbols), key=lambda i: abs(ordered[i]['coordinate']))
        require(zero_index in (zero-1, zero) and abs(ordered[zero_index]['coordinate']) <= repeat_eps, 'quantization sign ambiguous')
        scale = magnitude if zero_index == zero else -magnitude
        for i, entry in enumerate(ordered):
            entry['symbol'] = i if zero_index == zero else symbols - 1 - i
            entry['grid_error'] = abs(entry['coordinate'] - (entry['symbol'] - zero) / scale)
            require(entry['grid_error'] <= repeat_eps, 'quantization grid inconsistent')
    validate_words(leaves, symbols)
    return sorted(leaves, key=lambda e: e['symbol']), decisions, scale


def matrix_entry(estimates, weights, calibration_bounds):
    estimate = dot(estimates, weights)
    radius = max(8 * dot(weights, calibration_bounds), max(abs(v - estimate) for v in estimates), 5e-8)
    require(math.isfinite(estimate) and math.isfinite(radius), 'nonfinite matrix estimate')
    candidates = []
    # Unqualified broad intervals must not cause unbounded grid enumeration.
    lower = max(-(1 << 20) + 1, math.floor((estimate - radius) * 1e6) - 1)
    upper = min((1 << 20) - 1, math.ceil((estimate + radius) * 1e6) + 1)
    integers = range(lower, upper + 1) if upper - lower <= 32 else ()
    for integer in integers:
        if abs(integer) >= 1 << 20:
            continue
        value = struct.unpack('<f', struct.pack('<f', integer / 1e6))[0]
        if abs(value - estimate) <= radius:
            candidates.extend([0, 0x80000000] if integer == 0 else [struct.unpack('<I', struct.pack('<f', value))[0]])
    candidates = sorted(set(candidates))
    qualified = radius < 2.5e-7 and len(candidates) == 1
    return dict(estimate=estimate, empirical_half_width=radius, candidate_bits=candidates,
                float32_bits=candidates[0] if qualified else None,
                status='qualified_candidate' if qualified else 'unresolved')
