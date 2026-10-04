"""Spatial-control metadata checks against real static and per-frame decodes."""
from pathlib import Path
import tempfile
import unittest

import hoa_controls_vectors as vectors
from portable_tools import required_binary
from validate_hoa_controls import metadata
from validate_replay import command


class HoaControlMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = required_binary()
        temporary = tempfile.TemporaryDirectory(prefix='hoa-control-metadata-')
        cls.addClassCleanup(temporary.cleanup)
        cls.samples = []
        for name, options, cases in vectors.sequences():
            root = Path(temporary.name)/name
            root.mkdir()
            vectors.bundle(root/'packets', [vectors.packet(cases[0], **options)[0]], **options)
            decoded = command(binary, 'decode-sq', root/'packets', '--out', root/'pcm')
            cls.samples.append((name, options, decoded))

    def test_current_static_and_per_frame_reports_pass(self):
        self.assertEqual(len(self.samples), 13)
        self.assertEqual({decoded['backend'] for _, _, decoded in self.samples},
                         {'rust_hoa_spatial_controls_sq_drc_off_f64_fft_v3',
                          'rust_hoa_spatial_controls_sq_drc_off_f64_fft_v4'})
        for name, options, decoded in self.samples:
            with self.subTest(name=name):
                metadata(decoded, options, {})

    def test_historical_and_wrong_mode_backends_still_fail(self):
        backends = [f'rust_hoa_spatial_controls_sq_drc_off_f64_fft_v{version}'
                    for version in (1, 2, 3, 4)]
        for name, options, decoded in self.samples:
            for backend in backends:
                if backend == decoded['backend']:
                    continue
                with self.subTest(name=name, backend=backend):
                    with self.assertRaisesRegex(AssertionError, 'control backend/state differs'):
                        metadata(dict(decoded, backend=backend), options, {})


if __name__ == '__main__':
    unittest.main()
