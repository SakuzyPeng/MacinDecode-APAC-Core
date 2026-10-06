"""Order-scoped conditioning policy, using only artificial matrices and trees."""
import functools
import math
import struct
import unittest

from hoa_blackbox_lib.common import ExperimentError
from hoa_blackbox_lib.maths import dot, infer_tree, inverse
from test_hoa_blackbox import make_words


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def dct_matrix(n):
    return [[f32(round((1/math.sqrt(n) if j == 0 else
                       math.sqrt(2/n)*math.cos(math.pi*(i+.5)*j/n))*1e6)/1e6)
             for j in range(n)] for i in range(n)]


def diagonal(n, last):
    return [[(last if i == n-1 else 1.) if i == j else 0.
             for j in range(n)] for i in range(n)]


class ConditioningTests(unittest.TestCase):
    def test_default_and_orders_through_eight_retain_64_limit(self):
        with self.assertRaisesRegex(ExperimentError, 'exceeds 64'):
            inverse(diagonal(4, 80))
        for order in (1, 4, 8):
            with self.subTest(order=order):
                with self.assertRaisesRegex(ExperimentError, 'exceeds 64'):
                    inverse(diagonal((order+1)**2, 80), order=order)
        policy = {}
        inverse(diagonal(25, 2), order=4, diagnostics=policy)
        self.assertEqual(policy['condition_inf_limit'], 64)
        self.assertIsNone(policy['normalized_gram_residual_inf_limit'])

    def test_extension_rejects_wrong_dimensions_and_nonorthogonal_matrices(self):
        with self.assertRaisesRegex(ExperimentError, 'dimensions differ'):
            inverse(diagonal(121, 1), order=9)
        for order in (9, 10):
            with self.subTest(order=order):
                with self.assertRaisesRegex(ExperimentError, 'Gram residual'):
                    inverse(diagonal((order+1)**2, 80), order=order)

    def test_extension_still_rejects_singular_and_over_128(self):
        with self.assertRaisesRegex(ExperimentError, 'singular'):
            inverse(diagonal(100, 0), order=9)
        with self.assertRaisesRegex(ExperimentError, 'exceeds 128'):
            inverse(diagonal(121, 129), order=10)

    def test_rounded_high_order_bases_recover_512_symbols_under_ulp_noise(self):
        for order in (9, 10):
            n = (order+1)**2
            matrix = dct_matrix(n)
            with self.assertRaisesRegex(ExperimentError, 'exceeds 64'):
                inverse(matrix)
            words = make_words(819+n, 512)
            lookup = {word:q for q, word in enumerate(words)}
            def symbol(pattern):
                return next(lookup[pattern[:length]] for length in range(1,33)
                            if pattern[:length] in lookup)
            tails = {str(bit):symbol(str(bit)*32) for bit in (0,1)}
            sums = [math.fsum(row[j] for row in matrix) for j in range(n)]
            for sign in (-1, 1):
                with self.subTest(order=order, sign=sign):
                    alpha = sign*3/32
                    policy = {}
                    inv, condition, residual = inverse([[alpha*x for x in row] for row in matrix],
                                                       order=order, diagnostics=policy)
                    self.assertGreater(condition, 64)
                    self.assertLessEqual(condition, 128)
                    self.assertLessEqual(residual, 1e-10)
                    self.assertEqual(policy['condition_inf_limit'], 128)
                    self.assertLessEqual(policy['normalized_gram_residual_inf'], 1e-3)
                    first_inverse = [row[0] for row in inv]
                    errors = []
                    @functools.lru_cache(None)
                    def query(pattern):
                        q = symbol(pattern)
                        a, b = (q-256)/256, (tails[pattern[-1]]-256)/256
                        y = [f32(b*sums[j]+(a-b)*matrix[0][j]) for j in range(n)]
                        for j, value in enumerate(y):
                            if value:
                                bits = struct.unpack('<I', struct.pack('<f', value))[0]
                                y[j] = struct.unpack('<f', struct.pack('<I', bits+(1 if (j+q)%2 else -1)))[0]
                        value = dot(y, first_inverse)
                        errors.append(abs(value-a/alpha))
                        return value, pattern
                    entries, _, scale = infer_tree(query, coordinate=True, symbols=512)
                    self.assertEqual([entry['codeword'] for entry in entries], words)
                    self.assertEqual(scale, sign*24)
                    self.assertLess(max(errors), 1/(32*511))


if __name__ == '__main__':
    unittest.main()
