"""Static ambient configuration, synthesis, selector and failure interface contracts."""
import json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from hoa_static_ambient_vectors import cookie,packet,bundle,ambient_basis,state_fixtures
from hoa_mixed_vectors import basis
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class StaticAmbientTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary(); tmp=tempfile.TemporaryDirectory(prefix='apac-static-ambient-')
        self.addCleanup(tmp.cleanup); self.root=Path(tmp.name); self.index=0
    def path(self): self.index+=1; return self.root/str(self.index)
    def run_tool(self,*args): return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_fixed_and_frame_selector_positions_are_explicit(self):
        for transform in range(5):
            options=dict(order=1,mixed=False,selection=[0,1,2,3],transform=transform)
            root=self.path(); raw,truth=packet(ambient_basis(0,2),**options); bundle(root,[raw],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads((root/'parsed').read_text())['report']; a=r['hoa']['spatial']['ambient']; expected=truth['spatial']['ambient']
            for k,v in expected.items(): self.assertEqual(a[k],v,k)
            fields=[f for f in r['fields'] if f['name']=='hoa.ambient.transform_index']; self.assertEqual(len(fields),int(transform==4))
            if fields: self.assertEqual(fields[0]['bit_length'],2)
    def test_absent_transport_does_not_clear_coupled_output(self):
        options=dict(order=1,mixed=False,selection=None,transform=4); root=self.path()
        raws=[packet(ambient_basis(0,0),**options)[0],packet(dict(transform_index=2),**options)[0],packet(dict(transform_index=3),**options)[0]]
        bundle(root,raws,**options); out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,0,p.stderr)
        import struct
        pcm=list(struct.iter_unpack('<4f',(out/'pcm.f32le').read_bytes()))
        self.assertTrue(any(row[1] for row in pcm[:1024])); self.assertTrue(all(row[0]==row[1]==row[2]==row[3] for row in pcm[:2048]))
        self.assertTrue(all(row==(0.,0.,0.,0.) for row in pcm[2048:]))
    def test_invalid_selection_never_creates_output(self):
        for order,mixed,selection in ((3,True,[0,1,4,4]),(3,True,[0,1,11,8]),(2,True,[0,1,4,15]),(1,False,[0,1,2,2])):
            options=dict(order=order,mixed=mixed,selection=selection,transform=0); root=self.path()
            bundle(root,[packet({},**options)[0]],**options); out=self.path(); p=self.run_tool('decode-sq',root,'--out',out)
            self.assertEqual(p.returncode,1,str(selection)); self.assertFalse(out.exists()); self.assertIn('selection',p.stderr)
    def test_nonprefix_mask_and_history_source(self):
        options=dict(order=3,mixed=True,selection=[4,8,12,15],transform=0); root=self.path()
        raw=[packet(basis(0,4,mode),**options)[0] for mode in (4,3)]; bundle(root,raw,**options)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',1,'--packets',1,'--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads((root/'parsed').read_text())['report']
        for d in r['hoa']['spatial']['salient']['descriptors']:
            self.assertEqual(d['ambient_omitted_coefficients'],[4,8,12,15]); self.assertIn(0,d['coded_coefficient_indices'])
            self.assertTrue(all(d['restored'][k]==0 for k in [4,8,12,15]))
        self.assertIsNotNone(r['hoa']['spatial']['salient']['history_frame_sha256'])
    def test_late_and_embedded_failures_preserve_failure_markers(self):
        for f in state_fixtures()['fixtures']:
            for key in ('last_element_error','late_spatial_error','late_tail_error','embedded_error','outer_after_embedded_error'):
                root=self.path(); bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[key])],**f['options']); out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,key)
                error=json.loads(p.stderr)['error']; self.assertEqual(error['packet_index'],1); self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').is_file()); self.assertFalse((out/'decode-sq.json').exists())
    def test_fast_budget_and_overwrite_are_unchanged(self):
        options=dict(order=3,mixed=True,selection=[1,5,10,15],transform=4); raw=[packet(dict(basis(8,4,1),transform_index=1),**options)[0]]*3
        for encoder in (caf,mp4):
            source=self.path(); source.write_bytes(encoder(cookie(**options),raw,channels=16)[0]); out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--access','fast'); self.assertEqual(p.returncode,1); self.assertFalse(out.exists()); self.assertIn('HOA fast access',p.stderr)
            out=self.path(); p=self.run_tool('decode-sq',source,'--out',out,'--frames',7); self.assertEqual(p.returncode,0,p.stderr)
            before=(out/'pcm.f32le').read_bytes(); p=self.run_tool('decode-sq',source,'--out',out); self.assertEqual(p.returncode,1); self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path(); bundle(root,raw*7,**options); out=self.path(); p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1); self.assertTrue((out/'.incomplete.json').exists())
    def test_old_reports_have_no_new_ambient_field(self):
        import hoa_vectors as ambient
        import hoa_mixed_vectors as mixed
        for module,options in ((ambient,dict(order=1)),(mixed,dict(order=2))):
            root=self.path(); module.bundle(root,[module.packet({},**options)[0]],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            self.assertNotIn('ambient',json.loads((root/'parsed').read_text())['report']['hoa']['spatial'])


if __name__=='__main__': unittest.main()
