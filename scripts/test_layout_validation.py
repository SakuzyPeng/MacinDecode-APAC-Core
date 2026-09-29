"""Acceptance identity must reject missing syntax/math evidence, not silently replay."""
import copy,unittest
from validate_layouts import identity
from layout_vectors import digest

class LayoutValidationTests(unittest.TestCase):
    def test_reference_requires_all_stages_and_successful_math(self):
        counts=dict(sequences=1,cases=1,presence=131328)
        r=dict(profile='p',code_commit='c',source_sha256='s',vector_manifest_sha256='v',access_manifest_sha256='a',presence_manifest_sha256='p',atol=1e-6,rtol=1e-5,counts=counts)
        reference=dict(r,passed=True,mode='independent_math',errors=[],sequences=[dict(passed=True)],cases=[dict(passed=True)],presence=dict(passed=True,cases=131328),metrics=dict(pcm=dict(failed_samples=0)),pcm_metrics=dict(failed_samples=0))
        reference['stage_sha256']=digest({k:reference[k] for k in ('sequences','cases','presence')});identity(reference,r,counts)
        for key in ('passed','source_sha256','presence_manifest_sha256','stage_sha256','sequences','cases','presence'):
            bad=copy.deepcopy(reference);bad[key]=False if key=='passed' else [] if key in ('sequences','cases') else {} if key=='presence' else 'bad'
            with self.subTest(key=key),self.assertRaises(AssertionError):identity(bad,r,counts)
        bad=copy.deepcopy(reference);bad['metrics']['pcm']['failed_samples']=1
        with self.assertRaises(AssertionError):identity(bad,r,counts)
    def test_frozen_layouts_do_not_expand_legacy_enumeration(self):
        from channel_vectors import LAYOUTS,EXTENDED_LAYOUTS,layout
        self.assertEqual(list(LAYOUTS),[1,2,6,8]);self.assertEqual(list(EXTENDED_LAYOUTS),[12,24])
        self.assertEqual([i for i,t in enumerate(layout(24)[2]) if t==3],[2,6])
        self.assertEqual([layout(24)[3][i] for i in (0,1,3,9)],['Lw','Rw','LFE2','LFE3'])

if __name__=='__main__':unittest.main()
