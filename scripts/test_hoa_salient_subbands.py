"""Variable spatial descriptors, independent cookie positions and stateful interfaces."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from hoa_salient_subbands_vectors import cookie,packet,bundle,basis,state_fixtures,native_controls,shape
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class SpatialSubbandTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-spatial-subband-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.index=0
    def path(self):self.index+=1;return self.root/str(self.index)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def change_cookie(self,root,raw):
        (root/'cookie.bin').write_bytes(raw);p=root/'manifest.json';m=json.loads(p.read_text());m['file']['cookie']['value']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest());p.write_text(json.dumps(m))
    def test_counts_escape_positions_and_actual_descriptor_rows(self):
        for f in state_fixtures()['fixtures']:
            cfg=self.path();cfg.write_bytes(bytes.fromhex(f['cookie']));p=self.run_tool('parse-cookie',cfg);self.assertEqual(p.returncode,0,p.stderr);parsed=json.loads(p.stdout)
            for truth in f['cookie_counts']:
                field=next(v for v in parsed['fields'] if v['name']==truth['name'])
                for k,v in truth.items():self.assertEqual(field[k],v)
            self.assertTrue(any(v['value']==15 and v['bit_length']==10 for v in f['cookie_counts']))
            root=self.path();bundle(root,[bytes.fromhex(f['first'])],**f['options']);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            side=json.loads((root/'parsed').read_text())['report']['hoa']['spatial']['salient'];counts=f['options']['counts']
            self.assertEqual(len(side['descriptors']),sum(counts));self.assertEqual([v['subband_count'] for v in side['component_subbands']],counts)
            self.assertNotIn('subband_ends',side);self.assertNotIn('lines_per_window',side)
    def test_uniform_counts_have_real_common_endpoints(self):
        for count in (1,16):
            opts=dict(order=2,counts=[count]*5);root=self.path();bundle(root,[packet({},**opts)[0]],**opts);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            side=json.loads((root/'parsed').read_text())['report']['hoa']['spatial']['salient'];self.assertEqual(len(side['subband_ends']),count);self.assertEqual(len(side['descriptors']),5*count)
    def test_late_descriptors_and_nested_failures_preserve_failure_markers(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key)
                e=json.loads(p.stderr)['error'];self.assertEqual(e['packet_index'],1);self.assertIn('bit_offset',e);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
    def test_over_limit_counts_and_other_spatial_methods_rejected(self):
        for f in state_fixtures()['fixtures']:
            root=self.path();bundle(root,[bytes.fromhex(f['first'])],**f['options']);self.change_cookie(root,bytes.fromhex(f['bad_count_cookie']));out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('hoa-subband-count',p.stderr)
        raw=cookie();cfg=self.path();cfg.write_bytes(raw);r=json.loads(self.run_tool('parse-cookie',cfg).stdout);f=next(f for f in r['fields'] if f['name']=='components[0].hoa.parameter_1');wire=''.join(format(v,'08b') for v in raw);at=f['bit_offset']
        for method in (1,2,3):
            changed=pack(wire[:at]+bits(method,2)+wire[at+2:]);root=self.path();bundle(root,[packet({})[0]]);self.change_cookie(root,changed);out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('hoa.parameter_1',p.stderr)
    def test_stateful_range_keeps_each_components_history(self):
        _,opts,cases=list(native_controls())[2];root=self.path();bundle(root,[packet(c,**opts)[0] for c in cases],**opts);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'all');self.assertEqual(p.returncode,0,p.stderr)
        expected=json.loads((root/'all').read_text().splitlines()[-1])['report'];p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',len(cases)-1,'--packets',1,'--output',root/'last');self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads((root/'last').read_text())['report'],expected)
    def test_old_four_band_json_omits_new_geometry_and_metadata(self):
        import hoa_salient_vectors as pure
        import hoa_additive_vectors as add
        import hoa_dynamic_subbands_vectors as dynamic
        for writer,opts in ((pure,dict(order=3)),(add,dict(order=2)),(dynamic,dict(path='replace',subbands=3))):
            root=self.path();writer.bundle(root,[writer.packet({},**opts)[0]],**opts);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            side=json.loads((root/'parsed').read_text())['report']['hoa']['spatial']['salient'];self.assertEqual(side['subband_ends'],[32,80,216,1024]);self.assertEqual(len(side['descriptors']),20)
            for name in ('component_subbands','subband_profile','format_sha256'):self.assertNotIn(name,side)
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr);impl=json.loads(p.stdout)['pcm']['decoder_settings']['implementation']['value'];self.assertNotIn('hoa_salient_subband_counts',impl)
    def test_capacity_fast_limits_and_overwrite(self):
        for f in state_fixtures()['fixtures']:
            n=shape(f['options']['order'],f['options']['dynamic']);cap=18432 if n==9 else 32768;root=self.path();bundle(root,[pack('10001'+bits(cap+1,16))],**f['options']);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1);self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        opts=dict(order=2,path='add',dynamic=True);raw=[packet({},**opts)[0]]*3
        for encode in (caf,mp4):
            source=self.path();source.write_bytes(encode(cookie(**opts),raw,channels=16)[0]);out=self.path();p=self.run_tool('decode-sq',source,'--out',out,'--access','fast');self.assertEqual(p.returncode,1);self.assertFalse(out.exists())
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes();p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,raw*7,**opts);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
    def test_numeric_failure_coordinates_use_actual_descriptor_offsets(self):
        from validate_hoa_salient_subbands import measure
        counts=[1,3,4,9,16];rows=[dict(component_index=sc,subband_index=b) for sc,c in enumerate(counts) for b in range(c)];i=(sum(counts[:3])+8)*9+7;actual=[0.]*(sum(counts)*9);expected=actual.copy();actual[i]=.01
        metric=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
        with self.assertRaises(RuntimeError):measure(metric,actual,expected,dict(case=0,packet=0,role='current',stage='descriptors'),0,9,9,True,rows)
        self.assertEqual((metric['first_failure']['component'],metric['first_failure']['subband'],metric['first_failure']['slot']),(3,8,7))

if __name__=='__main__':unittest.main()
