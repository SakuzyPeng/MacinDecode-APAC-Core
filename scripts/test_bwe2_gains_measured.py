"""Measured gain sources, mixed provenance and rejection of unsafe replacements."""
import copy
import json
import unittest

import generate_bwe2_gains_measured as measured
from verify_bwe2_format import check_wire_values


class MeasuredBwe2GainsTests(unittest.TestCase):
    def setUp(self):
        self.measurement=measured.load_measurement()
        self.stored=json.loads((measured.DATA/measured.FORMAT_FILE).read_bytes())

    def test_canonical_source_and_copy(self):
        self.assertEqual(measured.measured_words(self.measurement),self.stored['excitation_gains_f32'])
        self.assertEqual(measured.regenerate(self.stored,self.measurement),self.stored)
        self.assertTrue(self.measurement['source']['decimal_grid_assumed'])
        self.assertEqual(self.measurement['source']['decimal_grid_step'],'0.00001')

    def test_gain_repair_preserves_lsf_words_and_source(self):
        broken=copy.deepcopy(self.stored);broken['excitation_gains_f32']=[0]*64
        repaired=measured.regenerate(broken,self.measurement)
        self.assertEqual(repaired,self.stored)
        self.assertEqual(repaired['source']['remaining_tables'],dict(lsf_codebooks_f32='original_observation'))
        self.assertEqual(repaired['source']['original_observation'],measured.ORIGINAL_SOURCE)

    def test_lsf_changes_cannot_hide_behind_a_new_digest(self):
        broken=copy.deepcopy(self.stored);broken['lsf_codebooks_f32'][0][0][0]^=1
        semantic={k:broken[k] for k in ('lsf_codebooks_f32','excitation_gains_f32')}
        broken['tables_sha256']=measured.sha(json.dumps(semantic,sort_keys=True,separators=(',',':')).encode())
        with self.assertRaisesRegex(ValueError,'semantic digest'):
            measured.regenerate(broken,self.measurement)

    def test_original_observation_is_preserved(self):
        initial=copy.deepcopy(self.stored);initial['source']=copy.deepcopy(measured.ORIGINAL_SOURCE)
        self.assertEqual(measured.regenerate(initial,self.measurement),self.stored)
        initial['source']['method']='independently measured LSF'
        with self.assertRaisesRegex(ValueError,'LSF provenance'):
            measured.regenerate(initial,self.measurement)

    def test_unqualified_or_wrong_gain_data_is_rejected(self):
        edits=(lambda d:d.update(count=63),lambda d:d.update(profile='lsf'),
               lambda d:d['source'].update(decimal_grid_assumed=False),
               lambda d:d['source'].update(candidate_sha256='unqualified'),
               lambda d:d['excitation_gains_f32'].__setitem__(0,True),
               lambda d:d['excitation_gains_f32'].__setitem__(0,0x7fc00000),
               lambda d:d['excitation_gains_f32'].__setitem__(0,-1),
               lambda d:d['excitation_gains_f32'].__setitem__(0,d['excitation_gains_f32'][0]^1),
               lambda d:d['excitation_gains_f32'].pop())
        for edit in edits:
            with self.subTest(edit=edit):
                value=copy.deepcopy(self.measurement);edit(value)
                with self.assertRaises(ValueError):measured.measured_words(value)
        with self.assertRaisesRegex(ValueError,'unverified frozen'):
            measured.from_evidence(b'{"all_determined":true}',b'{}',b'{}')

    def test_diagnostic_comparison_accepts_mixed_provenance_without_rewriting_it(self):
        observed=copy.deepcopy(self.stored);observed['source']=copy.deepcopy(measured.ORIGINAL_SOURCE)
        before=copy.deepcopy(self.stored)
        check_wire_values(self.stored,observed)
        self.assertEqual(self.stored,before)
        observed['excitation_gains_f32'][1]^=1
        with self.assertRaisesRegex(AssertionError,'wire dictionaries'):
            check_wire_values(self.stored,observed)


if __name__=='__main__':unittest.main()
