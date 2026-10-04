"""Additive configuration, carrier validation, history and portable failure semantics."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary,assert_hoa_fast
from hoa_additive_vectors import cookie,packet,bundle,basis,shape,state_fixtures,sequences
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class AdditiveHoaTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary(); tmp=tempfile.TemporaryDirectory(prefix='apac-additive-hoa-'); self.addCleanup(tmp.cleanup); self.root=Path(tmp.name); self.index=0
    def path(self): self.index+=1; return self.root/str(self.index)
    def run_tool(self,*args): return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def change_cookie(self,root,raw):
        (root/'cookie.bin').write_bytes(raw); path=root/'manifest.json'; m=json.loads(path.read_text()); m['file']['cookie']['value']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()); path.write_text(json.dumps(m))
    def test_new_shapes_descriptors_and_report_coordinates(self):
        for f in state_fixtures()['fixtures']:
            root=self.path(); bundle(root,[bytes.fromhex(f['first'])],**f['options'])
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads((root/'parsed').read_text())['report']; h=r['hoa']; dynamic=f['options']['dynamic']; slots=(f['options']['order']+1)**2
            self.assertEqual(h['additive']['combination'],'add'); self.assertEqual(h['additive']['coordinate_space'],'internal_slots' if dynamic else 'acn')
            self.assertTrue(all(d['ambient_omitted_coefficients']==[] and len(d['restored'])==slots for d in h['spatial']['salient']['descriptors']))
            out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,0,p.stderr)
            result=json.loads(p.stdout); self.assertEqual(result['backend'],'rust_hoa_additive_sq_drc_off_f64_fft_v1')
            self.assertEqual((out/'pcm.f32le').stat().st_size,shape(f['options']['order'],dynamic)*4096)
    def test_corruption_positions_and_success_markers(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path(); bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']); out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,key)
                e=json.loads(p.stderr)['error']; self.assertEqual(e['packet_index'],1); self.assertIn('bit_offset',e)
                self.assertTrue((out/'.incomplete.json').is_file()); self.assertFalse((out/'decode-sq.json').exists())
    def test_addition_flag_with_one_source_kind_is_accepted(self):
        import hoa_vectors as ambient
        import hoa_salient_vectors as salient
        import hoa_dynamic_vectors as dynamic
        for module, options in ((ambient, dict(order=1)), (salient, dict(order=2)), (dynamic, dict(mixed=False))):
            raw = module.cookie(**options); cfg = self.path(); cfg.write_bytes(raw)
            report = json.loads(self.run_tool('parse-cookie', cfg).stdout)
            field = next(f for f in report['fields'] if f['name'] == 'components[0].hoa.flag_d')
            wire = ''.join(format(v, '08b') for v in raw); at = field['bit_offset']
            changed = pack(wire[:at] + '1' + wire[at+1:])
            root = self.path(); module.bundle(root, [module.packet({}, **options)[0]], **options)
            before = self.path(); result = self.run_tool('decode-sq', root, '--out', before)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.change_cookie(root, changed)
            after = self.path(); result = self.run_tool('decode-sq', root, '--out', after)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((before/'pcm.f32le').read_bytes(), (after/'pcm.f32le').read_bytes())

    def test_additive_wide_quantization_and_ragged_orders_use_matching_payloads(self):
        from portable_tools import assert_hoa_configuration
        for change in (dict(quantization_bits=7), dict(component_orders=[1, 2, 2, 2, 2])):
            assert_hoa_configuration(self, dict(order=2, path='add', counts=[4]*5, **change))

    def test_additive_carrier_shortage_still_rejects_before_output(self):
        raw = cookie(order=2); cfg = self.path(); cfg.write_bytes(raw)
        report = json.loads(self.run_tool('parse-cookie', cfg).stdout)
        f = next(f for f in report['fields'] if f['name'] == 'components[0].hoa.ambient_components_encoded')
        wire = ''.join(format(v, '08b') for v in raw); at = f['bit_offset']
        changed = pack(wire[:at] + bits(5, f['bit_length']) + wire[at+f['bit_length']:])
        root = self.path(); bundle(root, [packet({}, order=2)[0]], order=2)
        self.change_cookie(root, changed); out = self.path()
        result = self.run_tool('decode-sq', root, '--out', out)
        self.assertEqual(result.returncode, 1); self.assertIn('hoa-tce-channels', result.stderr)
        self.assertFalse(out.exists())
    def test_addition_does_not_accept_old_omitted_wire_as_full_descriptors(self):
        import hoa_mixed_vectors as old
        root=self.path(); old.bundle(root,[old.packet({},order=2)[0]],order=2); self.change_cookie(root,cookie(order=2)); out=self.path()
        p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1); self.assertIn('bit_offset',json.loads(p.stderr)['error']); self.assertFalse((out/'decode-sq.json').exists())
    def test_capacity_fast_budget_and_overwrite(self):
        for order,dynamic in ((2,False),(3,False),(2,True)):
            options=dict(order=order,dynamic=dynamic); n=shape(order,dynamic); cap=18432 if n==9 else 32768
            root=self.path(); bundle(root,[pack('10001'+bits(cap+1,16))],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,1); self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        options=dict(order=2,dynamic=True); raw=[packet(basis(8,4,1,**options),**options)[0]]*3
        for encoder in (caf,mp4):
            source=self.path(); source.write_bytes(encoder(cookie(**options),raw,channels=16)[0]); out=self.path()
            assert_hoa_fast(self,source,out);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7); self.assertEqual(p.returncode,0,p.stderr)
            before=(out/'pcm.f32le').read_bytes(); p=self.run_tool('decode-sq',source,'--out',out); self.assertEqual(p.returncode,1); self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path(); bundle(root,raw*7,**options); out=self.path(); p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1); self.assertTrue((out/'.incomplete.json').exists()); self.assertFalse((out/'decode-sq.json').exists())
    def test_range_reports_preserve_full_descriptor_history(self):
        options=dict(order=2,dynamic=True,selection=[0,2,4,8],transform=4); root=self.path()
        cases=[dict(basis(0,0,m,order=2,dynamic=True),transform_index=i) for i,m in enumerate((4,3,5,3))]
        bundle(root,[packet(c,**options)[0] for c in cases],**options)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'all'); self.assertEqual(p.returncode,0,p.stderr)
        expected=json.loads((root/'all').read_text().splitlines()[-1])['report']
        p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',3,'--packets',1,'--output',root/'last'); self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads((root/'last').read_text())['report'],expected)
    def test_independent_pcm_comparison_rejects_full_output_error(self):
        from validate_hoa_additive import measured
        metric=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
        with self.assertRaises(Exception): measured(metric,[0.01]*16,[0.]*16,dict(case=0,stage='pcm'),0,9,16)
        self.assertEqual(metric['failed_samples'],16); self.assertEqual(metric['first_failure']['acn'],0)
    def test_legacy_reports_have_no_additive_fields(self):
        import hoa_mixed_vectors as mixed
        import hoa_static_ambient_vectors as static
        import hoa_dynamic_vectors as dynamic
        for module,options in ((mixed,dict(order=2)),(static,dict(order=3,mixed=True,selection=[1,5,10,15],transform=4)),(dynamic,dict(mixed=True))):
            root=self.path(); module.bundle(root,[module.packet({},**options)[0]],**options)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            self.assertNotIn('additive',json.loads((root/'parsed').read_text())['report']['hoa'])


if __name__=='__main__': unittest.main()
