"""Shared evidence pool and predeclared capture shards for a campaign.

The campaign lock is held by the orchestrator. Native workers never touch this
module. A shard boundary is not a resource-budget exception: only a completed
128-attempt window may advance, with no outstanding native writes.
"""
import gzip
import json
from pathlib import Path
import shutil
import sqlite3

from .common import (BudgetStop, EvidenceError, IdentityError, ShardBoundary,
                     canonical, digest, now, require)


class CampaignStorage:
    def __init__(self, store):
        self.store = store
        self.root = Path(store.config['campaign_root']).resolve()
        require(store.out.is_relative_to(self.root / 'batches'), 'batch outside campaign', EvidenceError)
        path = self.root / 'evidence.sqlite3'
        require(path.is_file(), 'campaign evidence index missing', EvidenceError)
        self.db = sqlite3.connect(path.as_uri()+('?mode=ro' if store.readonly else '?mode=rw'), uri=True)
        self.db.row_factory = sqlite3.Row
        self.batch = str(store.out.relative_to(self.root))
        require(type(store.config['shard_probes']) is int and 1 <= store.config['shard_probes'] <= 128,
                'invalid predeclared shard quota', EvidenceError)
        if not store.readonly:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            ensure_pool_schema(self.db)
            self.db.commit()
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS shards(id INTEGER PRIMARY KEY, state TEXT NOT NULL,
                    started_calls INTEGER NOT NULL, local_start INTEGER NOT NULL,
                    stored_bytes INTEGER NOT NULL DEFAULT 0, peak_bytes INTEGER NOT NULL DEFAULT 0,
                    reason TEXT, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shard_attempts(attempt INTEGER PRIMARY KEY, shard INTEGER NOT NULL);
            ''')
            store.db.execute('CREATE INDEX IF NOT EXISTS query_states ON queries(state)')
            store.db.execute('CREATE INDEX IF NOT EXISTS shard_attempt_counts ON shard_attempts(shard)')
            if not self.current():
                self._new()
            # A crash may occur between indexing an object in the shared pool
            # and charging its owning shard. Reconcile without touching bytes.
            with store.db:
                for row in store.db.execute('SELECT id FROM shards').fetchall():
                    total = self.db.execute('SELECT COALESCE(SUM(stored_bytes),0) FROM objects WHERE owner=? AND shard=?',
                                            (self.batch, row['id'])).fetchone()[0]
                    store.db.execute('UPDATE shards SET stored_bytes=? WHERE id=?', (total, row['id']))

        self.has_totals = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pool_totals'").fetchone() is not None
        # Other batch directories cannot have a writer while this campaign lock
        # is held. Reserve 1 MiB each for read-only SQLite sidecar creation.
        self.other_metadata = 0
        if not store.readonly:
            from .store import directory_bytes
            for path in (self.root/'batches').iterdir():
                if path.is_dir() and path.resolve()!=store.out:
                    self.other_metadata += directory_bytes(path) + 1024**2

    def close(self):
        self.db.close()

    def current(self):
        return self.store.db.execute('SELECT * FROM shards ORDER BY id DESC LIMIT 1').fetchone()

    def _new(self):
        store = self.store
        start = self.local_bytes()
        if self.current() is None:
            start -= store.disk_bytes()
            if self.db.execute('SELECT COUNT(*) FROM capture_attempts').fetchone()[0] == 0:
                start = 0
        with store.db:
            store.db.execute('INSERT INTO shards(state,started_calls,local_start,created) VALUES (?,?,?,?)',
                             ('active', self.calls(), start, now()))

    def local_bytes(self):
        return self.store.disk_bytes() + sum(p.stat().st_size for p in self.root.glob('evidence.sqlite3*') if p.is_file())

    def calls(self):
        return self.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]

    def added_bytes(self, row=None):
        row = row or self.current()
        return max(row['peak_bytes'], row['stored_bytes'] + max(0, self.local_bytes()-row['local_start']))

    def reserve(self, size):
        store, row = self.store, self.current()
        require(self.root.stat().st_dev == store.config.get('evidence_device',self.root.stat().st_dev),
                'campaign evidence volume changed', IdentityError)
        outstanding = sum(store.capture_reservations.values())
        added = self.added_bytes(row)
        error = None
        limits=json.loads(self.db.execute("SELECT value FROM meta WHERE key='total_limits'").fetchone()[0])
        if self.total_bytes() + outstanding + size + 65536 > limits['max_bytes']:
            error='campaign evidence budget exhausted'
        elif added + outstanding + size + 65536 > store.config['limits']['max_bytes']:
            error = 'evidence budget exhausted'
        elif shutil.disk_usage(self.root).free - outstanding - size < store.config['limits']['min_free']:
            error = 'minimum free disk space reached'
        if error:
            with store.db:
                store.db.execute('UPDATE shards SET state=?,reason=?,peak_bytes=? WHERE id=?',
                                 ('budget_exhausted', error, added, row['id']))
            raise BudgetStop(error)

    def before_capture(self):
        row, store = self.current(), self.store
        used = self.calls()-row['started_calls']
        if row['state'] == 'budget_exhausted':
            # Free-space recovery is an external change; other caps require an
            # explicit limit update. Never roll an exhausted shard forward.
            if row['reason'] == 'minimum free disk space reached':
                self.reserve(0)
                with store.db:
                    store.db.execute("UPDATE shards SET state='active',reason=NULL WHERE id=?", (row['id'],))
            else:
                raise BudgetStop(row['reason'])
        total_limits=json.loads(self.db.execute("SELECT value FROM meta WHERE key='total_limits'").fetchone()[0])
        if self.total_calls() >= total_limits['max_calls']:
            with store.db:
                store.db.execute("UPDATE shards SET state='budget_exhausted',reason='campaign native call budget exhausted' WHERE id=?", (row['id'],))
            raise BudgetStop('campaign native call budget exhausted')
        if used >= store.config['limits']['max_calls']:
            with store.db:
                store.db.execute("UPDATE shards SET state='budget_exhausted',reason='native call budget exhausted' WHERE id=?", (row['id'],))
            raise BudgetStop('native call budget exhausted')
        if used >= store.config['shard_probes']:
            if store.capture_reservations:
                raise ShardBoundary('planned capture shard completed')
            self.advance()

    def advance(self):
        store, row = self.store, self.current()
        require(row['state'] == 'active' and not store.capture_reservations,
                'cannot advance an exhausted or live shard', EvidenceError)
        require(self.calls()-row['started_calls'] == store.config['shard_probes'],
                'capture shard is not complete', EvidenceError)
        self.reserve(0)
        with store.db:
            store.db.execute("UPDATE shards SET state='complete',peak_bytes=? WHERE id=?",
                             (self.added_bytes(row), row['id']))
        self._new()

    def limit_updated(self):
        row = self.current()
        if row['state'] == 'budget_exhausted':
            with self.store.db:
                self.store.db.execute("UPDATE shards SET state='active',reason=NULL WHERE id=?", (row['id'],))

    def note_attempt(self, attempt):
        # Called inside Store.begin's transaction.
        self.store.db.execute('INSERT INTO shard_attempts VALUES (?,?)', (attempt, self.current()['id']))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO capture_attempts VALUES (?,?)',(self.batch,attempt))

    def total_calls(self):
        if self.has_totals:
            return self.db.execute('SELECT attempts FROM pool_totals WHERE id=1').fetchone()[0]
        return self.db.execute('SELECT COUNT(*) FROM capture_attempts').fetchone()[0]

    def total_bytes(self):
        # Constant-time pool sums and current-writer accounting. In-flight raw
        # files are covered separately by capture reservations; failed attempts
        # are folded into Store.base_bytes before another capture is admitted.
        stored = (self.db.execute('SELECT stored_bytes FROM pool_totals WHERE id=1').fetchone()[0]
                  if self.has_totals else self.db.execute('SELECT COALESCE(SUM(stored_bytes),0) FROM objects').fetchone()[0])
        control = 0
        for path in self.root.iterdir():
            try:
                if path.is_file(): control += path.stat().st_size
            except FileNotFoundError:
                pass
        return stored + self.other_metadata + self.store.disk_bytes() + control + 1024**2

    def reference(self,row):
        return dict(path=(row['path'] if 'path' in row.keys() else None) or str(self.root/'objects'/(row['sha']+'.gz')),
                    kind=row['kind'] if 'kind' in row.keys() else 'gzip',
                    offset=row['offset'] if 'offset' in row.keys() else 0,size=row['size'])

    def read_reference(self,identity,ref):
        try:
            if ref['kind'] in ('gzip','external-gzip'):
                with gzip.open(ref['path'],'rb') as stream:raw=stream.read(ref['size']+1)
            else:
                with Path(ref['path']).open('rb') as stream:
                    stream.seek(ref['offset']);raw=stream.read(ref['size'])
        except (OSError,EOFError) as error:raise EvidenceError('shared object is unreadable: '+identity) from error
        require(len(raw)==ref['size'] and digest(raw)==identity,'shared object hash differs',EvidenceError)
        return raw

    def ensure(self,identity):
        existing=self.db.execute('SELECT * FROM objects WHERE sha=?',(identity,)).fetchone()
        if existing:
            self.read_reference(identity,self.reference(existing))
            return
        local=self.store.db.execute('SELECT * FROM objects WHERE sha=?',(identity,)).fetchone()
        require(local is not None,'missing imported object',EvidenceError)
        self.store.read_blob(identity)
        self.reserve(65536)
        with self.db:
            self.db.execute('INSERT INTO objects(sha,size,stored_bytes,owner,shard,path,kind,offset) VALUES (?,?,?,?,?,?,?,?)',
                (identity,local['size'],0,self.batch,self.current()['id'],local['path'],local['kind'],local['offset']))

    def blob(self,raw,identity,encoded):
        from .store import atomic_file
        row=self.db.execute('SELECT * FROM objects WHERE sha=?',(identity,)).fetchone()
        if row:
            ref=self.reference(row)
            self.store.add_reference(identity,ref)
            require(self.store.read_blob(identity)==raw,'shared evidence object differs',EvidenceError)
            return ref
        self.reserve(len(encoded))
        path=self.root/'objects'/(identity+'.gz')
        if path.exists():require(gzip.decompress(path.read_bytes())==raw,'orphan object differs',EvidenceError)
        else:atomic_file(path,encoded)
        shard=self.current()['id']
        with self.db:
            self.db.execute('INSERT INTO objects(sha,size,stored_bytes,owner,shard,path,kind,offset) VALUES (?,?,?,?,?,?,?,?)',
                            (identity,len(raw),len(encoded),self.batch,shard,str(path),'gzip',0))
        with self.store.db:
            self.store.db.execute('UPDATE shards SET stored_bytes=stored_bytes+? WHERE id=?',(len(encoded),shard))
        return dict(path=str(path),kind='gzip',offset=0,size=len(raw))

    def register(self,key,request,receipt):
        for identity in receipt['artifacts'].values():self.ensure(identity)
        old=self.db.execute('SELECT request,receipt FROM observations WHERE key=?',(key,)).fetchone()
        if old:
            require(json.loads(old['request'])==request and json.loads(old['receipt'])==receipt,
                    'shared observation differs',EvidenceError)
        else:
            with self.db:
                self.db.execute('INSERT INTO observations VALUES (?,?,?,?)',
                                (key,canonical(request).decode(),canonical(receipt).decode(),self.batch))

    def lookup(self,key):
        row=self.db.execute('SELECT request,receipt FROM observations WHERE key=?',(key,)).fetchone()
        if row is None or self.store.readonly:return False
        request,receipt=json.loads(row['request']),json.loads(row['receipt'])
        require(digest(canonical(request))==key and request['native_identity']==receipt['native_identity']==self.store.config['native_identity'],
                'shared observation identity differs',IdentityError)
        self.reserve(65536)
        for identity in receipt['artifacts'].values():
            obj=self.db.execute('SELECT * FROM objects WHERE sha=?',(identity,)).fetchone()
            require(obj is not None,'shared object reference missing',EvidenceError)
            self.store.add_reference(identity,self.reference(obj))
        with self.store.db:
            self.store.db.execute('INSERT INTO queries VALUES (?,?,?,?,?)',(key,row['request'],'passed',row['receipt'],None))
        return True


    def summary(self):
        rows = [dict(r) for r in self.store.db.execute('SELECT * FROM shards ORDER BY id')]
        if rows:
            rows[-1]['peak_bytes'] = self.added_bytes()
        for row in rows:
            row['native_calls'] = self.store.db.execute('SELECT COUNT(*) FROM shard_attempts WHERE shard=?', (row['id'],)).fetchone()[0]
        return rows



def ensure_pool_schema(db):
    fields={r[1] for r in db.execute('PRAGMA table_info(objects)')}
    for field,declaration in (('path','TEXT'),('kind',"TEXT NOT NULL DEFAULT 'gzip'"),('offset','INTEGER NOT NULL DEFAULT 0')):
        if field not in fields: db.execute(f'ALTER TABLE objects ADD COLUMN {field} {declaration}')
    db.executescript("""
        CREATE INDEX IF NOT EXISTS pool_stored_bytes ON objects(stored_bytes);
        CREATE INDEX IF NOT EXISTS pool_owner_shard_bytes ON objects(owner,shard,stored_bytes);
        CREATE TABLE IF NOT EXISTS pool_totals(id INTEGER PRIMARY KEY CHECK(id=1),stored_bytes INTEGER NOT NULL,attempts INTEGER NOT NULL);
        INSERT OR IGNORE INTO pool_totals SELECT 1,(SELECT COALESCE(SUM(stored_bytes),0) FROM objects),(SELECT COUNT(*) FROM capture_attempts);
        CREATE TRIGGER IF NOT EXISTS pool_object_insert AFTER INSERT ON objects BEGIN
            UPDATE pool_totals SET stored_bytes=stored_bytes+NEW.stored_bytes WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS pool_object_delete AFTER DELETE ON objects BEGIN
            UPDATE pool_totals SET stored_bytes=stored_bytes-OLD.stored_bytes WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS pool_object_update AFTER UPDATE OF stored_bytes ON objects BEGIN
            UPDATE pool_totals SET stored_bytes=stored_bytes+NEW.stored_bytes-OLD.stored_bytes WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS pool_attempt_insert AFTER INSERT ON capture_attempts BEGIN
            UPDATE pool_totals SET attempts=attempts+1 WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS pool_attempt_delete AFTER DELETE ON capture_attempts BEGIN
            UPDATE pool_totals SET attempts=attempts-1 WHERE id=1; END;
    """)


def create_pool(root,total_limits=None):
    root = Path(root)
    (root / 'objects').mkdir()
    db = sqlite3.connect(root / 'evidence.sqlite3')
    db.executescript('''
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE capture_attempts(owner TEXT NOT NULL,attempt INTEGER NOT NULL,PRIMARY KEY(owner,attempt));
        CREATE TABLE objects(sha TEXT PRIMARY KEY,size INTEGER NOT NULL,stored_bytes INTEGER NOT NULL,
            owner TEXT NOT NULL,shard INTEGER NOT NULL,path TEXT,
            kind TEXT NOT NULL DEFAULT 'gzip',offset INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE observations(key TEXT PRIMARY KEY,request TEXT NOT NULL,receipt TEXT NOT NULL,owner TEXT NOT NULL);
    ''')
    ensure_pool_schema(db)
    db.execute('INSERT INTO meta VALUES (?,?)',('total_limits',canonical(total_limits or dict(max_calls=1000000,max_bytes=256*1024**3)).decode()))
    db.commit()
    db.close()
