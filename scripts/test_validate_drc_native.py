"""The new full encoder PCM gate retains the 27aa35b failure semantics."""
import hashlib,struct,unittest
from validate_drc_native import compare_encoder_pcm

class DrcNativePcmTests(unittest.TestCase):
    def test_original_tolerance_passes_and_metrics_are_retained(self):
        record={};reference=struct.pack('<4f',1.,-1.,0.,0.)
        compare_encoder_pcm(record,struct.pack('<4f',1.+5e-7,-1.,0.,0.),reference)
        self.assertTrue(record['pcm_metrics']['passed'])
        self.assertGreater(record['pcm_metrics']['max_absolute_error'],0)
    def test_complete_pcm_mismatch_is_fatal_with_first_coordinate(self):
        record={};raw=struct.pack('<4f',1.,-1.,0.,0.)
        with self.assertRaisesRegex(AssertionError,'full DRC encoder PCM exceeds original tolerance'):
            compare_encoder_pcm(record,raw,bytes(16))
        self.assertFalse(record['pcm_metrics']['passed'])
        self.assertEqual(record['pcm_metrics']['failed_samples'],2)
        self.assertEqual(record['pcm_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['pcm_metrics']['first_failure']['sample'],0)

if __name__=='__main__':unittest.main()
