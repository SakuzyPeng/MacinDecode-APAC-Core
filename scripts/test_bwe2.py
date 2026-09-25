"""BWE2 completion, parameter reuse, mathematical and failed-artifact contracts."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from portable_tools import required_binary
from spectrum_vectors import bundle
from bwe2_vectors import packet, source_case, cases, sequences
from generate_bwe2_math import DESTINATION,document
from verify_bwe2_format import extract
from validate_bwe2 import check_expected, check_native, native_conditioned_lsf, exact, validate_reference, COUNTS
from bwe2_oracle import conditioned_transform


class Bwe2Tests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();self.tmp=tempfile.TemporaryDirectory(prefix='bwe2-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def parse(self,payloads,depth='bwe2',*extra):
        bundle(self.root/'packets',payloads)
        return subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth',depth,
                               '--output',str(self.root/'out.jsonl'),*extra],capture_output=True,text=True,encoding='utf-8')
    def test_constants_counts_and_component_hash_gate(self):
        self.assertEqual(json.loads(DESTINATION.read_text()),document())
        self.assertEqual((len(list(cases()))*2,len(list(sequences()))*2),(COUNTS['spectra'],COUNTS['pcm']))
        p=self.root/'wrong-component';p.write_bytes(b'wrong')
        with self.assertRaises(ValueError):extract(p)
    def test_stages_boundaries_and_original_spectra_remain_distinct(self):
        generated=[]
        for block,grouping,upper in ((0,0,False),(2,0x55,True)):
            c=source_case(block,grouping,upper,flat=True);generated.append(packet(c))
        result=self.parse([p for p,t in generated]);self.assertEqual(result.returncode,2,result.stderr)
        summary=json.loads(result.stdout);self.assertEqual(summary['bwe2_complete_packets'],2);self.assertEqual(summary['whole_frame_complete_packets'],0)
        rows=[json.loads(s)['report'] for s in (self.root/'out.jsonl').read_text().splitlines()]
        for r,(_,t) in zip(rows,generated):
            check_expected(r,t)
            self.assertIsNone(r['component_end_bit_offset'])
            self.assertEqual(r['unknown_ranges'][-1]['bit_offset'],t['bwe2']['end_bit_offset'])
            self.assertEqual(r['bwe2']['channels'][1]['parameter_source_channel'],0)
            self.assertNotEqual(r['channels_after_bwe2'][0]['scaled'],r['channels_after_tns'][0]['scaled'])
        for key,value in [('bwe2_complete',False),('stop_bit_offset',0),('bwe2_stage','wrong')]:
            wrong=copy.deepcopy(rows[0]);wrong[key]=value
            with self.assertRaises(AssertionError):check_expected(wrong,generated[0][1])
        wrong=copy.deepcopy(rows[0]);wrong['channels_after_bwe2'][0]['scaled'][0]=13
        with self.assertRaises(AssertionError):check_expected(wrong,generated[0][1])
    def test_flags_are_effective_only_for_defined_channels_and_gains(self):
        blank,truth=packet(dict(bwe2_flags=(True,True)))
        case=source_case(2,0,flat=True);case.update(independent=True,right_block=0,right={0:(1,[1,0,0,0],100)})
        good,other=packet(case)
        invalid=dict(independent=True,block=0,right_block=2,right_grouping=0,
                     left={0:(1,[1,0,0,0],100)},right={0:(1,[1,0,0,0],100)},gain=100,left_bwe2={})
        bad,bad_truth=packet(invalid)
        result=self.parse([blank,good,bad,b'\x40',b'\x70']);self.assertEqual(result.returncode,1,result.stderr)
        rows=[json.loads(s) for s in (self.root/'out.jsonl').read_text().splitlines()]
        check_expected(rows[0]['report'],truth);check_expected(rows[1]['report'],other)
        self.assertEqual(rows[0]['report']['bwe2']['control_bits'],[True,True])
        self.assertTrue(all(not c['active'] for c in rows[0]['report']['bwe2']['channels']))
        self.assertEqual(rows[2]['error']['kind'],'bwe2-reuse')
        self.assertEqual(rows[2]['error']['bit_offset'],bad_truth['tns_end_bit_offset']+1)
        self.assertEqual(rows[2]['packet_index'],2)
        for row in rows[3:]:
            self.assertFalse(row['report']['bwe2_complete']);self.assertIsNone(row['report']['bwe2'])
            self.assertEqual(row['report']['channels_after_bwe2'],[])
        self.assertTrue((self.root/'out.jsonl.incomplete').exists())
    def test_legacy_tns_depth_does_not_consume_bwe_payload(self):
        data,truth=packet(source_case())
        result=self.parse([data],'tns');self.assertEqual(result.returncode,2,result.stderr)
        r=json.loads((self.root/'out.jsonl').read_text())['report']
        self.assertEqual(r['stop_bit_offset'],truth['tns_end_bit_offset']);self.assertNotIn('bwe2',r)
    def test_truncation_and_invalid_tail_preserve_failed_pcm_marker(self):
        data,truth=packet(source_case())
        truncated=data[:(truth['bwe2']['end_bit_offset']-1)//8]
        bundle(self.root/'packets',[data,truncated])
        result=subprocess.run([str(self.binary),'decode-sq',str(self.root/'packets'),'--out',str(self.root/'pcm')],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,1)
        error=json.loads(result.stdout or result.stderr)['error'];self.assertEqual(error['packet_index'],1)
        self.assertIsInstance(error['bit_offset'],int)
        self.assertTrue((self.root/'pcm/.incomplete.json').exists());self.assertFalse((self.root/'pcm/pcm.json').exists())
    def test_output_quota_and_overwrite_protection(self):
        data,_=packet(source_case(flat=True))
        result=self.parse([data]*128,'bwe2','--packets','128','--max-output-mib','1');self.assertEqual(result.returncode,1)
        path=self.root/'out.jsonl';original=path.read_bytes()
        self.assertTrue(path.with_name(path.name+'.incomplete').exists())
        result=subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth','bwe2','--output',str(path)],capture_output=True)
        self.assertEqual(result.returncode,1);self.assertEqual(path.read_bytes(),original)
    def test_exact_gate_rejects_missing_cases_and_any_changed_stage(self):
        item={key:key for key in ('cac_sha256','coupled_sha256','tns_sha256','filtered_sha256','bwe2_sha256','bwe2_analysis_sha256','expanded_sha256')}
        exact(item,item)
        for key in item:
            with self.assertRaises(AssertionError):exact(dict(item,**{key:'changed'}),item)
        with self.assertRaises(AssertionError):validate_reference(dict(passed=True,mode='independent_math',counts={},errors=[]),{})

    def test_pcm_quota_and_overwrite_with_active_bwe2(self):
        data,_=packet(source_case())
        bundle(self.root/'packets',[data])
        args=[str(self.binary),'decode-sq',str(self.root/'packets'),'--out',str(self.root/'pcm')]
        result=subprocess.run(args,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)
        original=(self.root/'pcm/pcm.f32le').read_bytes()
        self.assertEqual(subprocess.run(args,capture_output=True).returncode,1)
        self.assertEqual((self.root/'pcm/pcm.f32le').read_bytes(),original)
        bundle(self.root/'large',[data]*130)
        result=subprocess.run([str(self.binary),'decode-sq',str(self.root/'large'),'--out',str(self.root/'quota'),
                               '--max-output-mib','1'],capture_output=True)
        self.assertEqual(result.returncode,1)
        self.assertTrue((self.root/'quota/.incomplete.json').exists())
        self.assertFalse((self.root/'quota/pcm.json').exists())

    def test_layered_native_gate_detects_parameter_boundary_and_transform_corruption(self):
        data,truth=packet(source_case(flat=True))
        result=self.parse([data]);self.assertEqual(result.returncode,2,result.stderr)
        row=json.loads((self.root/'out.jsonl').read_text());r=row['report'];identity=r['packet_sha256']
        trace=dict(errors=[],process_exit_code=0,packet_calls=1,
                   bwe_entries=[dict(sequence=0,packet_sha256=identity,bit_offset=truth['tns_end_bit_offset'])],
                   tns_apply=[],bwe2_reads=[],bwe2_apply=[])
        native_data=dict(active=[c['active'] for c in r['bwe2']['channels']],lsf_indices=[],gain_indices=[])
        for ch,c in enumerate(r['bwe2']['channels']):
            before=r['channels_after_tns'][ch]['scaled'];ics=r['channels'][ch]['ics'];p=c['parameters']
            native_data['lsf_indices'].append(p['lsf_indices']);native_data['gain_indices'].append(p['gain_indices'])
            trace['tns_apply'].append(dict(sequence=0,packet_sha256=identity,channel_index=ch,after=before))
            analysis=r['channels_after_bwe2'][ch]['analysis']
            source=analysis['source_lpc'] if analysis else [1.]+[0.]*16
            target=analysis['target_lpc'] if analysis else [1.]+[0.]*16
            cutoff=r['channels_after_bwe2'][ch]['regions'][0]['target_start_line']
            expected=conditioned_transform(before,ics,p,source,target,cutoff)
            trace['bwe2_apply'].append(dict(sequence=0,packet_sha256=identity,channel_index=ch,bit_offset=r['stop_bit_offset'],
                ics=dict(block_type=ics['block_type'],max_sfb=ics['max_sfb'],active_window_groups=ics['window_groups']),
                before=before,after=expected,source_lpc=source,target_lpc=target,conditioned_lsf=native_conditioned_lsf(p['lsf_indices'])))
        trace['bwe2_reads'].append(dict(sequence=0,packet_sha256=identity,start_bit_offset=truth['tns_end_bit_offset'],
                                        end_bit_offset=r['stop_bit_offset'],data=native_data))
        self.assertTrue(check_native([row],trace)[0]['conditioned_transform_passed'])
        bad=copy.deepcopy(trace);bad['bwe2_reads'][0]['data']['lsf_indices'][0][0]=511
        with self.assertRaises(AssertionError):check_native([row],bad)
        bad=copy.deepcopy(trace);bad['bwe2_reads'][0]['end_bit_offset']+=1
        with self.assertRaises(AssertionError):check_native([row],bad)
        bad=copy.deepcopy(trace);bad['bwe2_apply'][0]['after'][256]=99.
        metrics=check_native([row],bad)[0]
        self.assertFalse(metrics['conditioned_transform_passed'])
        self.assertEqual(metrics['conditioned_transform_metrics'][0]['first_failure']['frequency_line'],256)


if __name__=='__main__':unittest.main()
