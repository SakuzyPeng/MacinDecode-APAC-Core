"""TNS syntax, numerical independence, staged reports and failure artifacts."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from generate_tns_math import DESTINATION, document
from portable_tools import required_binary
from spectrum_vectors import bundle, bits
from tns_vectors import packet, filter_spec, cases, sequences
from tns_oracle import reflection
from validate_tns import COUNTS, check_expected, exact, validate_reference
from validate_cac import pcm_stop


class TnsTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary()
        self.tmp=tempfile.TemporaryDirectory(prefix='tns-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def parse(self, data, *extra):
        bundle(self.root/'packets', data)
        return subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth','tns',
                               '--output',str(self.root/'tns.jsonl'),*extra],capture_output=True,text=True,encoding='utf-8')

    def test_constants_signs_and_required_matrix_are_fixed(self):
        self.assertEqual(json.loads(DESTINATION.read_text()),document())
        self.assertEqual(2*len(list(cases())),COUNTS['spectra'])
        self.assertEqual(2*len(list(sequences())),COUNTS['pcm'])
        for r in (3,4):
            for q in range(-(1<<(r-1)),1<<(r-1)):
                self.assertEqual(reflection(q,r)>0,q>0)
                self.assertEqual(reflection(q,r)<0,q<0)
                self.assertLess(abs(reflection(q,r)),1)

    def test_shared_and_independent_stages_preserve_raw_data_and_bwe_boundary(self):
        gen=[packet(dict(independent=independent,block=2,grouping=grouping,gain=100,cac_gain=26,
                         left={0:(1,[1,0,0,0],100)},right={0:(1,[0,1,0,0],100)},
                         left_tns=filter_spec([-1,0,1],14,window=7),right_tns=filter_spec([1],14,window=0)))
             for independent,grouping in [(False,0),(False,0x55),(True,0x7f)]]
        result=self.parse([p for p,t in gen])
        self.assertEqual(result.returncode,2,result.stderr)
        summary=json.loads(result.stdout)
        self.assertEqual(summary['tns_complete_packets'],3)
        self.assertEqual(summary['whole_frame_complete_packets'],0)
        rows=[json.loads(s)['report'] for s in (self.root/'tns.jsonl').read_text().splitlines()]
        for row,(_,truth) in zip(rows,gen):
            check_expected(row,truth)
            self.assertIsNone(row['component_end_bit_offset'])
            self.assertEqual(row['unknown_ranges'][-1]['bit_offset'],truth['tns_end_bit_offset'])
        for key,value in [('tns_complete',False),('stop_bit_offset',0),('tns_stage','wrong')]:
            bad=copy.deepcopy(rows[0]);bad[key]=value
            with self.assertRaises(AssertionError):check_expected(bad,gen[0][1])
        bad=copy.deepcopy(rows[0]);bad['channels_after_tns'][0]['scaled'][0]=1
        with self.assertRaises(AssertionError):check_expected(bad,gen[0][1])

    def test_zero_order_cursor_clipping_and_missing_cpe(self):
        gen=[packet(dict(max_sfb=m,left_tns={0:dict(filters=[dict(length=63,q=[]),dict(length=1,q=[1])])},right_tns={})) for m in (0,49)]
        result=self.parse([p for p,t in gen]+[b'\x40',b'\x70'])
        self.assertEqual(result.returncode,2,result.stderr)
        rows=[json.loads(s)['report'] for s in (self.root/'tns.jsonl').read_text().splitlines()]
        for row,(_,truth) in zip(rows,gen):
            check_expected(row,truth)
            f=row['tns'][0]['windows'][0]['filters']
            self.assertIsNone(f[0]['direction']);self.assertEqual(f[1]['top_band'],0)
            self.assertEqual(f[1]['start_line'],f[1]['end_line'])
        for row in rows[2:]:
            self.assertFalse(row['tns_complete']);self.assertEqual(row['tns'],[]);self.assertEqual(row['channels_after_tns'],[])

    def test_bad_length_order_and_truncation_keep_packet_positions_and_marker(self):
        valid,truth=packet(dict(left_tns=filter_spec([1])))
        bad_length,_=packet(dict(left_tns=filter_spec([],length=0)))
        bad_order,_=packet(dict(left_tns=filter_spec([1]*13)))
        result=self.parse([valid,bad_length,bad_order,valid[:-2]])
        self.assertEqual(result.returncode,1,result.stderr)
        rows=[json.loads(s) for s in (self.root/'tns.jsonl').read_text().splitlines()]
        for row,kind in zip(rows[1:],('tns-length','tns-order','truncated')):
            self.assertEqual(row['status'],'error')
            self.assertEqual(row['error']['kind'],kind)
            self.assertIsInstance(row['error']['bit_offset'],int)
        self.assertTrue((self.root/'tns.jsonl.incomplete').exists())

    def test_decoder_rejects_bwe_after_tns_and_keeps_failed_artifact(self):
        data,truth=packet(dict(left_tns=filter_spec([1]),bwe_flags='10'))
        bundle(self.root/'packets',[data])
        result=subprocess.run([str(self.binary),'decode-sq',str(self.root/'packets'),'--out',str(self.root/'pcm')],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,1)
        error=json.loads(result.stdout or result.stderr)['error']
        self.assertEqual(error['bit_offset'],truth['tns_end_bit_offset'])
        self.assertEqual(error['packet_index'],0)
        self.assertEqual(pcm_stop(dict(error=error)),'left BWE2')
        self.assertTrue((self.root/'pcm/.incomplete.json').exists())
        self.assertFalse((self.root/'pcm/pcm.json').exists())

    def test_quota_and_overwrite_protect_staged_reports(self):
        data,_=packet(dict(max_sfb=49,left_tns=filter_spec([1]*12)))
        result=self.parse([data]*200,'--packets','200','--max-output-mib','1')
        self.assertEqual(result.returncode,1)
        output=self.root/'tns.jsonl';original=output.read_bytes()
        self.assertTrue(output.with_name(output.name+'.incomplete').exists())
        result=subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth','tns','--output',str(output)],capture_output=True)
        self.assertEqual(result.returncode,1);self.assertEqual(output.read_bytes(),original)

    def test_exact_acceptance_rejects_changed_outputs_or_missing_cases(self):
        item=dict(cac_sha256='cac',coupled_sha256='coupled',tns_sha256='tns',filtered_sha256='filtered')
        exact(item,item)
        for key in item:
            with self.assertRaises(AssertionError):exact(dict(item,**{key:'changed'}),item)
        with self.assertRaises(AssertionError):validate_reference(dict(passed=True,mode='independent_math',counts={},errors=[]),{})

    def test_pcm_quota_and_overwrite_are_preserved_with_tns(self):
        data,_=packet(dict(left={0:(1,[1,0,0,0],100)},gain=100,left_tns=filter_spec([1,-1])))
        bundle(self.root/'packets',[data])
        args=[str(self.binary),'decode-sq',str(self.root/'packets'),'--out',str(self.root/'pcm')]
        result=subprocess.run(args,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        original=(self.root/'pcm/pcm.f32le').read_bytes()
        result=subprocess.run(args,capture_output=True)
        self.assertEqual(result.returncode,1)
        self.assertEqual((self.root/'pcm/pcm.f32le').read_bytes(),original)
        bundle(self.root/'large',[data]*130)
        result=subprocess.run([str(self.binary),'decode-sq',str(self.root/'large'),'--out',str(self.root/'quota'),
                               '--max-output-mib','1'],capture_output=True)
        self.assertEqual(result.returncode,1)
        self.assertTrue((self.root/'quota/.incomplete.json').exists())
        self.assertFalse((self.root/'quota/pcm.json').exists())


if __name__=='__main__':unittest.main()
