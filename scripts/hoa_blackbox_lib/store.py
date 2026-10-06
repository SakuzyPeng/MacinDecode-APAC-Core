"""SQLite journal and lossless content-addressed evidence storage."""
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from .common import (BudgetStop, EvidenceError, ExperimentError, canonical, capture_reservation,
                     digest, geometry, now, pcm_byte_count, pcm_samples, require, target_parts)


def atomic_file(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def directory_bytes(path):
    total = 0
    for member in path.rglob('*'):
        try:
            if member.is_file():
                total += member.stat().st_size
        except FileNotFoundError:
            # A native worker may rename a temporary output during a status
            # scan. Its outstanding capture reservation covers that space.
            continue
    return total


@contextmanager
def writer_lock(out):
    import fcntl
    path = Path(out) / 'runner.lock'
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ExperimentError('another writer is using this batch') from error
        try:
            yield stream.fileno()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Store:
    @classmethod
    def create(cls, out, config):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=False)
        for name in ('objects', 'attempts', 'results'):
            (out / name).mkdir()
        db = sqlite3.connect(out / 'state.sqlite3')
        db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE objects(sha TEXT PRIMARY KEY, size INTEGER NOT NULL,
                kind TEXT NOT NULL, path TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE queries(key TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL,
                receipt TEXT, error TEXT);
            CREATE TABLE attempts(id INTEGER PRIMARY KEY, query_key TEXT NOT NULL,
                state TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, error TEXT);
            CREATE TABLE uses(target TEXT NOT NULL, stage TEXT NOT NULL, label TEXT NOT NULL,
                query_key TEXT NOT NULL, PRIMARY KEY(target,stage,label));
            CREATE TABLE stages(target TEXT NOT NULL, name TEXT NOT NULL, sha TEXT NOT NULL,
                PRIMARY KEY(target,name));
            CREATE TABLE jobs(target TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT, comparison TEXT);
            CREATE TABLE imports(path TEXT PRIMARY KEY, count INTEGER NOT NULL, imported TEXT NOT NULL);
        ''')
        db.execute('INSERT INTO meta VALUES (?,?)', ('config', canonical(config).decode()))
        db.execute('INSERT INTO meta VALUES (?,?)', ('batch_status', '"ready"'))
        db.executemany('INSERT INTO jobs(target,status) VALUES (?,?)', [(t, 'pending') for t in config['targets']])
        db.commit()
        db.close()
        atomic_file(out / 'manifest.json', canonical(config))
        return cls(out)

    def __init__(self, out, readonly=False):
        self.out = Path(out).resolve()
        require((self.out / 'state.sqlite3').is_file(), 'batch does not exist', EvidenceError)
        self.readonly = readonly
        self.db = sqlite3.connect((self.out / 'state.sqlite3').as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True)
        self.db.row_factory = sqlite3.Row
        if not readonly:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
        self.config = self.meta('config')
        self.base_bytes = directory_bytes(self.out)
        self.hits = 0
        self.capture_reservations = {}
        self.pool = None
        if self.config.get('campaign_root'):
            from .campaign_store import CampaignStorage
            self.pool = CampaignStorage(self)

    def close(self):
        if self.pool:
            self.pool.close()
        self.db.close()

    def meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, canonical(value).decode()))

    def set_limits(self, **updates):
        self.config['limits'].update({k: v for k, v in updates.items() if v is not None})
        self.set_meta('config', self.config)
        atomic_file(self.out / 'manifest.json', canonical(self.config))
        if self.pool and updates:
            self.pool.limit_updated()

    def disk_bytes(self):
        # Reconciled at every open; account for SQLite growth conservatively.
        database = sum(p.stat().st_size for p in self.out.glob('state.sqlite3*') if p.is_file())
        return self.base_bytes + database

    def reserve(self, size):
        if self.pool:
            return self.pool.reserve(size)
        limits = self.config['limits']
        outstanding = sum(self.capture_reservations.values())
        if self.disk_bytes() + outstanding + size + 65536 > limits['max_bytes']:
            raise BudgetStop('evidence budget exhausted')
        if shutil.disk_usage(self.out).free - outstanding - size < limits['min_free']:
            raise BudgetStop('minimum free disk space reached')

    def blob(self, raw):
        identity = digest(raw)
        old = self.db.execute('SELECT sha FROM objects WHERE sha=?', (identity,)).fetchone()
        if old:
            require(self.read_blob(identity) == raw, 'object identity collision', EvidenceError)
            if self.pool:self.pool.ensure(identity)
            return identity
        encoded = gzip.compress(raw, compresslevel=6, mtime=0)
        if self.pool:
            reference = self.pool.blob(raw, identity, encoded)
            self.add_reference(identity,reference)
            return identity
        else:
            self.reserve(len(encoded))
            path = self.out / 'objects' / (identity + '.gz')
            if path.exists():
                require(gzip.decompress(path.read_bytes()) == raw, 'orphan object is corrupt', EvidenceError)
            else:
                atomic_file(path, encoded)
                self.base_bytes += len(encoded)
        with self.db:
            self.db.execute('INSERT INTO objects VALUES (?,?,?,?,?)', (identity, len(raw), 'gzip', str(path), 0))
        return identity

    def add_reference(self,identity,reference):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?,?)',
                            (identity,reference['size'],reference['kind'],reference['path'],reference['offset']))

    def external(self, path, offset=0, size=None, compressed=False):
        path = Path(path).resolve()
        maximum = max(2*1024**2, pcm_byte_count(self.config.get('order', 3), 4096) + 256*1024)
        if compressed:
            with gzip.open(path, 'rb') as stream:
                raw = stream.read(maximum + 1)
        else:
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(min(size, maximum+1) if size is not None else maximum + 1)
        require(len(raw) <= maximum, 'external evidence object too large', EvidenceError)
        if size is not None:
            require(len(raw) == size, 'truncated external object', EvidenceError)
        identity = digest(raw)
        if self.db.execute('SELECT sha FROM objects WHERE sha=?', (identity,)).fetchone():
            self.read_blob(identity)
        else:
            with self.db:
                self.db.execute('INSERT INTO objects VALUES (?,?,?,?,?)',
                                (identity, len(raw), 'external-gzip' if compressed else 'external', str(path), offset))
        return identity

    def read_blob(self, identity):
        row = self.db.execute('SELECT * FROM objects WHERE sha=?', (identity,)).fetchone()
        require(row is not None, 'missing evidence object: ' + identity, EvidenceError)
        try:
            if row['kind'] in ('gzip', 'external-gzip'):
                with gzip.open(row['path'], 'rb') as stream:
                    raw = stream.read(row['size'] + 1)
            else:
                with Path(row['path']).open('rb') as stream:
                    stream.seek(row['offset'])
                    raw = stream.read(row['size'])
        except (OSError, EOFError) as error:
            raise EvidenceError('unreadable evidence object: ' + identity) from error
        require(len(raw) == row['size'] and digest(raw) == identity, 'evidence hash differs: ' + identity, EvidenceError)
        return raw

    def query(self, key):
        row = self.db.execute('SELECT state,receipt,request FROM queries WHERE key=?', (key,)).fetchone()
        if row is None and self.pool and self.pool.lookup(key):
            row = self.db.execute('SELECT state,receipt,request FROM queries WHERE key=?', (key,)).fetchone()
        if row and row['state'] == 'passed':
            request = json.loads(row['request'])
            require(digest(canonical(request)) == key, 'query request hash differs', EvidenceError)
            receipt = json.loads(row['receipt'])
            require(receipt['key'] == key and receipt['native_identity'] == self.config['native_identity'],
                    'query receipt binding differs', EvidenceError)
            for identity in receipt['artifacts'].values():
                self.read_blob(identity)
            signature = request['signature']
            channels = geometry(signature.get('order', 3))['channels']
            raw = self.read_blob(receipt['pcm_sha256'])
            require(signature['channels'] == channels and len(raw) == signature['frames']*channels*4,
                    'cached PCM dimensions differ', EvidenceError)
            pcm_samples(raw, channels)
            self.hits += 1
            return receipt
        return None

    def audit_evidence(self):
        """Check even observations whose completed analysis will be skipped."""
        seen = set()
        for row in self.db.execute("SELECT key,request,receipt FROM queries WHERE state='passed'"):
            request = json.loads(row['request'])
            receipt = json.loads(row['receipt'])
            require(digest(canonical(request)) == row['key'] and receipt['key'] == row['key']
                    and request['native_identity'] == self.config['native_identity']
                    and receipt['native_identity'] == self.config['native_identity'],
                    'observation identity binding differs', EvidenceError)
            for identity in receipt['artifacts'].values():
                if identity not in seen:
                    self.read_blob(identity)
                    seen.add(identity)
            require(receipt['pcm_sha256'] == receipt['artifacts']['native/pcm.f32le'], 'PCM receipt binding differs', EvidenceError)
        return len(seen)

    def use(self, target, stage, label, key):
        with self.db:
            previous = self.db.execute('SELECT query_key FROM uses WHERE target=? AND stage=? AND label=?', (target, stage, label)).fetchone()
            require(previous is None or previous[0] == key, 'deterministic probe changed', EvidenceError)
            self.db.execute('INSERT OR IGNORE INTO uses VALUES (?,?,?,?)', (target, stage, label, key))

    def begin(self, key, request):
        if self.pool:
            self.pool.before_capture()
        else:
            count = self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            if count >= self.config['limits']['max_calls']:
                raise BudgetStop('native call budget exhausted')
        reserved = capture_reservation(request)
        self.reserve(reserved)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO queries(key,request,state) VALUES (?,?,?)', (key, canonical(request).decode(), 'pending'))
            cursor = self.db.execute('INSERT INTO attempts(query_key,state,started) VALUES (?,?,?)', (key, 'started', now()))
            attempt = cursor.lastrowid
            if self.pool:
                self.pool.note_attempt(attempt)
        self.capture_reservations[attempt] = reserved
        folder = self.out / 'attempts' / str(attempt)
        folder.mkdir()
        atomic_file(folder / 'request.json', canonical(dict(key=key, request=request)))
        return attempt, folder

    def finish(self, attempt, receipt):
        folder = self.out / 'attempts' / str(attempt)
        for identity in receipt['artifacts'].values():
            self.read_blob(identity)
        atomic_file(folder / 'receipt.json', canonical(receipt))
        with self.db:
            self.db.execute('UPDATE queries SET state=?,receipt=?,error=NULL WHERE key=?',
                            ('passed', canonical(receipt).decode(), receipt['key']))
            self.db.execute('UPDATE attempts SET state=?,finished=? WHERE id=?', ('passed', now(), attempt))
        if self.pool:
            row = self.db.execute('SELECT request FROM queries WHERE key=?', (receipt['key'],)).fetchone()
            self.pool.register(receipt['key'], json.loads(row['request']), receipt)
        # All input and output bytes have verified, persistent representations.
        shutil.rmtree(folder)
        self.capture_reservations.pop(attempt, None)

    def fail(self, attempt, message, interrupted=False):
        self.capture_reservations.pop(attempt, None)
        row = self.db.execute('SELECT query_key,state FROM attempts WHERE id=?', (attempt,)).fetchone()
        if row['state'] == 'passed':
            return
        with self.db:
            self.db.execute('UPDATE attempts SET state=?,finished=?,error=? WHERE id=?',
                            ('interrupted' if interrupted else 'failed', now(), message, attempt))
            self.db.execute('UPDATE queries SET state=?,error=? WHERE key=?', ('pending' if interrupted else 'failed', message, row[0]))
        self.base_bytes = directory_bytes(self.out)

    def recover(self):
        for row in self.db.execute('SELECT id FROM attempts WHERE state=?', ('started',)).fetchall():
            folder = self.out / 'attempts' / str(row[0])
            receipt = folder / 'receipt.json'
            if receipt.exists():
                self.finish(row[0], json.loads(receipt.read_text()))
            else:
                self.fail(row[0], 'interrupted before a complete receipt', interrupted=True)
        for row in self.db.execute("SELECT id,query_key FROM attempts WHERE state='passed'").fetchall():
            folder = self.out / 'attempts' / str(row['id'])
            if folder.exists():
                receipt = self.query(row['query_key'])
                require(receipt is not None, 'completed attempt lost its receipt', EvidenceError)
                self.finish(row['id'], receipt)

    def imported_query(self, key, request, receipt):
        if self.query(key):
            return False
        for identity in receipt['artifacts'].values():
            self.read_blob(identity)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO queries VALUES (?,?,?,?,?)',
                            (key, canonical(request).decode(), 'passed', canonical(receipt).decode(), None))
        if self.pool:self.pool.register(key,request,receipt)
        return True

    def stage(self, target, name):
        row = self.db.execute('SELECT sha FROM stages WHERE target=? AND name=?', (target, name)).fetchone()
        if row is None:
            return None
        raw = self.read_blob(row[0])
        path = self.out / 'results' / target.replace(':', '-') / (name + '.json')
        if path.exists():
            require(path.read_bytes() == raw, 'frozen stage file changed: ' + str(path), EvidenceError)
        elif not self.readonly:
            atomic_file(path, raw)
        return json.loads(raw)

    def save_stage(self, target, name, value):
        raw = canonical(value)
        previous = self.stage(target, name)
        if previous is not None:
            require(canonical(previous) == raw, 'attempt to change frozen stage', EvidenceError)
            return digest(raw)
        identity = self.blob(raw)
        path = self.out / 'results' / target.replace(':', '-') / (name + '.json')
        if path.exists():
            require(path.read_bytes() == raw, 'orphan stage differs', EvidenceError)
        else:
            self.reserve(len(raw))
            atomic_file(path, raw)
            self.base_bytes += len(raw)
        with self.db:
            self.db.execute('INSERT INTO stages VALUES (?,?,?)', (target, name, identity))
        return identity

    def job(self, target, status, error=None, comparison=None):
        target_parts(target)
        with self.db:
            self.db.execute('UPDATE jobs SET status=?,error=?,comparison=COALESCE(?,comparison) WHERE target=?',
                            (status, error, comparison, target))

    def summary(self):
        result = dict(schema_version=1, batch_status=self.meta('batch_status'),
                    order=self.config.get('order', 3),
                    native_jobs=self.config.get('native_jobs', 1),
                    quantization_bits=self.config.get('quantization_bits', 6),
                    targets=[dict(r) for r in self.db.execute('SELECT * FROM jobs ORDER BY target')],
                    native_calls=self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],
                    successful_queries=self.db.execute("SELECT COUNT(*) FROM queries WHERE state='passed'").fetchone()[0],
                    logical_probes=self.db.execute('SELECT COUNT(*) FROM uses').fetchone()[0],
                    imports=[dict(r) for r in self.db.execute('SELECT * FROM imports')],
                    limits=self.config['limits'], added_bytes=directory_bytes(self.out),
                    native_identity_sha256=digest(canonical(self.config['native_identity'])),
                    tool_fingerprint=self.config['tool_fingerprint'])
        if self.pool:
            result['shards'] = self.pool.summary()
            result['limits_scope'] = 'planned_shard'
            result['added_bytes'] += self.pool.db.execute('SELECT COALESCE(SUM(stored_bytes),0) FROM objects WHERE owner=?',
                                                         (self.pool.batch,)).fetchone()[0]
        return result
