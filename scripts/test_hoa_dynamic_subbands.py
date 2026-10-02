"""Effective subband counts, mandatory inactive mappings and portable failure contracts."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary,assert_hoa_fast
from hoa_dynamic_subbands_vectors import cookie,packet,bundle,state_fixtures,native_controls
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class SubbandHoaTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-hoa-subbands-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.index=0
    def path(self):self.index+=1;return self.root/str(self.index)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def change_cookie(self,root,raw):
        (root/'cookie.bin').write_bytes(raw);p=root/'manifest.json';m=json.loads(p.read_text());m['file']['cookie']['value']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest());p.write_text(json.dumps(m))
    def test_wire_rows_are_eight_for_every_effective_count(self):
        for f in state_fixtures()['fixtures']:
            root=self.path();bundle(root,[bytes.fromhex(f['first'])],**f['options']);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            d=json.loads((root/'parsed').read_text())['report']['hoa']['dynamic_selection'];self.assertEqual(len(d['mappings']),8)
            self.assertEqual(d['active_subband_count'],f['options']['subbands']);self.assertEqual(len(d['subband_ends']),f['options']['subbands'])
        case=dict(list_mode=True);self.assertEqual(packet(case,subbands=1)[0],packet(case,subbands=7)[0])
    def test_inactive_and_late_corruption_cannot_produce_success(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key)
                e=json.loads(p.stderr)['error'];self.assertEqual(e['packet_index'],1);self.assertIn('bit_offset',e)
                if key=='inactive_duplicate':self.assertEqual(e['bit_offset'],f['inactive_error_bit'])
                self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
    def test_unsupported_configuration_rejected_before_output(self):
        original=cookie(subbands=1,path='replace');cfg=self.path();cfg.write_bytes(original);r=json.loads(self.run_tool('parse-cookie',cfg).stdout);wire=''.join(format(v,'08b') for v in original)
        for name,value in [('components[0].hoa.dynamic_selection.parameter',3),('components[0].hoa.dynamic_selection.subbands_minus_one',8)]:
            f=next(f for f in r['fields'] if f['name']==name);at=f['bit_offset'];changed=pack(wire[:at]+bits(value,f['bit_length'])+wire[at+f['bit_length']:])
            root=self.path();bundle(root,[packet({},subbands=1,path='replace')[0]],subbands=1,path='replace');self.change_cookie(root,changed);out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('hoa-subband-count' if name.endswith('subbands_minus_one') else name,p.stderr)
    def test_old_eight_band_reports_keep_their_fields_and_profiles(self):
        import hoa_dynamic_vectors as dynamic
        import hoa_additive_vectors as additive
        for writer,opts in ((dynamic,dict(mixed=False)),(dynamic,dict(mixed=True)),(additive,dict(order=2,dynamic=True))):
            root=self.path();writer.bundle(root,[writer.packet({},**opts)[0]],**opts);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            d=json.loads((root/'parsed').read_text())['report']['hoa']['dynamic_selection'];self.assertEqual(len(d['subband_ends']),8)
            for field in ('active_subband_count','subband_profile','format_sha256'):self.assertNotIn(field,d)
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr)
            impl=json.loads(p.stdout)['pcm']['decoder_settings']['implementation']['value'];self.assertNotIn('hoa_dynamic_subband_count',impl);self.assertNotIn('hoa_dynamic_subband_profile',impl)
    def test_range_parsing_keeps_history_and_all_wire_maps(self):
        _,opts,cases=list(native_controls())[-1];root=self.path();bundle(root,[packet(c,**opts)[0] for c in cases],**opts)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'all');self.assertEqual(p.returncode,0,p.stderr)
        expected=json.loads((root/'all').read_text().splitlines()[-1])['report'];p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',len(cases)-1,'--packets',1,'--output',root/'last');self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads((root/'last').read_text())['report'],expected)
    def test_fast_limits_and_overwrite(self):
        raw=[packet({},subbands=1)[0]]*3
        root=self.path();bundle(root,[pack('10001'+bits(32769,16))],subbands=1);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1);self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        for encode in (caf,mp4):
            source=self.path();source.write_bytes(encode(cookie(subbands=1),raw,channels=16)[0]);out=self.path()
            assert_hoa_fast(self,source,out);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr)
            before=(out/'pcm.f32le').read_bytes();p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,raw*7,subbands=1);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())


if __name__=='__main__':unittest.main()
