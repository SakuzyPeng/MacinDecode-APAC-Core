"""Public AudioConverter runner and read-only import of original observations."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import threading

from .common import (EvidenceError, ExperimentError, IdentityError, canonical, digest,
                     file_digest, now, pcm_samples, require)
from . import wire

COMPONENT = Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
IDENTITY_FIELDS = ('binary_sha256', 'component_sha256', 'os_version', 'architecture')


def validate_public(artifacts, frames=2048, quantization_bits=6):
    try:
        replay = json.loads(artifacts['native/replay.json'])
        pcm = json.loads(artifacts['native/pcm.json'])
        policy = json.loads(artifacts['native/processing-policy.json'])
        raw = artifacts['native/pcm.f32le']
    except (KeyError, ValueError) as error:
        raise EvidenceError('incomplete native sidecars') from error
    pcm_samples(raw)
    require(frames in (2048, 3072, 4096) and len(raw) == frames*16*4,
            'native PCM length differs from request', EvidenceError)
    require(replay['complete'] and replay['backend'] == 'AudioConverterFillComplexBuffer'
            and replay['saved_frames'] == frames and replay['consumed_packets'] == frames//1024
            and replay['input_batch_packets'] == 1 and replay['original_source_accessed'] is False
            and replay['processing_policy'] == 'drc-off', 'native replay contract differs', EvidenceError)
    require(pcm['complete'] and pcm['frames'] == frames and pcm['channels'] == 16
            and pcm['sample_rate'] == 48000 and pcm['encoding'] == 'f32le'
            and pcm['interleaved'] and pcm['all_finite'] and pcm['sha256'] == digest(raw)
            and pcm['start_frame'] == 0 and pcm['layout']['value']['tag'] == wire.SIGNATURE['layout_tag']
            and pcm['source_cookie_sha256'] == digest(wire.cookie(quantization_bits)),
            'native PCM contract differs', EvidenceError)
    require(policy['verified'] and policy['policy'] == 'drc-off'
            and policy['request_order'] == 'properties_then_magic_cookie_then_initial_reset'
            and policy['initial_reset']['os_status'] == 0, 'processing policy not verified', EvidenceError)
    for name in ('mdrc', '^pro', 'ptlc'):
        require(policy['readback'][name] == dict(error=None, value=0)
                and replay['decoder_settings'][name] == dict(error=None, value=0),
                'processing-off readback differs', EvidenceError)
    require(replay['decoder_settings']['processing_policy']['value']['verified'],
            'final processing policy not verified', EvidenceError)


class NativeBackend:
    def __init__(self, binary, quantization_bits=6):
        require(sys.platform == 'darwin', 'native measurement requires macOS')
        require(quantization_bits in (6, 7, 8, 9), 'unsupported quantization width')
        self.quantization_bits = quantization_bits
        self.binary = Path(binary).resolve()
        self.identity = self.collect_identity()
        self.stamps = self.file_stamps()
        self.identity_lock = threading.Lock()
        self.process_lock = threading.Lock()
        self.processes = set()

    def collect_identity(self):
        require(not any(k.startswith('DYLD_') and v for k, v in os.environ.items()), 'injected decoder environment', IdentityError)
        return dict(binary_sha256=file_digest(self.binary), component_sha256=file_digest(COMPONENT),
                    os_version=subprocess.check_output(['sw_vers'], text=True).strip(), architecture=platform.machine())

    def file_stamps(self):
        return [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in (self.binary, COMPONENT)]

    def check(self, force=False):
        with self.identity_lock:
            stamps = self.file_stamps()
            if force or stamps != self.stamps:
                require(self.collect_identity() == self.identity, 'native environment changed', IdentityError)
                self.stamps = stamps

    def cancel(self):
        with self.process_lock:
            for proc in self.processes:
                if proc.poll() is None:
                    proc.kill()

    def capture(self, packets, folder):
        wire.write_bundle(folder / 'input', packets, self.quantization_bits)
        command = [str(self.binary), 'replay', str(folder / 'input'), '--out', str(folder / 'native'),
                   '--frames', str(1024*len(packets)), '--input-batch-packets', '1', '--processing-policy', 'drc-off', '--max-output-mib', '1']
        (folder / 'command.json').write_bytes(canonical(command))
        start = time.monotonic()
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self.process_lock:
            self.processes.add(proc)
        try:
            stdout, stderr = proc.communicate(timeout=30)
            code = proc.returncode
        except subprocess.TimeoutExpired as error:
            proc.kill()
            stdout, stderr = proc.communicate()
            (folder / 'stdout.txt').write_bytes(stdout)
            (folder / 'stderr.txt').write_bytes(stderr)
            raise ExperimentError('native replay exceeded 30 seconds') from error
        except BaseException:
            proc.kill()
            stdout, stderr = proc.communicate()
            (folder / 'stdout.txt').write_bytes(stdout)
            (folder / 'stderr.txt').write_bytes(stderr)
            raise
        finally:
            with self.process_lock:
                self.processes.discard(proc)
        (folder / 'stdout.txt').write_bytes(stdout)
        (folder / 'stderr.txt').write_bytes(stderr)
        (folder / 'process.json').write_bytes(canonical(dict(returncode=code, seconds=time.monotonic() - start)))
        require(code == 0, 'native replay failed: ' + stderr.decode(errors='replace')[-1600:])
        artifacts = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        validate_public(artifacts, 1024*len(packets), self.quantization_bits)
        self.check()
        return artifacts


class Runner:
    def __init__(self, store, backend, jobs=1):
        self.store, self.backend = store, backend
        require(type(jobs) is int and 1 <= jobs <= 4, 'jobs must be between 1 and 4')
        self.jobs = jobs
        self.batch_active = False
        require(backend.identity == store.config['native_identity'], 'batch native identity differs', IdentityError)
        self.quantization_bits = store.config.get('quantization_bits', 6)
        require(getattr(backend, 'quantization_bits', 6) == self.quantization_bits, 'native quantization width differs', IdentityError)
        self.writer = wire.Writer(self.quantization_bits)

    def prepare(self, target, stage, label, payload, replicate=''):
        self.backend.check()
        packets = self.writer.frames(payload)
        key, request = wire.request(self.backend.identity, packets, replicate, self.quantization_bits)
        self.store.use(target, stage, label, key)
        return key, request, packets

    def accept(self, attempt, key, artifacts, frames):
        self.backend.check()
        validate_public(artifacts, frames, self.quantization_bits)
        objects = {name: self.store.blob(raw) for name, raw in sorted(artifacts.items())}
        receipt = dict(key=key, artifacts=objects, pcm_sha256=objects['native/pcm.f32le'],
                       native_identity=self.backend.identity, returncode=0)
        self.store.finish(attempt, receipt)
        return key, pcm_samples(artifacts['native/pcm.f32le'])

    def probe(self, target, stage, label, payload, replicate=''):
        require(not self.batch_active, 'nested native capture during a batch')
        key, request, packets = self.prepare(target, stage, label, payload, replicate)
        cached = self.store.query(key)
        if cached:
            return key, pcm_samples(self.store.read_blob(cached['pcm_sha256']))
        attempt, folder = self.store.begin(key, request)
        try:
            artifacts = self.backend.capture(packets, folder)
            return self.accept(attempt, key, artifacts, 1024*len(packets))
        except BaseException as error:
            self.store.fail(attempt, str(error), interrupted=isinstance(error, (KeyboardInterrupt, SystemExit)))
            raise

    def batch(self, requests):
        from .scheduler import capture_batch
        return capture_batch(self, requests)

    def pcm(self, key):
        receipt = self.store.query(key)
        require(receipt is not None, 'referenced observation is incomplete', EvidenceError)
        return pcm_samples(self.store.read_blob(receipt['pcm_sha256']))


def bounded_member(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), 'external reference escapes import root', EvidenceError)
    require(path.is_file() and path.stat().st_size <= 2 * 1024 * 1024, 'invalid import member', EvidenceError)
    return path


def import_legacy(store, root):
    root = Path(root).resolve()
    env = json.loads(bounded_member(root, 'environment.json').read_text())
    require({key: env[key] for key in IDENTITY_FIELDS} == store.config['native_identity'],
            'legacy native identity differs', IdentityError)
    require(file_digest(bounded_member(root, 'probe-source.py')) == env['script_sha256'], 'legacy source snapshot differs', EvidenceError)
    count = 0
    # Only raw observations and necessary sidecars are imported. Candidate,
    # bootstrap, summary and comparison files are never opened here.
    for folder in sorted((root / 'probes').iterdir()):
        result_path = folder / 'result.json'
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text())
        if result.get('status') != 'passed' or result.get('returncode') != 0:
            continue
        require(result['script_sha256'] == env['script_sha256'], 'legacy producer identity differs', EvidenceError)
        artifacts = {name: bounded_member(root, str(folder.relative_to(root) / name)).read_bytes()
                     for name in ('native/replay.json', 'native/pcm.json', 'native/processing-policy.json', 'native/pcm.f32le')}
        validate_public(artifacts)
        raw = bounded_member(root, str(folder.relative_to(root) / 'input/packets.bin')).read_bytes()
        cfg = bounded_member(root, str(folder.relative_to(root) / 'input/cookie.bin')).read_bytes()
        require(cfg == wire.cookie() and digest(raw) == result['input_sha256'], 'legacy inputs differ', EvidenceError)
        index = [json.loads(line) for line in (folder / 'input/packets.jsonl').read_text().splitlines()]
        require(len(index) == 2, 'legacy packet count differs', EvidenceError)
        packets, offset = [], 0
        for i, item in enumerate(index):
            require(item['export_offset'] == offset and item['frames'] == 1024 and item['packet_index'] == i,
                    'legacy packet indexing differs', EvidenceError)
            packet = raw[offset:offset + item['bytes']]
            require(len(packet) == item['bytes'] and digest(packet) == item['sha256'], 'legacy packet hash differs', EvidenceError)
            packets.append(packet)
            offset += len(packet)
        require(offset == len(raw) and digest(artifacts['native/pcm.f32le']) == result['pcm_sha256'],
                'legacy evidence hash differs', EvidenceError)
        key, request = wire.request(store.config['native_identity'], packets)
        if store.query(key):
            continue
        store.reserve(65536)
        objects = {}
        for relative in ('request.json', 'result.json', 'stdout.txt', 'stderr.txt',
                         'input/cookie.bin', 'input/packets.bin', 'input/packets.jsonl', 'input/manifest.json',
                         'native/replay.json', 'native/pcm.json', 'native/processing-policy.json', 'native/pcm.f32le'):
            path = bounded_member(root, str(folder.relative_to(root) / relative))
            objects[relative] = store.external(path)
        receipt = dict(key=key, artifacts=objects, pcm_sha256=objects['native/pcm.f32le'],
                       native_identity=store.config['native_identity'], returncode=0, imported_from=str(folder))
        count += store.imported_query(key, request, receipt)
    with store.db:
        store.db.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?)', (str(root), count, now()))
    return count


def import_batch(store, root):
    from .store import Store
    source = Store(root, readonly=True)
    try:
        require(source.config['native_identity'] == store.config['native_identity'], 'import batch identity differs', IdentityError)
        count = 0
        for row in source.db.execute("SELECT * FROM queries WHERE state='passed'"):
            request = json.loads(row['request'])
            require(digest(canonical(request)) == row['key'], 'import query identity differs', EvidenceError)
            receipt = source.query(row['key'])
            validate_public({name: source.read_blob(value) for name, value in receipt['artifacts'].items()},
                            request['signature']['frames'], request['signature'].get('quantization_bits', 6))
            if store.query(row['key']):
                continue
            store.reserve(65536)
            for identity in receipt['artifacts'].values():
                info = source.db.execute('SELECT * FROM objects WHERE sha=?', (identity,)).fetchone()
                registered = store.external(Path(info['path']), offset=info['offset'], size=info['size'],
                                            compressed=info['kind'] in ('gzip', 'external-gzip'))
                require(registered == identity, 'imported object changed', EvidenceError)
            count += store.imported_query(row['key'], request, receipt)
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?)', (str(Path(root).resolve()), count, now()))
        return count
    finally:
        source.close()
