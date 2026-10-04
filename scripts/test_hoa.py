"""Focused HOA entry, failure-artifact and configuration contracts."""
import json,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary,assert_hoa_fast
from hoa_vectors import cookie,packet,bundle,excitation,state_fixture
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4

class HoaTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-hoa-test-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.counter=0
    def path(self):self.counter+=1;return self.root/str(self.counter)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_dispatch_is_hoa_asc_and_reports_are_typed(self):
        root=self.path();bundle(root,[packet(excitation(15))[0]])
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'hoa');self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads((root/'hoa').read_text())['report'];self.assertTrue(r['hoa']['hoa_complete']);self.assertEqual(r['hoa']['normalization'],'SN3D')
        self.assertEqual(r['hoa']['channels_after_hoa'][15]['acn_index'],15);self.assertEqual(r['elements'][0]['channels'],[])
        p=self.run_tool('parse-packets',root,'--depth','channels','--output',root/'old');self.assertEqual(p.returncode,2)
    def test_fast_matches_sequential_for_both_containers(self):
        raw=[packet(excitation(0))[0]]*4
        for name,encoder in [('caf',caf),('mp4',mp4)]:
            src=self.path();src.write_bytes(encoder(cookie(),raw,channels=16)[0]);out=self.path()
            assert_hoa_fast(self,src,out);out=self.path()
    def test_sequential_history_requires_origin_and_advances_before_selected_report(self):
        root=self.path();raw=[packet(dict(excitation(0),mode=5))[0],packet(excitation(15))[0]];bundle(root,raw)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',1,'--packets',1,'--output',root/'selected');self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads((root/'selected').read_text())['report'];self.assertEqual(r['hoa']['spatial']['effective_global_coding_mode'],5)
        # A valid ordinary replay bundle can still lack the full HOA history.
        manifest=root/'manifest.json';m=json.loads(manifest.read_text());m['start_packet']=1;m['file']['packet_count']['value']=3;m['file']['packet_table']['value']['valid_frames']=3072
        m['replay_window']=dict(requested_start_packet=1,requested_packets=2,actual_target_packets=2,included_preroll_packets=0,target_raw_start=1024,target_raw_end=3072)
        index=root/m['packet_index_file'];rows=[json.loads(s) for s in index.read_text().splitlines()]
        for row in rows:row['packet_index']+=1;row['raw_frame_position']['value']+=1024;row['dependency']['value']=dict(independently_decodable=True,preroll_packet_count=0);row['roll_distance']['value']=0
        index.write_text(''.join(json.dumps(r)+'\n' for r in rows));manifest.write_text(json.dumps(m))
        for command in ('decode-sq','parse-packets'):
            out=self.path();args=['--out',out] if command=='decode-sq' else ['--depth','hoa','--output',out]
            p=self.run_tool(command,root,*args);self.assertEqual(p.returncode,1,p.stdout);self.assertIn('packet zero',p.stderr);self.assertFalse(out.exists())
    def test_unsupported_cookie_fields_are_identified(self):
        original=cookie();src=self.path();src.write_bytes(original);parsed=json.loads(self.run_tool('parse-cookie',src).stdout)
        for name,value in [('global.profile_id',31),('components[0].hoa.flag_b',1),('components[0].hoa.parameter_0',0)]:
            f=next(f for f in parsed['fields'] if f['name']==name);wire=''.join(format(v,'08b') for v in original);pos=f['bit_offset'];wire=wire[:pos]+bits(value,f['bit_length'])+wire[pos+f['bit_length']:]
            root=self.path();bundle(root,[packet({})[0]]);cfg=pack(wire);(root/'cookie.bin').write_bytes(cfg);m=json.loads((root/'manifest.json').read_text());m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=__import__('hashlib').sha256(cfg).hexdigest());(root/'manifest.json').write_text(json.dumps(m))
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists())
            reason = {'global.profile_id': 'HOA profile 31 level 0', 'components[0].hoa.flag_b': 'hoa-component-count', 'components[0].hoa.parameter_0': 'parameter_0=0'}[name]
            self.assertIn(reason, p.stderr)
    def test_invalid_spatial_mode_and_late_element_do_not_export_success(self):
        f=state_fixture()
        for name in ('last_element_error','late_spatial_error','embedded_error','outer_after_embedded_error'):
            root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[name])],drc=True,rich=True)
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);e=json.loads(p.stderr)['error'];self.assertEqual(e['packet_index'],1);self.assertIn('bit_offset',e);self.assertTrue((out/'.incomplete.json').exists());self.assertFalse((out/'decode-sq.json').exists())
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertNotEqual(p.returncode,0)
            self.assertEqual((root/'parsed.incomplete').exists(),p.returncode==1)
    def test_capacity_output_budget_and_overwrite_protection(self):
        root=self.path();bundle(root,[pack('10001'+bits(32769,16))]);p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1)
        self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        root=self.path();bundle(root,[packet({})[0]]*20);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').exists())
        out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())

if __name__=='__main__':unittest.main()
