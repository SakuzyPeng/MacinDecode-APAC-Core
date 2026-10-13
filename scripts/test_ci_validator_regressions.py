"""Standalone validator contracts preserved by the CI execution context."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import ci_support
import validate_cac
import validate_hoa_shared
import validate_tns


class ReferenceTests(unittest.TestCase):
    def test_absent_reference_keeps_independent_mathematics(self):
        with patch.object(ci_support, 'CONTEXT', None):
            self.assertIsNone(ci_support.checked_reference(None))
            self.assertTrue(ci_support.mathematics())

    def test_empty_nonobject_and_unsuccessful_references_are_rejected(self):
        for reference in ({}, [], False, 0, '', {'passed': False}, {'passed': 1}):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                ci_support.checked_reference(reference)

    def test_successful_legacy_and_full_math_reports_remain_usable(self):
        for extra in ({}, {'mode': 'independent_math'},
                      {'mode': 'independent_math', 'ci_profile': 'ci-full'}):
            reference = dict(passed=True, cases=[{'passed': True}], errors=[], **extra)
            with self.subTest(extra=extra):
                self.assertIs(ci_support.checked_reference(reference), reference)
                self.assertFalse(ci_support.mathematics(reference))

    def test_empty_reference_fails_before_the_shared_validator_runs(self):
        with tempfile.TemporaryDirectory(prefix='apac-empty-reference-') as tmp:
            root = Path(tmp)
            binary = root / 'mapac'
            binary.write_bytes(b'unused executable')
            reference = root / 'reference.json'
            reference.write_text('{}\n', encoding='utf-8')
            output = root / 'report.json'
            argv = ['validate_hoa_shared', '--binary', str(binary),
                    '--reference-report', str(reference), '--report', str(output),
                    '--case', 'rate-tools-48000']
            with patch.object(sys, 'argv', argv), \
                    patch.object(validate_hoa_shared.vectors, 'manifest', return_value={'sha256': 'vectors'}), \
                    patch.object(validate_hoa_shared, 'validate') as validate, \
                    contextlib.redirect_stdout(io.StringIO()):
                status = validate_hoa_shared.main()
            self.assertEqual(status, 1)
            validate.assert_not_called()
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertIs(report['passed'], False)
            self.assertEqual(report['cases'], [])
            self.assertIn('reference', report['errors'][0])


class NativeDiagnosticTests(unittest.TestCase):
    def test_native_case_indices_continue_across_batches_and_reset_per_rate(self):
        # Exercise both native orchestrators without requiring Apple's binary
        # or LLDB on the Linux and Windows CI runners.
        cases = [dict(kind='matrix_basis') for _ in range(65)]
        expected = [(rate, index) for rate in (48000, 44100) for index in range(len(cases))]
        for validator in (validate_cac, validate_tns):
            with self.subTest(validator=validator.__name__):
                report = dict(native_artificial=[], started_utc='test')
                with patch.object(validator, 'cases', return_value=cases), \
                        patch.object(validator, 'packet', return_value=(b'packet', {})), \
                        patch.object(validator, 'bundle'), \
                        patch.object(validator, 'inspect', side_effect=lambda binary, packets, root, count: ({}, [{} for _ in range(count)])), \
                        patch.object(validator, 'trace_bundle', return_value={}), \
                        patch.object(validator, 'check_native', side_effect=lambda rows, *args: [dict(numeric_passed=True, isolated_numeric_passed=True) for _ in rows]), \
                        patch.object(validator, 'sha256_file', return_value='trace'), \
                        contextlib.redirect_stderr(io.StringIO()):
                    validator.native_artificial(Path('unused-native-binary'), report)
                self.assertEqual([(r['rate'], r['index']) for r in report['native_artificial']], expected)
                self.assertTrue(all(r['kind'] == 'matrix_basis' for r in report['native_artificial']))


if __name__ == '__main__':
    unittest.main()
