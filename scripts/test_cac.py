"""CAC syntax, numerical-model and CLI completion/failed-artifact contracts."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cac_vectors import cases, encode_runs, frame, packet, sequences
from generate_cac_math import DESTINATION, document
from portable_tools import required_binary
from spectrum_vectors import bundle
from validate_cac import check_expected, check_native, exact, pcm_stop, validate_reference, COUNTS
from verify_cac_codebooks import extract


class CacTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary()
        self.tmp=tempfile.TemporaryDirectory(prefix='cac-cli-test-')
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def parse(self,payloads,depth='cac',*extra):
        bundle(self.root/'packets',payloads)
        return subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth',depth,
                               '--output',str(self.root/'result.jsonl'),*extra],capture_output=True,text=True,encoding='utf-8')

    def test_constants_matrix_sizes_and_cross_group_runs_are_fixed(self):
        self.assertEqual(json.loads(DESTINATION.read_text()),document())
        self.assertEqual((len(list(cases()))*2,len(list(sequences()))*2),(COUNTS['spectra'],COUNTS['pcm']))
        self.assertEqual(encode_runs([9]*112),[(9,42),(9,42),(9,43)])
        path=self.root/'unverified-component';path.write_bytes(b'wrong version')
        with self.assertRaises(ValueError):extract(path)

    def test_cac_completion_preserves_raw_channels_and_whole_frame_status(self):
        payload,truth=frame(dict(block=2,grouping=0x55,left={0:(11,[8191,-17],160)},cac_gain=26))
        result=self.parse([payload])
        self.assertEqual(result.returncode,2,result.stderr)
        summary=json.loads(result.stdout)
        self.assertEqual(summary['cac_complete_packets'],1)
        self.assertEqual(summary['right_spectrum_packets'],1)
        self.assertEqual(summary['whole_frame_complete_packets'],0)
        row=json.loads((self.root/'result.jsonl').read_text(encoding='utf-8'))
        check_expected(row['report'],truth)
        for key,value in [('cac_complete',False),('shared_ics',False),('stop_bit_offset',0)]:
            bad=copy.deepcopy(row['report']);bad[key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):check_expected(bad,truth)
        bad=copy.deepcopy(row['report']);bad['channels_after_cac'][0]['scaled'][0]=0
        with self.assertRaises(AssertionError):check_expected(bad,truth)
        r=row['report']
        trace=dict(errors=[],process_exit_code=0,packet_calls=1,
                   spectra=[dict(sequence=0,packet_sha256=r['packet_sha256'],**{k:c[k] for k in
                       ('channel_index','stream_bit_offset','end_bit_offset','scaled')}) for c in r['channels']],
                   cac=[dict(sequence=0,packet_sha256=r['packet_sha256'],start_bit_offset=r['cac']['start_bit_offset'],
                       end_bit_offset=r['cac']['end_bit_offset'],tns_start_bit_offset=r['stop_bit_offset'],
                       runs=[{k:run[k] for k in ('gain_index','repeat_code')} for run in r['cac']['runs']],
                       before=[c['scaled'] for c in r['channels']],after=[c['scaled'] for c in r['channels_after_cac']])])
        self.assertTrue(check_native([row],trace)[0]['numeric_passed'])
        wrong=copy.deepcopy(trace);wrong['cac'][0]['runs'][0]['gain_index']=0
        with self.assertRaises(AssertionError):check_native([row],wrong)
        wrong=copy.deepcopy(trace);wrong['cac'][0]['tns_start_bit_offset']+=1
        with self.assertRaises(AssertionError):check_native([row],wrong)
        wrong=copy.deepcopy(trace);wrong['cac'][0]['after'][0]=[0.]*1024
        self.assertFalse(check_native([row],wrong)[0]['numeric_passed'])

    def test_zero_max_sfb_independent_and_absent_have_distinct_semantics(self):
        empty,truth=frame({});independent,other=packet(dict(independent=True))
        result=self.parse([empty,independent,b'\x40',b'\x70'])
        self.assertEqual(result.returncode,2,result.stderr)
        rows=[json.loads(line)['report'] for line in (self.root/'result.jsonl').read_text().splitlines()]
        check_expected(rows[0],truth);check_expected(rows[1],other)
        self.assertEqual(rows[0]['cac']['runs'],[])
        self.assertEqual(rows[0]['cac']['start_bit_offset'],rows[0]['cac']['end_bit_offset'])
        self.assertIsNone(rows[1]['cac'])
        for r in rows[2:]:self.assertFalse(r['cac_complete']);self.assertEqual(r['channels_after_cac'],[])

    def test_terminal_44_is_valid_but_45_is_an_error_with_a_marker(self):
        good,_=frame(dict(max_sfb=44,cac_gain=9,raw_runs=[(9,43)]))
        bad,_=frame(dict(max_sfb=45,cac_gain=9,raw_runs=[(9,43)]))
        result=self.parse([good,bad,good])
        self.assertEqual(result.returncode,1,result.stderr)
        rows=[json.loads(line) for line in (self.root/'result.jsonl').read_text().splitlines()]
        self.assertEqual(rows[1]['packet_index'],1)
        self.assertEqual(rows[1]['error']['kind'],'cac-terminal')
        self.assertIsInstance(rows[1]['error']['bit_offset'],int)
        self.assertTrue(rows[2]['report']['cac_complete'])
        self.assertTrue((self.root/'result.jsonl.incomplete').exists())

    def test_legacy_depth_still_stops_before_shared_right_stream(self):
        payload,_=frame(dict(left={0:(1,[1,0,0,0],160)},cac_gain=9))
        result=self.parse([payload],'spectrum')
        self.assertEqual(result.returncode,2,result.stderr)
        report=json.loads((self.root/'result.jsonl').read_text())['report']
        self.assertEqual(report['stop_reason'],'shared_ics_cac_deferred')
        self.assertEqual(len(report['channels']),1)
        self.assertNotIn('cac',report)

    def test_quota_and_overwrite_keep_failed_artifacts_honest(self):
        payload,_=frame(dict(left={0:(1,[1,0,0,0],160)},cac_gain=9))
        result=self.parse([payload]*200,'cac','--packets','200','--max-output-mib','1')
        self.assertEqual(result.returncode,1)
        output=self.root/'result.jsonl';original=output.read_bytes()
        self.assertTrue((self.root/'result.jsonl.incomplete').exists())
        result=subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth','cac','--output',str(output)],capture_output=True)
        self.assertEqual(result.returncode,1);self.assertEqual(output.read_bytes(),original)

    def test_exact_gate_checks_cac_output_and_rejects_missing_cases(self):
        item=dict(cac_sha256='parameters',coupled_sha256='spectrum')
        exact(item,item)
        for key in item:
            with self.assertRaises(AssertionError):exact(dict(item,**{key:'different'}),item)
        with self.assertRaises(AssertionError):validate_reference(dict(passed=True,mode='independent_math',counts={},errors=[]),{})
        with self.assertRaises(AssertionError):pcm_stop(dict(error=dict(operation='filesystem',message='broken input')))
        with self.assertRaises(AssertionError):pcm_stop(dict(error=dict(operation='SQ decoder',message='unrecognized decoder failure')))
        self.assertEqual(pcm_stop(dict(error=dict(operation='SQ decoder',message='unsupported nonzero left TNS',packet_index=3,bit_offset=91))), 'left TNS')


if __name__=='__main__':unittest.main()
