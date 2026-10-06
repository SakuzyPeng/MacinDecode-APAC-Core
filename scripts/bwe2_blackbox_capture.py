"""Bounded public replay capture with a local ledger and pinned external evidence."""
import fcntl
import gzip
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import shutil
import sqlite3
import struct
import subprocess
import time

from bwe2_blackbox_wire import ROOT, Writer, bundle, canonical, cookie, digest

COMPONENT = Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
MIB = 1024**2


def atomic(path, raw):
    temp = path.with_name(path.name+'.tmp')
    with temp.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def native_identity(binary):
    if any(k.startswith('DYLD_') and v for k,v in os.environ.items()):
        raise RuntimeError('injected native environment')
    return dict(binary_sha256=digest(Path(binary).read_bytes()),component_sha256=digest(COMPONENT.read_bytes()),
                os_version=subprocess.check_output(['sw_vers'],text=True).strip(),architecture=platform.machine())


def volume_identity(mount):
    mount = Path(mount)
    if not mount.is_mount():
        raise RuntimeError('external evidence volume is not mounted')
    info = plistlib.loads(subprocess.check_output(['diskutil','info','-plist',str(mount)]))
    if info.get('MountPoint') != str(mount) or not info.get('WritableVolume'):
        raise RuntimeError('external evidence volume is not writable')
    return dict(mount=str(mount),uuid=info['VolumeUUID'])


def default_signature():
    return dict(channels=2,layout_tag=(101<<16)|2,cookie_sha256=digest(cookie()))


def validate(artifacts, frames, signature=None):
    signature=default_signature() if signature is None else signature
    replay = json.loads(artifacts['native/replay.json'])
    pcm = json.loads(artifacts['native/pcm.json'])
    policy = json.loads(artifacts['native/processing-policy.json'])
    raw = artifacts['native/pcm.f32le']
    if len(raw) != frames*signature['channels']*4 or not all(math.isfinite(v[0]) for v in struct.iter_unpack('<f',raw)):
        raise RuntimeError('invalid PCM shape or values')
    if not (replay['complete'] and replay['backend']=='AudioConverterFillComplexBuffer'
            and replay['saved_frames']==frames and replay['consumed_packets']==frames//1024
            and replay['input_batch_packets']==1 and replay['original_source_accessed'] is False
            and replay['processing_policy']=='drc-off'):
        raise RuntimeError('native replay contract differs')
    if not (pcm['complete'] and pcm['frames']==frames and pcm['channels']==signature['channels'] and pcm['sample_rate']==48000
            and pcm['encoding']=='f32le' and pcm['interleaved'] and pcm['all_finite']
            and pcm['sha256']==digest(raw) and pcm['start_frame']==0
            and pcm['layout']['value']['tag']==signature['layout_tag'] and pcm['source_cookie_sha256']==signature['cookie_sha256']):
        raise RuntimeError('PCM metadata differs')
    if not (policy['verified'] and policy['policy']=='drc-off'
            and policy['request_order']=='properties_then_magic_cookie_then_initial_reset'
            and policy['initial_reset']['os_status']==0):
        raise RuntimeError('processing policy differs')
    for name in ('mdrc','^pro','ptlc'):
        if policy['readback'][name] != dict(error=None,value=0) or replay['decoder_settings'][name] != dict(error=None,value=0):
            raise RuntimeError('processing-off property differs')
    if not replay['decoder_settings']['processing_policy']['value']['verified']:
        raise RuntimeError('final processing policy was not verified')


class Capture:
    def __init__(self, out, binary, evidence=None, mount=None, writer=None):
        try:
            self.initialize(out,binary,evidence,mount,writer)
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            if hasattr(self,'lock') and not self.lock.closed:self.lock.close()
            raise

    def initialize(self, out, binary, evidence=None, mount=None, writer=None):
        self.out,self.binary = Path(out).resolve(),Path(binary).resolve()
        self.out.mkdir(parents=True,exist_ok=True)
        self.lock = (self.out/'writer.lock').open('a')
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('batch already has a writer')
        self.writer = Writer() if writer is None else writer
        self.signature=getattr(self.writer,'signature',default_signature)()
        self.identity = native_identity(self.binary)
        config = self.out/'manifest.json'
        if config.exists():
            self.config = json.loads(config.read_bytes())
            if self.config['native_identity'] != self.identity:
                raise RuntimeError('native identity changed')
            if self.config.get('wire_signature',default_signature()) != self.signature:
                raise RuntimeError('capture wire signature differs')
            if evidence is not None and str(Path(evidence).resolve())!=self.config['evidence']:
                raise RuntimeError('evidence path changed')
        else:
            if evidence is None or mount is None:
                raise ValueError('new batch requires evidence and mount')
            pin=volume_identity(mount)
            path=Path(evidence).resolve()
            if not path.is_relative_to(Path(mount).resolve()) or path.exists():
                raise ValueError('new evidence directory must be absent on the mounted volume')
            path.mkdir(parents=True)
            self.config=dict(schema_version=1,native_identity=self.identity,binary=str(self.binary),wire_signature=self.signature,
                evidence=str(path),volume=pin,max_native_calls=4096,max_evidence_bytes=512*MIB,
                min_free_bytes=1024*MIB,timeout_seconds=30,created=time.time(),
                code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
                code_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'])),
                aac_sha256=digest((ROOT/'data/sq-codebooks.json').read_bytes()))
            atomic(config,canonical(self.config))
            atomic(path/'volume-pin.json',canonical(pin))
        self.evidence=Path(self.config['evidence'])
        self.check(force=True)
        (self.evidence/'objects').mkdir(exist_ok=True)
        (self.evidence/'attempts').mkdir(exist_ok=True)
        self.db=sqlite3.connect(self.out/'state.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, key TEXT, status TEXT, started REAL, error TEXT, receipt BLOB)')
        if 'reserved_bytes' not in {r[1] for r in self.db.execute('PRAGMA table_info(attempts)')}:
            self.db.execute('ALTER TABLE attempts ADD COLUMN reserved_bytes INTEGER NOT NULL DEFAULT 0')
        self.db.execute('CREATE TABLE IF NOT EXISTS observations(key TEXT PRIMARY KEY, receipt BLOB)')
        self.db.execute('CREATE TABLE IF NOT EXISTS uses(label TEXT, key TEXT, spec BLOB, PRIMARY KEY(label,key))')
        self.db.execute('CREATE TABLE IF NOT EXISTS objects(hash TEXT PRIMARY KEY, bytes INTEGER)')
        self.db.execute("UPDATE attempts SET status='interrupted' WHERE status='running'")
        self.db.commit()

    def stamps(self):
        return [(p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns) for p in (self.binary,COMPONENT)]

    def check(self, force=False):
        pin=self.config['volume']
        mount=Path(pin['mount'])
        if not mount.is_mount() or not self.evidence.is_dir() or self.evidence.stat().st_dev!=mount.stat().st_dev:
            raise RuntimeError('external volume disconnected; no local fallback is permitted')
        device=(mount.stat().st_dev,os.statvfs(mount).f_fsid)
        if force or getattr(self,'volume_device',None)!=device:
            if volume_identity(mount)!=pin or json.loads((self.evidence/'volume-pin.json').read_bytes())!=pin:
                raise RuntimeError('external volume identity changed')
            self.volume_device=device
        stamps=self.stamps()
        if force or stamps!=getattr(self,'native_stamps',None):
            if native_identity(self.binary)!=self.identity:
                raise RuntimeError('native binary or component changed')
            self.native_stamps=stamps
        for p in (self.out,self.evidence):
            if shutil.disk_usage(p).free < self.config['min_free_bytes']+2*MIB:
                raise RuntimeError('insufficient free space')

    def blob(self, raw):
        self.check()
        key=digest(raw)
        path=self.evidence/'objects'/(key+'.gz')
        if path.exists():
            if gzip.decompress(path.read_bytes())!=raw:
                raise RuntimeError('content-addressed object is corrupt')
        else:
            used=self.charged_bytes()
            if used+len(raw)>self.config['max_evidence_bytes']:
                raise RuntimeError('uncompressed evidence budget exhausted')
            compressed=gzip.compress(raw,mtime=0)
            if gzip.decompress(compressed)!=raw:
                raise RuntimeError('compression round-trip failed')
            atomic(path,compressed)
        self.db.execute('INSERT OR IGNORE INTO objects VALUES (?,?)',(key,len(raw)))
        self.db.commit()
        return key

    def read_blob(self, key):
        self.check()
        raw=gzip.decompress((self.evidence/'objects'/(key+'.gz')).read_bytes())
        if digest(raw)!=key:
            raise RuntimeError('evidence hash mismatch')
        return raw

    def charged_bytes(self):
        stored=self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM objects').fetchone()[0]
        retained=self.db.execute('SELECT COALESCE(SUM(reserved_bytes),0) FROM attempts').fetchone()[0]
        return stored+retained

    def probe(self, label, spec, replicate=''):
        self.check()
        packets=self.writer.program(spec)
        request=dict(native_identity=self.identity,cookie_sha256=self.signature['cookie_sha256'],
            packets=[dict(sha256=digest(p),bytes=len(p)) for p in packets],frames=1024*len(packets),
            channels=self.signature['channels'],rate=48000,processing_policy='drc-off',input_batch_packets=1,replicate=replicate)
        key=digest(canonical(request))
        self.db.execute('INSERT OR IGNORE INTO uses VALUES (?,?,?)',(label,key,canonical(spec)))
        self.db.commit()
        cached=self.db.execute('SELECT receipt FROM observations WHERE key=?',(key,)).fetchone()
        if cached:
            receipt=json.loads(cached[0])
            artifacts={name:self.read_blob(h) for name,h in receipt['artifacts'].items()}
            validate(artifacts,request['frames'],self.signature)
            return key,artifacts['native/pcm.f32le']
        calls=self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        used=self.charged_bytes()
        reservation=max(2*MIB,request['frames']*self.signature['channels']*4+2*sum(map(len,packets))+256*1024)
        if calls>=self.config['max_native_calls'] or used+reservation>self.config['max_evidence_bytes']:
            raise RuntimeError('batch capture budget exhausted')
        cur=self.db.execute('INSERT INTO attempts(key,status,started,reserved_bytes) VALUES (?,?,?,?)',(key,'running',time.time(),reservation))
        attempt=cur.lastrowid
        self.db.commit()
        folder=self.evidence/'attempts'/f'{attempt:06d}'
        folder.mkdir()
        atomic(folder/'request.json',canonical(request))
        getattr(self.writer,'bundle',bundle)(folder/'input',packets)
        output_mib=max(1,(request['frames']*self.signature['channels']*4+65536+128+MIB-1)//MIB)
        command=[str(self.binary),'replay',str(folder/'input'),'--out',str(folder/'native'),'--frames',str(request['frames']),
                 '--input-batch-packets','1','--processing-policy','drc-off','--max-output-mib',str(output_mib)]
        atomic(folder/'command.json',canonical(command))
        start=time.monotonic()
        try:
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                stdout,stderr=proc.communicate(timeout=self.config['timeout_seconds'])
            except BaseException:
                proc.kill()
                stdout,stderr=proc.communicate()
                atomic(folder/'stdout.txt',stdout)
                atomic(folder/'stderr.txt',stderr)
                raise
            atomic(folder/'stdout.txt',stdout)
            atomic(folder/'stderr.txt',stderr)
            atomic(folder/'process.json',canonical(dict(returncode=proc.returncode,seconds=time.monotonic()-start)))
            if proc.returncode:
                raise RuntimeError('native replay failed: '+stderr.decode(errors='replace')[-1200:])
            self.check()
            artifacts={str(p.relative_to(folder)):p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}
            validate(artifacts,request['frames'],self.signature)
            references={name:self.blob(raw) for name,raw in artifacts.items()}
            receipt=canonical(dict(key=key,artifacts=references,native_identity=self.identity,attempt=attempt))
            atomic(folder/'receipt.json',receipt)
            self.db.execute('INSERT INTO observations VALUES (?,?)',(key,receipt))
            self.db.execute("UPDATE attempts SET status='success',receipt=? WHERE id=?",(receipt,attempt))
            self.db.commit()
            # Every byte is retained in the verified lossless object pool.
            for p in sorted(folder.rglob('*'),reverse=True):
                if p.is_file() and p.name!='receipt.json':
                    relative=str(p.relative_to(folder))
                    if self.read_blob(references[relative])!=p.read_bytes():
                        raise RuntimeError('object differs before raw cleanup')
                    p.unlink()
                elif p.is_dir():
                    p.rmdir()
            self.db.execute('UPDATE attempts SET reserved_bytes=0 WHERE id=?',(attempt,))
            self.db.commit()
            return key,artifacts['native/pcm.f32le']
        except BaseException as error:
            self.db.execute("UPDATE attempts SET status=?,error=? WHERE id=? AND status='running'",
                ('interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed',repr(error),attempt))
            self.db.commit()
            raise

    def status(self):
        return dict(attempts=dict(self.db.execute('SELECT status,COUNT(*) FROM attempts GROUP BY status')),
                    observations=self.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],
                    evidence_uncompressed_bytes=self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM objects').fetchone()[0],
                    charged_bytes=self.charged_bytes(),
                    native_identity=self.identity,volume=self.config['volume'])

    def audit(self):
        """Verify every stored object and public success receipt without capture."""
        self.check(force=True)
        objects={}
        for key,size in self.db.execute('SELECT hash,bytes FROM objects'):
            raw=self.read_blob(key)
            if len(raw)!=size:raise RuntimeError('object length differs from ledger')
            objects[key]=raw
        receipts=0
        for key,raw_receipt in self.db.execute('SELECT key,receipt FROM observations'):
            receipt=json.loads(raw_receipt)
            if receipt['key']!=key or receipt['native_identity']!=self.identity:
                raise RuntimeError('observation identity differs')
            artifacts={name:objects[h] for name,h in receipt['artifacts'].items()}
            request=json.loads(artifacts['request.json'])
            if digest(canonical(request))!=key:raise RuntimeError('request key differs')
            validate(artifacts,request['frames'],self.signature)
            saved=self.evidence/'attempts'/f'{receipt["attempt"]:06d}'/'receipt.json'
            if saved.read_bytes()!=raw_receipt:raise RuntimeError('durable receipt differs from ledger')
            receipts+=1
        self.check(force=True)
        return dict(objects=len(objects),receipts=receipts,verified=True,status=self.status())

    def close(self):
        self.db.close()
        fcntl.flock(self.lock,fcntl.LOCK_UN)
        self.lock.close()


def read_status(out):
    out=Path(out).resolve()
    config=json.loads((out/'manifest.json').read_bytes())
    db=sqlite3.connect(f'file:{out/"state.sqlite3"}?mode=ro',uri=True)
    try:
        return dict(attempts=dict(db.execute('SELECT status,COUNT(*) FROM attempts GROUP BY status')),
            observations=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],
            evidence_uncompressed_bytes=db.execute('SELECT COALESCE(SUM(bytes),0) FROM objects').fetchone()[0],
            native_identity=config['native_identity'],volume=config['volume'],
            volume_mounted=Path(config['volume']['mount']).is_mount())
    finally:db.close()
