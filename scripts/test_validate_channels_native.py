"""The native gate must reject missing/wrong mappings, boundaries and DRC identity."""
import copy,unittest
from validate_channels_native import native_check
from validate_drc_off import identity

class ChannelNativeTests(unittest.TestCase):
    def test_missing_native_boundary_evidence_is_fatal(self):
        report=dict(embedded_preroll=None,channel_count=6,packet_sha256='test',elements=[])
        with self.assertRaises(AssertionError):native_check([report],dict(packets=[{}],events=[]))
    def test_dynamic_drc_identity_checks_every_channel(self):
        import hashlib,struct
        n=8;before=[[0.]*1024 for _ in range(n)];before[7][31]=0.25
        after=copy.deepcopy(before);after[7][31]+=0.125
        def hashes(v):return [hashlib.sha256(struct.pack('<1024f',*x)).hexdigest() for x in v]
        event=dict(sequence=0,role='current',frames=1024,before=before,after=after,before_sha256=hashes(before),after_sha256=hashes(after),identity=False)
        result=identity(event,n);self.assertFalse(result['identity']);self.assertEqual(result['first_failure']['channel'],7)
        with self.assertRaises(AssertionError):identity(event,6)
        event['identity']=True
        with self.assertRaises(AssertionError):identity(event,n)
