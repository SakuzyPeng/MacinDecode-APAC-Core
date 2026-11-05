"""Evidence integrity checks for the targeted native diagnostic driver."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from diagnose_macos27_apac import patch_bits, retain_pcm, dft_audit, compare_values


class DiagnosticEvidenceTests(unittest.TestCase):
    def test_cross_byte_patch_preserves_neighbors(self):
        # Bits 5..14 cross a byte boundary; both outside ranges remain unchanged.
        source=bytes.fromhex('a55a')
        changed=patch_bits(source,5,10,3)
        self.assertEqual(changed.hex(),'a006')
        self.assertEqual(patch_bits(changed,5,10,685),source)
        for args in [(-1,1,0),(15,2,0),(0,2,4),(0,2,-1)]:
            with self.assertRaises(ValueError):
                patch_bits(source,*args)

    def test_dedup_keeps_original_pcm_paths_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=struct.pack('<2f',0.25,-0.5)
            digest=hashlib.sha256(raw).hexdigest()
            for i in range(2):
                dest=root/str(i);dest.mkdir()
                (dest/'pcm.f32le').write_bytes(raw)
                (dest/'pcm.json').write_text(json.dumps(dict(sha256=digest,frames=1,channels=2)))
                record=retain_pcm(dest,root/'store')
                self.assertEqual((dest/'pcm.f32le').read_bytes(),raw)
                self.assertEqual((dest/'pcm.f32le').stat().st_ino,Path(record['storage']).stat().st_ino)
            self.assertEqual(len(list((root/'store').iterdir())),1)

    def test_corrupt_pcm_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);dest=root/'trial';dest.mkdir()
            (dest/'pcm.f32le').write_bytes(b'bad')
            (dest/'pcm.json').write_text(json.dumps(dict(sha256='wrong',frames=1,channels=2)))
            with self.assertRaises(ValueError):
                retain_pcm(dest,root/'store')
            self.assertEqual((dest/'pcm.f32le').read_bytes(),b'bad')

    def test_incomplete_or_wrong_alignment_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            row=dict(length=512,direction=1,input_sha256='a',output_sha256='b',
                     actual_or_mod64=0,actual_oi_mod64=0)
            def save(audit):
                (root/'dft.jsonl').write_text(json.dumps(row)+'\n'+json.dumps(audit)+'\n')
            save(dict(audit=True,calls=1,unknown=0))
            self.assertEqual(dft_audit(root,'aligned64')['calls'],1)
            with self.assertRaises(ValueError):
                dft_audit(root,'offset16')
            for audit in [dict(audit=True,calls=2,unknown=0),dict(audit=True,calls=1,unknown=1)]:
                save(audit)
                with self.assertRaises(ValueError):
                    dft_audit(root,'aligned64')

    def test_fixed_tolerance_reports_first_failure(self):
        result=compare_values([0.0,1.0],[0.0000005,1.0001])
        self.assertEqual(result['failed_samples'],1)
        self.assertEqual(result['first_failure']['sample'],1)
        with self.assertRaises(ValueError):
            compare_values([0.0],[])


if __name__=='__main__':
    unittest.main()
