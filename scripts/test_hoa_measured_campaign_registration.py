"""Production registration must enforce validation beyond a passed flag."""
import copy
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

from hoa_blackbox_lib.common import ExperimentError
import register_hoa_measured_campaign as registration


def complete_mode1_checks(precision=6):
    levels = 1 << precision
    zero = levels // 2
    checks = []
    for gain in (128, 129):
        for symbol in range(levels):
            checks.append(dict(label=f'symbol:{gain}:{symbol}', gain=gain, line=0,
                               pcm_bit_identical_to_mode0=True))
        for i in range(8):
            checks.append(dict(label=f'mixed:{gain}:{i}', gain=gain, line=0,
                               pcm_bit_identical_to_mode0=True))
        for symbol in (0, zero-1, zero, levels-1):
            checks.append(dict(label=f'padding:{gain}:{symbol}', gain=gain, line=0,
                               pcm_bit_identical_to_mode0=True, padding_bit_identical=True))
        for symbol in (0, zero//2, zero, 3*zero//2, levels-1):
            checks.append(dict(label=f'line1-symbol:{gain}:{symbol}', gain=gain, line=1,
                               pcm_bit_identical_to_mode0=False, max_heldout_rms_to_limit_ratio=0.00025))
    for i in range(4):
        checks.append(dict(label=f'line1-mixed:{i}', gain=129, line=1,
                           pcm_bit_identical_to_mode0=False, max_heldout_rms_to_limit_ratio=0.00025))
    return checks


class RegistrationTests(unittest.TestCase):
    def test_high_order_write_refuses_before_evidence_or_source_access(self):
        for order in (4, 10):
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(sys, 'argv', ['register', '--campaign', tmp, '--order', str(order),
                                                '--out', str(Path(tmp)/'report.json'), '--write']), \
                 mock.patch.object(registration, 'prepare', side_effect=AssertionError('must not prepare')):
                with self.assertRaisesRegex(ExperimentError, 'production HOA support ends at order 3'):
                    registration.main()
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_current_engine_frozen_validation_passes_registration_gate(self):
        from test_hoa_blackbox import FakeBackend, fake_engine, new_store
        with tempfile.TemporaryDirectory() as temporary:
            store = new_store(Path(temporary)/'batch', order=4)
            try:
                fake_engine(store, FakeBackend(order=4)).run()
                validation = store.stage('mode1', 'validation')
                self.assertEqual(validation['status'], 'passed')
                registration.audit_mode1_validation(validation['checks'], 6)
            finally:
                store.close()

    def test_all_precisions_accept_complete_coverage_with_heldout_rounding(self):
        for precision in range(6, 10):
            with self.subTest(precision=precision):
                registration.audit_mode1_validation(complete_mode1_checks(precision), precision)

    def test_legacy_passed_validation_without_heldout_carrier_is_refused(self):
        legacy = dict(status='passed', checks=[c for c in complete_mode1_checks() if c['line'] == 0])
        with self.assertRaisesRegex(ExperimentError, 'coverage is incomplete'):
            registration.audit_mode1_validation(legacy['checks'], 6)

    def test_missing_mixture_or_padding_result_is_refused(self):
        checks = complete_mode1_checks()
        checks = [c for c in checks if c['label'] != 'mixed:129:7']
        with self.assertRaisesRegex(ExperimentError, 'coverage is incomplete'):
            registration.audit_mode1_validation(checks, 6)
        checks = complete_mode1_checks()
        next(c for c in checks if c['label'] == 'padding:128:0').pop('padding_bit_identical')
        with self.assertRaisesRegex(ExperimentError, 'fresh decoder'):
            registration.audit_mode1_validation(checks, 6)

    def test_bad_rms_carrier_gain_or_primary_pcm_is_refused(self):
        for change in ({'max_heldout_rms_to_limit_ratio': None},
                       {'max_heldout_rms_to_limit_ratio': float('nan')},
                       {'max_heldout_rms_to_limit_ratio': -0.1},
                       {'max_heldout_rms_to_limit_ratio': 1.001},
                       {'gain': 128}, {'line': 0}):
            with self.subTest(change=change):
                checks = complete_mode1_checks()
                checks[-1].update(change)
                with self.assertRaises(ExperimentError):
                    registration.audit_mode1_validation(checks, 6)
        checks = complete_mode1_checks()
        checks[0]['pcm_bit_identical_to_mode0'] = False
        with self.assertRaisesRegex(ExperimentError, 'primary carrier'):
            registration.audit_mode1_validation(checks, 6)

    def test_duplicate_labels_are_refused(self):
        checks = complete_mode1_checks()
        checks.append(copy.deepcopy(checks[-1]))
        with self.assertRaisesRegex(ExperimentError, 'duplicate'):
            registration.audit_mode1_validation(checks, 6)

    def test_partial_order_refuses_before_any_evidence_or_source_access(self):
        with mock.patch.object(registration, 'read_campaign', return_value={'orders': {'4': {'status': 'partial'}}}), \
             mock.patch.object(registration, 'Store', side_effect=AssertionError('must refuse before reading')):
            with self.assertRaisesRegex(ExperimentError, 'all 32 codebooks and four matrices'):
                registration.audit_order(Path('unused-campaign'), 4)


if __name__ == '__main__':
    unittest.main()
