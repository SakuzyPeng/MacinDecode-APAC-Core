"""Removable-volume recovery changes only mount metadata after integrity checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hoa_blackbox_lib.common import EvidenceError,IdentityError,canonical
from hoa_blackbox_lib.store import Store,writer_lock
from test_hoa_blackbox_campaign import pooled,runner
from hoa_blackbox_lib import wire
from verify_hoa_campaign_storage import verify


class StorageTests(unittest.TestCase):
    def fixture(self,root):
        root=root.resolve()
        s=pooled(root);r=runner(s)
        key,_=r.probe('mode1','test','basis',r.writer.fixed(wire.vector(48,order=4)))
        cfg=dict(native_identity=s.config['native_identity'],evidence_device=-2,status='interrupted',limits=s.config['limits'])
        (root/'campaign.json').write_bytes(canonical(cfg))
        s.save_stage('mode1','test-frozen',dict(evidence=key))
        s.config['evidence_device']=-2;s.set_meta('config',s.config)
        (s.out/'manifest.json').write_bytes(canonical(s.config))
        s.close()
        pin=dict(campaign=str(root),volume_uuid='original-volume',native_identity=cfg['native_identity'])
        actual=dict(volume_uuid=pin['volume_uuid'],device=root.stat().st_dev,mount_point=str(root),filesystem='synthetic')
        return pin,actual,key

    def test_rebind_preserves_evidence_stages_attempts_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,actual,key=self.fixture(root)
            path=root/'batches/order4-q6'
            before=Store(path,readonly=True)
            receipt=before.query(key);stages=[tuple(r) for r in before.db.execute('SELECT * FROM stages')]
            attempts=[tuple(r) for r in before.db.execute('SELECT * FROM attempts')]
            limits=dict(before.config['limits']);before.close()
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=actual):
                self.assertFalse(verify(root,pin)['device_rebound'])
                self.assertEqual(json.loads((root/'campaign.json').read_text())['evidence_device'],-2)
                self.assertTrue(verify(root,pin,True)['device_rebound'])
                verify(root,pin,True)  # Retrying is harmless.
            after=Store(path,readonly=True)
            self.assertEqual(after.query(key),receipt)
            self.assertEqual([tuple(r) for r in after.db.execute('SELECT * FROM stages')],stages)
            self.assertEqual([tuple(r) for r in after.db.execute('SELECT * FROM attempts')],attempts)
            self.assertEqual(after.config['limits'],limits)
            self.assertEqual(after.config['evidence_device'],actual['device']);after.close()

    def test_wrong_volume_or_active_writer_refuses_before_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,actual,_=self.fixture(root)
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=dict(actual,volume_uuid='other')):
                with self.assertRaises(IdentityError):verify(root,pin,True)
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=actual),writer_lock(root):
                with self.assertRaisesRegex(Exception,'another writer'):verify(root,pin,True)
            self.assertEqual(json.loads((root/'campaign.json').read_text())['evidence_device'],-2)

    def test_validation_addendum_rebind_keeps_observations_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,actual,key=self.fixture(root)
            (root/'campaign.json').rename(root/'validation.json')
            before=Store(root/'batches/order4-q6',readonly=True)
            receipt=before.query(key);limits=dict(before.config['limits']);before.close()
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=actual):
                self.assertTrue(verify(root,pin,True)['device_rebound'])
            after=Store(root/'batches/order4-q6',readonly=True)
            self.assertEqual(after.query(key),receipt);self.assertEqual(after.config['limits'],limits);after.close()
            self.assertEqual(json.loads((root/'validation.json').read_text())['evidence_device'],actual['device'])

    def test_corruption_prevents_rebinding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,actual,key=self.fixture(root)
            s=Store(root/'batches/order4-q6',readonly=True);sha=s.query(key)['pcm_sha256'];s.close()
            (root/'objects'/(sha+'.gz')).write_bytes(b'damaged')
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=actual):
                with self.assertRaises(EvidenceError):verify(root,pin,True)
            self.assertEqual(json.loads((root/'campaign.json').read_text())['evidence_device'],-2)

    def test_coordinate_pool_external_objects_are_verified_without_copying(self):
        from hoa_blackbox_lib.native import import_batch
        from test_hoa_blackbox_coordinate import new_coordinate,synthetic_prior
        from test_hoa_blackbox_campaign import HighFakeBackend
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();old=root/'old';old.mkdir();new=root/'new';new.mkdir()
            self.fixture(old)
            s=new_coordinate(new,synthetic_prior(HighFakeBackend()),q=6)
            try:
                import_batch(s,old/'batches/order4-q6')
                cfg=dict(native_identity=s.config['native_identity'],evidence_device=-2,status='interrupted',limits=s.config['limits'])
                (new/'coordinate.json').write_bytes(canonical(cfg))
                s.config['evidence_device']=-2;s.set_meta('config',s.config)
            finally:s.close()
            pin=dict(campaign=str(new),volume_uuid='original-volume',native_identity=cfg['native_identity'])
            actual=dict(volume_uuid=pin['volume_uuid'],device=new.stat().st_dev,mount_point=str(root),filesystem='synthetic')
            with patch('verify_hoa_campaign_storage.volume_identity',return_value=actual):
                self.assertTrue(verify(new,pin,True)['device_rebound'])


if __name__=='__main__':unittest.main()
