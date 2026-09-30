"""Focused salient state, dispatch, output, and failure contracts."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from hoa_salient_vectors import basis,bundle,cookie,packet,state_fixture
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class SalientTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();temporary=tempfile.TemporaryDirectory(prefix='apac-salient-test-');self.addCleanup(temporary.cleanup);self.root=Path(temporary.name);self.index=0
    def path(self):self.index+=1;return self.root/str(self.index)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_core_channels_are_distinct_from_output_coefficients(self):
        root=self.path();bundle(root,[packet(basis(15,0,1))[0],packet({})[0]])
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
        report=json.loads((root/'parsed').read_text().splitlines()[0])['report'];h=report['hoa'];self.assertEqual((h['transport_channels'],h['core_channels'],h['coefficient_count']),(16,5,16))
        self.assertFalse(report['elements'][15]['present']);self.assertTrue(any(h['channels_after_hoa'][15]['scaled']))
        self.assertEqual(report['elements'][0]['configuration']['output_channels'],[]);self.assertEqual(report['elements'][0]['configuration']['transport_channels'],[0])
        out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads(p.stdout);self.assertEqual(r['pcm']['layout']['value']['ambisonic_normalization'],'SN3D');self.assertIn('salient',r['backend']);self.assertEqual(r['saved_frames'],2048)
    def test_late_and_embedded_errors_keep_failure_markers(self):
        f=state_fixture()
        for key in ('last_element_error','late_spatial_error','late_tail_error','embedded_error','outer_after_embedded_error'):
            root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[key])],drc=True,rich=True);out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key);error=json.loads(p.stderr)['error'];self.assertEqual(error['packet_index'],1);self.assertIn('bit_offset',error)
            self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
    def test_fixed_configuration_rejects_other_subband_or_component_counts(self):
        raw=cookie();cfg=self.path();cfg.write_bytes(raw);parsed=json.loads(self.run_tool('parse-cookie',cfg).stdout)
        for name,value in [('components[0].hoa.salient[0].subbands_minus_one',2),('components[0].hoa.salient[4].order',2),('global.sample_rate_index',5)]:
            field=next(f for f in parsed['fields'] if f['name']==name);wire=''.join(format(v,'08b') for v in raw);start=field['bit_offset'];changed=pack(wire[:start]+bits(value,field['bit_length'])+wire[start+field['bit_length']:])
            root=self.path();bundle(root,[packet({})[0]]);(root/'cookie.bin').write_bytes(changed);m=json.loads((root/'manifest.json').read_text());m['file']['cookie']['value']=dict(bytes=len(changed),sha256=hashlib.sha256(changed).hexdigest())
            if name=='global.sample_rate_index':m['file']['format']['sample_rate']=32000
            (root/'manifest.json').write_text(json.dumps(m));out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertIn(name,p.stderr);self.assertFalse(out.exists())
    def test_fast_rejection_empty_range_budget_and_overwrite(self):
        payloads=[packet(basis(15,4,1))[0]]*3
        for encoder in (caf,mp4):
            source=self.path();source.write_bytes(encoder(cookie(),payloads,channels=16)[0]);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--access','fast');self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('HOA fast access',p.stderr)
            out=self.path();p=self.run_tool('decode-sq',source,'--out',out,'--start-frame',3072,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').read_bytes(),b'');self.assertTrue(json.loads(p.stdout)['input']['consistency_verified'])
            out=self.path();p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes()
            p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,payloads*7);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file())
    def test_decoder_report_compatibility_with_old_ambient_context(self):
        from hoa_vectors import packet as ambient_packet,bundle as ambient_bundle
        root=self.path();ambient_bundle(root,[ambient_packet({})[0]]);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
        h=json.loads((root/'parsed').read_text())['report']['hoa'];self.assertEqual(h['core_channels'],16);self.assertNotIn('salient',h['spatial']);self.assertEqual(h['numeric_profile'],'apac-hoa-ambient-math-v1')


if __name__=='__main__':unittest.main()
