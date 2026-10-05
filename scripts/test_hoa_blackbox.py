"""Synthetic oracles and failure injection for the black-box batch workflow."""
from array import array
import gzip
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from hoa_blackbox_lib.common import (BudgetStop, EvidenceError, ExperimentError, IdentityError,
    ROOT, canonical, digest, target_parts, tool_fingerprint)
from hoa_blackbox_lib.engine import Engine
from hoa_blackbox_lib.maths import infer_tree, inverse, matrix_entry, vmul
from hoa_blackbox_lib.native import Runner, import_batch
from hoa_blackbox_lib.store import Store, writer_lock
from hoa_blackbox_lib import wire


def make_words(seed):
    rng = random.Random(seed)
    words = ['']
    while len(words) < 64:
        index = rng.choice([i for i, word in enumerate(words) if len(word) < 12])
        word = words.pop(index)
        words.extend([word+'0', word+'1'])
    rng.shuffle(words)
    return words


class FakeWriter:
    def fixed(self, values, gain=128, line=0, active=True):
        return canonical(dict(mode=0, values=values, gain=gain, line=line, active=active))

    def padded(self, mode, cluster, pattern, gain=128, extra=0):
        return canonical(dict(mode=mode, cluster=cluster, wire=pattern.ljust(600+extra, '0'), gain=gain, line=0))

    def coded(self, mode, cluster, values, words, gain=128, line=0, padding=0,
              groups=None, signs=None, active=True):
        groups = groups if groups is not None else [list(range(16))]
        raw = ''
        for start in range(0, 320, 16):
            for book, group in enumerate(groups):
                for channel in group:
                    raw += (words[book] if mode == 2 else words)[values[start+channel]]
                    if mode == 3:
                        raw += str(int(signs[start+channel]))
        return canonical(dict(mode=mode, cluster=cluster, wire=raw+'0'*padding,
                              gain=gain, line=line, active=active))

    def frames(self, payload):
        if isinstance(payload, (list, tuple)):
            return list(payload)
        return [payload, self.fixed([32]*320, active=False)]


class FakeBackend:
    identity = dict(binary_sha256='fake', component_sha256='fake', os_version='synthetic', architecture='synthetic')

    def __init__(self, interrupt_at=None, fail_cluster=None):
        self.calls = 0
        self.interrupt_at = interrupt_at
        self.fail_cluster = fail_cluster
        self.words = {(1, None): make_words(10)}
        self.words.update({(2, 0): make_words(20), (2, 1): make_words(21), (3, None): make_words(22)})
        self.words.update({(4, c): make_words(30+c) for c in range(4)})
        order = list(range(16))
        random.Random(13).shuffle(order)
        self.groups = {2: [order[:7], order[7:]], 3: [list(reversed(order))]}
        self.matrices = {c: [[(-1. if (((i+c)%16)&j).bit_count()%2 else 1.)/4
                             for j in range(16)] for i in range(16)] for c in range(4)}

    def check(self, force=False):
        pass

    def capture(self, packets, folder):
        self.calls += 1
        if self.interrupt_at == self.calls:
            raise KeyboardInterrupt()
        samples = array('f')
        history = [0.]*16
        for packet in packets:
            request = json.loads(packet)
            mode, cluster = request['mode'], request.get('cluster')
            if mode == 4 and cluster == self.fail_cluster:
                raise ExperimentError('synthetic target failure')
            signs = [True]*16
            if mode == 0:
                q = request['values'][:16]
            else:
                raw = request['wire'];position = 0;q = [0]*16
                groups = self.groups.get(mode, [list(range(16))])
                for group_index, group in enumerate(groups):
                    key = (mode, group_index if mode == 2 else cluster)
                    for channel in group:
                        found = [(i, word) for i, word in enumerate(self.words[key]) if raw.startswith(word, position)]
                        if len(found) != 1:
                            raise ExperimentError('synthetic codeword was not decoded uniquely')
                        value, word = found[0];q[channel] = value;position += len(word)
                        if mode == 3:
                            signs[channel] = raw[position] == '1'
                            position += 1
            coeff = [(v-32)/32 for v in q]
            if mode == 3:
                coeff = [previous+v/32*(1 if positive else -1) for previous, v, positive in zip(history, q, signs)]
            if mode == 4:
                coeff = vmul(coeff, self.matrices[cluster])
            history = coeff[:]
            scale = 1. if request['gain'] == 128 else 2**0.25
            for t in range(1024):
                samples.extend(v*scale*(1 if not request['line'] or t%2 else -1)
                               if request.get('active', True) else 0. for v in coeff)
        if sys.byteorder != 'little':samples.byteswap()
        pcm = samples.tobytes()
        props = {name: dict(error=None, value=0) for name in ('mdrc', '^pro', 'ptlc')}
        policy = dict(verified=True, policy='drc-off', request_order='properties_then_magic_cookie_then_initial_reset',
                      initial_reset=dict(os_status=0), readback=props)
        frames = 1024*len(packets)
        replay = dict(complete=True, backend='AudioConverterFillComplexBuffer', saved_frames=frames,
                      consumed_packets=len(packets), input_batch_packets=1, original_source_accessed=False, processing_policy='drc-off',
                      decoder_settings=dict(props, processing_policy=dict(value=dict(verified=True))))
        meta = dict(complete=True, frames=frames, channels=16, sample_rate=48000, encoding='f32le',
                    interleaved=True, all_finite=True, sha256=digest(pcm), start_frame=0,
                    layout=dict(value=dict(tag=wire.SIGNATURE['layout_tag'])), source_cookie_sha256=digest(wire.cookie()))
        return {'native/pcm.f32le': pcm, 'native/replay.json': canonical(replay),
                'native/pcm.json': canonical(meta), 'native/processing-policy.json': canonical(policy),
                'input/packets.bin': b''.join(packets), 'input/cookie.bin': wire.cookie()}


def new_store(path, targets=('mode1',), calls=4096):
    config = dict(schema_version=1, targets=list(targets), native_identity=FakeBackend.identity,
                  tool_fingerprint=tool_fingerprint(), limits=dict(max_bytes=128*1024**2, max_calls=calls, min_free=0))
    return Store.create(path, config)


def fake_engine(store, backend, jobs=1):
    runner = Runner(store, backend, jobs=jobs)
    runner.writer = FakeWriter()
    return Engine(store, runner)


class BatchTests(unittest.TestCase):
    def test_query_reuse_and_independent_replicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = FakeBackend();engine = fake_engine(store, backend)
            packet = engine.writer.fixed(wire.vector(48))
            a, pcm = engine.capture('mode1', 'test', 'a', packet)
            b, _ = engine.capture('mode1', 'test', 'b', packet)
            c, _ = engine.capture('mode1', 'test', 'repeat', packet, True)
            self.assertEqual(a, b);self.assertNotEqual(a, c);self.assertEqual(backend.calls, 2)
            self.assertEqual(store.query(a)['pcm_sha256'], store.query(c)['pcm_sha256'])
            store.close()

    def test_incomplete_attempt_is_not_reused_and_budget_is_cumulative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'batch';store = new_store(path, calls=1)
            engine = fake_engine(store, FakeBackend(interrupt_at=1))
            packet = engine.writer.fixed(wire.vector(48))
            with self.assertRaises(KeyboardInterrupt):engine.capture('mode1', 'test', 'a', packet)
            store.close();store = Store(path);store.recover()
            backend = FakeBackend();engine = fake_engine(store, backend)
            with self.assertRaises(BudgetStop):engine.capture('mode1', 'test', 'a', packet)
            self.assertEqual(backend.calls, 0)
            store.set_limits(max_calls=2)
            engine.capture('mode1', 'test', 'a', packet)
            self.assertEqual(store.summary()['native_calls'], 2)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM attempts WHERE state="interrupted"').fetchone()[0], 1)
            store.close()

    def test_evidence_corruption_stops_without_new_native_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = FakeBackend();engine = fake_engine(store, backend)
            packet = engine.writer.fixed(wire.vector(48));key, _ = engine.capture('mode1', 'test', 'a', packet)
            identity = store.query(key)['pcm_sha256']
            row = store.db.execute('SELECT path FROM objects WHERE sha=?', (identity,)).fetchone()
            Path(row[0]).write_bytes(gzip.compress(b'corrupted'))
            with self.assertRaises(EvidenceError):engine.capture('mode1', 'test', 'a', packet)
            with self.assertRaises(EvidenceError):store.audit_evidence()
            self.assertEqual(backend.calls, 1);store.close()

    def test_completed_receipt_recovery_and_frozen_stage_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = FakeBackend();engine = fake_engine(store, backend)
            payload = engine.writer.fixed(wire.vector(48))
            key, _ = engine.capture('mode1', 'test', 'a', payload)
            receipt = store.query(key)
            request = json.loads(store.db.execute('SELECT request FROM queries WHERE key=?', (key,)).fetchone()[0])
            attempt, folder = store.begin(key, request)
            (folder/'receipt.json').write_bytes(canonical(receipt))
            store.recover()
            self.assertFalse(folder.exists())
            self.assertEqual(store.db.execute('SELECT state FROM attempts WHERE id=?', (attempt,)).fetchone()[0], 'passed')
            store.save_stage('mode1', 'codebook', dict(value=1))
            (store.out/'results/mode1/codebook.json').write_text('{"value":2}\n')
            with self.assertRaises(EvidenceError):store.stage('mode1', 'codebook')
            store.close()

    def test_identity_disk_budget_and_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = FakeBackend()
            backend.identity = dict(backend.identity, component_sha256='different')
            with self.assertRaises(IdentityError):Runner(store, backend)
            backend = FakeBackend();engine = fake_engine(store, backend)
            store.set_limits(max_bytes=1)
            with self.assertRaises(BudgetStop):engine.capture('mode1', 'test', 'a', engine.writer.fixed(wire.vector(48)))
            self.assertEqual(backend.calls, 0)
            with writer_lock(store.out):
                with self.assertRaises(ExperimentError):
                    with writer_lock(store.out):pass
            store.close()

    def test_import_references_objects_without_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = new_store(Path(tmp)/'source');b = FakeBackend();e = fake_engine(source, b)
            payload = e.writer.fixed(wire.vector(48));key, _ = e.capture('mode1', 'test', 'a', payload)
            source.close()
            target = new_store(Path(tmp)/'target')
            self.assertEqual(import_batch(target, Path(tmp)/'source'), 1)
            self.assertEqual(list((target.out/'objects').iterdir()), [])
            new_backend = FakeBackend();new_engine = fake_engine(target, new_backend)
            self.assertEqual(new_engine.capture('mode1', 'test', 'a', payload)[0], key)
            self.assertEqual(new_backend.calls, 0);target.close()

    def test_mode1_resume_is_identical_to_continuous_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = new_store(Path(tmp)/'full');backend = FakeBackend();fake_engine(full, backend).run()
            expected = {name: full.stage('mode1', name) for name in ('codebook', 'validation')}
            words = [e['codeword'] for e in expected['codebook']['entries']]
            self.assertEqual(words, backend.words[1, None])
            continuous_calls = backend.calls
            full.close()
            path = Path(tmp)/'resumed';resumed = new_store(path)
            interrupted = FakeBackend(interrupt_at=80)
            with self.assertRaises(KeyboardInterrupt):fake_engine(resumed, interrupted).run()
            successful_before = resumed.db.execute('SELECT COUNT(*) FROM queries WHERE state="passed"').fetchone()[0]
            resumed.close();resumed = Store(path);resumed.recover();remaining = FakeBackend()
            fake_engine(resumed, remaining, jobs=2).run()
            self.assertEqual(successful_before+remaining.calls, continuous_calls)
            for name, value in expected.items():self.assertEqual(resumed.stage('mode1', name), value)
            before = remaining.calls;fake_engine(resumed, remaining).run()
            self.assertEqual(remaining.calls, before);resumed.close()

    def test_mode4_synthetic_recovery_and_target_failure_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch', ('mode4:0', 'mode4:1'))
            backend = FakeBackend(fail_cluster=0);fake_engine(store, backend, jobs=2).run()
            self.assertEqual(store.summary()['targets'][0]['status'], 'failed')
            self.assertEqual(store.summary()['targets'][1]['status'], 'validated')
            book = store.stage('mode4:1', 'codebook');matrix = store.stage('mode4:1', 'matrix')
            self.assertEqual([e['codeword'] for e in book['entries']], backend.words[4, 1])
            import struct
            self.assertEqual([e['float32_bits'] for e in matrix['entries']],
                             [struct.unpack('<I', struct.pack('<f', v))[0] for row in backend.matrices[1] for v in row])
            store.close()

    def test_mode2_partition_and_mode3_signs_and_history_are_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch', ('mode2:0', 'mode2:1', 'mode3'))
            backend = FakeBackend()
            fake_engine(store, backend, jobs=2).run()
            self.assertTrue(all(t['status'] == 'validated' for t in store.summary()['targets']))
            self.assertEqual(store.stage('_mode2', 'layout')['groups'], backend.groups[2])
            for target, key in [('mode2:0', (2, 0)), ('mode2:1', (2, 1)), ('mode3', (3, None))]:
                book = store.stage(target, 'codebook')
                self.assertEqual([e['codeword'] for e in book['entries']], backend.words[key])
            self.assertEqual(store.stage('mode3', 'layout')['groups'], backend.groups[3])
            checks = store.stage('mode3', 'validation')['checks']
            self.assertTrue(any(c['preparation_frames'] == 2 for c in checks))
            before = backend.calls
            fake_engine(store, backend).run()
            self.assertEqual(backend.calls, before)
            store.close()

    def test_packet_program_identity_and_frame_contract(self):
        from hoa_blackbox_lib.native import validate_public
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch', ('mode3',))
            backend = FakeBackend();engine = fake_engine(store, backend)
            packet = engine.writer.fixed(wire.vector(48))
            a, two = engine.capture('mode3', 'test', 'two', packet)
            b, three = engine.capture('mode3', 'test', 'three', engine.mode3_program(packet))
            self.assertNotEqual(a, b)
            self.assertEqual((len(two), len(three)), (2048*16, 3072*16))
            receipt = store.query(b)
            artifacts = {name:store.read_blob(h) for name,h in receipt['artifacts'].items()}
            with self.assertRaises(EvidenceError):validate_public(artifacts, 2048)
            validate_public(artifacts, 3072)
            store.close()

    def test_frozen_comparison_does_not_reopen_a_changed_reference(self):
        from unittest.mock import patch
        from hoa_blackbox_lib.reference import compare
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch')
            book = dict(entries=[dict(symbol=i, codeword=w, bit_length=len(w))
                                 for i, w in enumerate(make_words(16))])
            validation = dict(status='passed', codebook_sha256=digest(canonical(book)))
            comparison = dict(status='passed', codebook_sha256=digest(canonical(book)),
                              validation_sha256=digest(canonical(validation)))
            for name, value in [('codebook', book), ('validation', validation), ('comparison', comparison)]:
                store.save_stage('mode1', name, value)
            with patch('hoa_blackbox_lib.reference.ROOT', Path(tmp)/'absent-reference'):
                compare(store, ['mode1'])
            self.assertEqual(store.stage('mode1', 'comparison'), comparison)
            store.close()

    def test_guard_and_unknown_target(self):
        for target in ('mode2', 'mode4:4', '../mode1'):
            with self.assertRaises(ExperimentError):target_parts(target)
        source = "from hoa_blackbox_lib.common import install_discovery_guard,ROOT; install_discovery_guard(); (ROOT/'data/hoa-salient-format-v1.json').read_bytes()"
        proc = subprocess.run([sys.executable, '-B', '-c', source], cwd=ROOT/'scripts', capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('forbidden', proc.stderr)

    def test_cli_does_not_lower_the_free_space_floor(self):
        import argparse
        import hoa_blackbox
        with self.assertRaisesRegex(ExperimentError, '1024 MiB'):
            hoa_blackbox.limits(argparse.Namespace(max_evidence_mib=None, max_native_calls=None, min_free_mib=512))


class ConcurrentBackend(FakeBackend):
    def __init__(self, fail=False):
        super().__init__()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.cancelled = 0
        self.fail = fail

    def capture(self, packets, folder):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.03)
            if self.fail:
                (folder/'partial.bin').write_bytes(b'retained interrupted evidence')
                raise ExperimentError('injected concurrent failure')
            return super().capture(packets, folder)
        finally:
            with self.lock:
                self.active -= 1

    def cancel(self):
        self.cancelled += 1


class ConcurrencyTests(unittest.TestCase):
    def requests(self, engine, count=4):
        return [('mode1', 'parallel', str(i), engine.writer.fixed(wire.vector(i)), '') for i in range(count)]

    def test_bounded_overlap_coalescing_and_independent_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = ConcurrentBackend()
            engine = fake_engine(store, backend, jobs=2)
            requests = self.requests(engine)
            requests.insert(1, ('mode1', 'parallel', 'duplicate', requests[0][3], ''))
            requests.append(('mode1', 'parallel', 'repeat', requests[0][3], 'independent-repeat'))
            with engine.runner.batch(requests) as results:values = list(results)
            self.assertEqual(backend.peak, 2)
            self.assertEqual(backend.calls, 5)
            self.assertEqual(values[0][0], values[1][0])
            self.assertNotEqual(values[0][0], values[-1][0])
            self.assertEqual(values[0][1], values[-1][1])
            self.assertEqual(store.capture_reservations, {})
            with engine.runner.batch(requests) as results:self.assertEqual(list(results), values)
            self.assertEqual(backend.calls, 5)
            store.close()

    def test_call_budget_finishes_reserved_work_and_resume_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch', calls=1);backend = ConcurrentBackend()
            engine = fake_engine(store, backend, jobs=4);requests = self.requests(engine, 3)
            with self.assertRaises(BudgetStop):
                with engine.runner.batch(requests) as results:list(results)
            self.assertEqual(backend.calls, 1)
            self.assertEqual(store.summary()['successful_queries'], 1)
            self.assertEqual(store.capture_reservations, {})
            store.set_limits(max_calls=3)
            with engine.runner.batch(requests) as results:self.assertEqual(len(list(results)), 3)
            self.assertEqual(backend.calls, 3)
            store.close()

    def test_inflight_space_reservations_preserve_free_space_floor(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = ConcurrentBackend()
            store.set_limits(min_free=1024**3)
            engine = fake_engine(store, backend, jobs=2)
            with patch('hoa_blackbox_lib.store.shutil.disk_usage', return_value=SimpleNamespace(free=1024**3+3*1024**2)):
                with self.assertRaises(BudgetStop):
                    with engine.runner.batch(self.requests(engine, 2)) as results:list(results)
            self.assertEqual(backend.calls, 1)
            self.assertEqual(store.capture_reservations, {})
            store.close()

    def test_failed_batch_drains_workers_and_retains_unaccepted_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = ConcurrentBackend(fail=True)
            engine = fake_engine(store, backend, jobs=2);requests = self.requests(engine, 2)
            with self.assertRaises(ExperimentError):
                with engine.runner.batch(requests) as results:list(results)
            self.assertEqual(backend.active, 0)
            self.assertEqual(store.summary()['successful_queries'], 0)
            self.assertEqual(len(list((store.out/'attempts').glob('*/partial.bin'))), 2)
            self.assertEqual(store.capture_reservations, {})
            backend.fail = False
            with engine.runner.batch(requests) as results:self.assertEqual(len(list(results)), 2)
            self.assertEqual(store.summary()['native_calls'], 4)
            self.assertEqual(len(list((store.out/'attempts').glob('*/partial.bin'))), 2)
            store.close()

    def test_early_consumer_exit_joins_workers_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'batch');backend = ConcurrentBackend()
            engine = fake_engine(store, backend, jobs=2)
            with engine.runner.batch(self.requests(engine)) as results:next(results)
            self.assertEqual(backend.active, 0)
            self.assertEqual(store.summary()['native_calls'], 2)
            self.assertEqual(store.summary()['successful_queries'], 1)
            self.assertEqual(store.capture_reservations, {})
            self.assertFalse(engine.runner.batch_active)
            store.close()


class MathTests(unittest.TestCase):
    def test_negative_scale_and_ambiguous_zero(self):
        words = make_words(5)
        def query(pattern):
            q = next(i for i, word in enumerate(words) if pattern.startswith(word))
            return (q-32)/-7, pattern
        entries, _, scale = infer_tree(query, coordinate=True)
        self.assertEqual(scale, -7);self.assertEqual([e['codeword'] for e in entries], words)
        self.assertIsNone(matrix_entry([0., 0.], [0.5, 0.5], [0., 0.])['float32_bits'])
        with self.assertRaises(ExperimentError):inverse([[1., 1.], [1., 1.]])
        self.assertIsNone(matrix_entry([0., 1000.], [0.5, 0.5], [1., 1.])['float32_bits'])


if __name__ == '__main__':
    unittest.main()
