"""Synthetic mathematics and fake-public-interface tests; no target tables."""
import gzip
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import bwe2_blackbox_capture as capture
from bwe2_blackbox_math import affine_scale,envelope,fit_envelope,lpc_to_lsf,lsf_to_lpc
from bwe2_blackbox_results import float_candidates,grid_candidates
from bwe2_blackbox_wire import Writer,canonical,cookie,digest,source


class Mathematics(unittest.TestCase):
    def test_synthetic_filter_and_gain(self):
        frequencies=np.arange(1,17)*12000/17+np.sin(np.arange(16))*30
        self.assertLess(np.max(abs(lpc_to_lsf(lsf_to_lpc(frequencies))-frequencies)),1e-6)
        carrier=np.array(source('comb',123),dtype=float)*128
        for gain in (.00012,.12345,160.):
            values=carrier.copy()
            ids=np.arange(512)
            values[256:768]=carrier[128+ids%128]*gain/envelope(frequencies)
            result=fit_envelope(values,carrier)
            self.assertLess(abs(result['gain']/gain-1),1e-10)
            self.assertLess(np.max(abs(np.array(result['lsf'])-frequencies)),1e-6)

    def test_comb_whitens_source_correlation(self):
        for cutoff in (256,384):
            values=np.array(source('comb',19,cutoff=cutoff))[:cutoff]
            symmetric=np.r_[values**2,0.,values[:0:-1]**2]
            self.assertLess(np.max(abs(np.fft.ifft(symmetric).real[1:17])),1e-14)

    def test_exact_float32_offset_ambiguity(self):
        a=np.arange(32,dtype=np.float32).reshape(2,16)*32+256
        b=np.arange(48,dtype=np.float32).reshape(3,16)/8
        first=a[:,None,:]+b[None,:,:]
        second=(a+1)[:,None,:]+(b-1)[None,:,:]
        self.assertTrue(np.array_equal(first.view(np.uint32),second.view(np.uint32)))
        self.assertFalse(np.array_equal(a,a+1))

    def test_distinct_grid_words_and_ambiguous_interval(self):
        self.assertEqual(len(grid_candidates(.12345,1e-7)),1)
        self.assertGreater(len(grid_candidates(.12345,2e-5)),1)
        value=float(np.float32(164.73929))
        self.assertEqual(len(float_candidates(value-1e-6,value+1e-6)),1)

    def test_affine_baseline(self):
        b=np.array([1.,-3,2,4]);r=np.array([3.,4,-5,1])
        ratio,error=affine_scale(b+.123*(r-b),b,r)
        self.assertAlmostEqual(ratio,.123)
        self.assertLess(error,1e-14)

    def test_nonpositive_transfer_is_not_a_result(self):
        with self.assertRaises(ArithmeticError):
            fit_envelope(np.zeros(1024),np.array(source('comb')))

    def test_wire_matches_independent_disabled_stereo_syntax(self):
        from spectrum_vectors import frame
        writer=Writer()
        values=source('tone',line=0)
        expected,_=frame(dict(gain=128,left={0:(1,[1,0,0,0],128)}))
        self.assertEqual(writer.packet(values),expected)
        with self.assertRaises(ValueError):writer.packet(values,parameters=[512,0,0])

    def test_reference_read_is_denied(self):
        code="import bwe2_blackbox; open('data/bwe2-format-v1.json','rb')"
        env=dict(__import__('os').environ,PYTHONPATH=str(Path(__file__).parent.resolve()))
        result=subprocess.run([sys.executable,'-B','-c',code],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('reference target access is forbidden',result.stderr)


class FakeProcess:
    def __init__(self,command,**kwargs):
        root=Path(command[command.index('--out')+1]);root.mkdir()
        frames=int(command[command.index('--frames')+1]);self.returncode=0
        raw=struct.pack('<f',0.)*(frames*2)
        properties={name:dict(error=None,value=0) for name in ('mdrc','^pro','ptlc')}
        settings=dict(properties,processing_policy=dict(value=dict(verified=True)))
        replay=dict(complete=True,backend='AudioConverterFillComplexBuffer',saved_frames=frames,consumed_packets=frames//1024,
            input_batch_packets=1,original_source_accessed=False,processing_policy='drc-off',decoder_settings=settings)
        pcm=dict(complete=True,frames=frames,channels=2,sample_rate=48000,encoding='f32le',interleaved=True,all_finite=True,
            sha256=digest(raw),start_frame=0,layout=dict(value=dict(tag=(101<<16)|2)),source_cookie_sha256=digest(cookie()))
        policy=dict(verified=True,policy='drc-off',request_order='properties_then_magic_cookie_then_initial_reset',
            initial_reset=dict(os_status=0),readback=properties)
        for name,value in (('replay.json',replay),('pcm.json',pcm),('processing-policy.json',policy)):
            (root/name).write_bytes(canonical(value))
        (root/'pcm.f32le').write_bytes(raw)

    def communicate(self,timeout=None):return b'',b''
    def kill(self):self.returncode=-9


class Evidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.mount=self.root/'external';self.mount.mkdir()
        self.out=self.root/'local';self.pool=self.mount/'evidence'
        self.pin=dict(mount=str(self.mount),uuid='synthetic-volume')
        self.patches=[mock.patch.object(capture,'native_identity',return_value=dict(binary_sha256='fake')),
                      mock.patch.object(capture,'volume_identity',return_value=self.pin),
                      mock.patch.object(capture.Capture,'check'),
                      mock.patch.object(capture.subprocess,'Popen',side_effect=FakeProcess)]
        # Preserve the real guard to test disconnection independently.
        self.real_check=capture.Capture.check
        for p in self.patches[:3]:p.start()
        # Real git identity is collected before replacing Popen.
        self.cap=capture.Capture(self.out,self.root/'binary',self.pool,self.mount)
        self.patches[3].start()

    def tearDown(self):
        self.cap.close()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_cache_repetition_and_successful_raw_cleanup(self):
        spec=dict(source='silence')
        a,raw=self.cap.probe('first',spec)
        b,other=self.cap.probe('cached',spec)
        self.assertEqual((a,raw),(b,other))
        self.assertEqual(self.cap.status()['attempts'],dict(success=1))
        self.cap.probe('repeat',spec,'independent-1')
        self.assertEqual(self.cap.status()['attempts'],dict(success=2))
        self.assertEqual([p.name for p in (self.pool/'attempts'/'000001').iterdir()],['receipt.json'])

    def test_corrupt_evidence_is_not_recaptured(self):
        key,_=self.cap.probe('first',dict(source='silence'))
        receipt=json.loads(self.cap.db.execute('SELECT receipt FROM observations WHERE key=?',(key,)).fetchone()[0])
        h=receipt['artifacts']['native/pcm.f32le']
        (self.pool/'objects'/(h+'.gz')).write_bytes(gzip.compress(b'bad'))
        with self.assertRaisesRegex(RuntimeError,'evidence hash mismatch'):
            self.cap.probe('second',dict(source='silence'))
        self.assertEqual(self.cap.status()['attempts'],dict(success=1))

    def test_budget_and_writer_exclusion(self):
        self.cap.config['max_native_calls']=1
        self.cap.probe('first',dict(source='silence'))
        with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
            self.cap.probe('other',dict(source='tone'))
        with self.assertRaisesRegex(RuntimeError,'already has a writer'):
            capture.Capture(self.out,self.root/'binary')

    def test_disconnect_has_no_local_fallback(self):
        with mock.patch.object(Path,'is_mount',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'disconnected'):
                self.real_check(self.cap)

    def test_failed_and_interrupted_attempts_still_consume_budget(self):
        class Failed(FakeProcess):
            def __init__(self,*a,**kw):super().__init__(*a,**kw);self.returncode=1
        with mock.patch.object(capture.subprocess,'Popen',side_effect=Failed):
            with self.assertRaisesRegex(RuntimeError,'native replay failed'):
                self.cap.probe('failed',dict(source='silence'))
        self.assertEqual(self.cap.status()['attempts'],dict(failed=1))
        self.assertGreaterEqual(self.cap.charged_bytes(),2*capture.MIB)
        self.assertTrue((self.pool/'attempts'/'000001'/'native'/'pcm.f32le').is_file())
        self.cap.config['max_native_calls']=1
        with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
            self.cap.probe('retry',dict(source='silence'))

    def test_read_only_status_works_while_writer_owns_batch(self):
        self.cap.probe('first',dict(source='silence'))
        self.assertEqual(capture.read_status(self.out)['observations'],1)

    def test_identity_change_rejects_resume(self):
        self.cap.close()
        with mock.patch.object(capture,'native_identity',return_value=dict(binary_sha256='changed')):
            with self.assertRaisesRegex(RuntimeError,'identity changed'):
                capture.Capture(self.out,self.root/'binary')
        self.cap=capture.Capture(self.out,self.root/'binary')

    def test_resume_reuses_observations_and_ignores_incomplete_attempt(self):
        first=self.cap.probe('first',dict(source='silence'))
        self.cap.db.execute("INSERT INTO attempts(key,status,started,reserved_bytes) VALUES ('unfinished','running',0,100)")
        self.cap.db.commit();self.cap.close()
        self.cap=capture.Capture(self.out,self.root/'binary')
        self.assertEqual(self.cap.probe('resume',dict(source='silence')),first)
        self.assertEqual(self.cap.status()['attempts'],dict(interrupted=1,success=1))

    def test_full_audit_and_changed_receipt(self):
        self.cap.probe('first',dict(source='silence'))
        self.assertEqual(self.cap.audit()['receipts'],1)
        (self.pool/'attempts'/'000001'/'receipt.json').write_bytes(b'{}')
        with self.assertRaisesRegex(RuntimeError,'receipt differs'):
            self.cap.audit()


class Comparison(unittest.TestCase):
    def test_reference_is_not_read_before_qualification(self):
        from bwe2_blackbox_compare import compare
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);candidate=root/'candidate.json';validation=root/'validation.json'
            candidate.write_bytes(canonical(dict(kind='frozen-bwe2-gain-candidate',all_determined=False)))
            validation.write_bytes(canonical(dict(kind='invalid')))
            with self.assertRaisesRegex(ValueError,'complete frozen'):
                compare(root,candidate,validation,root/'missing-reference.json')


if __name__=='__main__':unittest.main()
