"""Synthetic numerical and public-wire tests; never read target means."""
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager, redirect_stderr
from io import StringIO

import hoa_mean_blackbox as m


def f32(x):
    return m.value(m.word(x))


def synth(values, initial=None):
    exponents, digits = m.dyadic(values)
    result = [0.]*len(values) if initial is None else list(initial)
    for exponent, row in zip(exponents, digits):
        for j, q in enumerate(row):
            result[j] = f32(result[j]+f32((q-256)/256*2.**exponent))
    return result


class MeanTests(unittest.TestCase):
    def test_exact_synthesis_and_cancellation(self):
        rng = random.Random(41829)
        for _ in range(300):
            values = [m.value((rng.randrange(110, 128)<<23) | rng.getrandbits(23) | (rng.getrandbits(1)<<31)) for _ in range(121)]
            for group in m.partition(values):
                trial = [values[j] if j in group else 0. for j in range(121)]
                self.assertEqual(synth(trial), trial)
                cancelled = synth([-v for v in trial], values)
                self.assertTrue(all(cancelled[j] == 0 for j in group))
                for direction in (-1, 1):
                    adjacent = [m.value(m.adjacent(m.word(v), direction)) if j in group else 0.
                                for j,v in enumerate(values)]
                    residual = synth([-v for v in adjacent], values)
                    self.assertTrue(all(residual[j] == values[j]-adjacent[j] != 0 for j in group))

    def test_edges_and_signed_zero(self):
        for v in (1., -1., 2.**-20, -2.**-20, .5, -.5):
            for d in (-1, 1):
                other = m.value(m.adjacent(m.word(v), d))
                self.assertEqual(math.copysign(1, other-v), d)
                self.assertEqual(synth([-other], [v]), [v-other])
        self.assertEqual(m.partition([0., -0.]), [[0,1]])
        self.assertEqual(synth([-0., 0.], [-0., 0.]), [0., 0.])
        self.assertEqual(synth([2.**-30]),[2.**-30])
        with self.assertRaises(m.ExperimentError):
            m.dyadic([2.**-50])

    def test_cookie_flag_only(self):
        for order in (1, 3, 9, 10):
            a,b = m.cookie(order, False),m.cookie(order, True)
            self.assertEqual(len(a),len(b))
            self.assertEqual(sum((x^y).bit_count() for x,y in zip(a,b)),1)
            self.assertEqual(a,m.wire.cookie(9,order))

    def test_flat_and_heldout_packets(self):
        w=m.Writer()
        values=[f32((j-60)/65536) for j in range(121)]
        self.assertEqual(w.packet(values),w.packet(values))
        self.assertNotEqual(w.packet(values,'flat'),w.packet(values,'notch',511))
        self.assertNotEqual(w.packet(values,'notch',511),w.packet(values,'impulse',511))
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'input'
            packets=[w.packet(values)]*2
            w.bundle(folder,packets,True)
            data=json.loads((folder/'manifest.json').read_bytes())
            self.assertEqual(data['file']['cookie']['value']['sha256'],m.digest(m.cookie(10,True)))
            self.assertEqual((folder/'packets.bin').read_bytes(),b''.join(packets))

    def test_projection(self):
        reference=m.array('f',[float(i%17-8) for i in range(2048*121)])
        known=[(j-60)/128 for j in range(121)]
        measured=m.array('f',[v*known[i%121] for i,v in enumerate(reference)])
        estimates,residual=m.projection(measured,reference)
        self.assertEqual(estimates,known)
        self.assertEqual(residual,[0.]*121)

    def test_guard(self):
        code="import hoa_mean_blackbox as m;m.guard();open(m.ROOT/'data/hoa-spatial-controls-format-v1.json')"
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=Path(__file__).resolve().parent,
                         capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('target/reference access forbidden',p.stderr)

    def test_volume_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            pin=dict(mount=root,uuid='test',device=Path(root).stat().st_dev)
            m.check_volume(pin)
            pin['device']+=1
            with self.assertRaises(m.EvidenceError):m.check_volume(pin)


class JournalTests(unittest.TestCase):
    @contextmanager
    def experiment(self, max_calls=20):
        with tempfile.TemporaryDirectory() as root:
            pin=dict(mount=root,uuid='synthetic-volume',device=Path(root).stat().st_dev)
            identity=dict(binary_sha256='fake',component_sha256='fake',os_version='fake',architecture='fake')
            config=dict(targets=[m.TARGET],order=10,quantization_bits=9,native_identity=identity,
                volume=pin,binary=sys.executable,tool_fingerprint=m.fingerprint(),limits=dict(m.DEFAULT_LIMITS,max_calls=max_calls))
            with mock.patch.object(m,'volume_identity',return_value=pin),mock.patch.object(m,'native_identity',return_value=identity):
                store=m.MeanStore.create(Path(root)/'batch',config)
                try:
                    yield store,m.Runner(store)
                finally:
                    store.close()

    @staticmethod
    def fake_native(command,**kwargs):
        source=Path(command[2]);dest=Path(command[command.index('--out')+1]);dest.mkdir()
        manifest=json.loads((source/'manifest.json').read_bytes())
        frames=int(command[command.index('--frames')+1]);n=manifest['file']['format']['channels']
        means=not bool((source/'cookie.bin').read_bytes()[20]&1)
        raw=m.array('f',[float(means)]*(frames*n)).tobytes()
        readback={name:dict(error=None,value=0) for name in ('mdrc','^pro','ptlc')}
        replay=dict(complete=True,backend='AudioConverterFillComplexBuffer',saved_frames=frames,
            consumed_packets=frames//1024,input_batch_packets=1,original_source_accessed=False,
            processing_policy='drc-off',decoder_settings=dict(readback))
        pcm=dict(complete=True,frames=frames,channels=n,sample_rate=48000,encoding='f32le',interleaved=True,
            all_finite=True,sha256=m.digest(raw),start_frame=0,source_cookie_sha256=manifest['file']['cookie']['value']['sha256'],
            layout=manifest['file']['layout'])
        policy=dict(verified=True,policy='drc-off',request_order='properties_then_magic_cookie_then_initial_reset',
            initial_reset=dict(os_status=0),readback=readback)
        for name,data in [('replay.json',replay),('pcm.json',pcm),('processing-policy.json',policy)]:
            (dest/name).write_bytes(m.canonical(data))
        (dest/'pcm.f32le').write_bytes(raw)
        return subprocess.CompletedProcess(command,0,b'',b'')

    def test_dedup_mean_identity_and_independent_repeat(self):
        with self.experiment() as (store,runner),mock.patch.object(m.subprocess,'run',side_effect=self.fake_native) as native,redirect_stderr(StringIO()):
            a,x=runner.probe('test','first',[0.]*121)
            b,y=runner.probe('test','same',[0.]*121)
            c,z=runner.probe('test','enabled',[0.]*121,means=True)
            d,_=runner.probe('test','repeat',[0.]*121,repeat=True)
            self.assertEqual(a,b);self.assertNotEqual(a,c);self.assertNotEqual(a,d)
            self.assertFalse(any(x));self.assertTrue(all(z))
            self.assertEqual(native.call_count,3)
            self.assertEqual(store.summary()['native_calls'],3)
            store.audit_evidence()

    def test_interruption_resume_and_corruption(self):
        with self.experiment() as (store,runner),redirect_stderr(StringIO()):
            with mock.patch.object(m.subprocess,'run',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):runner.probe('test','recover',[0.]*121)
            store.recover()
            with mock.patch.object(m.subprocess,'run',side_effect=self.fake_native) as native:
                key,_=runner.probe('test','recover',[0.]*121)
                again,_=runner.probe('test','recover',[0.]*121)
                self.assertEqual(key,again);self.assertEqual(native.call_count,1)
            self.assertEqual(store.summary()['native_calls'],2)
            receipt=store.query(key)
            path=store.db.execute('SELECT path FROM objects WHERE sha=?',(receipt['pcm_sha256'],)).fetchone()[0]
            Path(path).write_bytes(b'corrupted')
            with self.assertRaises(m.EvidenceError):store.query(key)

    def test_exhausted_budget_and_concurrent_writer(self):
        with self.experiment(max_calls=1) as (store,runner),redirect_stderr(StringIO()):
            with mock.patch.object(m.subprocess,'run',side_effect=self.fake_native):
                runner.probe('test','first',[0.]*121)
                with self.assertRaises(m.BudgetStop):runner.probe('test','second',[0.]*121,repeat=True)
            with m.writer_lock(store.out):
                with self.assertRaises(m.ExperimentError):
                    with m.writer_lock(store.out):pass

    def test_identity_change_rejected_before_capture(self):
        with self.experiment() as (store,runner):
            runner.stamps=[]
            with mock.patch.object(m,'native_identity',return_value={'different':True}):
                with self.assertRaises(m.IdentityError):runner.probe('test','bad',[0.]*121)
            self.assertEqual(store.summary()['native_calls'],0)


if __name__=='__main__':
    unittest.main()
