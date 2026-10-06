"""High-order wire geometry and campaign recovery, using synthetic observations."""
from array import array
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from hoa_blackbox_lib.common import (BudgetStop, EvidenceError, IdentityError, canonical,
    capture_reservation, digest, geometry, pcm_byte_count, pcm_samples, tool_fingerprint)
from hoa_blackbox_lib import wire
from hoa_blackbox_lib.campaign import PLAN, ready_for, record_application
from hoa_blackbox_lib.campaign import make_plan
from hoa_blackbox_lib.campaign_store import create_pool
from hoa_blackbox_lib.engine import Engine
from hoa_blackbox_lib.native import NativeBackend, Runner
from hoa_blackbox_lib.store import Store, writer_lock, directory_bytes
from test_hoa_blackbox import FakeBackend, FakeWriter


def low_order_wire_snapshot(module, order, precision):
    """Synthetic syntax inputs only; never loads a salient dictionary."""
    writer=module.Writer(precision,order)
    identity=dict(binary_sha256='00'*32,component_sha256='11'*32,
                  os_version='synthetic',architecture='synthetic')
    values=[i%(1<<precision) for i in range(writer.symbols)]
    words=[format(q,f'0{precision}b') for q in range(1<<precision)]
    groups=[list(range(0,writer.n,2)),list(range(1,writer.n,2))]
    packets=[writer.fixed(module.vector(0,zero=writer.zero,order=order)),
             writer.fixed(module.vector((1<<precision)-1,writer.n-1,writer.zero,order),129,1),
             writer.fixed([writer.zero]*writer.symbols,active=False),
             writer.padded(1,None,'010011',129,64),writer.padded(4,2,'1011'),
             writer.coded(1,None,values,words),
             writer.coded(2,None,values,[words,words],groups=groups),
             writer.coded(3,None,values,words,signs=[bool(i%2) for i in range(writer.symbols)]),
             writer.coded(4,3,values,words,129,1)]
    programs=[writer.frames(packets[0]),packets[:3],packets[:4]]
    requests=[module.request(identity,frames,replicate,precision,order)[0]
              for frames in programs for replicate in ('','independent-1')]
    return dict(cookie=digest(module.cookie(precision,order)),
                packets=[digest(p) for p in packets],requests=requests)


def pooled(root, name='order4-q6', quota=128, calls=4096, order=4, targets=('mode1',)):
    if not (root/'evidence.sqlite3').exists():
        (root/'batches').mkdir()
        create_pool(root)
    config = dict(schema_version=1, targets=list(targets), order=order, quantization_bits=6,
                  campaign_root=str(root), shard_probes=quota, native_identity=FakeBackend.identity,
                  tool_fingerprint=tool_fingerprint(), prior_sha256=None,
                  limits=dict(max_bytes=512*1024**2,max_calls=calls,min_free=0))
    return Store.create(root/'batches'/name,config)


def runner(store, backend=None, jobs=1):
    backend = backend or FakeBackend(order=store.config['order'])
    r = Runner(store,backend,jobs=jobs)
    r.writer = FakeWriter(order=store.config['order'])
    return r


class HighFakeBackend(FakeBackend):
    def __init__(self,**kwargs):
        super().__init__(order=4,**kwargs)
        # A synthetic decimal-grid Householder matrix, unrelated to target data.
        self.matrices={c:[[struct.unpack('<f',struct.pack('<f',round((float((i+c)%self.n==j)-2/self.n)*1e6)/1e6))[0]
                           for j in range(self.n)] for i in range(self.n)] for c in range(4)}

    def capture(self,packets,folder):
        artifacts=super().capture(packets,folder)
        if len(packets)==2:
            raw=artifacts['native/pcm.f32le']
            raw=raw[:len(raw)//2]*2
            artifacts['native/pcm.f32le']=raw
            meta=json.loads(artifacts['native/pcm.json']);meta['sha256']=digest(raw)
            artifacts['native/pcm.json']=canonical(meta)
        return artifacts


class CampaignTests(unittest.TestCase):
    def test_low_order_inputs_and_request_identities_match_8de2342(self):
        # Frozen using only the baseline wire writer and public AAC tables.
        expected={
            (1,6):'d5c7161afe7526e6a9415fca8547b8fdc869c477868d2fe5d4ba134f56388936',
            (1,7):'2e7b6a0dd232b4518466813c4039f0e210b042a52cd5896b411ea60ea4aaf549',
            (1,8):'b1f2ff6c24f0ec4c0942770a495db74e8f4e3e69b43784b22770e9221753dc75',
            (1,9):'d2547f30781d25420197aba6bb6400e89645090d81df902b3598a22d99487a99',
            (2,6):'95686517c89bf1857c5f2e4fca29fe5c8ad81a461de25a0c19abec3c46074f82',
            (2,7):'d75952e836faf978b09709c2192f04d1435495e54ed06aa149d39c0e18aec202',
            (2,8):'e83bb9c9bdf0b6bf65d0e441232b00ae055d33a8dcef210bcf01ce99c1a622b3',
            (2,9):'2ec09768d7b770e4c089d3847b27ea2b56f2f661873e2f13bfb77fd6b9e16c0e',
            (3,6):'dbaca6a0e011a7ea4171d0ccb0b7b67302b75eb4b09519ff3369464c6cb5fd0a',
            (3,7):'404db4f70171a210371a060de5d0129f19a420e8994c34c11d682a2bdf9c8184',
            (3,8):'a7e62ac5cfae4ae104b20df4aa1daef760011c5bd2fd056ad5a74a23d9efd210',
            (3,9):'1816e5468a2d60c6ff85c728c1994c617b0e319e92dbedbbc39471035631fd61',
        }
        for (order,precision),identity in expected.items():
            with self.subTest(order=order,precision=precision):
                self.assertEqual(digest(canonical(low_order_wire_snapshot(wire,order,precision))),identity)
        default=wire.Writer();explicit=wire.Writer(6,3)
        values=wire.vector(48)
        self.assertEqual(default.frames(default.fixed(values)),explicit.frames(explicit.fixed(values)))
        self.assertEqual(wire.cookie(),wire.cookie(6,3))

    def test_pool_totals_are_transactional_and_bound_actual_disk_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=pooled(root,quota=3);r=runner(s)
            for q in range(8):
                r.probe('mode1','totals',str(q),r.writer.fixed(wire.vector(q,order=4)))
                total=s.pool.db.execute('SELECT stored_bytes,attempts FROM pool_totals WHERE id=1').fetchone()
                self.assertEqual(total[0],s.pool.db.execute('SELECT SUM(stored_bytes) FROM objects').fetchone()[0])
                self.assertEqual(total[1],q+1)
                self.assertGreaterEqual(s.pool.total_bytes(),directory_bytes(root))
            s.close()
            second=pooled(root,name='second');r=runner(second)
            r.probe('mode1','totals','second',r.writer.fixed(wire.vector(15,order=4)))
            self.assertEqual(second.pool.total_calls(),9)
            self.assertGreaterEqual(second.pool.total_bytes(),directory_bytes(root));second.close()

    def test_explicit_scope_and_zero_total_limits_are_checked_before_creation(self):
        plan=make_plan([6,7,8,9,10])
        self.assertEqual(plan['expected_codebooks'],160)
        self.assertEqual(plan['expected_matrices'],20)
        self.assertEqual(make_plan(),PLAN)
        with self.assertRaises(Exception):make_plan([8,6])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'must-not-exist'
            for option in ('--max-total-native-calls','--max-total-evidence-mib'):
                result=subprocess.run([sys.executable,'-B','scripts/hoa_blackbox.py','campaign-run','--out',str(out),
                                       '--binary','/not-a-binary',option,'0'],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('positive',result.stdout)
                self.assertFalse(out.exists())

    def test_live_producer_change_stops_capture(self):
        backend=object.__new__(NativeBackend)
        backend.identity_lock=threading.Lock()
        backend.producer_fingerprint='frozen';backend.producer_stamps=['old']
        backend.source_stamps=lambda:['new']
        with patch('hoa_blackbox_lib.native.tool_fingerprint',return_value='changed'):
            with self.assertRaisesRegex(IdentityError,'producer changed'):backend.check()

    def test_campaign_caps_and_volume_identity_are_not_reset_by_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=pooled(Path(tmp),quota=3);r=runner(s)
            with s.pool.db:
                s.pool.db.execute("UPDATE meta SET value=? WHERE key='total_limits'",(canonical(dict(max_calls=4,max_bytes=256*1024**3)).decode(),))
            for q in range(4):r.probe('mode1','caps',str(q),r.writer.fixed(wire.vector(q,order=4)))
            with self.assertRaisesRegex(BudgetStop,'campaign native call'):
                r.probe('mode1','caps','4',r.writer.fixed(wire.vector(4,order=4)))
            self.assertEqual([v['native_calls'] for v in s.summary()['shards']],[3,1])
            s.config['evidence_device']=-1
            with self.assertRaisesRegex(IdentityError,'volume changed'):s.reserve(0)
            s.config.pop('evidence_device')
            with s.pool.db:
                s.pool.db.execute("UPDATE meta SET value=? WHERE key='total_limits'",(canonical(dict(max_calls=10,max_bytes=1)).decode(),))
            with self.assertRaisesRegex(BudgetStop,'campaign evidence'):s.reserve(0)
            s.close()

    def test_high_order_recovery_matches_uninterrupted_and_sharded_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            aroot=root/'continuous';aroot.mkdir()
            a=pooled(aroot,quota=128,targets=('mode1','mode4:0'))
            backend=HighFakeBackend();e=Engine(a,runner(a,backend,jobs=4))
            self.assertEqual(e.preflight()['status'],'passed')
            e.run()
            frozen={(r['target'],r['name']):a.stage(r['target'],r['name']) for r in a.db.execute('SELECT target,name FROM stages').fetchall()}
            expected_calls=backend.calls
            self.assertEqual([r['status'] for r in a.summary()['targets']],['validated','validated'])
            self.assertEqual([x['codeword'] for x in frozen['mode1','codebook']['entries']],backend.words[1,None])
            expected=[struct.unpack('<I',struct.pack('<f',v))[0] for row in backend.matrices[0] for v in row]
            self.assertEqual([x['float32_bits'] for x in frozen['mode4:0','matrix']['entries']],expected)
            a.close()
            broot=root/'resumed';broot.mkdir()
            b=pooled(broot,quota=37,targets=('mode1','mode4:0'))
            interrupted=HighFakeBackend(interrupt_at=90)
            with self.assertRaises(KeyboardInterrupt):Engine(b,runner(b,interrupted)).preflight()
            path=b.out;b.close();b=Store(path);b.recover()
            new=HighFakeBackend();e=Engine(b,runner(b,new,jobs=4));e.preflight();e.run()
            for (target,name),value in frozen.items():self.assertEqual(b.stage(target,name),value)
            self.assertEqual(b.summary()['native_calls'],expected_calls+1)
            calls=new.calls;e.preflight();e.run();self.assertEqual(new.calls,calls)
            b.close()

    def test_high_order_geometry_and_escaped_boundaries(self):
        self.assertEqual(wire.escaped(30,(5,10,16)), '11110')
        self.assertEqual(wire.escaped(31,(5,10,16)), '11111'+'0'*10)
        self.assertEqual(wire.escaped(1054,(5,10,16)), '1'*15+'0'*16)
        for order in range(1,11):
            g=geometry(order)
            self.assertEqual(g['channels'],(order+1)**2)
            self.assertEqual((g['profile'],g['level']),
                             (5,0) if order<=3 else (5,1) if order<=5 else (5,2) if order==6 else (0,0))
            for q in (6,7,8,9):
                cookie=wire.cookie(q,order)
                self.assertEqual(int.from_bytes(cookie[:4],'big'),len(cookie))
                w=wire.Writer(q,order)
                packet=w.fixed(wire.vector(w.zero,zero=w.zero,order=order))
                key,request=wire.request(FakeBackend.identity,w.frames(packet),quantization_bits=q,order=order)
                self.assertEqual(key,digest(canonical(request)))
                self.assertEqual(request['signature']['channels'],g['channels'])
        with self.assertRaisesRegex(Exception,'unsupported HOA order'):
            geometry(11)
        raw=bytes(pcm_byte_count(10,4096))
        self.assertGreater(len(raw),1024**2)
        self.assertEqual(len(pcm_samples(raw,121)),4096*121)
        with self.assertRaises(EvidenceError):
            pcm_samples(raw,100)

    def test_native_cap_and_reservation_scale_to_four_high_order_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            fake=FakeBackend(order=10)
            writer=FakeWriter(order=10)
            packet=writer.fixed([32]*writer.symbols,active=False)
            packets=[packet]*4
            artifacts=fake.capture(packets,folder)
            captured=[]
            class Process:
                returncode=0
                def __init__(self,command,**kwargs):
                    captured.extend(command)
                    for name,raw in artifacts.items():
                        if name.startswith('native/'):
                            path=folder/name;path.parent.mkdir(exist_ok=True)
                            path.write_bytes(raw)
                def communicate(self,timeout=None):return b'',b''
                def poll(self):return 0
            native=object.__new__(NativeBackend)
            native.order=10;native.quantization_bits=6;native.binary=Path('/synthetic/apac-tool')
            native.process_lock=threading.Lock();native.processes=set();native.check=lambda:None
            with patch('hoa_blackbox_lib.native.subprocess.Popen',Process):
                result=native.capture(packets,folder)
            self.assertEqual(captured[captured.index('--max-output-mib')+1],'2')
            self.assertEqual(len(result['native/pcm.f32le']),pcm_byte_count(10,4096))
            _,request=wire.request(fake.identity,packets,order=10)
            self.assertGreater(capture_reservation(request),2*len(result['native/pcm.f32le']))

    def test_native_cap_covers_replay_sidecars_at_mib_boundaries(self):
        # The public replay command reserves 64 KiB after charging its incomplete
        # marker. PCM-only rounding fails at order 7/four, 8/three and 10/two frames.
        for order,frames in ((3,4),(7,4),(8,3),(10,2),(10,4)):
            with self.subTest(order=order,frames=frames), tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);backend=FakeBackend(order=order)
                writer=FakeWriter(order=order)
                packets=[writer.fixed([32]*writer.symbols,active=False)]*frames
                artifacts=backend.capture(packets,folder)
                required=len(artifacts['native/pcm.f32le'])+65536+128
                class Process:
                    def __init__(self,command,**kwargs):
                        limit=int(command[command.index('--max-output-mib')+1])*1024**2
                        self.returncode=0 if limit>=required else 1
                        if not self.returncode:
                            for name,raw in artifacts.items():
                                if name.startswith('native/'):
                                    path=folder/name;path.parent.mkdir(exist_ok=True)
                                    path.write_bytes(raw)
                    def communicate(self,timeout=None):
                        return b'',b'output limit: PCM and sidecar reservation' if self.returncode else b''
                    def poll(self):return self.returncode
                native=object.__new__(NativeBackend)
                native.order=order;native.quantization_bits=6;native.binary=Path('/synthetic/apac-tool')
                native.process_lock=threading.Lock();native.processes=set();native.check=lambda:None
                with patch('hoa_blackbox_lib.native.subprocess.Popen',Process):
                    result=native.capture(packets,folder)
                self.assertEqual(result['native/pcm.f32le'],artifacts['native/pcm.f32le'])

    def test_parallel_shards_keep_requests_and_independent_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=pooled(Path(tmp),quota=3)
            backend=FakeBackend(order=4);r=runner(store,backend,jobs=4)
            values=[1,2,3,1,4,5,6,7,8]
            requests=[('mode1','trial',str(i),r.writer.fixed(wire.vector(q,order=4)),'') for i,q in enumerate(values)]
            with r.batch(requests) as results:
                first=[key for key,_ in results]
            self.assertEqual(backend.calls,8)
            self.assertEqual(first[0],first[3])
            shards=store.summary()['shards']
            self.assertEqual([s['native_calls'] for s in shards],[3,3,2])
            with r.batch(requests) as results:
                self.assertEqual([key for key,_ in results],first)
            self.assertEqual(backend.calls,8)
            r.probe('mode1','trial','repeat',requests[0][3],'independent-1')
            self.assertEqual(backend.calls,9)
            self.assertEqual(store.summary()['native_calls'],9)
            store.close()

    def test_shared_pool_has_one_pcm_copy_and_one_observation_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=pooled(root)
            ra=runner(a);packet=ra.writer.fixed(wire.vector(48,order=4))
            key,_=ra.probe('mode1','trial','a',packet)
            receipt=a.query(key);raw=a.read_blob(receipt['pcm_sha256'])
            count=a.pool.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
            a.close()
            b=pooled(root,name='order4-q6-copy');rb=runner(b)
            self.assertEqual(rb.probe('mode1','trial','b',packet)[0],key)
            self.assertEqual(rb.backend.calls,0)
            self.assertEqual(b.pool.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0],count)
            self.assertEqual(b.read_blob(receipt['pcm_sha256']),raw)
            self.assertEqual(list((root/'batches'/'order4-q6-copy'/'objects').iterdir()),[])
            b.close()

    def test_interrupted_attempt_recollected_successful_probe_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=pooled(root,quota=3)
            r=runner(s,FakeBackend(order=4,interrupt_at=2))
            a=r.writer.fixed(wire.vector(40,order=4));b=r.writer.fixed(wire.vector(41,order=4))
            key,_=r.probe('mode1','trial','a',a)
            with self.assertRaises(KeyboardInterrupt):r.probe('mode1','trial','b',b)
            out=s.out;s.close();s=Store(out);s.recover();r=runner(s)
            self.assertEqual(r.probe('mode1','trial','a',a)[0],key)
            r.probe('mode1','trial','b',b)
            self.assertEqual(r.backend.calls,1)
            self.assertEqual(s.summary()['native_calls'],3)
            self.assertEqual(s.db.execute("SELECT COUNT(*) FROM attempts WHERE state='interrupted'").fetchone()[0],1)
            s.close()

    def test_exhaustion_does_not_open_a_new_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=pooled(Path(tmp),quota=128,calls=2);r=runner(s)
            for q in (40,41):r.probe('mode1','trial',str(q),r.writer.fixed(wire.vector(q,order=4)))
            with self.assertRaises(BudgetStop):r.probe('mode1','trial','42',r.writer.fixed(wire.vector(42,order=4)))
            self.assertEqual(len(s.summary()['shards']),1)
            self.assertEqual(s.pool.current()['state'],'budget_exhausted')
            with self.assertRaises(EvidenceError):s.pool.advance()
            s.set_limits(max_calls=3)
            r.probe('mode1','trial','42',r.writer.fixed(wire.vector(42,order=4)))
            self.assertEqual(s.summary()['native_calls'],3)
            s.close()

    def test_corrupt_pool_and_identity_change_refuse_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=pooled(root);r=runner(a)
            key,_=r.probe('mode1','trial','x',r.writer.fixed(wire.vector(48,order=4)))
            sha=a.query(key)['pcm_sha256'];a.close()
            b=pooled(root,name='order4-q6-second')
            b.config['native_identity']=dict(FakeBackend.identity,architecture='changed')
            with self.assertRaises(IdentityError):b.query(key)
            b.config['native_identity']=FakeBackend.identity
            (root/'objects'/(sha+'.gz')).write_bytes(b'corrupt')
            with self.assertRaises(EvidenceError):b.query(key)
            b.close()

    def test_campaign_dependency_and_writer_gate(self):
        priors=dict(matrices={'0':{}},groups={'2:0':{},'2:1':{}})
        self.assertTrue(ready_for('mode1',priors))
        self.assertTrue(ready_for('mode2:0',priors))
        self.assertTrue(ready_for('mode4:0',priors))
        self.assertFalse(ready_for('mode4:1',priors))
        self.assertFalse(ready_for('mode3',priors))
        self.assertEqual(PLAN['expected_codebooks'],224)
        with tempfile.TemporaryDirectory() as tmp:
            with writer_lock(tmp):
                with self.assertRaisesRegex(Exception,'another writer'):
                    with writer_lock(tmp):pass


if __name__ == '__main__':
    unittest.main()
