"""Fast-range entry contracts, prefix failures and independently specified work."""
import copy,json,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from channel_vectors import LAYOUTS,cookie,packet,bundle,excitation,sequences
from access_vectors import generated,expected_access,cases,manifest
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode
from validate_access import check_fast

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-access-test-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.counter=0
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def source(self,n,payloads,rate=48000,container='caf',**options):
        self.counter+=1;path=self.root/f'{self.counter}.audio';cfg=cookie(n,rate,**options)
        if container=='caf':raw,_=caf_encode(cfg,payloads,rate,channels=n)
        else:raw,_,_=mp4_encode(cfg,payloads,rate,channels=n)
        path.write_bytes(raw);return path
    def decode(self,src,mode=None,**options):
        self.counter+=1;dst=self.root/f'output-{self.counter}';args=[]
        if mode is not None:args+=['--access',mode]
        for key,value in options.items():args+=['--'+key.replace('_','-'),value]
        p=self.run_tool('decode-sq',src,'--out',dst,*args);return p,dst
    def test_default_is_unchanged_and_fast_is_explicit_for_files(self):
        for n in LAYOUTS:
            payloads=[packet(excitation(n,n-1),n)[0]]*5;src=self.source(n,payloads)
            outputs=[]
            for mode in (None,'sequential','fast'):
                p,out=self.decode(src,mode,start_frame=3097,frames=31);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout)
                self.assertEqual('access' in r,mode is not None);outputs.append((out/'pcm.f32le').read_bytes())
                if mode=='fast':self.assertEqual(r['access']['prefix_scanned_packets'],2);self.assertEqual(r['warmup_packets'],1)
            self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[0],outputs[2])
            directory=self.root/f'bundle-{n}';bundle(directory,payloads,n)
            p,out=self.decode(directory,'fast');self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('CAF/MP4',p.stderr)
    def test_scan_bounds_numeric_paths_and_metadata_match_for_every_layout(self):
        for n in LAYOUTS:
            seen=set()
            for index,case in enumerate(cases(n)):
                if case[0] in seen:continue
                seen.add(case[0]);d=generated(n,48000,index,case)
                self.counter+=1;src=self.root/f'case{self.counter}';src.write_bytes(d['mp4'])
                name,start,count=d['ranges'][2];results=[]
                for mode in ('sequential','fast'):
                    p,out=self.decode(src,mode,start_frame=start,frames=count);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);results.append((r,(out/'pcm.f32le').read_bytes()))
                self.assertEqual(results[0][1],results[1][1]);check_fast(results[1][0]['access'],expected_access(d,start,count))
                for key in ('metadata_before_output_sha256','metadata_after_processing_sha256'):
                    self.assertEqual(results[0][0]['access'][key],results[1][0]['access'][key])
    def test_empty_output_scans_history_without_synthesizing(self):
        for container in ('caf','mp4'):
            n=6;options=dict(drc=True,rich=True);payloads=[packet(dict(drc=dict(header=True,loudness_value=v)),n,**options)[0] for v in (1,165,255)]
            src=self.source(n,payloads,container=container,**options);states=[]
            for mode in ('sequential','fast'):
                p,out=self.decode(src,mode,start_frame=3072,frames=1);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual((out/'pcm.f32le').read_bytes(),b'');states.append(r['access']['metadata_after_processing_sha256'])
                if mode=='fast':self.assertEqual(r['packets'],0);self.assertEqual(r['access']['prefix_scanned_packets'],3);self.assertIsNone(r['access']['synthesis_start_packet'])
            self.assertEqual(*states)
    def test_prefix_errors_and_first_error_priority_are_identical(self):
        fixtures=json.loads((Path(__file__).resolve().parents[1]/'data/channel-state-fixtures-v1.json').read_text())['fixtures']
        for f in fixtures:
            n=f['channels'];cfg=bytes.fromhex(f['cookie']);good=bytes.fromhex(f['first'])
            invalids=[bytes.fromhex(f[k]) for k in ('last_element_error','late_drc_error')]
            outer,truth=packet(dict(frame_type=2,preroll=excitation(n,0)),n,drc=True,rich=True)
            both=bytearray(outer)
            for bit in (truth['inner_range']['start_bit_offset']+truth['inner']['elements'][0]['start_bit_offset']+1,truth['elements'][0]['start_bit_offset']+1):both[bit//8]|=1<<(7-bit%8)
            invalids.append(bytes(both))
            for bad in invalids:
                raw,_=caf_encode(cfg,[good,bad,good,good,good],channels=n);self.counter+=1;src=self.root/f'bad-{self.counter}';src.write_bytes(raw)
                errors=[]
                for mode in ('sequential','fast'):
                    p,out=self.decode(src,mode,start_frame=4096,frames=1);self.assertEqual(p.returncode,1,p.stdout);errors.append(json.loads(p.stderr)['error']);self.assertTrue((out/'.incomplete.json').exists());self.assertFalse((out/'decode-sq.json').exists())
                self.assertEqual(*errors);self.assertEqual(errors[0]['packet_index'],1)
    def test_each_byte_prefix_truncation_preserves_failure_semantics(self):
        n=2;options=dict(drc=True,rich=True);case=next(c for c in sequences(n) if c[0]=='joint_tools');raw=packet(case[2][1],n,**options)[0];good=packet({},n,**options)[0]
        for end in range(len(raw)):
            bad=raw[:end] or b'\xff';src=self.source(n,[good,bad,good,good],**options);results=[]
            for mode in ('sequential','fast'):
                p,out=self.decode(src,mode,start_frame=3072,frames=1);results.append(p)
            self.assertEqual(results[0].returncode,results[1].returncode,str(end))
            if results[0].returncode:self.assertEqual(json.loads(results[0].stderr)['error'],json.loads(results[1].stderr)['error'],str(end))
    def test_no_4096_limit_and_input_verification_includes_unused_tail(self):
        n=1;good=packet(excitation(n,0),n)[0];src=self.source(n,[good]*4101)
        p,out=self.decode(src,'fast',start_frame=4098*1024+7,frames=13);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout)
        self.assertEqual(r['access']['prefix_scanned_packets'],4097);self.assertEqual(r['warmup_packets'],1);self.assertEqual(r['integrity_checked_packets'],4101)
        src=self.source(n,[good]*5+[b'\xff'])
        p,_=self.decode(src,'fast',start_frame=3072,frames=1);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)['integrity_checked_packets'],6)
        p,_=self.decode(src,'fast',start_frame=5120,frames=1);self.assertEqual(p.returncode,1)
    def test_output_budget_overwrite_and_bad_ranges(self):
        n=8;good=packet(excitation(n,7),n)[0];src=self.source(n,[good]*40)
        p,out=self.decode(src,'fast',max_output_mib=1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').exists())
        p,out=self.decode(src,'fast',start_frame=3072,frames=9);self.assertEqual(p.returncode,0,p.stderr);raw=(out/'pcm.f32le').read_bytes()
        p=self.run_tool('decode-sq',src,'--access','fast','--out',out);self.assertEqual(p.returncode,1);self.assertEqual(raw,(out/'pcm.f32le').read_bytes())
        for options in (dict(frames=0),dict(start_frame=2**64-1),dict(start_frame=40*1024+1)):
            p,_=self.decode(src,'fast',**options);self.assertEqual(p.returncode,1)
    def test_missing_or_wrong_work_evidence_cannot_pass(self):
        d=generated(2,48000,0,next(cases(2)));expected=expected_access(d,*d['ranges'][2][1:]);actual=dict(expected,timings_seconds=dict(total=0.))
        check_fast(actual,expected)
        for key in expected:
            bad=dict(actual);bad[key]=None
            if expected[key] is None:bad[key]=1
            with self.assertRaises(AssertionError):check_fast(bad,expected)
        from validate_access import MANIFEST
        self.assertEqual(json.loads(MANIFEST.read_text(encoding='utf-8')),manifest())
