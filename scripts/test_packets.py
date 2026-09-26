"""Portable packet parsing, state, timeline and failed-artifact contracts."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from portable_tools import required_binary
from packet_vectors import packet,bundle,cookie,basis,sequences,manifest,bits,pack,window
from packet_oracle import Decoder
from validate_packets import check_expected,coverage,validate_reference,exact,COUNT
from validate_portable import compare_pcm



class PacketTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();self.tmp=tempfile.TemporaryDirectory(prefix='packet-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def run_tool(self,*args):
        return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def make(self,seq,scene=False,origin=None,rate=48000,name='packets'):
        generated=[packet(c,rate,scene) for c in seq]
        bundle(self.root/name,[p for p,_ in generated],rate,scene,origin)
        return generated
    def parse(self,depth='packet',name='packets',out='out.jsonl',extra=()):
        return self.run_tool('parse-packets',self.root/name,'--depth',depth,'--packets',150,'--output',self.root/out,*extra)
    def decode(self,name='packets',out='pcm',extra=()):
        return self.run_tool('decode-sq',self.root/name,'--out',self.root/out,*extra)
    def test_frozen_matrix_and_generator_identity(self):
        document=manifest()
        self.assertEqual(len(document['sequences']),COUNT)
        frozen=json.loads((Path(__file__).resolve().parents[1]/'data/packet-vectors-v1.json').read_text())
        self.assertEqual(document,frozen)
        with self.assertRaises(AssertionError):validate_reference(dict(passed=True,mode='independent_math',counts={},errors=[]),{})
        with self.assertRaises(AssertionError):exact(dict(packet_state_sha256='changed'),dict(packet_state_sha256='original'))
    def test_complete_packets_preserve_every_bit_and_embedded_coordinates(self):
        seq=[dict(absent=True,frame_type=2),dict(basis((2,0x55)),scene_update=True),
             dict(basis((3,0),'right'),frame_type=2,preroll=basis((2,0x7f)),scene_update=True),dict(absent=True)]
        generated=self.make(seq,scene=True,origin=[0,0,0,0,0])
        result=self.parse();self.assertEqual(result.returncode,0,result.stderr)
        rows=[json.loads(s)['report'] for s in (self.root/'out.jsonl').read_text().splitlines()]
        for row,(_,truth) in zip(rows,generated):check_expected(row,truth)
        summary=json.loads(result.stdout);self.assertEqual(summary['cpe_absent_packets'],2)
        self.assertEqual(summary['embedded_preroll_packets'],1);self.assertEqual(summary['packet_complete_packets'],4)
        old=self.parse('bwe2',out='old.jsonl');self.assertEqual(old.returncode,2)
        for r in map(json.loads,(self.root/'old.jsonl').read_text().splitlines()):
            self.assertEqual(r['report']['status'],'partial');self.assertIsNone(r['report']['component_end_bit_offset'])
            self.assertNotIn('packet_complete',r['report'])
    def test_absence_preroll_and_all_window_pairs_match_independent_pcm(self):
        seq=[]
        for previous in range(4):
            for current in range(4):
                seq += [basis((previous,0)),dict(basis((current,0),'right'),frame_type=2,preroll=basis((previous,0))),dict(absent=True),{}]
        generated=self.make(seq,scene=True)
        result=self.decode();self.assertEqual(result.returncode,0,result.stderr)
        raw=(self.root/'pcm/pcm.f32le').read_bytes();actual=struct.unpack('<'+str(len(raw)//4)+'f',raw)
        oracle=Decoder();expected=[v for _,truth in generated for v in oracle.decode(truth)]
        self.assertTrue(compare_pcm(actual,expected)['passed'])
        meta=json.loads(result.stdout);self.assertEqual(meta['embedded_preroll_frames'],16)
        self.assertEqual(meta['saved_frames'],len(seq)*1024)
    def test_nonzero_origins_explicit_ranges_and_unselected_integrity(self):
        seq=[basis((i%4,0),'right' if i%2 else 'left') for i in range(10)]
        self.make(seq,scene=True)
        self.assertEqual(self.decode(out='full').returncode,0)
        full=(self.root/'full/pcm.f32le').read_bytes()
        window(self.root/'packets',self.root/'window',3,4,9)
        result=self.decode('window',out='selected',extra=('--start-frame',4*1024+7,'--frames',2100))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'selected/pcm.f32le').read_bytes(),full[(4*1024+7)*8:(4*1024+2107)*8])
        meta=json.loads(result.stdout);self.assertEqual(meta['warmup_packets'],1)
        self.assertEqual(meta['integrity_checked_packets'],6)
        self.assertEqual(self.decode('window','zero',('--frames',0)).returncode,1)
        self.assertEqual(self.decode('window','outside',('--start-frame',1)).returncode,1)
        # The short request must still reject corruption in an unused packet.
        path=self.root/'window/packets.bin';raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        self.assertEqual(self.decode('window','bad',('--frames',1)).returncode,1)
    def test_insufficient_dependencies_and_embedded_random_access(self):
        seq=[basis((0,0)),basis((2,0)),dict(basis((3,0),'right'),frame_type=2,preroll=basis((2,0))),{},{}]
        self.make(seq)
        self.assertEqual(self.decode(out='full').returncode,0)
        window(self.root/'packets',self.root/'window',2,2,5)
        result=self.decode('window','seek');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'seek/pcm.f32le').read_bytes(),(self.root/'full/pcm.f32le').read_bytes()[2*8192:])
        p=self.root/'window/packets.jsonl';rows=[json.loads(s) for s in p.read_text().splitlines()]
        rows[0]['dependency']['value']['preroll_packet_count']=1
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.assertEqual(self.decode('window','bad').returncode,1)
    def test_neutral_restatement_and_configuration_values_are_checked(self):
        self.make([dict(basis((0,0)),scene_update=True)],scene=True)
        self.assertEqual(self.parse().returncode,0)
        row=json.loads((self.root/'out.jsonl').read_text())['report']
        field=next(f for f in row['fields'] if f['name'].endswith('controls[0].parameter_0'))
        path=self.root/'packets/packets.bin';raw=bytearray(path.read_bytes());at=field['bit_offset']+field['bit_length']-1
        raw[at//8]|=1<<(7-at%8)
        bundle(self.root/'bad',[bytes(raw)],scene=True)
        result=self.parse(name='bad',out='bad.jsonl');self.assertEqual(result.returncode,2)
        r=json.loads((self.root/'bad.jsonl').read_text())['report'];coverage(r)
        self.assertIn('non-neutral',r['stop_reason']);self.assertFalse(r['packet_complete'])
        self.assertEqual(self.decode('bad','bad-pcm').returncode,1)
        self.assertTrue((self.root/'bad-pcm/.incomplete.json').exists())
        config=bytearray(cookie(48000,True));config[121//8]|=1<<(7-121%8)
        (self.root/'bad-cookie.bin').write_bytes(config)
        # Direct cookie mutation keeps syntax intact; unsupported level remains explicit.
        report=json.loads(self.run_tool('parse-cookie',self.root/'bad-cookie.bin').stdout)
        self.assertEqual(next(f['value'] for f in report['fields'] if f['name']=='global.level_id'),1)
        (self.root/'packets/cookie.bin').write_bytes(config)
        p=self.root/'packets/manifest.json';m=json.loads(p.read_text())
        m['file']['cookie']['value']=dict(bytes=len(config),sha256=hashlib.sha256(config).hexdigest());p.write_text(json.dumps(m))
        result=self.decode(out='bad-config');self.assertEqual(result.returncode,1)
        error=json.loads(result.stderr)['error'];self.assertEqual(error['operation'],'SQ decoder')
        self.assertIn('global.level_id=1 at cookie bit 118',error['message'])
    def test_truncation_limits_markers_and_output_protection(self):
        seq=[basis((0,0)),dict(basis((0,0)),frame_type=2,preroll=basis((0,0)))]
        generated=self.make(seq)
        self.assertEqual(self.decode().returncode,0)
        original=(self.root/'pcm/pcm.f32le').read_bytes()
        self.assertEqual(self.decode().returncode,1);self.assertEqual((self.root/'pcm/pcm.f32le').read_bytes(),original)
        bad=generated[1][0][:-1]
        bundle(self.root/'bad',[generated[0][0],bad])
        result=self.decode('bad','failed');self.assertEqual(result.returncode,1)
        error=json.loads(result.stderr)['error'];self.assertEqual(error['packet_index'],1);self.assertIsInstance(error['bit_offset'],int)
        self.assertTrue((self.root/'failed/.incomplete.json').exists())
        self.assertFalse((self.root/'failed/pcm.json').exists())
        bundle(self.root/'large',[generated[0][0]]*140)
        self.assertEqual(self.decode('large','quota',('--max-output-mib',1)).returncode,1)
        self.assertTrue((self.root/'quota/.incomplete.json').exists())
        too_long=pack('10'+'0'+'01'+bits(4097,16))+bytes(4098)
        bundle(self.root/'oversize',[too_long])
        result=self.parse(name='oversize',out='oversize.jsonl');self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads((self.root/'oversize.jsonl').read_text())['error']['kind'],'preroll-size')


if __name__=='__main__':unittest.main()
