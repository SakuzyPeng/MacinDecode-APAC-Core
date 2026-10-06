"""Measured means provenance, repair scope and rejection of unqualified input."""
import copy
import json
import unittest

import generate_hoa_spatial_means_measured as measured


class MeasuredMeansTests(unittest.TestCase):
    def test_measured_source_and_format_copy(self):
        source = measured.load_measurement()
        stored = json.loads((measured.DATA/measured.FORMAT_FILE).read_bytes())
        self.assertEqual(measured.measured_words(source), stored['mean_coefficients_f32'])
        self.assertEqual(measured.regenerate(stored, source), stored)

    def test_repair_replaces_only_mean_payload(self):
        source = measured.load_measurement()
        stored = json.loads((measured.DATA/measured.FORMAT_FILE).read_bytes())
        broken = copy.deepcopy(stored)
        broken['mean_coefficients_f32'] = [0]*121
        broken['mean_sha256'] = 'broken'
        self.assertEqual(measured.regenerate(broken, source), stored)
        broken['tables'][0]['long_ends'][0] -= 1
        with self.assertRaisesRegex(ValueError, 'semantic digest'):
            measured.regenerate(broken, source)

    def test_preserve_original_grid_provenance(self):
        source = measured.load_measurement()
        stored = json.loads((measured.DATA/measured.FORMAT_FILE).read_bytes())
        initial = copy.deepcopy(stored)
        initial['source'] = initial['source']['original_observation']
        self.assertEqual(measured.regenerate(initial, source), stored)
        self.assertEqual(stored['source']['remaining_tables'], 'original_observation')

    def test_bad_values_scope_and_provenance_rejected(self):
        for change in (
            lambda d: d.update(count=120),
            lambda d: d.update(storage='different'),
            lambda d: d['source'].update(old_values_used_in_discovery=True),
            lambda d: d['source'].update(candidate_sha256='unknown'),
            lambda d: d['mean_coefficients_f32'].__setitem__(0, True),
            lambda d: d['mean_coefficients_f32'].__setitem__(0, 0x7fc00000),
            lambda d: d['mean_coefficients_f32'].__setitem__(0, -1),
            lambda d: d['mean_coefficients_f32'].__setitem__(0, d['mean_coefficients_f32'][0]^1),
            lambda d: d['mean_coefficients_f32'].pop(),
        ):
            source = measured.load_measurement()
            change(source)
            with self.assertRaises(ValueError):
                measured.measured_words(source)

    def test_unqualified_evidence_is_not_a_source(self):
        for raw in (b'{}', b'{"eligible":true}', b'{"float32_bits":null}'):
            with self.assertRaisesRegex(ValueError, 'unverified frozen'):
                measured.from_evidence(raw, raw, raw)


if __name__ == '__main__':
    unittest.main()
