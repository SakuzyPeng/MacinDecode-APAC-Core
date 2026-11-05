#!/usr/bin/env python3
"""Offline, resumable consolidation of content-addressed gzip evidence.

Logical .gz paths remain in the original journals. Use evidence_packs to read
them. Each durable, verified pack is indexed before any loose file is removed.
"""
import argparse
from contextlib import ExitStack
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid

from evidence_packs import read_gzip_object

NAME = re.compile(r'^[0-9a-f]{64}\.gz$')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def signature(stamp):
    return stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns


def object_directories(root):
    if root.name == 'objects':
        yield root
        return
    for parent, directories, files in os.walk(root):
        if 'objects' in directories:
            yield Path(parent) / 'objects'
            directories.remove('objects')
        directories[:] = sorted(d for d in directories if d not in ('.packs', '.git'))


def offline_locks(root):
    """Acquire the same locks as HOA writers, plus our own migration lock."""
    stack = ExitStack()
    try:
        paths = [root / '.evidence-pack.lock']
        for parent, directories, files in os.walk(root):
            directories[:] = [d for d in directories if d not in ('objects', '.packs', '.git')]
            paths.extend(Path(parent) / n for n in ('runner.lock', 'writer.lock') if n in files)
        for path in sorted(set(paths)):
            stream = stack.enter_context(path.open('a+b'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stack
    except BaseException:
        stack.close()
        raise


def pack_directory(directory, check_volume=lambda: None, chunk_bytes=256*1024**2,
                   chunk_objects=10000, progress=lambda row: None):
    check_volume()
    packed = directory / '.packs'
    packed.mkdir(exist_ok=True)
    db = sqlite3.connect(packed / 'index.sqlite3')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA cache_size=-4096')
    db.execute('''CREATE TABLE IF NOT EXISTS objects(
        name TEXT PRIMARY KEY, pack TEXT NOT NULL, offset INTEGER NOT NULL,
        length INTEGER NOT NULL, raw_size INTEGER NOT NULL,
        compressed_sha256 TEXT NOT NULL) WITHOUT ROWID''')
    db.commit()
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    packs_fd = os.open(packed, os.O_RDONLY | os.O_DIRECTORY)
    total = dict(directory=str(directory), objects=0, compressed_bytes=0,
                 source_allocated_bytes=0, packs=0, resumed_deletions=0)
    pending = []; output = None; temp = None; final = None

    def remove_sources(rows):
        check_volume()
        for name, offset, length, raw_size, checksum, stamp, allocated in rows:
            try:
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if signature(current) != stamp:
                raise RuntimeError('source changed before removal: ' + str(directory / name))
            os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)

    def finish():
        nonlocal pending, output, temp, final
        if not pending:
            return
        check_volume()
        output.flush(); os.fsync(output.fileno()); output.close(); output = None
        # Reread every stored byte; original payload hashes were checked on input.
        with temp.open('rb') as stream:
            for name, offset, length, raw_size, checksum, stamp, allocated in pending:
                stream.seek(offset)
                encoded = stream.read(length)
                if len(encoded) != length or sha256(encoded) != checksum:
                    raise RuntimeError('pack verification failed: ' + name)
        check_volume()
        temp.rename(final); os.fsync(packs_fd)
        with db:
            db.executemany('INSERT INTO objects VALUES (?,?,?,?,?,?)',
                [(name, final.name, offset, length, raw_size, checksum)
                 for name, offset, length, raw_size, checksum, stamp, allocated in pending])
        # An interrupted cleanup is retried from the durable index on the next run.
        remove_sources(pending)
        total['objects'] += len(pending)
        total['compressed_bytes'] += sum(r[2] for r in pending)
        total['source_allocated_bytes'] += sum(r[6] for r in pending)
        total['packs'] += 1
        pending = []; temp = final = None
        progress(dict(total))

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not NAME.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                    continue
                stamp = entry.stat(follow_symlinks=False)
                existing = db.execute('SELECT pack,offset,length,raw_size,compressed_sha256 FROM objects WHERE name=?',
                                      (entry.name,)).fetchone()
                if existing:
                    pack, offset, length, raw_size, checksum = existing
                    encoded = Path(entry.path).read_bytes()
                    with (packed / pack).open('rb') as stream:
                        stream.seek(offset); saved = stream.read(length)
                    if encoded != saved or sha256(saved) != checksum or sha256(gzip.decompress(saved)) != entry.name[:-3]:
                        raise RuntimeError('interrupted migration differs: ' + entry.path)
                    remove_sources([(entry.name, offset, length, raw_size, checksum, signature(stamp), stamp.st_blocks*512)])
                    total['resumed_deletions'] += 1
                    continue
                if output is None:
                    check_volume()
                    if shutil.disk_usage(directory).free < chunk_bytes + 1024**3:
                        raise RuntimeError('insufficient free space for the next pack')
                    name = 'pack-' + uuid.uuid4().hex + '.bin'
                    final = packed / name; temp = packed / (name + '.partial')
                    output = temp.open('xb')
                encoded = Path(entry.path).read_bytes()
                raw = gzip.decompress(encoded)
                if sha256(raw) != entry.name[:-3]:
                    raise RuntimeError('source content hash differs: ' + entry.path)
                offset = output.tell(); output.write(encoded)
                pending.append((entry.name, offset, len(encoded), len(raw), sha256(encoded),
                                signature(stamp), stamp.st_blocks*512))
                if output.tell() >= chunk_bytes or len(pending) >= chunk_objects:
                    finish()
        finish()
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('pack index validation failed')
        progress(dict(total, complete=True))
        return total
    finally:
        if output is not None:
            output.close()
        db.close(); os.close(directory_fd); os.close(packs_fd)


def verify_directory(directory):
    index = directory / '.packs' / 'index.sqlite3'
    if not index.is_file():
        return dict(directory=str(directory), objects=0)
    db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
    count = 0
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('pack index integrity check failed: ' + str(index))
        for name, raw_size in db.execute('SELECT name,raw_size FROM objects'):
            raw = read_gzip_object(directory / name)
            if len(raw) != raw_size or sha256(raw) != name[:-3]:
                raise RuntimeError('packed object hash differs: ' + name)
            count += 1
        return dict(directory=str(directory), objects=count, verified=True)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    identity = root.stat().st_dev, root.stat().st_ino, os.statvfs(root).f_fsid
    def check_volume():
        if (root.stat().st_dev, root.stat().st_ino, os.statvfs(root).f_fsid) != identity:
            raise RuntimeError('evidence volume disconnected or changed')
    def report(value):
        print(json.dumps(value, sort_keys=True), flush=True)
    started = time.monotonic()
    with offline_locks(root):
        results = []
        for directory in object_directories(root):
            if args.verify_only:
                check_volume(); result = verify_directory(directory); report(result)
            else:
                result = pack_directory(directory, check_volume, progress=report)
            results.append(result)
    report(dict(complete=True, seconds=round(time.monotonic()-started),
                objects=sum(r['objects'] for r in results), directories=len(results)))


if __name__ == '__main__':
    main()
