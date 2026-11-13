"""Regression controls for unavailable BWE2 cases in the portable matrices."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bwe2_vectors import packet, source_case
from ci_support import reject_bwe2_case, requires_bwe2
from ci_evidence import coverage
from ci_catalog import group


class Bwe2AvailabilityTests(unittest.TestCase):
    def test_gate_accepts_parser_and_decoder_error_shapes(self):
        parser_error = dict(kind='bwe2-unavailable', message='feature required', bit_offset=25)
        decoder_error = dict(message='bwe2-unavailable at bit 25', packet_index=1, bit_offset=25)
        for name, error in [('spectra', parser_error), ('pcm', decoder_error)]:
            row = dict(index=7, input_sha256='a'*64, passed=True, bwe2_unavailable=True, error=error)
            entry = dict(groups={name: group([row], ['index', 'input_sha256'])}, rejections=[])
            report = dict(passed=True, errors=[], **{name: [row]})
            coverage(report, entry)
            with patch.dict(error, bit_offset=True), self.assertRaises(AssertionError):
                coverage(report, entry)
            with patch.dict(row, pcm_sha256='b'*64), self.assertRaises(AssertionError):
                coverage(report, entry)

    def test_wire_activity_includes_zero_source_and_nested_frames(self):
        _, inactive = packet(dict(bwe2_flags=(True, True)))
        _, active = packet(source_case())
        self.assertFalse(requires_bwe2(inactive))
        self.assertTrue(requires_bwe2(active))
        self.assertTrue(requires_bwe2({'components': [{'inner': active}]}))
        self.assertFalse(requires_bwe2({'scaled': [0.0] * 1024}))

    def test_rejection_requires_the_exact_first_packet_and_error_kind(self):
        _, inactive = packet({})
        _, active = packet(source_case())
        generated = [(b'first', inactive), (b'second', {'inner': active})]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bwe2-unavailable').mkdir()
            (root / 'bwe2-unavailable/.incomplete.json').write_text('{}')
            error = dict(operation='SQ packet', message='bwe2-unavailable at bit 25', packet_index=1, bit_offset=25)
            result = subprocess.CompletedProcess([], 1, json.dumps({'error': error}), '')
            report = {'cases': []}
            with patch('ci_support.subprocess.run', return_value=result):
                self.assertTrue(reject_bwe2_case('mapac', root/'input', root, report,
                                                {'index': 7}, generated))
            self.assertEqual(report['cases'][0]['index'], 7)
            self.assertTrue(report['cases'][0]['bwe2_unavailable'])
            for code, damage in [(0, error), (1, dict(error, packet_index=0)),
                                 (1, dict(error, message='unrelated parse error'))]:
                result = subprocess.CompletedProcess([], code, json.dumps({'error': damage}), '')
                with patch('ci_support.subprocess.run', return_value=result), self.assertRaises(AssertionError):
                    reject_bwe2_case('mapac', root/'input', root, {'cases': []}, {'index': 7}, generated)


if __name__ == '__main__':
    unittest.main()
