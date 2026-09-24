#!/usr/bin/env python3
"""Small native regression checks for the acceptance runner."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from portable_tools import required_binary

from validate import Validator


@unittest.skipUnless(sys.platform == "darwin", "requires macOS AudioToolbox")
class RepresentativeTests(unittest.TestCase):
    def test_short_and_full_ranges_match_sequential_reference(self):
        binary = required_binary()
        cases = [
            (1, [(0, 1), (0, 1), (0, 1)], 1),
            (16, [(0, 16), (8, 8), (0, 16)], 16),
            (17, [(0, 17), (8, 9), (0, 17)], 17),
            (8192, [(0, 8192), (4096, 4096), (0, 8192)], 17),
            (12000, [(0, 8192), (6000, 6000), (3808, 8192)], 17),
            (20000, [(0, 8192), (10000, 8192), (11808, 8192)], 17),
        ]
        for frames, expected_ranges, expected_tail in cases:
            with self.subTest(frames=frames), tempfile.TemporaryDirectory(
                prefix="apac-validation-test-"
            ) as tmp:
                root = Path(tmp)
                validator = Validator(SimpleNamespace(
                    binary=binary, corpus=root, max_reference_mib=8,
                ))
                fixture = root / "fixture"
                validator.command(
                    "fixture", "--out", fixture, "--signals", "sine",
                    "--duration", str(frames / 48000), "--max-output-mib", 2,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    result = validator.representative(fixture / "sine/encoded.caf")
                self.assertTrue(result["passed"])
                self.assertEqual(result["valid_frames"], frames)
                self.assertEqual(
                    [(r["start_frame"], r["frames"]) for r in result["ranges"]],
                    expected_ranges,
                )
                self.assertTrue(all(r["comparison"]["passed"] for r in result["ranges"]))
                self.assertEqual(result["partial_tail_frames"], expected_tail)


if __name__ == "__main__":
    unittest.main()
