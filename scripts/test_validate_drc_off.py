"""DRC kernels/timing must pass; Apple outer-rounding remains diagnostic."""
import copy
import hashlib
import struct
import unittest
from unittest.mock import patch
import validate_drc_off as gate


class OffGateTests(unittest.TestCase):
    def fixture(self):
        samples=[[1.0]*1024,[0.5]*1024]
        def event(kind):
            return dict(kind=kind,sequence=0,role='current',frames=1024,status=0,
                before=copy.deepcopy(samples),after=copy.deepcopy(samples),identity=True,
                before_sha256=[hashlib.sha256(v).hexdigest() for v in gate.pcm_bytes(samples)],
                after_sha256=[hashlib.sha256(v).hexdigest() for v in gate.pcm_bytes(samples)],
                state_before=dict(delay_samples=0),state_after=dict(delay_samples=0),delay_samples=0)
        trace=dict(component_sha256=gate.COMPONENT_SHA256,errors=[],pending_returns=0,process_exit_code=0,
            packets=[{}],events=[event('wrapper_process'),event('processor_process'),dict(kind='gain_read',status=0,sequence=0,role='current'),
                dict(kind='selection',status=0,selected=dict(count=0,set_ids=[],loudness_normalization_gain_db=0.0))])
        settings={k:dict(value=0,error=None) for k in ('mdrc','^pro','ptlc')}
        audit=dict(initial=dict(initial_reset=dict(operation='AudioConverterReset',os_status=0,before_input=True),verified=True,requests=[dict(property=k,requested=0,os_status=0) for k in ('mdrc','^pro','^tlc')],readback=settings),final_readback=settings)
        pcm=dict(complete=True,all_finite=True,frames=1024,start_frame=0,sha256='test',decoder_settings=dict(processing_policy=dict(value=audit)))
        replay=dict(complete=True,original_source_accessed=False,saved_frames=1024,range=dict(frames=1024,start_frame=0,drain_to_eof=False),
            produced_raw_frames=1024,discarded_before_frames=0,discarded_after_frames=0,consumed_packet_frames=1024,consumed_packets=1)
        return trace,replay,pcm
    def inspect(self,trace,replay,pcm):
        e=next(e for e in trace['events'] if e['kind']=='wrapper_process')
        raw=struct.pack('<2048f',*[v/32768 for pair in zip(*e['after']) for v in pair])
        with patch.object(gate,'sha256_file',return_value=gate.COMPONENT_SHA256):return gate.inspect(trace,replay,pcm,raw,'drc-off')
    def test_exact_identity_requires_explicit_settings(self):
        data=self.fixture();self.assertTrue(self.inspect(*data)['passed'])
        data[2]['decoder_settings']['processing_policy']['value']['initial']['requests'].pop()
        with self.assertRaisesRegex(AssertionError,'explicitly set'):self.inspect(*data)
    def test_outer_rounding_is_recorded_without_defining_portable_correctness(self):
        data=self.fixture();e=data[0]['events'][0]
        e['after'][0][37]=struct.unpack('<f',struct.pack('<I',0x3f800001))[0]
        e['after_sha256']=[hashlib.sha256(v).hexdigest() for v in gate.pcm_bytes(e['after'])];e['identity']=False
        r=self.inspect(*data);self.assertTrue(r['passed']);self.assertTrue(r['kernel_identity'])
        self.assertFalse(r['whole_path_identity']);self.assertEqual(r['gate_profile'],gate.GATE_PROFILE)
        self.assertEqual(r['changed_samples'],1);self.assertEqual(r['max_ulp'],1)
        self.assertEqual(r['frames'][0]['first_failure']['frame'],37)
    def test_nonidentity_kernel_cannot_be_hidden_by_an_exact_wrapper(self):
        data=self.fixture();e=data[0]['events'][1]
        e['after'][1][13]=0.25
        e['after_sha256']=[hashlib.sha256(v).hexdigest() for v in gate.pcm_bytes(e['after'])];e['identity']=False
        result=self.inspect(*data)
        self.assertFalse(result['passed']);self.assertFalse(result['kernel_identity'])
        self.assertTrue(result['whole_path_identity'])
    def test_wrong_kernel_input_is_rejected_even_if_it_copies_that_input(self):
        data=self.fixture();e=data[0]['events'][1]
        e['before'][0][0]=2.;e['after'][0][0]=2.
        for part in ('before','after'):e[part+'_sha256']=[hashlib.sha256(v).hexdigest() for v in gate.pcm_bytes(e[part])]
        with self.assertRaisesRegex(AssertionError,'kernel input differs'):self.inspect(*data)
    def test_gain_concealment_and_nonzero_delay_cannot_pass(self):
        data=self.fixture();data[0]['events'][2]['status']=0xffffffff
        data[0]['events'].append(dict(kind='gain_reset'))
        r=self.inspect(*data);self.assertFalse(r['passed']);self.assertEqual(r['gain_resets'],1)
        data=self.fixture();data[0]['events'][0]['state_after']['delay_samples']=1
        self.assertFalse(self.inspect(*data)['passed'])
    def test_missing_snapshot_does_not_vacuously_pass(self):
        data=self.fixture();data[0]['events']=[e for e in data[0]['events'] if e['kind']!='processor_process']
        with self.assertRaisesRegex(AssertionError,'missing'):self.inspect(*data)

if __name__=='__main__':unittest.main()
