"""Focused mixed HOA dispatch, compact parameters, state and failure contracts."""
import hashlib, json, subprocess, tempfile, unittest
from pathlib import Path
from portable_tools import required_binary,assert_hoa_fast
from hoa_mixed_vectors import basis, bundle, cookie, packet, state_fixtures
from spectrum_vectors import bits, pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class MixedHoaTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary(); tmp=tempfile.TemporaryDirectory(prefix='apac-mixed-test-')
        self.addCleanup(tmp.cleanup); self.root=Path(tmp.name); self.index=0
    def path(self): self.index+=1; return self.root/str(self.index)
    def run_tool(self,*args): return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_dimension_mapping_mask_and_backend(self):
        for order,rate in ((2,48000),(3,44100)):
            n=(order+1)**2; root=self.path(); case=basis(n-1,4,1,order=order,ambient=[1,-1,1,-1])
            bundle(root,[packet(case,order=order,rate=rate)[0]],order=order,rate=rate)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads((root/'parsed').read_text())['report']; h=r['hoa']
            self.assertEqual((h['transport_channels'],h['core_channels'],h['coefficient_count']),(n,9,n))
            self.assertEqual(h['mixed']['salient_transport_channels'],[4,5,6,7,8]); self.assertEqual(h['mixed']['unused_transport_channels'],list(range(9,n)))
            for d in h['spatial']['salient']['descriptors']:
                self.assertEqual(d['coded_coefficient_indices'],list(range(4,n))); self.assertEqual(len(d['quantized']),n-4)
                self.assertEqual(d['ambient_omitted_coefficients'],[0,1,2,3]); self.assertEqual(d['restored'][:4],[0.]*4)
            out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,0,p.stderr)
            decoded=json.loads(p.stdout); self.assertEqual(decoded['backend'],'rust_hoa_mixed_sq_drc_off_f64_fft_v1')
            self.assertEqual((out/'pcm.f32le').stat().st_size,1024*n*4)
            self.assertTrue(any(h['channels_after_hoa'][n-1]['scaled']))
    def test_mode_switch_and_selected_report_retain_the_right_history(self):
        for order in (2,3):
            n=(order+1)**2
            cases=[basis(n-1,4,4,order=order,cluster=2),basis(4,0,3,order=order),basis(4,0,5,order=order,angles=(37,121)),basis(4,0,3,order=order)]
            raw=[packet(c,order=order)[0] for c in cases]; root=self.path(); bundle(root,raw,order=order)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'all'); self.assertEqual(p.returncode,0,p.stderr)
            rows=[json.loads(line)['report'] for line in (root/'all').read_text().splitlines()]
            for i in (1,3):
                for d in rows[i]['hoa']['spatial']['salient']['descriptors']: self.assertEqual(d['restored'][:4],[0.]*4)
                self.assertEqual(rows[i]['hoa']['spatial']['salient']['history_frame_sha256'],hashlib.sha256(raw[i-1]).hexdigest())
            p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',3,'--packets',1,'--output',root/'selected'); self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads((root/'selected').read_text())['report'],rows[3])
    def test_late_and_embedded_failure_artifacts(self):
        for f in state_fixtures()['fixtures']:
            for key in ('last_element_error','late_spatial_error','late_tail_error','embedded_error','outer_after_embedded_error'):
                root=self.path(); bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[key])],order=f['order'],rate=f['rate'],drc=True,rich=True)
                out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,key)
                error=json.loads(p.stderr)['error']; self.assertEqual(error['packet_index'],1); self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').is_file()); self.assertFalse((out/'decode-sq.json').exists())
                p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertNotEqual(p.returncode,0)
                self.assertEqual((root/'parsed.incomplete').exists(),p.returncode==1)
    def test_mixed_capacity_limits_are_independently_enforced(self):
        for order,capacity in ((2,18432),(3,32768)):
            root=self.path(); bundle(root,[pack('10001'+bits(capacity+1,16))],order=order)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed'); self.assertEqual(p.returncode,1,p.stderr)
            self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
    def test_configuration_restrictions_are_not_widened(self):
        raw=cookie(order=3); path=self.path(); path.write_bytes(raw)
        parsed=json.loads(self.run_tool('parse-cookie',path).stdout); wire=''.join(format(v,'08b') for v in raw)
        variants=[('components[0].hoa.flag_a','0'),('components[0].hoa.ambient_components_encoded','0101'),
                  ('components[0].hoa.parameter_1','01'),('components[0].hoa.salient[4].order','10')]
        for name,encoded in variants:
            field=next(f for f in parsed['fields'] if f['name']==name); at=field['bit_offset']
            cfg=pack(wire[:at]+encoded+wire[at+field['bit_length']:]); cfg=len(cfg).to_bytes(4,'big')+cfg[4:]
            root=self.path(); bundle(root,[packet({})[0]]); (root/'cookie.bin').write_bytes(cfg)
            m=json.loads((root/'manifest.json').read_text()); m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest()); (root/'manifest.json').write_text(json.dumps(m))
            out=self.path(); p=self.run_tool('decode-sq',root,'--out',out); self.assertEqual(p.returncode,1,name); self.assertFalse(out.exists())
            self.assertIn(name,p.stderr); self.assertIn('cookie bit',p.stderr)
    def test_fast_layout_budget_and_overwrite(self):
        for order in (2,3):
            n=(order+1)**2; raw=[packet(basis(n-1,4,1,order=order),order=order)[0]]*3
            for encoder in (caf,mp4):
                source=self.path(); source.write_bytes(encoder(cookie(order=order),raw,channels=n)[0]); out=self.path()
                assert_hoa_fast(self,source,out);out=self.path()
                out=self.path(); p=self.run_tool('decode-sq',source,'--out',out,'--frames',7); self.assertEqual(p.returncode,0,p.stderr)
                before=(out/'pcm.f32le').read_bytes(); p=self.run_tool('decode-sq',source,'--out',out); self.assertEqual(p.returncode,1); self.assertEqual(before,(out/'pcm.f32le').read_bytes())
            root=self.path(); bundle(root,raw*11,order=order); out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1); self.assertEqual(p.returncode,1); self.assertTrue((out/'.incomplete.json').exists())
    def test_missing_binary_is_an_error(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{'APAC_TOOL_BINARY':str(self.path())}):
            with self.assertRaises(FileNotFoundError): required_binary()


if __name__=='__main__': unittest.main()
