"""Nonempty windows must carry real state evidence, and native baselines stay strict."""
import copy,json,tempfile,unittest
from pathlib import Path
from validate_access import state_identity
from validate_access_native import media

class AccessValidationTests(unittest.TestCase):
    def test_missing_state_evidence_is_not_equal_history(self):
        r=dict(saved_frames=1,access=dict(metadata_before_output_sha256='a'*64,metadata_after_processing_sha256='b'*64))
        self.assertEqual(state_identity(r),('a'*64,'b'*64))
        for key in r['access']:
            for value in (None,'','g'*64,'a'*63):
                bad=copy.deepcopy(r);bad['access'][key]=value
                with self.assertRaises(AssertionError):state_identity(bad)
        r['saved_frames']=0
        with self.assertRaises(AssertionError):state_identity(r)
        r['access']['metadata_before_output_sha256']=None;state_identity(r)
    def test_native_missing_or_unqualified_windows_stop_before_source_access(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prior=root/'prior.json'
            records=[dict(source_sha256=str(i),passed=True,windows=[dict(kind=k,passed=True) for k in ('head','middle','refresh','tail')]) for i in range(13)]
            for variant in ('missing','duplicate','failed_source','failed_window'):
                rows=copy.deepcopy(records)
                if variant=='missing':rows[0]['windows'].pop()
                elif variant=='duplicate':rows[1]['source_sha256']=rows[0]['source_sha256']
                elif variant=='failed_source':rows[0]['passed']=False
                else:rows[0]['windows'][0]['passed']=False
                prior.write_text(json.dumps(dict(passed=True,media=rows)))
                with self.assertRaises(AssertionError):media(root/'missing-binary',{},root/'missing-collection',prior,3)
