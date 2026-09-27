"""Portable regression tests for the native encoder PCM acceptance gate."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from packet_vectors import bundle, packet
import validate_packet_native as native


class NativePacketControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-control-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = dict(controls=[], failure_directory=self.root/'failures')
        self.native_pcm = struct.pack('<f', 1.0) * (4 * 1024 * 2)
        self.candidate_pcm = self.native_pcm
        self.rate = 48000
        self.rows = [dict(embedded_preroll=None, fields=[
            dict(name='components[0].tce[0].present', value=False)
        ]) for _ in range(4)]

    def command(self, binary, action, *args):
        destination = Path(args[args.index('--out') + 1])
        if action == 'fixture':
            self.rate = int(args[args.index('--sample-rate') + 1])
            signal = args[args.index('--signals') + 1]
            generated = destination/signal
            generated.mkdir(parents=True)
            (generated/'encoded.caf').write_bytes(b'synthetic fixture placeholder')
            (generated/'manifest.json').write_text(json.dumps(dict(
                drc_configuration_verified=True, requested={},
                actual_encoder_settings=dict(cdrc=dict(value=0)),
            )))
            return {}
        if action == 'dump':
            payload, _ = packet(dict(absent=True), self.rate)
            bundle(destination, [payload] * 4, self.rate)
            return dict(actual_packets=4)
        self.assertEqual(action, 'decode-sq')
        manifest = json.loads((Path(args[0])/'manifest.json').read_text())
        window = manifest.get('replay_window')
        first, end = (window['target_raw_start'], window['target_raw_end']) if window else (0, 4096)
        destination.mkdir()
        (destination/'pcm.f32le').write_bytes(self.candidate_pcm[first*8:end*8])
        return dict(range=dict(start_frame=first), saved_frames=end-first)

    def trace(self, binary, directory, root):
        (root/'native-state.json').write_text('{}')
        (root/'native-pcm').mkdir()
        (root/'native-pcm/pcm.f32le').write_bytes(self.native_pcm)
        return {}

    def run_controls(self):
        # Replace native acquisition only; retain the real PCM comparator,
        # window selection, acceptance logic and failure-artifact handling.
        with patch.multiple(native, command=self.command, trace_bundle=self.trace,
                            inspect=lambda *args: self.rows, native_checks=lambda *args: []), \
                contextlib.redirect_stderr(io.StringIO()):
            native.controls(self.root/'unused-binary', self.report)

    def test_pcm_within_tolerance_accepts_all_controls_and_windows(self):
        self.candidate_pcm = struct.pack('<f', 1.0 + 5e-7) * (4 * 1024 * 2)
        self.run_controls()
        self.assertEqual(len(self.report['controls']), 16)
        for control in self.report['controls']:
            self.assertTrue(control['passed'])
            self.assertTrue(control['native_float_metrics']['passed'])
            self.assertGreater(control['native_float_metrics']['max_absolute_error'], 0)
            self.assertEqual(len(control['windows']), 3)
        self.assertFalse(self.report['failure_directory'].exists())

    def test_pcm_mismatch_fails_and_preserves_metrics_and_audio(self):
        self.candidate_pcm = bytes(len(self.native_pcm))
        with self.assertRaisesRegex(AssertionError, 'native encoder control exceeds original tolerance'):
            self.run_controls()
        self.assertEqual(len(self.report['controls']), 1)
        control = self.report['controls'][0]
        self.assertFalse(control['passed'])
        metrics = control['native_float_metrics']
        self.assertFalse(metrics['passed'])
        self.assertEqual(metrics['max_absolute_error'], 1.0)
        self.assertEqual(metrics['failed_samples'], 8192)
        self.assertEqual(control['pcm_sha256'], hashlib.sha256(self.candidate_pcm).hexdigest())
        snapshot = self.report['failure_directory']/'control-48000-silence-1'
        self.assertEqual((snapshot/'native-pcm/pcm.f32le').read_bytes(), self.native_pcm)
        self.assertEqual((snapshot/'rust-pcm/pcm.f32le').read_bytes(), self.candidate_pcm)
        self.assertTrue((snapshot/'packets/manifest.json').is_file())
        self.assertTrue((snapshot/'native-state.json').is_file())


if __name__ == '__main__':
    unittest.main()
