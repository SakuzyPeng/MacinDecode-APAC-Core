"""Extended layout isolation, container access and exact failure contracts."""
import copy,json,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from channel_vectors import layout,cookie,packet,bundle,excitation
from layout_vectors import PROFILE,sequences
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4
from spectrum_vectors import bits,pack
from validate_channels import check

class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();t=tempfile.TemporaryDirectory(prefix='apac-layout-test-');self.addCleanup(t.cleanup);self.root=Path(t.name);self.counter=0
    def command(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def next(self):self.counter+=1;return self.root/str(self.counter)
    def source(self,n,payloads,kind='caf',rate=48000,**opts):
        p=self.next();raw=(caf if kind=='caf' else mp4)(cookie(n,rate,**opts),payloads,rate=rate,channels=n)[0];p.write_bytes(raw);return p
    def decode(self,source,mode=None,**opts):
        out=self.next();args=[] if mode is None else ['--access',mode]
        for k,v in opts.items():args+=['--'+k.replace('_','-'),v]
        return self.command('decode-sq',source,'--out',out,*args),out
    def test_all_channels_are_isolated_and_new_profile_is_explicit(self):
        for n in (12,24):
            seq=[]
            for ch in range(n):seq += [excitation(n,ch),dict(elements=[None]*len(layout(n)[2]))]
            generated=[packet(c,n) for c in seq];root=self.next();bundle(root,[p for p,t in generated],n)
            r=self.command('parse-packets',root,'--depth','channels','--packets',len(seq),'--output',root/'parsed');self.assertEqual(r.returncode,0,r.stderr)
            rows=[json.loads(l)['report'] for l in (root/'parsed').read_text().splitlines()]
            for row,(_,truth) in zip(rows,generated):check(row,truth,n)
            p,out=self.decode(root);self.assertEqual(p.returncode,0,p.stderr);report=json.loads(p.stdout)
            self.assertEqual(report['channel_layout_profile'],PROFILE);self.assertEqual(report['pcm']['decoder_settings']['implementation']['value']['channel_layout_profile'],PROFILE)
            raw=(out/'pcm.f32le').read_bytes();v=struct.unpack('<'+str(len(raw)//4)+'f',raw)
            for ch in range(n):
                window=v[ch*2048*n:(ch+1)*2048*n];self.assertTrue(any(window[ch::n]))
                for other in range(n):
                    if ch!=other:self.assertFalse(any(window[other::n]))
    def test_tools_and_metadata_ranges_match_three_inputs(self):
        for n in (12,24):
            for rate in (48000,44100):
                options=dict(scene=True,drc=True,rich=True);seq=next(seq for kind,opts,seq in sequences(n) if kind=='joint_tools')
                raw=[packet(c,n,rate,**options)[0] for c in seq];root=self.next();bundle(root,raw,n,rate,**options)
                p,out=self.decode(root);self.assertEqual(p.returncode,0,p.stderr);full=(out/'pcm.f32le').read_bytes()
                for kind in ('caf','mp4'):
                    src=self.source(n,raw,kind,rate,**options)
                    for start,frames in ((0,31),(3*1024+17,1001),(len(raw)*1024-9,99),(len(raw)*1024,1)):
                        outputs=[];states=[]
                        for mode in ('sequential','fast'):
                            r,d=self.decode(src,mode,start_frame=start,frames=frames);self.assertEqual(r.returncode,0,r.stderr);meta=json.loads(r.stdout)
                            outputs.append((d/'pcm.f32le').read_bytes());states.append(tuple(meta['access'][k] for k in ('metadata_before_output_sha256','metadata_after_processing_sha256')))
                            if mode=='fast':self.assertLessEqual(meta['warmup_packets'],1)
                        self.assertEqual(*outputs);self.assertEqual(*states);self.assertEqual(outputs[0],full[start*n*4:(start+frames)*n*4])
    def test_truncations_late_errors_and_limits_leave_failure_markers(self):
        fixtures=json.loads((Path(__file__).resolve().parents[1]/'data/layout-state-fixtures-v1.json').read_text())['fixtures']
        for f in fixtures:
            n=f['channels'];options=dict(drc=True,rich=True);good=bytes.fromhex(f['first']);bad=[bytes.fromhex(f[k]) for k in ('last_element_error','late_drc_error')]
            bad += [good[:i] for i in range(1,len(good))]
            for payload in bad:
                src=self.source(n,[good,payload,good,good],**options);errors=[]
                for mode in ('sequential','fast'):
                    r,out=self.decode(src,mode,start_frame=3072,frames=1);self.assertEqual(r.returncode,1,r.stdout);errors.append(json.loads(r.stderr)['error'])
                    self.assertTrue((out/'.incomplete.json').exists());self.assertFalse((out/'decode-sq.json').exists())
                self.assertEqual(*errors);self.assertEqual(errors[0]['packet_index'],1)
            src=self.source(n,[good]*30,**options);r,out=self.decode(src,'fast',max_output_mib=1);self.assertEqual(r.returncode,1);self.assertTrue((out/'.incomplete.json').exists())
            r,out=self.decode(src,'fast',frames=1);self.assertEqual(r.returncode,0,r.stderr);before=(out/'pcm.f32le').read_bytes()
            r=self.command('decode-sq',src,'--out',out);self.assertEqual(r.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
    def test_fixed_capacity_nested_preroll_and_layout_qualification(self):
        for n,capacity in ((12,24576),(24,49152)):
            for payload in (pack('10001'+bits(capacity+1,16)),pack('10001'+bits(0,16)),pack('10001'+bits(1,16)+'000'+'10000000'+'00000000')):
                root=self.next();bundle(root,[payload],n);r=self.command('parse-packets',root,'--depth','channels','--output',root/'parsed');self.assertEqual(r.returncode,1,r.stdout)
                error=json.loads((root/'parsed').read_text())['error'];self.assertIn(error['kind'],('preroll-size','nested-preroll'))
            root=self.next();bundle(root,[packet({},n)[0]],n);m=json.loads((root/'manifest.json').read_text());m['file']['layout']['value']['tag']^=1<<16;(root/'manifest.json').write_text(json.dumps(m))
            r,out=self.decode(root);self.assertEqual(r.returncode,1);self.assertFalse(out.exists());self.assertIn('layout',r.stderr)
            cfg=bytearray(cookie(n));position=118
            for bit in range(4):cfg[(position+bit)//8]&=~(1<<(7-(position+bit)%8))
            (root/'cookie.bin').write_bytes(cfg);m['file']['cookie']['value']['sha256']=__import__('hashlib').sha256(cfg).hexdigest();(root/'manifest.json').write_text(json.dumps(m))
            r,out=self.decode(root);self.assertEqual(r.returncode,1);self.assertFalse(out.exists());self.assertIn('level_id',r.stderr)
    def test_long_prefix_empty_output_and_unparsed_tail(self):
        for n in (12,24):
            raw=packet({},n)[0];src=self.source(n,[raw]*4101)
            r,out=self.decode(src,'fast',start_frame=4098*1024+1,frames=1);self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout);self.assertEqual(v['access']['prefix_scanned_packets'],4097)
            r,out=self.decode(src,'fast',start_frame=4101*1024,frames=1);self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout);self.assertEqual(v['access']['synthesized_packets'],0)
            src=self.source(n,[raw]*3+[b'\xff'],'mp4');r,_=self.decode(src,'fast',frames=1);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['integrity_checked_packets'],4)
            r,_=self.decode(src,'fast',start_frame=3072,frames=1);self.assertEqual(r.returncode,1)
    def test_old_report_without_optional_profile_stays_compatible(self):
        for n in (1,2,6,8):
            src=self.source(n,[packet({},n)[0]]);r,_=self.decode(src);self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout)
            self.assertNotIn('channel_layout_profile',v);self.assertNotIn('channel_layout_profile',v['pcm']['decoder_settings']['implementation']['value'])

if __name__=='__main__':unittest.main()
