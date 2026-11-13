import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from check_sources import audit, contains_lsf_table, inspect_bytes, sha


class DistributionGuardTests(unittest.TestCase):
    def test_private_tables_are_rejected_by_name_and_shape(self):
        self.assertIn('excluded-path', inspect_bytes('data/bwe2-format-v1.json', b'{}'))
        self.assertIn('excluded-lsf-table', inspect_bytes('data/renamed.json', b'{"lsf_codebooks_f32":[]}'))
        self.assertTrue(contains_lsf_table([[[0] * 16 for _ in range(512)] for _ in range(2)]))
        self.assertFalse(contains_lsf_table({'lsf_indices': [163, 24]}))

    def test_changed_numerical_input_and_removed_historical_file_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)
            git('init', '-b', 'main')
            git('config', 'user.name', 'Source audit test')
            git('config', 'user.email', 'test@example.com')
            (root / 'data').mkdir()
            raw = b'{"coefficient":1}\n'
            table = root / 'data/math.json'
            table.write_bytes(raw)
            manifest = dict(profile='apac-core-distribution-sources-v1', numerical_consumers={},
                files=[dict(path='data/math.json', role='runtime-numeric', classification='formula', sha256=sha(raw))])
            (root / 'data/source-provenance-v1.json').write_text(json.dumps(manifest))
            self.assertTrue(audit(root)['passed'])
            table.write_bytes(b'{"coefficient":2}\n')
            self.assertFalse(audit(root)['passed'])
            table.write_bytes(raw)
            private = root / 'data/bwe2-format-v1.json'
            private.write_text('{}')
            git('add', '.')
            git('commit', '-m', 'Synthetic forbidden-path control')
            git('rm', str(private))
            git('commit', '-m', 'Remove from current tree only')
            self.assertTrue(audit(root)['passed'])
            result = audit(root, history=True)
            self.assertFalse(result['passed'])
            self.assertEqual(result['commits_checked'], 2)
            self.assertTrue(any(row.get('path') == 'data/bwe2-format-v1.json' and row.get('object') for row in result['findings']))


if __name__ == '__main__':
    unittest.main()
