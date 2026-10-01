"""Reduced-slot HOA dimensions, malformed maps, first errors and portable entry contracts."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from hoa_dynamic_vectors import cookie,packet,bundle,basis,maps,state_fixtures
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class DynamicHoaTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary(); tmp=tempfile.TemporaryDirectory(prefix='apac-dynamic-hoa-'); self.addCleanup(tmp.cleanup); self.root=Path(tmp.name); self.index=0
    def path(self): self.index+=1; return self.root/str(self.index)
    def run_tool(self,*args): return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_internal_slots_and_output_acn_are_separate(self):
        for mixed in (False,True):
            options=dict(mixed=mixed,selection=[0,2,4,8] if mixed else None,transform=4 if mixed else 0)
            case=dict(basis(7,4,1,mixed=mixed),list_mode=True,mappings=maps(7,True),transform_index=2)
            root=self.path(); bundle(root,[packet(case,**options)[0]],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads((root/'parsed').read_text())['report']; h=r['hoa']; d=h['dynamic_selection']
            self.assertEqual((h['order'],h['coefficient_count'],h['output_order'],h['output_coefficient_count']),(2,9,3,16))
            self.assertEqual(len(d['before_selection']),9); self.assertEqual(len(h['channels_after_hoa']),16)
            self.assertTrue(all('slot_index' in s and 'acn_index' not in s for s in d['before_selection']))
            self.assertNotIn('mixed',h); self.assertNotIn('ambient',h['spatial'])
            for v in d.get('internal_ambient',{}).get('channels_after_transform',[]): self.assertIn('slot_index',v); self.assertNotIn('acn_index',v)
            self.assertEqual(d['mappings'][0]['target_acn_indices'],maps(7,True)[0])
            out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads(p.stdout); self.assertEqual(r['pcm']['channels'],16); self.assertEqual(r['pcm']['layout']['value']['ambisonic_order'],3)
            self.assertEqual((out/'pcm.f32le').stat().st_size,65536)
    def test_bad_maps_and_late_failures_have_packet_and_bit_positions(self):
        for f in state_fixtures()['fixtures']:
            for key in ('dynamic_error','bitmap_too_few','bitmap_too_many','last_element_error','late_spatial_error','late_tail_error','embedded_error','outer_after_embedded_error'):
                root=self.path(); bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[key])],**f['options']); out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,key)
                e=json.loads(p.stderr)['error']; self.assertEqual(e['packet_index'],1); self.assertIn('bit_offset',e)
                if key=='dynamic_error': self.assertEqual(e['bit_offset'],f['dynamic_error_bit'])
                self.assertTrue((out/'.incomplete.json').is_file()); self.assertFalse((out/'decode-sq.json').exists())
    def test_reserved_method_and_excess_band_count_are_rejected_before_output(self):
        original=cookie(mixed=True,selection=[0,2,4,8]); path=self.path(); path.write_bytes(original)
        r=json.loads(self.run_tool('parse-cookie',path).stdout); wire=''.join(format(v,'08b') for v in original)
        for name,value in [('components[0].hoa.dynamic_selection.parameter',3),('components[0].hoa.dynamic_selection.subbands_minus_one',8)]:
            f=next(f for f in r['fields'] if f['name']==name); at=f['bit_offset']; raw=pack(wire[:at]+bits(value,f['bit_length'])+wire[at+f['bit_length']:])
            root=self.path(); bundle(root,[packet({},mixed=True,selection=[0,2,4,8])[0]],mixed=True,selection=[0,2,4,8]); (root/'cookie.bin').write_bytes(raw)
            manifest=json.loads((root/'manifest.json').read_text()); manifest['file']['cookie']['value']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()); (root/'manifest.json').write_text(json.dumps(manifest))
            out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,name); self.assertFalse(out.exists()); self.assertIn('hoa-subband-count' if name.endswith('subbands_minus_one') else name,p.stderr)
    def test_range_reports_advance_history_from_zero(self):
        options=dict(mixed=True,selection=[0,2,4,8],transform=4,method=0); root=self.path()
        cases=[dict(basis(7,4,m,mixed=True),list_mode=bool(i%2),mappings=maps(i, bool(i%2)),transform_index=i%4) for i,m in enumerate((4,3,5,3))]
        bundle(root,[packet(c,**options)[0] for c in cases],**options)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'all'); self.assertEqual(p.returncode,0,p.stderr)
        expected=json.loads((root/'all').read_text().splitlines()[-1])['report']
        p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',3,'--packets',1,'--output',root/'selected'); self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads((root/'selected').read_text())['report'],expected)
    def test_capacity_fast_budget_and_overwrite(self):
        root=self.path(); bundle(root,[pack('10001'+bits(32769,16))]); p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed')
        self.assertEqual(p.returncode,1); self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        raw=[packet(basis(8,4,1))[0]]*3
        for encoder in (caf,mp4):
            source=self.path(); source.write_bytes(encoder(cookie(),raw,channels=16)[0]); out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--access','fast'); self.assertEqual(p.returncode,1); self.assertFalse(out.exists()); self.assertIn('HOA fast access',p.stderr)
            out=self.path(); p=self.run_tool('decode-sq',source,'--out',out,'--frames',7); self.assertEqual(p.returncode,0,p.stderr)
            before=(out/'pcm.f32le').read_bytes(); p=self.run_tool('decode-sq',source,'--out',out); self.assertEqual(p.returncode,1); self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path(); bundle(root,raw*7); out=self.path(); p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1); self.assertTrue((out/'.incomplete.json').exists())
    def test_old_reports_omit_dynamic_dimensions(self):
        import hoa_vectors as ambient
        import hoa_mixed_vectors as mixed
        import hoa_static_ambient_vectors as static
        for module,options in ((ambient,dict(order=1)),(mixed,dict(order=2)),(static,dict(order=3,mixed=True,selection=[1,5,10,15],transform=4))):
            root=self.path(); module.bundle(root,[module.packet({},**options)[0]],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            h=json.loads((root/'parsed').read_text())['report']['hoa']
            for name in ('dynamic_selection','output_order','output_coefficient_count'): self.assertNotIn(name,h)


if __name__=='__main__': unittest.main()
