"""External raw-evidence references remain reusable through a shared pool."""
from pathlib import Path
import tempfile
import unittest

from hoa_blackbox_lib.native import import_batch
from hoa_blackbox_lib import wire
from test_hoa_blackbox_campaign import pooled,runner


class ImportedPoolTests(unittest.TestCase):
    def test_new_capture_sharing_imported_artifacts_is_reusable_without_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            aroot=Path(tmp)/'old';aroot.mkdir()
            broot=Path(tmp)/'new';broot.mkdir()
            source=pooled(aroot);a=runner(source)
            old,_=a.probe('mode1','test','old',a.writer.fixed(wire.vector(40,order=4)))
            original_files={p.name:p.read_bytes() for p in (aroot/'objects').iterdir()}
            source_path=source.out;source.close()
            target=pooled(broot);b=runner(target)
            try:
                self.assertEqual(import_batch(target,source_path),1)
                self.assertIsNotNone(target.query(old))
                new,_=b.probe('mode1','test','new',b.writer.fixed(wire.vector(41,order=4)))
                cookie=target.query(new)['artifacts']['input/cookie.bin']
                self.assertFalse((broot/'objects'/(cookie+'.gz')).exists())
            finally:target.close()
            reused=pooled(broot,name='order4-q6-reused');c=runner(reused)
            try:
                self.assertEqual(c.probe('mode1','test','new',c.writer.fixed(wire.vector(41,order=4)))[0],new)
                self.assertEqual(c.backend.calls,0)
            finally:reused.close()
            self.assertEqual({p.name:p.read_bytes() for p in (aroot/'objects').iterdir()},original_files)


if __name__=='__main__':unittest.main()
