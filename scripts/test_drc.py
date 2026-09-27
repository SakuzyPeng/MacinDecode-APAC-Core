"""Portable DRC integer, boundary, state and failure contracts."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from portable_tools import required_binary
from drc_vectors import GAIN_CODES,packet,payload,bundle,bits,pack
from packet_vectors import basis
from validate_packets import coverage


from validate_drc import check_expected as check


class DrcTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();t=tempfile.TemporaryDirectory(prefix='drc-test-');self.addCleanup(t.cleanup);self.root=Path(t.name)
    def run_tool(self,*args):
        return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def inspect(self,cases,rate=48000,scene=False,name='batch'):
        entries=[packet(c,rate,scene) for c in cases]
        root=self.root/name;bundle(root,[p for p,_ in entries],rate,scene)
        result=self.run_tool('parse-packets',root,'--depth','drc','--packets',len(entries),'--output',root/'report.jsonl')
        rows=[json.loads(l) for l in (root/'report.jsonl').read_text().splitlines()]
        return result,rows,[t for _,t in entries],root
    def test_all_initial_gains_and_delta_words(self):
        cases=[dict(absent=True,drc=dict(gains=[(-1 if sign else 1)*v],negative_zero=bool(sign)))
               for sign in (0,1) for v in range(256)]
        cases += [dict(absent=True,drc=dict(mode=1,gains=[10,10+d],times=[1])) for _,d in GAIN_CODES]
        result,rows,truth,_=self.inspect(cases)
        self.assertEqual(result.returncode,2,result.stderr)
        for r,t in zip(rows,truth):check(r['report'],t)
    def test_temporal_words_reservoir_and_nodes(self):
        cases=[dict(absent=True,drc=dict(mode=1,frame_end=False,gains=[0],times=[i])) for i in range(1,33)]
        cases += [dict(absent=True,drc=dict(mode=1,gains=list(range(32)),frame_end=False,times=[1]*32)),
                  dict(absent=True,drc=dict(mode=1,gains=[4,3,2],frame_end=False,times=[1,16,2]))]
        result,rows,truth,_=self.inspect(cases)
        self.assertEqual(result.returncode,2,result.stderr)
        for r,t in zip(rows,truth):check(r['report'],t)
    def test_restatement_metadata_preroll_and_every_window(self):
        for rate in (44100,48000):
            cases=[]
            for i,(block,mask) in enumerate(((0,0),(1,0),(2,0),(2,0x55),(2,0x7f),(3,0))):
                core=basis((block,mask),independent=bool(i%2))
                cases.append(dict(core,drc=dict(header=True,extension=True)))
                cases.append(dict(absent=True,scene_update=True,drc=dict(header=True,metadata_only=True,gains=[-23])))
                cases.append(dict(core,frame_type=2,preroll=dict(core,drc=dict(header=True,gains=[12])),drc=dict(gains=[8])))
            result,rows,truth,_=self.inspect(cases,rate,True,name=str(rate))
            self.assertEqual(result.returncode,2,result.stderr)
            for r,t in zip(rows,truth):check(r['report'],t)
            self.assertFalse(rows[0]['report']['drc_history_sufficient'])
            self.assertTrue(all(r['report']['drc_history_sufficient'] for r in rows[1:]))
    def test_malformed_times_and_unsupported_header_fail_without_history_commit(self):
        cases=[dict(absent=True,drc=dict(mode=1,frame_end=False,gains=[0],times=[33])),
               dict(absent=True,drc=dict(mode=1,gains=[0,0],times=[16])),
               dict(absent=True,drc=dict(mode=1,gains=[0,0,0],times=[17,2])),dict(absent=True)]
        result,rows,_,_=self.inspect(cases)
        self.assertEqual(result.returncode,1,result.stderr)
        for r in rows[:3]:self.assertEqual(r['error']['kind'],'drc-node-time')
        self.assertFalse(rows[3]['report']['drc_history_sufficient'])
    def test_byte_truncation_output_protection_and_limit(self):
        raw,truth=packet(dict(absent=True,drc=dict(header=True,mode=1,gains=[0,-14],times=[3])))
        values=[raw[:i] for i in range(1,len(raw)-1)]
        directory=self.root/'packets';bundle(directory,values)
        result=self.run_tool('parse-packets',directory,'--depth','drc','--output',self.root/'broken.jsonl')
        self.assertEqual(result.returncode,1,result.stderr)
        rows=[json.loads(l) for l in (self.root/'broken.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['status']=='error' for r in rows))
        self.assertTrue((self.root/'broken.jsonl.incomplete').exists())
        before=(self.root/'broken.jsonl').read_bytes()
        self.assertEqual(self.run_tool('parse-packets',directory,'--depth','drc','--output',self.root/'broken.jsonl').returncode,1)
        self.assertEqual((self.root/'broken.jsonl').read_bytes(),before)
        limited=self.root/'many';bundle(limited,[raw]*512)
        result=self.run_tool('parse-packets',limited,'--depth','drc','--packets',512,'--output',self.root/'limited.jsonl','--max-output-mib',1)
        self.assertEqual(result.returncode,1)
        self.assertTrue((self.root/'limited.jsonl.incomplete').exists())
    def test_drc_off_pcm_metadata_and_payload_failures(self):
        _,_,_,root=self.inspect([dict(absent=True)])
        result=self.run_tool('decode-sq',root,'--out',root/'pcm')
        self.assertEqual(result.returncode,0,result.stderr)
        r=json.loads(result.stdout)
        self.assertEqual(r['drc_processing'],'off');self.assertEqual(r['loudness_normalization'],'off')
        self.assertEqual(r['drc_payload_frames'],1);self.assertTrue(r['drc_payloads_complete'])
        self.assertEqual((root/'pcm/pcm.f32le').read_bytes(),bytes(8192))
        raw=pack('01000000'+'0'+'1'+'0'*256+'1'+'0'*16)
        bad=self.root/'bad';bundle(bad,[raw])
        result=self.run_tool('decode-sq',bad,'--out',bad/'pcm')
        self.assertEqual(result.returncode,1)
        self.assertTrue((bad/'pcm/.incomplete.json').exists())
        self.assertFalse((bad/'pcm/pcm.json').exists())
    def test_unqualified_instruction_effect_is_rejected_with_cookie_position(self):
        raw,_=packet(dict(absent=True));root=self.root/'effect'
        bundle(root,[raw],rich=True,effect=0)
        result=self.run_tool('decode-sq',root,'--out',root/'pcm')
        self.assertEqual(result.returncode,1)
        error=json.loads(result.stderr)['error']
        self.assertEqual(error['operation'],'SQ decoder')
        self.assertRegex(error['message'],r'instructions\[0\].effect=0 at cookie bit [0-9]+')


if __name__=='__main__':unittest.main()
