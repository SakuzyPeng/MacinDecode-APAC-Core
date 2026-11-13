"""Distribution contents, executable permissions and checksums on all targets."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from package_release import TARGETS, create_package


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-package-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/'source'
        for name in ('README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY.md',
                     'LICENSES/Apache-2.0.txt', 'guide/commands.md',
                     'docs/private.md', 'reports/local.json', 'artifacts/sample.pcm'):
            path = self.source/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding='utf-8')
        self.binary = self.root/'executable'
        self.binary.write_bytes(b'synthetic executable')
        self.commit = 'a'*40
        (self.source/'Cargo.toml').write_text('[workspace.package]\nversion = "0.1.0"\n', encoding='utf-8')

    def package(self, target, out='dist', commit=None, **kwargs):
        return create_package(self.binary, target, self.root/out, commit or self.commit,
                              binary_version='mapac 0.1.0', rust_version='rustc synthetic',
                              root=self.source, **kwargs)

    def release_notes(self):
        path = self.source/'guide/releases/v0.1.0.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('First CLI release.\n', encoding='utf-8')
        return path

    def test_all_archives_contain_only_public_files_and_executable(self):
        for target, (executable, extension) in TARGETS.items():
            with self.subTest(target=target):
                archive, checksum = self.package(target)
                if extension == '.zip':
                    with zipfile.ZipFile(archive) as bundle:
                        self.assertIsNone(bundle.testzip())
                        files = {name.split('/', 1)[1]: bundle.read(name) for name in bundle.namelist()}
                        mode = next(item.external_attr >> 16 for item in bundle.infolist()
                                    if item.filename.endswith('/'+executable))
                else:
                    with tarfile.open(archive) as bundle:
                        files = {item.name.split('/', 1)[1]: bundle.extractfile(item).read()
                                 for item in bundle.getmembers()}
                        mode = next(item.mode for item in bundle.getmembers()
                                    if item.name.endswith('/'+executable))
                self.assertEqual(set(files), {executable, 'build-info.json', 'README.md',
                                             'README.en.md', 'LICENSE', 'THIRD_PARTY.md',
                                             'LICENSES/Apache-2.0.txt', 'guide/commands.md'})
                self.assertEqual(files[executable], self.binary.read_bytes())
                self.assertEqual(mode & 0o777, 0o755)
                info = json.loads(files['build-info.json'])
                self.assertEqual(info['commit'], self.commit)
                self.assertEqual(info['target'], target)
                self.assertEqual(info['binary_sha256'], hashlib.sha256(files[executable]).hexdigest())
                self.assertEqual(checksum.read_text(), f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')

    def test_identical_inputs_make_identical_archives_and_existing_outputs_are_preserved(self):
        for target in TARGETS:
            with self.subTest(target=target):
                first, first_sha = self.package(target, 'first')
                second, second_sha = self.package(target, 'second')
                before = first.read_bytes()
                self.assertEqual(before, second.read_bytes())
                self.assertEqual(first_sha.read_bytes(), second_sha.read_bytes())
                with self.assertRaises(FileExistsError):
                    self.package(target, 'first')
                self.assertEqual(first.read_bytes(), before)

    def test_unsupported_architectures_and_missing_license_are_rejected(self):
        with self.assertRaises(ValueError):
            self.package('x86_64-apple-darwin')
        with self.assertRaises(ValueError):
            self.package('aarch64-apple-darwin', commit='../not-a-commit')
        self.assertFalse((self.root/'dist').exists())
        (self.source/'LICENSES/Apache-2.0.txt').unlink()
        with self.assertRaises(FileNotFoundError):
            self.package('aarch64-apple-darwin')
        self.assertFalse((self.root/'dist').exists())

    def test_release_archives_are_versioned_and_include_notes_and_revision(self):
        notes = self.release_notes()
        for target, (_, extension) in TARGETS.items():
            with self.subTest(target=target):
                archive, checksum = self.package(target, release_tag='v0.1.0')
                name = f'mapac-v0.1.0-{target}'
                self.assertEqual(archive.name, name + extension)
                self.assertEqual(checksum.name, archive.name + '.sha256')
                if extension == '.zip':
                    with zipfile.ZipFile(archive) as bundle:
                        info = json.loads(bundle.read(name + '/build-info.json'))
                        packaged_notes = bundle.read(name + '/guide/releases/v0.1.0.md')
                else:
                    with tarfile.open(archive) as bundle:
                        info = json.load(bundle.extractfile(name + '/build-info.json'))
                        packaged_notes = bundle.extractfile(name + '/guide/releases/v0.1.0.md').read()
                self.assertEqual(info['commit'], self.commit)
                self.assertEqual(info['release_tag'], 'v0.1.0')
                self.assertEqual(packaged_notes, notes.read_bytes())

    def test_mismatched_versions_or_missing_notes_cannot_be_released(self):
        for tag in ('v0.2.0', '0.1.0', '../v0.1.0', 'v0.1.0/../../private'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.package('aarch64-apple-darwin', release_tag=tag)
        with self.assertRaises(FileNotFoundError):
            self.package('aarch64-apple-darwin', release_tag='v0.1.0')
        self.release_notes()
        with self.assertRaises(ValueError):
            create_package(self.binary, 'aarch64-apple-darwin', self.root/'dist', self.commit,
                           binary_version='mapac 0.0.9', rust_version='rustc synthetic',
                           release_tag='v0.1.0', root=self.source)
        self.assertFalse((self.root/'dist').exists())

    def test_symlinked_documentation_is_rejected(self):
        notes = self.release_notes()
        notes.unlink()
        try:
            notes.symlink_to(self.source/'docs/private.md')
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows account cannot create symlinks')
            raise
        with self.assertRaises(ValueError):
            self.package('aarch64-apple-darwin', release_tag='v0.1.0')
        self.assertFalse((self.root/'dist').exists())


if __name__ == '__main__':
    unittest.main()
