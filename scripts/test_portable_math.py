"""Independent rounding, oracle and acceptance gate regressions."""
import copy
from decimal import Decimal as D, localcontext
import json
import os
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generate_sq_math import DESTINATION, document
from portable_tools import required_binary
from sq_math import ieee_bits, inverse_magnitude, sin_pi, cos_pi
from sq_oracle import cosine_grid, direct_imdct, scaled_channel
from spectrum_vectors import frame, bundle
from sq_oracle import Decoder
from validate_replay import command
from validate_portable import COUNTS, compare_pcm, finalize, match_record, validate_reference


class MathematicalTests(unittest.TestCase):
    def test_constants_reproduce_at_both_precisions(self):
        self.assertEqual(json.loads(DESTINATION.read_text()), document())

    def test_v1_constants_remain_reproducible_as_historical_data(self):
        self.assertEqual(json.loads(DESTINATION.with_name('sq-math-v1.json').read_text()),
                         document('apac-sq-math-v1'))

    def test_single_rounding_pcm_against_decimal_across_windows_and_gains(self):
        binary = required_binary()
        with tempfile.TemporaryDirectory(prefix='sq-v2-math-') as temporary:
            for rate in (44100, 48000):
                for sf, q in ((98, 3), (198, 3), (255, 8191)):
                    signal = dict(gain=sf, left={0: (11, [q, -q], sf)}, right={1: (11, [-q, q], sf)})
                    for short in (False, True):
                        with self.subTest(rate=rate, sf=sf, q=q, short=short):
                            sequence = ([{}, signal, signal, {}, {}] if not short else
                                        [dict(block=1), dict(signal, block=2, grouping=0x55),
                                         dict(signal, block=2, grouping=0x55), dict(block=3), {}])
                            generated = [frame(case, rate) for case in sequence]
                            root = Path(temporary)/f'{rate}-{sf}-{short}'
                            root.mkdir()
                            bundle(root/'packets', [raw for raw, _ in generated], rate)
                            result = command(binary, 'decode-sq', root/'packets', '--out', root/'pcm')
                            self.assertEqual(result['numeric_profile'], 'apac-sq-math-v2')
                            raw = (root/'pcm/pcm.f32le').read_bytes()
                            actual = struct.unpack(f'<{len(raw)//4}f', raw)
                            decoder = Decoder()
                            expected = [x for _, truth in generated for x in decoder.decode(truth)]
                            metrics = compare_pcm(actual, expected)
                            self.assertTrue(metrics['passed'], metrics)

    def test_ieee_rounding_ties_subnormals_carry_and_signed_zero(self):
        with localcontext() as ctx:
            ctx.prec = 200
            self.assertEqual(ieee_bits(D(1) + D(2)**-24, 32), 0x3f800000)
            self.assertEqual(ieee_bits(D(1) + 3*D(2)**-24, 32), 0x3f800002)
            self.assertEqual(ieee_bits(D(2) - D(2)**-24, 32), 0x40000000)
            self.assertEqual(ieee_bits(D(2)**-149, 32), 1)
            self.assertEqual(ieee_bits(D(2)**-150, 32), 0)
            self.assertEqual(ieee_bits(-D(2)**-149, 32), 0x80000001)
            self.assertEqual(ieee_bits(D('-0'), 32), 0)
            self.assertEqual(ieee_bits(D(1) + D(2)**-53, 64), 0x3ff0000000000000)
            self.assertEqual(ieee_bits(D(1) + 3*D(2)**-53, 64), 0x3ff0000000000002)
            with self.assertRaises(OverflowError):
                ieee_bits(D(2)**128, 32)
            with self.assertRaises(ValueError):
                ieee_bits(D('NaN'), 64)

    def test_quadrants_exact_roots_and_direct_transform_symmetry(self):
        with localcontext() as ctx:
            ctx.prec = 100
            self.assertEqual(sin_pi(-1, 2), -1)
            self.assertEqual(cos_pi(1, 2), 0)
            self.assertEqual(sin_pi(1, 6), D('0.5'))
            self.assertEqual(inverse_magnitude(4096), 65536)
        for n in (128, 1024):
            grid = cosine_grid(n)
            self.assertEqual(len(grid), 8*n)
            self.assertEqual([grid[i*n] for i in (0, 2, 4, 6)], [1, 0, -1, 0])
            impulse = direct_imdct(n, ((0, 1.0),))
            self.assertEqual(impulse[0], impulse[n-1].copy_negate())
            self.assertEqual(impulse[n], impulse[-1])

    def test_scaled_truth_comes_from_integer_values_and_scale_factors(self):
        _, expected = frame(dict(left={0:(11,[8191,-17],160)}))
        a = scaled_channel(expected[0])
        expected[0]['scaled'] = [float('nan')]*1024
        self.assertEqual(scaled_channel(expected[0]), a)
        self.assertGreater(a[0], 0)
        self.assertLess(a[1], 0)


class AcceptanceGateTests(unittest.TestCase):
    def test_missing_binary_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'APAC_TOOL_BINARY':str(Path(tmp)/'missing')}):
            with self.assertRaises(FileNotFoundError):
                required_binary()

    def test_sample_failure_and_nonfinite_cannot_pass(self):
        result = compare_pcm([0., 0.1], [0., 0.2])
        self.assertFalse(result['passed'])
        self.assertEqual(result['first_failure']['sample'], 1)
        self.assertEqual(result['failed_samples'], 1)
        with self.assertRaises(AssertionError):
            compare_pcm([float('nan')], [0.])
        with self.assertRaises(AssertionError):
            compare_pcm([], [])

    def test_exact_gate_rejects_one_bit_or_identity_change(self):
        record = dict(rate=48000, index=0, kind='test', frames=4096,
                      input_sha256='input', quantized_sha256='q', scaled_sha256='s',
                      structure_sha256='structure', pcm_sha256='pcm')
        match_record(record, record)
        for key in record:
            altered = dict(record, **{key:'different'})
            with self.subTest(key=key), self.assertRaises(AssertionError):
                match_record(altered, record)

    def test_missing_failed_or_mismatched_reference_is_rejected(self):
        current = dict(schema_version=1, numeric_profile='test', code_commit='commit',
                       source_sha256='source', tables_sha256='tables', atol=1e-6, rtol=1e-5)
        reference = dict(current, passed=True, mode='independent_math', errors=[],
                         counts=COUNTS, pcm_metrics=dict(failed_samples=0))
        for stage, count in COUNTS.items():
            reference[stage] = [dict(rate=rate, index=i, passed=True)
                                for rate in (48000, 44100) for i in range(count//2)]
        validate_reference(reference, current)
        older=dict(reference,code_commit='older',source_sha256='older source')
        validate_reference(older,current,regression=True)
        with self.assertRaises(AssertionError):validate_reference(older,current)
        for key in ('tables_sha256','numeric_profile'):
            with self.assertRaises(AssertionError):
                validate_reference(dict(older,**{key:'changed'}),current,regression=True)
        for key in ('source_sha256', 'tables_sha256', 'code_commit', 'mode', 'passed'):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                validate_reference(dict(reference, **{key:'wrong'}), current)
        bad = copy.deepcopy(reference)
        bad['pcm'].pop()
        with self.assertRaises(AssertionError):
            validate_reference(bad, current)
        bad = copy.deepcopy(reference)
        bad['spectra'][12]['index'] = 11
        with self.assertRaises(AssertionError):
            validate_reference(bad, current)
        empty = dict(mode='independent_math', spectra=[], pcm=[], errors=[])
        finalize(empty)
        self.assertFalse(empty['passed'])


if __name__ == '__main__':
    unittest.main()
