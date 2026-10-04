"""Native PCM differences remain visible without defining mathematical truth."""
import hashlib,struct,unittest
from validate_drc_native import compare_encoder_pcm
from native_pcm import finalize

class DrcNativePcmTests(unittest.TestCase):
    def test_original_tolerance_passes_and_metrics_are_retained(self):
        record={};reference=struct.pack('<4f',1.,-1.,0.,0.)
        compare_encoder_pcm(record,struct.pack('<4f',1.+5e-7,-1.,0.,0.),reference)
        self.assertTrue(record['pcm_metrics']['passed'])
        self.assertGreater(record['pcm_metrics']['max_absolute_error'],0)
    def test_complete_pcm_mismatch_retains_first_coordinate_and_separate_status(self):
        record={};raw=struct.pack('<4f',1.,-1.,0.,0.)
        compare_encoder_pcm(record,raw,bytes(16))
        self.assertFalse(record['pcm_metrics']['passed'])
        self.assertEqual(record['pcm_metrics']['failed_samples'],2)
        self.assertEqual(record['pcm_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['pcm_metrics']['first_failure']['sample'],0)
        for strict, exit_code in ((False,0),(True,2)):
            report=dict(passed=True,require_native_pcm=strict)
            self.assertEqual(finalize(report,[record]),exit_code)
            self.assertTrue(report['structural_passed'])
            self.assertFalse(report['native_pcm_comparison']['passed'])
            self.assertFalse(report['independent_math_verified'])
            self.assertEqual(report['passed'],not strict)

    def test_structural_failure_cannot_be_exempted_and_missing_pcm_is_not_a_pass(self):
        report=dict(passed=False)
        self.assertEqual(finalize(report,[]),1)
        self.assertFalse(report['passed'])
        self.assertFalse(report['native_pcm_comparison']['passed'])
        self.assertEqual(finalize(dict(passed=True,require_native_pcm=True),[]),2)

    def test_length_and_nonfinite_fail_even_in_diagnostic_mode(self):
        for actual,reference in ((b'',b''),(bytes(3),bytes(3)),(bytes(4),bytes(8)),
                                 (struct.pack('<f',float('nan')),bytes(4)),
                                 (bytes(4),struct.pack('<f',float('inf')))):
            with self.assertRaises(AssertionError):compare_encoder_pcm({},actual,reference)

if __name__=='__main__':unittest.main()
