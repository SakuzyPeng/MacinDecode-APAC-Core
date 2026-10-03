"""A selected validator must run or fail, never silently qualify as skipped."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run_portable_suite as suite


class PortableSuiteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-portable-suite-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'apac-tool'
        self.binary.touch()
        self.presence = self.root / 'layout_presence'
        self.presence.touch()

    def run_suite(self, *options, output='reports', codes=None):
        destination = self.root / output
        stdout, stderr = io.StringIO(), io.StringIO()

        def finish(command, **kwargs):
            name = Path(command[2]).stem
            return subprocess.CompletedProcess(command, (codes or {}).get(name, 0),
                                               stdout='', stderr='')

        argv = ['run_portable_suite.py', '--binary', str(self.binary),
                '--out', str(destination), *options]
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(suite.subprocess, 'run', side_effect=finish) as run, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = suite.main()
            except SystemExit as error:
                code = error.code
        return code, stderr.getvalue(), destination, run

    def test_missing_presence_argument_fails_before_starting_any_validator(self):
        for index, options in enumerate(((), ('--only', 'validate_drc', 'validate_layouts'))):
            with self.subTest(options=options):
                code, stderr, destination, run = self.run_suite(*options, output=str(index))
                self.assertEqual(code, 2, stderr)
                self.assertIn('--presence-binary', stderr)
                run.assert_not_called()
                self.assertFalse(destination.exists())

    def test_nonexistent_presence_binary_fails_before_creating_output(self):
        code, stderr, destination, run = self.run_suite(
            '--only', 'validate_layouts', '--presence-binary', str(self.root / 'missing'),
        )
        self.assertEqual(code, 2, stderr)
        self.assertIn('--presence-binary', stderr)
        run.assert_not_called()
        self.assertFalse(destination.exists())

    def test_explicitly_skipped_layout_does_not_require_presence_binary(self):
        code, stderr, destination, run = self.run_suite(
            '--only', 'validate_layouts', 'validate_drc', '--skip', 'validate_layouts',
        )
        self.assertEqual(code, 0, stderr)
        run.assert_called_once()
        summary = json.loads((destination / 'summary.json').read_text())
        self.assertEqual([row['name'] for row in summary], ['validate_drc'])
        self.assertEqual(summary[0]['returncode'], 0)

    def test_fast_subset_does_not_require_presence_binary(self):
        code, stderr, destination, run = self.run_suite('--fast', '--jobs', '2')
        self.assertEqual(code, 0, stderr)
        summary = json.loads((destination / 'summary.json').read_text())
        self.assertEqual([row['name'] for row in summary], suite.FAST)
        self.assertNotIn('validate_layouts', [row['name'] for row in summary])
        self.assertEqual(run.call_count, len(summary))

    def test_presence_is_forwarded_and_concurrent_failures_fail_the_suite(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                code, stderr, destination, run = self.run_suite(
                    '--only', 'validate_layouts', 'validate_drc', '--jobs', '2',
                    '--presence-binary', str(self.presence), output=str(failure),
                    codes={'validate_drc': 3 if failure else 0},
                )
                self.assertEqual(code, int(failure), stderr)
                summary = json.loads((destination / 'summary.json').read_text())
                self.assertEqual([row['returncode'] for row in summary],
                                 [0, 3 if failure else 0])
                command = next(call.args[0] for call in run.call_args_list
                               if Path(call.args[0][2]).stem == 'validate_layouts')
                index = command.index('--presence-binary')
                self.assertEqual(command[index + 1], str(self.presence.resolve()))


if __name__ == '__main__':
    unittest.main()
