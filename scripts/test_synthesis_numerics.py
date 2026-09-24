"""Window-profile integrity and incompatible reference intervals."""
import copy
import json
from pathlib import Path
import struct
import unittest

from check_synthesis_reference import disjoint_intervals
from verify_sine_windows import check_windows
from validate_synthesis import sequences


class NumericalProfileTests(unittest.TestCase):
    def test_reference_intervals_do_not_hide_contradictory_targets(self):
        self.assertEqual(disjoint_intervals([0.,1.,-1.],[0.,1.+1e-6,-1.-1e-6]),[])
        failures=disjoint_intervals([-0.04850320518016815],[-0.048508815467357635])
        self.assertEqual(len(failures),1)
        self.assertLess(failures[0]['interval16'][1],failures[0]['interval64'][0])
        with self.assertRaises(AssertionError):disjoint_intervals([float('nan')],[0.])
        with self.assertRaises(AssertionError):disjoint_intervals([0.],[])
        with self.assertRaises(AssertionError):disjoint_intervals([0.],[0.],atol=-1)

    def test_window_verifier_checks_every_float_bit_and_reference_build(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'data/sq-sine-windows.json').read_text())
        trace=dict(errors=[],process_exit_code=0,component_sha256=data['reference_component_sha256'],windows={k:data[k] for k in ['long','short']})
        hashes=check_windows(data,trace)
        self.assertEqual(hashes['long'],'1b44dedd53c577d9298b784de7c4ec346f939dbf1e0886de93aeb1d211c5587c')
        altered=copy.deepcopy(trace)
        word=struct.unpack('<I',struct.pack('<f',altered['windows']['long'][256]))[0]
        altered['windows']['long'][256]=struct.unpack('<f',struct.pack('<I',word^1))[0]
        with self.assertRaises(AssertionError):check_windows(data,altered)
        altered=copy.deepcopy(trace);altered['component_sha256']='unverified'
        with self.assertRaises(AssertionError):check_windows(data,altered)
        altered=copy.deepcopy(trace);altered['windows']['short'].pop()
        with self.assertRaises(AssertionError):check_windows(data,altered)

    def test_pressure_sign_combinations_remain_in_the_acceptance_matrix(self):
        cases=list(sequences())
        self.assertEqual(len(cases),4974)
        additional=[s for kind,s in cases if kind=='heldout_sign_overlap']
        self.assertEqual(len(additional),16)
        self.assertEqual(len({tuple(s[1]['left'][0][1]) for s in additional}),16)
        self.assertTrue(all(s[1]['gain']==255 and s[1]==s[2] for s in additional))


if __name__=='__main__':unittest.main()
