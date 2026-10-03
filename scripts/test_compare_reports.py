"""Report comparisons must ignore build identity without hiding regressions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('compare_reports.py')


class CompareReportsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-report-comparison-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def compare(self, left, right, *options, suffix='.json'):
        paths = [self.root / ('left' + suffix), self.root / ('right' + suffix)]
        for path, value in zip(paths, (left, right)):
            path.write_text(json.dumps(value) + '\n', encoding='utf-8')
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), *map(str, paths), *options],
            capture_output=True, text=True,
        )

    def test_build_and_environment_changes_do_not_change_results(self):
        left = dict(tool_sha256='debug-binary', platform='Linux',
                    architecture='x86_64', python='3.12', passed=True,
                    pcm_sha256='identical-pcm', tables_sha256='identical-tables')
        right = dict(left, tool_sha256='release-binary', platform='macOS',
                     architecture='arm64', python='3.13')
        result = self.compare(left, right)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('equal after normalization', result.stdout)

    def test_semantic_results_and_input_hashes_still_differ(self):
        left = dict(passed=True, pcm_sha256='pcm', tables_sha256='tables',
                    vector_manifest_sha256='vectors')
        for key in left:
            with self.subTest(key=key):
                right = dict(left)
                right[key] = False if key == 'passed' else 'changed'
                result = self.compare(left, right)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('$.' + key, result.stdout)

    def test_nonpositive_limits_are_rejected_for_json_and_jsonl(self):
        for suffix in ('.json', '.jsonl'):
            for limit in ('0', '-1'):
                with self.subTest(suffix=suffix, limit=limit):
                    result = self.compare({'pcm_sha256': 'before'},
                                          {'pcm_sha256': 'after'},
                                          '--limit', limit, suffix=suffix)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn('--limit must be positive', result.stderr)
                    self.assertNotIn('equal after normalization', result.stdout)

    def test_positive_limit_caps_missing_keys_without_hiding_failure(self):
        result = self.compare({'a': 1, 'b': 2, 'c': 3}, {}, '--limit', '1')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('only in left'), 1)


if __name__ == '__main__':
    unittest.main()
