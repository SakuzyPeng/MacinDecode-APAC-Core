"""Native PCM differences remain visible without defining mathematical truth."""
import hashlib,struct,unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
import validate_drc_native as native
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


class DrcNativeStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='drc-native-state-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mismatch = None

    def inspect_bundle(self, binary, directory, root, frames, record):
        # Supply deterministic inspection results; keep the real comparison,
        # state-control acceptance, input generation and artifact retention.
        reference = struct.pack('<f', 1.0) * (frames * 2)
        candidate = (bytes(len(reference)) if (record['rate'], record['kind']) == self.mismatch
                     else struct.pack('<f', 1.0 + 5e-7) * (frames * 2))
        (root/'native/native-pcm').mkdir(parents=True)
        (root/'native/native-state.json').write_text('{}')
        (root/'native/native-pcm/pcm.f32le').write_bytes(reference)
        (root/'rust-pcm').mkdir()
        (root/'rust-pcm/pcm.f32le').write_bytes(candidate)
        compare_encoder_pcm(record, candidate, reference)
        return record, [], candidate

    def run_controls(self, report):
        with patch.object(native, 'inspect_bundle', self.inspect_bundle):
            native.state_controls(self.root/'unused-binary', report)

    def test_pcm_within_tolerance_accepts_all_eight_state_controls(self):
        report = dict(passed=False, failure_directory=str(self.root/'failures'))
        self.run_controls(report)
        controls = report['state_controls']
        self.assertEqual(len(controls), 8)
        for rate in (48000, 44100):
            for kind in ('preroll', 'metadata_updates'):
                self.assertEqual(sum(c['rate'] == rate and c['kind'] == kind for c in controls), 2)
        self.assertTrue(all(c['passed'] and c['pcm_metrics']['passed'] for c in controls))
        self.assertTrue(all(c['pcm_metrics']['max_absolute_error'] > 0 for c in controls))
        self.assertFalse(Path(report['failure_directory']).exists())

    def test_state_pcm_mismatch_fails_in_both_modes_and_preserves_evidence(self):
        for rate in (48000, 44100):
            for kind in ('preroll', 'metadata_updates'):
                for strict in (False, True):
                    with self.subTest(rate=rate, kind=kind, strict=strict):
                        self.mismatch = rate, kind
                        failures = self.root/f'{rate}-{kind}-{strict}'
                        report = dict(passed=False, require_native_pcm=strict,
                                      failure_directory=str(failures))
                        with self.assertRaisesRegex(AssertionError, 'native DRC state control exceeds original tolerance'):
                            self.run_controls(report)
                        controls = report['state_controls']
                        self.assertTrue(all(c['passed'] for c in controls[:-1]))
                        failed = controls[-1]
                        self.assertEqual((failed['rate'], failed['kind']), self.mismatch)
                        self.assertFalse(failed['passed'])
                        self.assertFalse(failed['pcm_metrics']['passed'])
                        self.assertEqual(failed['pcm_metrics']['first_failure'],
                                         dict(sample=0, channel=0, reference=1.0, candidate=0.0))
                        snapshot = failures/f'state-{rate}-{failed["index"]}'
                        candidate = (snapshot/'rust-pcm/pcm.f32le').read_bytes()
                        reference = (snapshot/'native/native-pcm/pcm.f32le').read_bytes()
                        self.assertEqual(candidate, bytes(len(reference)))
                        self.assertEqual(hashlib.sha256(candidate).hexdigest(), failed['pcm_sha256'])
                        self.assertEqual(hashlib.sha256(reference).hexdigest(), failed['native_pcm_sha256'])
                        self.assertTrue((snapshot/'native/native-state.json').is_file())
                        self.assertTrue((snapshot/'packets/manifest.json').is_file())
                        self.assertEqual(finalize(report, controls), 1)
                        self.assertFalse(report['structural_passed'])
                        self.assertFalse(report['passed'])


if __name__=='__main__':unittest.main()
