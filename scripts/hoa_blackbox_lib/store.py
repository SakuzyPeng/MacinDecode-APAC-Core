"""SQLite journal and lossless content-addressed evidence storage."""
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from .common import (BudgetStop, EvidenceError, ExperimentError, canonical, digest,
                     now, pcm_samples, require, target_parts)


def atomic_file(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


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
            yield
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
        self.base_bytes = sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file())
        self.hits = 0

    def close(self):
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

    def disk_bytes(self):
        # Reconciled at every open; account for SQLite growth conservatively.
        database = sum(p.stat().st_size for p in self.out.glob('state.sqlite3*') if p.is_file())
        return self.base_bytes + database

    def reserve(self, size):
        limits = self.config['limits']
        if self.disk_bytes() + size + 65536 > limits['max_bytes']:
            raise BudgetStop('evidence budget exhausted')
        if shutil.disk_usage(self.out).free - size < limits['min_free']:
            raise BudgetStop('minimum free disk space reached')

    def blob(self, raw):
        identity = digest(raw)
        old = self.db.execute('SELECT sha FROM objects WHERE sha=?', (identity,)).fetchone()
        if old:
            require(self.read_blob(identity) == raw, 'object identity collision', EvidenceError)
            return identity
        encoded = gzip.compress(raw, compresslevel=6, mtime=0)
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

    def external(self, path, offset=0, size=None, compressed=False):
        path = Path(path).resolve()
        if compressed:
            with gzip.open(path, 'rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
        else:
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(size if size is not None else 2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, 'external evidence object too large', EvidenceError)
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
        if row and row['state'] == 'passed':
            require(digest(canonical(json.loads(row['request']))) == key, 'query request hash differs', EvidenceError)
            receipt = json.loads(row['receipt'])
            require(receipt['key'] == key and receipt['native_identity'] == self.config['native_identity'],
                    'query receipt binding differs', EvidenceError)
            for identity in receipt['artifacts'].values():
                self.read_blob(identity)
            pcm_samples(self.read_blob(receipt['pcm_sha256']))
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
        count = self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        if count >= self.config['limits']['max_calls']:
            raise BudgetStop('native call budget exhausted')
        self.reserve(2 * 1024 * 1024)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO queries(key,request,state) VALUES (?,?,?)', (key, canonical(request).decode(), 'pending'))
            cursor = self.db.execute('INSERT INTO attempts(query_key,state,started) VALUES (?,?,?)', (key, 'started', now()))
            attempt = cursor.lastrowid
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
        # All input and output bytes have verified, persistent representations.
        shutil.rmtree(folder)

    def fail(self, attempt, message, interrupted=False):
        row = self.db.execute('SELECT query_key,state FROM attempts WHERE id=?', (attempt,)).fetchone()
        if row['state'] == 'passed':
            return
        with self.db:
            self.db.execute('UPDATE attempts SET state=?,finished=?,error=? WHERE id=?',
                            ('interrupted' if interrupted else 'failed', now(), message, attempt))
            self.db.execute('UPDATE queries SET state=?,error=? WHERE key=?', ('pending' if interrupted else 'failed', message, row[0]))
        self.base_bytes = sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file())

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
                require(self.query(row['query_key']) is not None, 'completed attempt lost its receipt', EvidenceError)
                shutil.rmtree(folder)

    def imported_query(self, key, request, receipt):
        if self.query(key):
            return False
        for identity in receipt['artifacts'].values():
            self.read_blob(identity)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO queries VALUES (?,?,?,?,?)',
                            (key, canonical(request).decode(), 'passed', canonical(receipt).decode(), None))
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
        return dict(schema_version=1, batch_status=self.meta('batch_status'),
                    targets=[dict(r) for r in self.db.execute('SELECT * FROM jobs ORDER BY target')],
                    native_calls=self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],
                    successful_queries=self.db.execute("SELECT COUNT(*) FROM queries WHERE state='passed'").fetchone()[0],
                    logical_probes=self.db.execute('SELECT COUNT(*) FROM uses').fetchone()[0],
                    imports=[dict(r) for r in self.db.execute('SELECT * FROM imports')],
                    limits=self.config['limits'], added_bytes=sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file()),
                    native_identity_sha256=digest(canonical(self.config['native_identity'])),
                    tool_fingerprint=self.config['tool_fingerprint'])
