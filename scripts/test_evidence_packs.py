"""Packed evidence remains hash-checked, resumable, and usable across campaigns."""
import gzip
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evidence_packs import object_exists, read_gzip_object
from pack_evidence import pack_directory, verify_directory, offline_locks
from hoa_blackbox_lib.common import EvidenceError
from hoa_blackbox_lib.store import Store, writer_lock


class PackTests(unittest.TestCase):
    def loose(self, directory, raw):
        path = directory / (hashlib.sha256(raw).hexdigest() + '.gz')
        path.write_bytes(gzip.compress(raw, mtime=0))
        return path

    def test_multiple_packs_preserve_bytes_and_logical_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'objects'; directory.mkdir()
            original = {self.loose(directory, raw): raw for raw in (b'', b'hello', bytes(range(256))*5000)}
            result = pack_directory(directory, chunk_objects=1)
            self.assertEqual(result['objects'], 3)
            self.assertEqual(result['packs'], 3)
            for path, raw in original.items():
                self.assertFalse(path.exists())
                self.assertTrue(object_exists(path))
                self.assertEqual(read_gzip_object(path), raw)
            self.assertEqual(verify_directory(directory)['objects'], 3)
            self.assertEqual(pack_directory(directory)['objects'], 0)
            # New captures can coexist with earlier packs.
            path = self.loose(directory, b'new')
            self.assertEqual(read_gzip_object(path), b'new')
            self.assertEqual(pack_directory(directory)['objects'], 1)

    def test_corrupt_input_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); path = self.loose(directory, b'original')
            path.write_bytes(gzip.compress(b'different'))
            with self.assertRaisesRegex(RuntimeError, 'source content hash'):
                pack_directory(directory)
            self.assertTrue(path.is_file())

    def test_resume_after_durable_index_before_source_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); path = self.loose(directory, b'recover me')
            unlink = __import__('os').unlink
            def interrupted(name, *args, **kwargs):
                if name == path.name:
                    raise OSError('simulated disconnection')
                return unlink(name, *args, **kwargs)
            with patch('pack_evidence.os.unlink', side_effect=interrupted):
                with self.assertRaisesRegex(OSError, 'simulated disconnection'):
                    pack_directory(directory)
            self.assertTrue(path.exists())
            self.assertEqual(read_gzip_object(path), b'recover me')
            result = pack_directory(directory)
            self.assertEqual(result['resumed_deletions'], 1)
            self.assertFalse(path.exists())
            self.assertEqual(read_gzip_object(path), b'recover me')

    def test_pack_corruption_and_loose_override_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); path = self.loose(directory, b'untouched')
            pack_directory(directory)
            next((directory / '.packs').glob('*.bin')).write_bytes(b'damaged')
            with self.assertRaisesRegex(OSError, 'checksum'):
                read_gzip_object(path)
            path.write_bytes(b'bad loose override')
            with self.assertRaises(gzip.BadGzipFile):
                read_gzip_object(path)

    def test_active_writer_prevents_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with writer_lock(root):
                with self.assertRaises(BlockingIOError):
                    with offline_locks(root):
                        pass

    def test_campaign_and_external_import_keep_original_database_rows(self):
        from test_hoa_blackbox_campaign import pooled, runner
        from hoa_blackbox_lib import wire
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); campaign = root / 'campaign'; campaign.mkdir()
            store = pooled(campaign); run = runner(store)
            key, _ = run.probe('mode1', 'pack', 'basis', run.writer.fixed(wire.vector(48, order=4)))
            receipt = store.query(key)
            rows = [tuple(row) for row in store.db.execute('SELECT * FROM objects')]
            objects = list(store.db.execute('SELECT sha,path,size FROM objects'))
            store.close()
            pack_directory(campaign / 'objects')
            store = Store(campaign / 'batches/order4-q6', readonly=True)
            try:
                self.assertEqual(store.query(key), receipt)
                self.assertEqual([tuple(row) for row in store.db.execute('SELECT * FROM objects')], rows)
                self.assertGreater(store.audit_evidence(), 0)
                for identity, path, size in objects:
                    self.assertEqual(hashlib.sha256(store.pool.read_reference(identity,
                        dict(path=path, kind='gzip', size=size))).hexdigest(), identity)
            finally:
                store.close()
            # Import compressed evidence by its unchanged (now virtual) path.
            other = root / 'other'; other.mkdir(); target = pooled(other)
            try:
                for identity, path, size in objects:
                    self.assertEqual(target.external(path, size=size, compressed=True), identity)
                    self.assertEqual(hashlib.sha256(target.read_blob(identity)).hexdigest(), identity)
            finally:
                target.close()


if __name__ == '__main__':
    unittest.main()
