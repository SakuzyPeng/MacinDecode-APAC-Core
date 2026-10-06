#!/usr/bin/env python3
"""Independent HOA spatial-control mean measurements through public PCM.

The discovery path reads only public AAC tables. Mode-0 dyadic carriers cancel
candidate values; no grid, target dictionary or decoder-internal state is used.
"""
import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import struct
import subprocess
import sys
import time

from hoa_blackbox_lib.common import (ROOT, DEFAULT_LIMITS, BudgetStop, EvidenceError,
    ExperimentError, IdentityError, canonical, digest, file_digest, now, require)
from hoa_blackbox_lib.store import Store, atomic_file, writer_lock
import hoa_mean_blackbox_wire as wire
from hoa_mean_blackbox_wire import geometry, pcm_samples

POLICY = 'hoa-spatial-means-dyadic-cancellation-v1'
TARGET = 'spatial-means'
COMPONENT = Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')


def source_paths():
    return [Path(__file__).resolve(), ROOT/'scripts/hoa_mean_blackbox_wire.py', ROOT/'data/sq-codebooks.json',
            *(ROOT/'scripts/hoa_blackbox_lib'/name for name in
              ('__init__.py', 'common.py', 'store.py', 'wire.py'))]


def fingerprint():
    return digest(canonical(dict(policy=POLICY, python=sys.version,
        files={str(p.relative_to(ROOT)): file_digest(p) for p in source_paths()})))


def guard():
    def audit(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes)):
            return
        value = os.fsdecode(args[0])
        p = Path(value).absolute()
        forbidden = ('/data/hoa-', '/data/cac-', '/data/bwe2-', '/docs/local/',
                     'hoa_controls_vectors', 'hoa_salient_vectors', 'hoa_salient_subbands_vectors')
        if any(s in str(p) for s in forbidden) or p.name in ('comparison.json', 'reference.json'):
            raise EvidenceError('target/reference access forbidden in discovery: '+str(p))
    sys.addaudithook(audit)


def word(value):
    require(math.isfinite(value), 'nonfinite coefficient')
    return struct.unpack('<I', struct.pack('<f', value))[0]


def value(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def adjacent(bits, direction):
    require(direction in (-1, 1), 'invalid adjacent direction')
    if bits & 0x7fffffff == 0:
        return 1 if direction > 0 else 0x80000001
    result = bits + (direction if bits < 0x80000000 else -direction)
    require(math.isfinite(value(result)), 'adjacent value is nonfinite')
    return result


def ulp_exponent(v):
    bits = word(v) & 0x7fffffff
    exponent = bits >> 23
    return exponent-127-23 if exponent else -149


def partition(values, columns=None, chunks=5):
    """Group Float32 values so five q9 digits cover neighbors exactly."""
    columns = list(range(len(values))) if columns is None else list(columns)
    nonzero = sorted((j for j in columns if values[j]), key=lambda j: ulp_exponent(values[j]))
    groups = []
    while nonzero:
        shift = ulp_exponent(values[nonzero[0]])-1
        require(shift >= -48, 'coefficient below exact first-band carrier range')
        group = []
        while nonzero and abs(values[nonzero[0]]) < math.ldexp(1., shift+8*chunks-1):
            group.append(nonzero.pop(0))
        require(group, 'dyadic group cannot encode its coefficient')
        groups.append(group)
    zeros = [j for j in columns if not values[j]]
    if zeros:
        groups.append(zeros)
    return groups


def dyadic(values, components=5):
    """Exact signed base-256 digits, highest carrier first, no target data."""
    nonzero = [v for v in values if v]
    for v in values:
        require(value(word(v)) == v, 'coefficient is not exactly Float32')
    if not nonzero:
        return [0]*components, [[256]*len(values) for _ in range(components)]
    # Sparse mantissas (e.g. the known 2**-30 calibration) can still be encoded
    # when their neighboring Float32 values lie below the available wire grid.
    shift = max(-48, min(ulp_exponent(v) for v in nonzero)-1)
    integers = [int(math.ldexp(v, -shift)) for v in values]
    require(all(math.ldexp(float(v), shift) == original for v, original in zip(integers, values)),
            'coefficient is not on the selected dyadic grid')
    require(all(abs(v) < 1 << (8*components) for v in integers), 'dyadic dynamic range too wide')
    exponents = [shift+8*k+8 for k in reversed(range(components))]
    digits = [[256+(-1 if v < 0 else 1)*((abs(v) >> (8*k)) & 255) for v in integers]
              for k in reversed(range(components))]
    return exponents, digits


def cookie(order=10, means=False):
    raw = bytearray(wire.cookie(9, order))
    # Known cookie syntax: 166 header bits, full-order flag, then flag_a.
    at = 167
    require(raw[at//8] & (1 << (7-at%8)), 'unexpected default mean flag')
    if means:
        raw[at//8] &= ~(1 << (7-at%8))
    return bytes(raw)


class Writer:
    def __init__(self, order=10):
        self.order = order
        self.g = geometry(order)
        self.n, self.components = self.g['channels'], self.g['components']
        self.aac = json.loads((ROOT/'data/sq-codebooks.json').read_text())
        require(self.aac['long_offsets'][-1] == 1024, 'AAC carrier does not cover the full spectrum')

    def carrier(self, exponent, spectrum='flat', line=1):
        require(spectrum in ('flat', 'notch', 'impulse'), 'unknown carrier shape')
        require(0 <= line < 1024, 'spectral line outside carrier')
        sf_value = 100+4*exponent
        global_gain = min(255, max(0, sf_value))
        delta = sf_value-global_gain
        require(-60 <= delta <= 60, 'exact dyadic carrier exponent outside wire range')
        bands = len(self.aac['long_offsets'])-1
        section = wire.bits(1, 4)
        remaining = bands
        while remaining >= 31:
            section += wire.bits(31, 5)
            remaining -= 31
        section += wire.bits(remaining, 5)
        sf = self.aac['scalefactor']
        scalefactors = ''.join(wire.bits(sf['codes'][d+60], sf['bits'][d+60])
                              for d in [delta]+[0]*(bands-1))
        book = self.aac['spectral'][0]
        encoded = []
        for start in range(0, 1024, 4):
            vals = [int(i == line) if spectrum == 'impulse' else int(spectrum != 'notch' or i != line)
                    for i in range(start, start+4)]
            index = 0
            for v in vals:
                index = index*3+v+1
            encoded.append(wire.bits(book['codes'][index], book['bits'][index]))
        return ('10'+wire.bits(bands, 6)+wire.bits(global_gain, 8)+section+scalefactors
                +''.join(encoded)+'00')

    def packet(self, values, spectrum='flat', line=1, padding=0, force_active=True):
        require(len(values) == self.n, 'wrong coefficient dimension')
        exponents, digits = dyadic(values, self.components)
        carriers = []
        for i, (exponent, row) in enumerate(zip(exponents, digits)):
            active = any(q != 256 for q in row) or (force_active and i == 0)
            carriers.append(self.carrier(exponent, spectrum, line) if active else '0')
        payload = ''.join(wire.bits(q, 9) for row in digits for _ in range(4) for q in row)
        bits = '0100'+''.join(carriers)+'0'*(self.n-self.components)+'1000'+payload
        bits += '0'*(-len(bits) % 8)+'0'
        return wire.pack(bits)+bytes(padding)

    def bundle(self, folder, packets, means):
        wire.write_bundle(folder, packets, 9, self.order)
        cfg = cookie(self.order, means)
        manifest = json.loads((folder/'manifest.json').read_bytes())
        manifest['file']['cookie']['value'] = dict(bytes=len(cfg), sha256=digest(cfg))
        (folder/'cookie.bin').write_bytes(cfg)
        (folder/'manifest.json').write_bytes(canonical(manifest))


def volume_identity(path):
    path = Path(path).resolve()
    require(path.is_dir(), 'evidence volume is not mounted', EvidenceError)
    raw = subprocess.check_output(['diskutil', 'info', '-plist', str(path)], timeout=15)
    info = plistlib.loads(raw)
    require(info.get('Mounted', True) and info.get('MountPoint'), 'volume is not mounted', EvidenceError)
    root = Path(info['MountPoint'])
    return dict(mount=str(root), uuid=info['VolumeUUID'], device=root.stat().st_dev)


def check_volume(pin):
    root = Path(pin['mount'])
    require(root.is_dir() and root.stat().st_dev == pin['device'], 'evidence volume disconnected or replaced', EvidenceError)
    if str(root).startswith('/Volumes/'):
        require(os.path.ismount(root), 'external evidence mount disappeared', EvidenceError)


class MeanStore(Store):
    def __init__(self, out, readonly=False):
        manifest = Path(out)/'manifest.json'
        require(manifest.is_file(), 'batch manifest unavailable', EvidenceError)
        pin = json.loads(manifest.read_bytes())['volume']
        fresh = volume_identity(pin['mount'])
        require(fresh['uuid'] == pin['uuid'], 'evidence volume UUID changed', EvidenceError)
        # A remount may receive another device number; bind this open session.
        self.pin = fresh
        super().__init__(out, readonly)

    def reserve(self, size):
        check_volume(self.pin)
        return super().reserve(size)

    def read_blob(self, identity):
        check_volume(self.pin)
        return super().read_blob(identity)

    def set_meta(self, key, v):
        check_volume(self.pin)
        return super().set_meta(key, v)

    def use(self, *args):
        check_volume(self.pin)
        return super().use(*args)

    def finish(self, *args):
        check_volume(self.pin)
        return super().finish(*args)

    def query(self, key):
        row = self.db.execute('SELECT state,receipt,request FROM queries WHERE key=?', (key,)).fetchone()
        if row and row['state'] == 'passed':
            request, receipt = json.loads(row['request']), json.loads(row['receipt'])
            require(digest(canonical(request)) == key and receipt['key'] == key
                    and request['native_identity'] == receipt['native_identity'] == self.config['native_identity'],
                    'query identity binding differs', EvidenceError)
            artifacts = {name:self.read_blob(sha) for name,sha in receipt['artifacts'].items()}
            require(receipt['pcm_sha256'] == receipt['artifacts']['native/pcm.f32le'],
                    'PCM receipt binding differs', EvidenceError)
            validate_public(artifacts, request)
            self.hits += 1
            return receipt
        return None

    def begin(self, key, request):
        count = self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        if count >= self.config['limits']['max_calls']:
            raise BudgetStop('native call budget exhausted')
        sig = request['signature']
        require(sig['channels'] == geometry(sig['order'])['channels']
                and sig['frames'] in (2048, 3072, 4096), 'invalid capture geometry', EvidenceError)
        raw = sig['channels']*sig['frames']*4
        size = max(2*1024**2, 3*raw+2*sum(p['bytes'] for p in request['packets'])+256*1024)
        self.reserve(size)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO queries(key,request,state) VALUES (?,?,?)',
                            (key, canonical(request).decode(), 'pending'))
            cursor = self.db.execute('INSERT INTO attempts(query_key,state,started) VALUES (?,?,?)',
                                     (key, 'started', now()))
            attempt = cursor.lastrowid
        self.capture_reservations[attempt] = size
        folder = self.out/'attempts'/str(attempt)
        folder.mkdir()
        atomic_file(folder/'request.json', canonical(dict(key=key, request=request)))
        return attempt, folder

    def job(self, target, status, error=None):
        check_volume(self.pin)
        require(target == TARGET, 'unknown measurement target')
        with self.db:
            self.db.execute('UPDATE jobs SET status=?,error=? WHERE target=?',(status,error,target))

    def summary(self):
        result=super().summary()
        previous=self.config.get('prior_resource_usage',{})
        result['experiment_native_calls']=result['native_calls']+previous.get('native_calls',0)
        result['experiment_evidence_bytes']=result['added_bytes']+previous.get('evidence_bytes',0)
        return result


def native_identity(binary):
    require(sys.platform == 'darwin', 'native measurements require macOS')
    require(not any(k.startswith('DYLD_') and v for k, v in os.environ.items()), 'injected decoder environment')
    return dict(binary_sha256=file_digest(binary), component_sha256=file_digest(COMPONENT),
                os_version=subprocess.check_output(['sw_vers'], text=True).strip(), architecture=platform.machine())


def validate_public(artifacts, request):
    sig = request['signature']
    require(digest(artifacts['input/cookie.bin']) == request['cookie_sha256']
            == digest(cookie(sig['order'], sig['mean_enabled'])), 'input cookie differs', EvidenceError)
    require(sig['channels'] == geometry(sig['order'])['channels'], 'PCM geometry differs', EvidenceError)
    offset = 0
    for packet in request['packets']:
        raw_packet = artifacts['input/packets.bin'][offset:offset+packet['bytes']]
        require(len(raw_packet) == packet['bytes'] and digest(raw_packet) == packet['sha256'],
                'input packet differs', EvidenceError)
        offset += packet['bytes']
    require(offset == len(artifacts['input/packets.bin']), 'input packet span differs', EvidenceError)
    raw = artifacts['native/pcm.f32le']
    pcm_samples(raw, sig['channels'])
    replay = json.loads(artifacts['native/replay.json'])
    pcm = json.loads(artifacts['native/pcm.json'])
    policy = json.loads(artifacts['native/processing-policy.json'])
    require(len(raw) == sig['frames']*sig['channels']*4, 'PCM length differs', EvidenceError)
    require(replay['complete'] and replay['backend'] == 'AudioConverterFillComplexBuffer'
        and replay['saved_frames'] == sig['frames'] and replay['consumed_packets'] == sig['packets']
        and replay['input_batch_packets'] == 1 and replay['original_source_accessed'] is False
        and replay['processing_policy'] == 'drc-off', 'replay contract differs', EvidenceError)
    require(pcm['complete'] and pcm['frames'] == sig['frames'] and pcm['channels'] == sig['channels']
        and pcm['sample_rate'] == 48000 and pcm['encoding'] == 'f32le' and pcm['interleaved']
        and pcm['all_finite'] and pcm['sha256'] == digest(raw) and pcm['start_frame'] == 0
        and pcm['source_cookie_sha256'] == request['cookie_sha256']
        and pcm['layout']['value']['tag'] == sig['layout_tag'], 'PCM contract differs', EvidenceError)
    require(policy['verified'] and policy['policy'] == 'drc-off'
        and policy['request_order'] == 'properties_then_magic_cookie_then_initial_reset'
        and policy['initial_reset']['os_status'] == 0, 'processing policy differs', EvidenceError)
    for name in ('mdrc', '^pro', 'ptlc'):
        require(policy['readback'][name] == dict(error=None, value=0)
            and replay['decoder_settings'][name] == dict(error=None, value=0), 'processing readback differs', EvidenceError)


class Runner:
    def __init__(self, store):
        self.store = store
        self.binary = Path(store.config['binary'])
        self.identity = native_identity(self.binary)
        require(self.identity == store.config['native_identity'], 'native identity changed', IdentityError)
        require(fingerprint() == store.config['tool_fingerprint'], 'measurement producer changed', IdentityError)
        self.stamps = self.file_stamps()

    def file_stamps(self):
        return [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino)
                for p in [self.binary, COMPONENT, *source_paths()]]

    def check(self):
        check_volume(self.store.pin)
        stamps = self.file_stamps()
        if stamps != self.stamps:
            require(native_identity(self.binary) == self.identity, 'native identity changed', IdentityError)
            require(fingerprint() == self.store.config['tool_fingerprint'], 'measurement producer changed', IdentityError)
            self.stamps = stamps

    def probe(self, stage, label, values, *, means=False, order=10, spectrum='flat',
              line=1, frames=2, padding=0, repeat=False, force_active=True):
        self.check()
        w = Writer(order)
        require(frames in (2, 3, 4), 'invalid packet count')
        packet = w.packet(values, spectrum, line, padding, force_active)
        packets = [packet]*frames
        sig = dict(wire.SIGNATURE, order=order, channels=w.n, layout_tag=w.g['layout_tag'],
                   quantization_bits=9, packets=frames, frames=1024*frames, mean_enabled=means)
        request = dict(native_identity=self.identity, signature=sig,
            cookie_sha256=digest(cookie(order, means)),
            packets=[dict(sha256=digest(p), bytes=len(p), frames=1024) for p in packets],
            replicate=f'{stage}/{label}' if repeat else '')
        key = digest(canonical(request))
        self.store.use(TARGET, stage, label, key)
        receipt = self.store.query(key)
        if receipt:
            return key, pcm_samples(self.store.read_blob(receipt['pcm_sha256']), w.n)
        attempt, folder = self.store.begin(key, request)
        try:
            w.bundle(folder/'input', packets, means)
            output_mib = (1024*frames*w.n*4+65536+128+1024**2-1)//1024**2
            command = [str(self.binary), 'replay', str(folder/'input'), '--out', str(folder/'native'),
                '--frames', str(1024*frames), '--input-batch-packets', '1', '--processing-policy', 'drc-off',
                '--max-output-mib', str(output_mib)]
            atomic_file(folder/'command.json', canonical(command))
            start = time.monotonic()
            try:
                p = subprocess.run(command, capture_output=True, timeout=30)
                (folder/'stdout.txt').write_bytes(p.stdout)
                (folder/'stderr.txt').write_bytes(p.stderr)
                atomic_file(folder/'process.json', canonical(dict(returncode=p.returncode, seconds=time.monotonic()-start)))
                require(p.returncode == 0, 'native replay failed: '+p.stderr.decode(errors='replace')[-1400:])
            except subprocess.TimeoutExpired as error:
                (folder/'stdout.txt').write_bytes(error.stdout or b'')
                (folder/'stderr.txt').write_bytes(error.stderr or b'')
                atomic_file(folder/'process.json', canonical(dict(returncode=None, timeout_seconds=30)))
                raise ExperimentError('native replay exceeded 30 seconds') from error
            self.check()
            artifacts = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            validate_public(artifacts, request)
            objects = {name:self.store.blob(raw) for name,raw in sorted(artifacts.items())}
            receipt = dict(key=key, artifacts=objects, pcm_sha256=objects['native/pcm.f32le'],
                           native_identity=self.identity, returncode=0)
            self.store.finish(attempt, receipt)
            print(json.dumps(dict(stage=stage, label=label, native_call=attempt)), file=sys.stderr, flush=True)
            return key, pcm_samples(artifacts['native/pcm.f32le'], w.n)
        except BaseException as error:
            check_volume(self.store.pin)
            self.store.fail(attempt, str(error), interrupted=isinstance(error, (KeyboardInterrupt, SystemExit)))
            raise

    def pcm(self, key):
        receipt = self.store.query(key)
        require(receipt is not None, 'missing successful observation', EvidenceError)
        return pcm_samples(self.store.read_blob(receipt['pcm_sha256']), 121)


def projection(pcm, reference, channels=121):
    require(len(pcm) == len(reference), 'projection length differs')
    values, residuals = [], []
    for j in range(channels):
        x, y = pcm[j::channels], reference[j::channels]
        energy = math.fsum(v*v for v in y)
        require(energy > 0, 'unobservable calibration channel')
        gain = math.fsum(a*b for a,b in zip(x,y))/energy
        residual = math.sqrt(math.fsum((a-gain*b)**2 for a,b in zip(x,y))/energy)
        values.append(gain)
        residuals.append(residual)
    return values, residuals


class Engine:
    def __init__(self, store):
        self.store, self.runner = store, Runner(store)

    def calibration(self):
        saved = self.store.stage(TARGET, 'calibration')
        if saved is not None:
            return saved
        key, ref = self.runner.probe('calibration', 'unit', [1.]*121)
        checks = []
        for v in (-1., -.5, 0., .5, .125, 2.**-10, 2.**-20, 2.**-30):
            evidence, pcm = self.runner.probe('calibration', str(v), [v]*121)
            estimated, residual = projection(pcm, ref)
            error = max(abs(x-v) for x in estimated)
            require(error <= max(abs(v)*2e-6, 1e-40), 'flat carrier calibration inaccurate')
            require(max(residual) <= max(abs(v)*2e-6, 1e-40), 'flat carrier shape differs')
            if not v:
                require(not any(pcm), 'disabled-mean zero is not zero')
            checks.append(dict(value=v, evidence=evidence, error=error, residual=max(residual)))
        result = dict(policy=POLICY, reference=key, checks=checks, channels=121,
                      method='known exact dyadic full-band mode-0 spectra', old_values_consulted=False)
        self.store.save_stage(TARGET, 'calibration', result)
        return result

    def candidate(self):
        saved = self.store.stage(TARGET, 'candidate')
        if saved is not None:
            return saved
        calibration = self.calibration()
        ref = self.runner.pcm(calibration['reference'])
        evidence, observed = self.runner.probe('estimate', 'mean-only', [0.]*121, means=True)
        estimated, residuals = projection(observed, ref)
        require(all(r <= max(abs(v)*2e-6, 1e-35) for v,r in zip(estimated,residuals)),
                'mean output is not a constant-spectrum calibration response')
        bits = [word(v) for v in estimated]
        resolved, history = set(), []
        for iteration in range(6):
            pending = [j for j in range(121) if j not in resolved]
            if not pending:
                break
            values = [value(b) for b in bits]
            for group_no, group in enumerate(partition(values, pending)):
                trial = [-values[j] if j in group else 0. for j in range(121)]
                key, pcm = self.runner.probe('refinement', f'{iteration}:{group_no}', trial, means=True)
                correction, residual = projection(pcm, ref)
                rows = []
                for j in group:
                    zero = not any(pcm[j::121])
                    after = bits[j] if zero else word(values[j]+correction[j])
                    rows.append(dict(channel=j, before=bits[j], correction=correction[j],
                                     after=after, zero_pcm=zero, residual=residual[j]))
                    if zero:
                        resolved.add(j)
                    else:
                        require(after != bits[j], f'cancellation cannot refine channel {j}')
                        bits[j] = after
                history.append(dict(evidence=key, iteration=iteration, rows=rows))
        require(len(resolved) == 121, 'not all channels cancelled within six iterations')
        entries = [dict(channel=j, estimate=estimated[j], initial_shape_residual=residuals[j],
                        candidate_bits=[bits[j]] if bits[j]&0x7fffffff else [0, 0x80000000],
                        float32_bits=bits[j] if bits[j]&0x7fffffff else None,
                        zero_sign_ambiguous=not bool(bits[j]&0x7fffffff)) for j in range(121)]
        result = dict(schema_version=1, profile=POLICY, entries=entries, initial_evidence=evidence,
            calibration_sha256=digest(canonical(calibration)), refinement=history,
            method='exact dyadic cancellation; Float32 neighbors reserved for held-out validation',
            grid_assumed=False, old_values_consulted=False)
        self.store.save_stage(TARGET, 'candidate', result)
        return result

    def validate(self):
        saved = self.store.stage(TARGET, 'validation')
        if saved is not None:
            return saved
        candidate = self.candidate()
        values = [value(e['candidate_bits'][0]) for e in candidate['entries']]
        ref = self.runner.pcm(self.calibration()['reference'])
        checks = []
        groups = partition(values)
        for group_no, group in enumerate(groups):
            for repetition in range(2):
                trial = [-values[j] if j in group else 0. for j in range(121)]
                key, pcm = self.runner.probe('validation', f'cancel:{group_no}:{repetition}', trial,
                    means=True, padding=128 if repetition else 0, repeat=True)
                require(all(not any(pcm[j::121]) for j in group), 'independent cancellation failed')
                checks.append(dict(kind='cancellation', columns=group, evidence=key, exact_zero=True))
            nonzero = [j for j in group if values[j]]
            for direction in (-1, 1):
                neighbors = {j:value(adjacent(word(values[j]), direction)) for j in nonzero}
                if not nonzero:
                    continue
                trial = [-neighbors[j] if j in neighbors else 0. for j in range(121)]
                key, pcm = self.runner.probe('validation', f'neighbor:{group_no}:{direction}', trial, means=True, repeat=True)
                measured, residual = projection(pcm, ref)
                errors = []
                for j in nonzero:
                    expected = values[j]-neighbors[j]
                    require(any(pcm[j::121]), 'adjacent Float32 candidate is indistinguishable')
                    require(measured[j]*expected > 0 and abs(measured[j]-expected) <= abs(expected)*2e-6
                            and residual[j] <= abs(expected)*2e-6, 'adjacent Float32 residual differs')
                    errors.append(abs(measured[j]-expected)/abs(expected))
                checks.append(dict(kind='adjacent_float32', columns=nonzero, direction=direction,
                                   evidence=key, max_relative_error=max(errors)))
            for line in (1, 37, 511):
                negative = [-values[j] if j in group else 0. for j in range(121)]
                positive = [-v for v in negative]
                a, pcm = self.runner.probe('validation', f'notch:{group_no}:{line}', negative,
                    means=True, spectrum='notch', line=line, repeat=True)
                b, control = self.runner.probe('validation', f'impulse:{group_no}:{line}', positive,
                    spectrum='impulse', line=line, repeat=True)
                max_relative, equal = 0., True
                for j in group:
                    x, y = pcm[j::121], control[j::121]
                    energy = math.fsum(v*v for v in y)
                    error = math.fsum((v-w)**2 for v,w in zip(x,y))
                    require(error <= energy*4e-12 if energy else error == 0, 'held-out spectral-line validation failed')
                    max_relative = max(max_relative, math.sqrt(error/energy) if energy else 0.)
                    equal &= x.tobytes() == y.tobytes()
                checks.append(dict(kind='heldout_spectral_line', line=line, columns=group,
                                   evidence=[a,b], pcm_bit_identical=equal, relative_rms=max_relative))
        for order in (3, 9):
            n = geometry(order)['channels']
            for group_no, group in enumerate(partition(values[:n])):
                trial = [-values[j] if j in group else 0. for j in range(n)]
                key, pcm = self.runner.probe('validation', f'order:{order}:{group_no}', trial,
                    means=True, order=order, frames=3, repeat=True)
                require(all(not any(pcm[j::n]) for j in group), 'cross-order cancellation differs')
                checks.append(dict(kind='cross_order', order=order, columns=group, evidence=key, exact_zero=True))
        qualified = sum(e['float32_bits'] is not None for e in candidate['entries'])
        result = dict(status='passed' if qualified == 121 else 'partial', qualified=qualified,
            candidate_sha256=digest(canonical(candidate)), checks=checks,
            old_values_consulted=False, tolerance_relative_rms=2e-6,
            qualification='exact cancellation in independent decoders and distinguishable adjacent Float32 values')
        self.store.save_stage(TARGET, 'validation', result)
        return result


def compare(out):
    with writer_lock(out):
        store = MeanStore(out)
        try:
            store.audit_evidence()
            candidate, validation = (store.stage(TARGET, name) for name in ('candidate', 'validation'))
            require(candidate is not None and validation is not None, 'frozen candidate and validation required')
            require(validation['candidate_sha256'] == digest(canonical(candidate)), 'validation candidate binding differs')
            saved = store.stage(TARGET, 'comparison')
            if saved is not None:
                return saved
            path = ROOT/'data/hoa-spatial-controls-format-v1.json'
            original = path.read_bytes()
            reference = json.loads(original)['mean_coefficients_f32']
            require(len(reference) == len(candidate['entries']) == 121, 'reference dimensions differ')
            differences = [dict(channel=e['channel'], candidate=e['float32_bits'], reference=reference[e['channel']])
                for e in candidate['entries'] if e['float32_bits'] is not None and e['float32_bits'] != reference[e['channel']]]
            unresolved = [e['channel'] for e in candidate['entries'] if e['float32_bits'] is None]
            result = dict(candidate_sha256=digest(canonical(candidate)), validation_sha256=digest(canonical(validation)),
                reference_file_sha256=digest(original), differences=differences, unresolved=unresolved,
                matched=121-len(differences)-len(unresolved), eligible=not differences and not unresolved and validation['status']=='passed')
            store.save_stage(TARGET, 'comparison', result)
            return result
        finally:
            store.close()


def import_raw(destination, paths):
    """Only validated raw observations; no old stages, candidates or commands."""
    imported = 0
    for path in paths:
        source = MeanStore(path, readonly=True)
        try:
            require(source.config['native_identity'] == destination.config['native_identity'],
                    'imported native identity differs', IdentityError)
            source.audit_evidence()
            for row in source.db.execute("SELECT key,request,receipt FROM queries WHERE state='passed'"):
                request, receipt = json.loads(row['request']), json.loads(row['receipt'])
                artifacts = {name:source.read_blob(sha) for name,sha in receipt['artifacts'].items()}
                validate_public(artifacts,request)
                for sha in receipt['artifacts'].values():
                    original=source.db.execute('SELECT * FROM objects WHERE sha=?',(sha,)).fetchone()
                    require(destination.external(original['path'], offset=original['offset'],
                        size=original['size'], compressed=original['kind'] in ('gzip','external-gzip')) == sha,
                        'imported evidence identity differs', EvidenceError)
                imported += destination.imported_query(row['key'],request,receipt)
        finally:
            source.close()
    return imported


def evidence_ancestors(paths):
    result, visiting, seen = [], set(), set()
    def visit(path):
        path=Path(path).resolve()
        require(path not in visiting, 'cyclic evidence ancestry', EvidenceError)
        if path in seen:
            return
        visiting.add(path)
        source=MeanStore(path,readonly=True)
        try:
            parents=source.config.get('prior_evidence',[])
        finally:
            source.close()
        for parent in parents:
            visit(parent)
        visiting.remove(path)
        seen.add(path)
        result.append(path)
    for path in paths:
        visit(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'resume', 'status', 'compare'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--volume', type=Path)
    parser.add_argument('--import-evidence', type=Path, nargs='*', default=[])
    parser.add_argument('--until', choices=('calibration', 'candidate', 'validation'), default='validation')
    args = parser.parse_args()
    if args.command == 'compare':
        print(json.dumps(compare(args.out)))
        return
    guard()
    if args.command == 'run':
        require(args.binary is not None and args.volume is not None, '--binary and --volume are required')
        pin = volume_identity(args.volume)
        require(args.out.resolve().is_relative_to(Path(pin['mount'])), 'output is outside pinned volume')
        args.import_evidence=evidence_ancestors(args.import_evidence)
        prior_calls, prior_bytes = 0, 0
        for path in args.import_evidence:
            prior=MeanStore(path,readonly=True)
            try:
                summary=prior.summary()
                prior_calls += summary['native_calls']
                prior_bytes += summary['added_bytes']
            finally:
                prior.close()
        remaining=dict(DEFAULT_LIMITS)
        remaining['max_calls']-=prior_calls
        remaining['max_bytes']-=prior_bytes
        require(remaining['max_calls']>0 and remaining['max_bytes']>0,'prior evidence exhausted the experiment budget')
        config = dict(schema_version=1, created_utc=now(), targets=[TARGET], order=10, quantization_bits=9,
            binary=str(args.binary.resolve()), native_identity=native_identity(args.binary), native_jobs=1,
            volume=pin, policy=POLICY, tool_fingerprint=fingerprint(), limits=remaining,
            prior_evidence=[str(p.resolve()) for p in args.import_evidence],
            prior_resource_usage=dict(native_calls=prior_calls,evidence_bytes=prior_bytes),
            code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            source_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)))
        check_volume(pin)
        store = MeanStore.create(args.out, config)
        try:
            store.set_meta('source_snapshot', {str(p.relative_to(ROOT)):store.blob(p.read_bytes()) for p in source_paths()})
            store.set_meta('imported_observations',import_raw(store,args.import_evidence))
        finally:
            store.close()
    if args.command == 'status':
        store = MeanStore(args.out, readonly=True)
        try:
            print(json.dumps(store.summary()))
        finally:
            store.close()
        return
    with writer_lock(args.out):
        store = MeanStore(args.out)
        try:
            require(fingerprint() == store.config['tool_fingerprint'], 'producer changed; preserve batch and use explicit raw import', IdentityError)
            store.recover()
            store.audit_evidence()
            store.set_meta('batch_status', 'running')
            store.job(TARGET,'running')
            engine = Engine(store)
            result = getattr(engine, 'validate' if args.until == 'validation' else args.until)()
            store.set_meta('batch_status', args.until+'_complete')
            store.job(TARGET, result.get('status','frozen') if args.until=='validation' else args.until+'_complete')
            print(json.dumps(dict(stage=args.until, result=result, resources=store.summary())))
        except BaseException as error:
            check_volume(store.pin)
            store.set_meta('batch_status', 'budget_exhausted' if isinstance(error, BudgetStop) else 'stopped')
            store.set_meta('stop_reason', str(error))
            store.job(TARGET,'stopped',str(error))
            raise
        finally:
            store.close()


if __name__ == '__main__':
    main()
