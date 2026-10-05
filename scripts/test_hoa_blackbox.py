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
import struct
import unittest

from hoa_blackbox_lib.common import (BudgetStop, EvidenceError, ExperimentError, IdentityError,
    ROOT, canonical, digest, geometry, target_parts, tool_fingerprint)
from hoa_blackbox_lib.engine import Engine
from hoa_blackbox_lib.maths import infer_tree, inverse, matrix_entry, vmul
from hoa_blackbox_lib.native import Runner, import_batch
from hoa_blackbox_lib.store import Store, writer_lock
from hoa_blackbox_lib import wire


def make_words(seed, symbols=64):
    rng = random.Random(seed)
    words = ['']
    while len(words) < symbols:
        index = rng.choice([i for i, word in enumerate(words) if len(word) < 12])
        word = words.pop(index)
        words.extend([word+'0', word+'1'])
    rng.shuffle(words)
    return words


class FakeWriter:
    def __init__(self, quantization_bits=6, order=3):
        self.order = order
        self.n, self.symbols = geometry(order)["channels"], geometry(order)["symbols"]
        self.quantization_bits = quantization_bits
        self.zero = 1 << (quantization_bits-1)

    def fixed(self, values, gain=128, line=0, active=True):
        return canonical(dict(mode=0, values=values, gain=gain, line=line, active=active))

    def padded(self, mode, cluster, pattern, gain=128, extra=0):
        return canonical(dict(mode=mode, cluster=cluster, wire=pattern.ljust(600+extra, '0'), gain=gain, line=0))

    def coded(self, mode, cluster, values, words, gain=128, line=0, padding=0,
              groups=None, signs=None, active=True):
        groups = groups if groups is not None else [list(range(self.n))]
        raw = ''
        for start in range(0, self.symbols, self.n):
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
        return [payload, self.fixed([self.zero]*self.symbols, active=False)]


class FakeBackend:
    identity = dict(binary_sha256='fake', component_sha256='fake', os_version='synthetic', architecture='synthetic')

    def __init__(self, interrupt_at=None, fail_cluster=None, quantization_bits=6, order=3):
        self.order = order
        self.n, self.symbols = geometry(order)["channels"], geometry(order)["symbols"]
        self.calls = 0
        self.interrupt_at = interrupt_at
        self.fail_cluster = fail_cluster
        self.quantization_bits = quantization_bits
        self.zero = 1 << (quantization_bits-1)
        self.words = {(1, None): make_words(10, 2*self.zero)}
        self.words.update({(2, 0): make_words(20, 2*self.zero), (2, 1): make_words(21, 2*self.zero), (3, None): make_words(22, 2*self.zero)})
        self.words.update({(4, c): make_words(30+c, 2*self.zero) for c in range(4)})
        order = list(range(self.n))
        random.Random(13).shuffle(order)
        self.groups = {2: [order[:self.n//2-1], order[self.n//2-1:]], 3: [list(reversed(order))]}
        self.matrices = {c: [[(-1. if (((i+c)%self.n)&j).bit_count()%2 else 1.)/(self.n**0.5)
                             for j in range(self.n)] for i in range(self.n)] for c in range(4)}

        if self.n == 9:
            # A dense, invertible synthetic decimal-grid matrix; not a target.
            self.matrices = {c: [[struct.unpack('<f', struct.pack('<f', round((float((i+c)%self.n == j)-2/self.n)*1e6)/1e6))[0]
                                  for j in range(self.n)] for i in range(self.n)] for c in range(4)}

    def check(self, force=False):
        pass

    def capture(self, packets, folder):
        self.calls += 1
        if self.interrupt_at == self.calls:
            raise KeyboardInterrupt()
        samples = array('f')
        history = [0.]*self.n
        for packet in packets:
            request = json.loads(packet)
            mode, cluster = request['mode'], request.get('cluster')
            if mode == 4 and cluster == self.fail_cluster:
                raise ExperimentError('synthetic target failure')
            signs = [True]*self.n
            if mode == 0:
                q = request['values'][:self.n]
            else:
                raw = request['wire'];position = 0;q = [0]*self.n
                groups = self.groups.get(mode, [list(range(self.n))])
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
            coeff = [(v-self.zero)/self.zero for v in q]
            if mode == 3:
                coeff = [previous+v/self.zero*(1 if positive else -1) for previous, v, positive in zip(history, q, signs)]
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
        meta = dict(complete=True, frames=frames, channels=self.n, sample_rate=48000, encoding='f32le',
                    interleaved=True, all_finite=True, sha256=digest(pcm), start_frame=0,
                    layout=dict(value=dict(tag=geometry(self.order)['layout_tag'])), source_cookie_sha256=digest(wire.cookie(self.quantization_bits, self.order)))
        return {'native/pcm.f32le': pcm, 'native/replay.json': canonical(replay),
                'native/pcm.json': canonical(meta), 'native/processing-policy.json': canonical(policy),
                'input/packets.bin': b''.join(packets), 'input/cookie.bin': wire.cookie(self.quantization_bits, self.order)}


def synthetic_priors(order=3):
    backend = FakeBackend(order=order)
    return dict(schema_version=1, profile='synthetic-priors', order=order, huffman_words_included=False,
                component_sha256=backend.identity['component_sha256'], architecture=backend.identity['architecture'],
                matrices={str(c):dict(matrix_f32=[struct.unpack('<I', struct.pack('<f', v))[0] for row in m for v in row],
                                     empirical_half_width=5e-8) for c, m in backend.matrices.items()},
                groups={f'{mode}:{i}':dict(indices=g) for mode, groups in backend.groups.items() for i, g in enumerate(groups)})


def new_store(path, targets=('mode1',), calls=4096, quantization_bits=6, order=3):
    priors = synthetic_priors(order) if quantization_bits > 6 else None
    config = dict(schema_version=1, targets=list(targets), native_identity=FakeBackend.identity,
                  quantization_bits=quantization_bits, order=order, prior_sha256=digest(canonical(priors)) if priors else None,
                  tool_fingerprint=tool_fingerprint(), limits=dict(max_bytes=128*1024**2, max_calls=calls, min_free=0))
    store = Store.create(path, config)
    if priors:
        store.save_stage('_shared', 'priors', priors)
    return store


def fake_engine(store, backend, jobs=1):
    runner = Runner(store, backend, jobs=jobs)
    runner.writer = FakeWriter(store.config.get('quantization_bits', 6), store.config.get('order', 3))
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


class PrecisionTests(unittest.TestCase):
    def test_wider_precisions_all_modes_use_only_synthetic_prior_geometry(self):
        for precision in (7, 8, 9):
            with self.subTest(precision=precision), tempfile.TemporaryDirectory() as tmp:
                self.check_precision_flow(Path(tmp), precision)

    def check_precision_flow(self, tmp, precision):
        targets = ('mode1', 'mode2:0', 'mode2:1', 'mode3', 'mode4:2')
        store = new_store(tmp/'batch', targets, calls=20000, quantization_bits=precision)
        backend = FakeBackend(quantization_bits=precision)
        fake_engine(store, backend, jobs=4).run()
        self.assertTrue(all(t['status']=='validated' for t in store.summary()['targets']))
        for target, key in [('mode1',(1,None)),('mode2:0',(2,0)),('mode2:1',(2,1)),('mode3',(3,None)),('mode4:2',(4,2))]:
            book = store.stage(target, 'codebook')
            self.assertEqual(book['quantization_bits'], precision)
            self.assertEqual([e['codeword'] for e in book['entries']], backend.words[key])
        self.assertIsNone(store.stage('mode4:2','matrix'))
        self.assertTrue(store.stage('mode4:2','validation')['matrix_reused'])
        self.assertIsNone(store.stage('mode4:2','validation')['matrix_qualified'])
        before = backend.calls
        fake_engine(store, backend, jobs=2).run()
        self.assertEqual(backend.calls, before)
        store.close()

    def test_precision_binds_cookie_request_and_backend(self):
        self.assertEqual(sum((a^b).bit_count() for a,b in zip(wire.cookie(6),wire.cookie(7))), 1)
        cookies, requests = set(), set()
        for precision in (6, 7, 8, 9):
            zero = 1 << (precision-1)
            writer = wire.Writer(precision)
            packets = writer.frames(writer.fixed(wire.vector(2*zero-1, zero=zero)))
            cookies.add(wire.cookie(precision))
            # Hold the packet bytes fixed so cookie/config identity alone must differ.
            key, _ = wire.request(FakeBackend.identity, [b'probe', b'tail'], quantization_bits=precision)
            requests.add(key)
            self.assertEqual(len(packets[0])-len(wire.Writer(6).fixed(wire.vector(63))), 40*(precision-6))
            with tempfile.TemporaryDirectory() as tmp:
                store = new_store(Path(tmp)/'batch', quantization_bits=precision)
                with self.assertRaises(IdentityError):Runner(store, FakeBackend(quantization_bits=9 if precision != 9 else 6))
                store.close()
        self.assertEqual(len(cookies), 4)
        self.assertEqual(len(requests), 4)
        for precision in (5, 10):
            with self.assertRaises(ExperimentError):wire.Writer(precision)

    def test_untrusted_prior_content_is_rejected_before_sampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = new_store(Path(tmp)/'q7', quantization_bits=7)
            store.config['prior_sha256'] = 'wrong'
            backend = FakeBackend(quantization_bits=7)
            with self.assertRaises(EvidenceError):fake_engine(store, backend)
            self.assertEqual(backend.calls,0)
            store.close()

    def test_wide_synthetic_coordinate_direction(self):
        for symbols in (128, 256, 512):
            words = make_words(87, symbols)
            for direction in (1,-1):
                def query(pattern):
                    symbol = next(i for i,word in enumerate(words) if pattern.startswith(word))
                    return (symbol-symbols//2)/(23*direction),pattern
                entries, _, scale = infer_tree(query, coordinate=True, symbols=symbols)
                self.assertEqual(scale,23*direction)
                self.assertEqual([e['codeword'] for e in entries],words)



class OrderTests(unittest.TestCase):
    def test_order_binds_cookie_request_pcm_shape_and_backend(self):
        keys, cookies = set(), set()
        for order in (1, 2, 3):
            g = geometry(order)
            self.assertEqual(g['channels'], (order+1)**2)
            self.assertEqual(g['symbols'], g['channels']*g['components']*4)
            cookies.add(wire.cookie(6, order))
            key, request = wire.request(FakeBackend.identity, [b'probe', b'tail'], order=order)
            keys.add(key)
            self.assertEqual(request['signature']['channels'], g['channels'])
            with tempfile.TemporaryDirectory() as tmp:
                store = new_store(Path(tmp)/'source', order=order)
                backend = FakeBackend(order=order)
                engine = fake_engine(store, backend, jobs=2)
                cases = [('a', engine.writer.fixed(engine.vector(engine.zero)))]*2
                with engine.capture_batch('mode1', 'shape', cases) as output:
                    observed = list(output)
                self.assertEqual(backend.calls, 1)
                self.assertEqual(len(observed[0][1]), 2048*g['channels'])
                store.close()
                imported = new_store(Path(tmp)/'imported', order=order)
                self.assertEqual(import_batch(imported, Path(tmp)/'source'), 1)
                other = FakeBackend(order=2 if order != 2 else 1)
                with self.assertRaises(IdentityError):Runner(imported, other)
                imported.close()
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(cookies), 3)
        for order in (0, 4, True):
            with self.assertRaises(ExperimentError):wire.Writer(order=order)

    def test_lower_order_all_modes_and_qualified_geometry_export(self):
        from hoa_blackbox_lib.common import TARGETS
        from hoa_blackbox_lib.priors import export_batches
        for order in (1, 2):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'batch'
                store = new_store(path, TARGETS, order=order)
                backend = FakeBackend(order=order)
                fake_engine(store, backend, jobs=4).run()
                self.assertTrue(all(t['status']=='validated' for t in store.summary()['targets']), store.summary())
                for target in TARGETS:
                    mode, index = target_parts(target)
                    key = (mode, index if mode in (2,4) else None)
                    book = store.stage(target,'codebook')
                    self.assertEqual(book['order'],order)
                    self.assertEqual([e['codeword'] for e in book['entries']],backend.words[key])
                    validation = store.stage(target,'validation')
                    matrix = store.stage(target,'matrix') if mode==4 else None
                    if matrix:
                        expected = [struct.unpack('<I',struct.pack('<f',v))[0] for row in backend.matrices[index] for v in row]
                        self.assertEqual([e['float32_bits'] for e in matrix['entries']], expected)
                    comparison = dict(status='passed',eligible_codebook=True,eligible_matrix=mode==4,
                                      codebook_sha256=digest(canonical(book)),validation_sha256=digest(canonical(validation)))
                    if mode in (2,3):comparison['group_exact']=True
                    if matrix:comparison['matrix_sha256']=digest(canonical(matrix))
                    store.save_stage(target,'comparison',comparison)
                self.assertEqual(store.stage('mode2:0','validation')['checks'],store.stage('mode2:1','validation')['checks'])
                self.assertEqual(store.stage('mode2:0','validation')['joint_codebooks_sha256'],
                                 [digest(canonical(store.stage(f'mode2:{i}','codebook'))) for i in range(2)])
                before = backend.calls
                fake_engine(store, backend, jobs=2).run()
                self.assertEqual(before,backend.calls)
                store.close()
                priors = export_batches(order,[path])
                self.assertEqual(priors['order'],order)
                self.assertEqual(len(priors['matrices']),4)
                self.assertEqual(len(priors['groups']),3)
                self.assertNotIn('codeword',canonical(priors).decode())
                for c in range(4):self.assertEqual(len(priors['matrices'][str(c)]['matrix_f32']), (order+1)**4)
                with self.assertRaises(ExperimentError):export_batches(3,[path])
                comparison_file=path/'results/mode4-0/comparison.json'
                comparison_file.write_text('{}')
                with self.assertRaises(EvidenceError):export_batches(order,[path])

    def test_joint_nine_bit_books_fit_default_budget_with_imported_calibration(self):
        for order in (1,2):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as tmp:
                first = new_store(Path(tmp)/'calibration', quantization_bits=9, order=order)
                backend = FakeBackend(quantization_bits=9, order=order)
                engine = fake_engine(first, backend, jobs=4)
                engine.calibrate()
                first.close()
                store = new_store(Path(tmp)/'mode2', ('mode2:0','mode2:1'), quantization_bits=9, order=order)
                import_batch(store, Path(tmp)/'calibration')
                backend = FakeBackend(quantization_bits=9, order=order)
                fake_engine(store, backend, jobs=4).run()
                self.assertTrue(all(t['status']=='validated' for t in store.summary()['targets']))
                self.assertLess(backend.calls,4096)
                self.assertEqual(store.stage('mode2:0','validation')['checks'], store.stage('mode2:1','validation')['checks'])
                store.close()


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
    def test_search_boundary_accepts_32_bits_and_rejects_hidden_deeper_leaves(self):
        for symbols in (64, 128, 256, 512):
            for depth in (32, 33):
                words = ['']
                for n in range(depth):
                    word = '0'*n
                    words.remove(word)
                    words.extend((word+'0', word+'1'))
                while len(words) < symbols:
                    word = min(words, key=lambda w: (len(w), w))
                    words.remove(word)
                    words.extend((word+'0', word+'1'))
                def query(pattern):
                    # The real probe also has zero padding beyond bit 32.
                    raw = pattern.ljust(64, '0')
                    return next(i for i,w in enumerate(words) if raw.startswith(w)), pattern
                with self.subTest(symbols=symbols, depth=depth):
                    if depth == 32:
                        entries, _, _ = infer_tree(query, symbols=symbols)
                        self.assertEqual([e['codeword'] for e in entries], words)
                    else:
                        with self.assertRaises(ExperimentError):infer_tree(query, symbols=symbols)

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
