"""Regression coverage for complete and reproducible dependency notices."""
from pathlib import Path
import tempfile
import unittest

from generate_dependency_licenses import notice_header, render_notices

SOURCE = 'registry+https://github.com/rust-lang/crates.io-index'


class DependencyLicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-licenses-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.packages = []

    def add_package(self, name):
        directory = self.root/name
        directory.mkdir()
        (directory/'Cargo.toml').write_text('', encoding='utf-8')
        (directory/'LICENSE-MIT').write_text('Copyright '+name+'\nMIT terms\n', encoding='utf-8')
        (directory/'NOTICE').write_text('Additional notice '+name+'\n', encoding='utf-8')
        package = dict(name=name, version='1.0.0', source=SOURCE, license='MIT',
                       manifest_path=str(directory/'Cargo.toml'), license_file=None)
        self.packages.append(package)
        return directory

    def write_lock(self):
        content = 'version = 4\n'
        for package in self.packages:
            content += ('\n[[package]]\nname = "'+package['name']+'"\nversion = "1.0.0"\n'
                        'source = "'+SOURCE+'"\n')
        (self.root/'Cargo.lock').write_text(content, encoding='utf-8')

    def test_all_locked_packages_keep_licenses_and_notices_in_stable_order(self):
        self.add_package('zeta')
        self.add_package('alpha')
        self.write_lock()
        workspace = dict(name='workspace', source=None)
        text = render_notices(self.root, dict(packages=[workspace, *self.packages]))
        self.assertTrue(text.startswith(notice_header(self.root)))
        self.assertLess(text.index('Package alpha'), text.index('Package zeta'))
        for name in ('alpha', 'zeta'):
            self.assertIn('Copyright '+name+'\nMIT terms\n', text)
            self.assertIn('Additional notice '+name+'\n', text)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(text, render_notices(self.root, dict(packages=list(reversed(self.packages)))))

    def test_missing_locked_package_and_missing_license_are_rejected(self):
        directory = self.add_package('alpha')
        self.write_lock()
        with self.assertRaises(ValueError):
            render_notices(self.root, dict(packages=[]))
        (directory/'LICENSE-MIT').unlink()
        (directory/'NOTICE').unlink()
        with self.assertRaises(ValueError):
            render_notices(self.root, dict(packages=self.packages))

    def test_explicit_license_file_and_license_directory_are_preserved(self):
        directory = self.add_package('alpha')
        license_dir = directory/'LICENSES'
        license_dir.mkdir()
        (license_dir/'BSD.txt').write_text('BSD copyright and terms\n', encoding='utf-8')
        (directory/'terms.txt').write_text('Explicit terms\n', encoding='utf-8')
        self.packages[0]['license_file'] = 'terms.txt'
        self.write_lock()
        text = render_notices(self.root, dict(packages=self.packages))
        self.assertIn('--- LICENSES/BSD.txt ---\n\nBSD copyright and terms\n', text)
        self.assertIn('--- terms.txt ---\n\nExplicit terms\n', text)

    def test_author_file_can_carry_the_license_and_copyright(self):
        directory = self.add_package('alpha')
        (directory/'LICENSE-MIT').unlink()
        (directory/'NOTICE').unlink()
        (directory/'AUTHORS').write_text('MIT terms\nCopyright alpha\n', encoding='utf-8')
        self.write_lock()
        text = render_notices(self.root, dict(packages=self.packages))
        self.assertIn('--- AUTHORS ---\n\nMIT terms\nCopyright alpha\n', text)


if __name__ == '__main__':
    unittest.main()
