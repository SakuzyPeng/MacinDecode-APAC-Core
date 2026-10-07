"""Check release provenance against real lightweight and annotated Git tags."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from verify_release_tag import verify_release_tag


class ReleaseTagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-release-tag-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Release test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.hooksPath', str(self.root/'no-hooks'))
        self.git('config', 'commit.gpgSign', 'false')
        self.git('config', 'tag.gpgSign', 'false')
        self.git('commit', '--quiet', '--allow-empty', '-m', 'first')
        self.first = self.git('rev-parse', 'HEAD')
        self.git('commit', '--quiet', '--allow-empty', '-m', 'second')
        self.second = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True).strip()

    def verify(self, tag, commit):
        return verify_release_tag(tag, commit, str(self.root))

    def test_missing_tag_can_be_created_at_the_build_commit(self):
        self.assertIsNone(self.verify('v0.1.0', self.second))
        self.assertEqual(self.git('tag', '--list'), '')

    def test_matching_lightweight_and_annotated_tags_are_accepted(self):
        self.git('tag', 'v0.1.0', self.second)
        self.git('tag', '-a', 'v0.2.0', self.second, '-m', 'annotated')
        self.assertNotEqual(self.git('rev-parse', 'v0.2.0'), self.second)
        for tag in ('v0.1.0', 'v0.2.0'):
            self.assertEqual(self.verify(tag, self.second), self.second)

    def test_mismatched_lightweight_and_annotated_tags_are_rejected(self):
        self.git('tag', 'v0.1.0', self.first)
        self.git('tag', '-a', 'v0.2.0', self.first, '-m', 'annotated')
        for tag in ('v0.1.0', 'v0.2.0'):
            with self.assertRaisesRegex(ValueError, 'refusing to modify release assets'):
                self.verify(tag, self.second)
            self.assertEqual(self.git('rev-parse', tag + '^{}'), self.first)

    def test_remote_failure_is_not_treated_as_a_missing_tag(self):
        with self.assertRaises(subprocess.CalledProcessError):
            verify_release_tag('v0.1.0', self.second, str(self.root/'missing-remote'))

    def test_invalid_ref_or_commit_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify('v*', self.second)
        with self.assertRaises(ValueError):
            self.verify('v0.1.0', 'main')


if __name__ == '__main__':
    unittest.main()
