"""Portable CAF CLI failure contracts and container boundary controls."""
import json,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from caf_vectors import VARIANTS,encode,chunk,varint
from packet_vectors import cookie,packet

class CafTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();t=tempfile.TemporaryDirectory(prefix='caf-test-');self.addCleanup(t.cleanup);self.root=Path(t.name);self.n=0
    def run_caf(self,raw,*args):
        self.n+=1;src=self.root/f'{self.n}.unrelated';src.write_bytes(raw);out=self.root/f'out{self.n}'
        p=subprocess.run([str(self.binary),'decode-sq',str(src),'--out',str(out),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
        return p,out,src
    def raw(self,**kw):return encode(cookie(48000),[packet({})[0]]*4,**kw)
    def test_all_chunk_orders_optional_layout_and_terminal_data(self):
        for i in range(len(VARIANTS)):
            raw,truth=self.raw(variant=i,edit=i)
            p,out,_=self.run_caf(raw);self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads(p.stdout)
            for k,v in truth.items():self.assertEqual(r['input'][k],v,k)
            self.assertEqual((out/'pcm.f32le').read_bytes(),bytes(4*8192))
    def test_every_truncation_and_duplicate_is_rejected(self):
        raw,truth=self.raw(variant=24)
        for end in range(len(raw)):
            p,_,_=self.run_caf(raw[:end]);self.assertEqual(p.returncode,1,str(end));self.assertIn('byte_offset',json.loads(p.stderr)['error'])
        for tag,c in truth['chunks'].items():
            p,_,_=self.run_caf(raw+raw[c['offset']-12:c['offset']+c['bytes']]);self.assertEqual(p.returncode,1,tag)
    def test_description_cookie_and_layout_conflicts(self):
        raw,truth=self.raw()
        for offset,payload in [(20,struct.pack('>d',float('nan'))),(20,struct.pack('>d',44100)),(28,b'flac'),(32,struct.pack('>I',1)),(36,struct.pack('>I',20)),(40,struct.pack('>I',0)),(44,struct.pack('>I',8)),(48,struct.pack('>I',16)),(truth['chunks']['chan']['offset'],struct.pack('>I',(102<<16)|2))]:
            bad=bytearray(raw);bad[offset:offset+len(payload)]=payload;p,_,_=self.run_caf(bad);self.assertEqual(p.returncode,1)
    def test_lengths_counts_and_unknown_chunk_headers(self):
        raw,truth=self.raw();pakt=truth['chunks']['pakt']['offset'];data=truth['chunks']['data']['offset']
        for offset,payload in [(12,struct.pack('>q',-2)),(12,struct.pack('>q',2**63-1)),(pakt,struct.pack('>q',0)),(pakt,struct.pack('>q',2**63-1)),(pakt+8,struct.pack('>q',-1)),(pakt+16,struct.pack('>i',-1)),(pakt+20,struct.pack('>i',1)),(pakt+24,b'\0'),(pakt+24,b'\xff'*4),(data-8,struct.pack('>q',3))]:
            bad=bytearray(raw);bad[offset:offset+len(payload)]=payload;p,_,_=self.run_caf(bad);self.assertEqual(p.returncode,1)
        for extra in [b'x',chunk(b'free',b'x',99),chunk(b'free',b'x',-1)]:self.assertEqual(self.run_caf(raw+extra)[0].returncode,1)
    def test_varint_byte_boundaries_without_decoding_unused_packets(self):
        # Only the first valid packet is decoded. Large opaque later packets are
        # still structurally/hash checked, never claimed to have valid APAC syntax.
        sizes=(127,128,16383,16384,2097151,2097152,16*1024*1024)
        for size in sizes:
            raw,_=encode(cookie(48000),[packet({})[0],bytes(size)])
            p,_,_=self.run_caf(raw,'--frames',1);self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads(p.stdout);self.assertEqual(r['packets'],1);self.assertEqual(r['integrity_checked_packets'],2)
        raw,_=encode(cookie(48000),[packet({})[0],bytes(16*1024*1024+1)])
        self.assertEqual(self.run_caf(raw,'--frames',1)[0].returncode,1)
    def test_warmup_beyond_bundle_dependency_search_limit(self):
        raw,_=encode(cookie(48000),[packet({})[0]]*4100)
        p,out,_=self.run_caf(raw,'--start-frame',4097*1024+7,'--frames',13)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)['warmup_packets'],4097);self.assertEqual((out/'pcm.f32le').read_bytes(),bytes(13*8))
    def test_decode_failure_marker_and_no_overwrite(self):
        raw,_=encode(cookie(48000),[packet({})[0],b'\xff'])
        p,out,src=self.run_caf(raw);self.assertEqual(p.returncode,1);self.assertEqual(json.loads(p.stderr)['error']['packet_index'],1)
        self.assertTrue((out/'.incomplete.json').is_file());before=(out/'pcm.f32le').read_bytes()
        p=subprocess.run([str(self.binary),'decode-sq',str(src),'--out',str(out)],capture_output=True)
        self.assertEqual(p.returncode,1);self.assertEqual((out/'pcm.f32le').read_bytes(),before)
    def test_output_limit_and_empty_eof(self):
        raw,_=encode(cookie(48000),[packet({})[0]]*200)
        p,out,_=self.run_caf(raw,'--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file())
        p,out,_=self.run_caf(raw,'--start-frame',200*1024,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').stat().st_size,0)
        for args in [('--frames',0),('--start-frame',200*1024+1),('--start-frame',2**64-1)]:self.assertEqual(self.run_caf(raw,*args)[0].returncode,1)
    def test_empty_audio_has_no_synthetic_frames(self):
        raw,_=encode(cookie(48000),[])
        p,out,_=self.run_caf(raw);self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads(p.stdout);self.assertEqual(r['packets'],0);self.assertEqual(r['saved_frames'],0)
        self.assertEqual((out/'pcm.f32le').read_bytes(),b'')
    def test_frozen_vectors_and_independent_expected_input_reject_tampering(self):
        from caf_vectors import manifest
        from validate_caf import MANIFEST,check_input
        self.assertEqual(json.loads(MANIFEST.read_text(encoding='utf-8')),manifest())
        _,truth=self.raw()
        for key in ('packet_count','metadata_sha256','packets_sha256','audio_sha256','layout_source','packet_table','consistency_verified'):
            bad=dict(truth);bad[key]=None
            with self.assertRaises(AssertionError):check_input(bad,truth)
