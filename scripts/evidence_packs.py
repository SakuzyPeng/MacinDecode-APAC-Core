"""Read loose or packed gzip evidence without changing its logical path."""
from contextlib import contextmanager
from functools import lru_cache
import gzip
import hashlib
import io
from pathlib import Path
import sqlite3


@lru_cache(maxsize=32)
def _index(path, device, inode):
    db = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True,
                         check_same_thread=False)
    db.execute('PRAGMA cache_size=-1024')
    return db


def packed_reference(path):
    path = Path(path)
    index = path.parent / '.packs' / 'index.sqlite3'
    try:
        stamp = index.stat()
    except FileNotFoundError:
        return None
    try:
        db = _index(str(index.absolute()), stamp.st_dev, stamp.st_ino)
        row = db.execute('SELECT pack,offset,length,compressed_sha256 FROM objects WHERE name=?',
                         (path.name,)).fetchone()
    except sqlite3.Error as error:
        raise OSError('unreadable evidence pack index: ' + str(index)) from error
    if row is None:
        return None
    pack, offset, length, checksum = row
    if Path(pack).name != pack or offset < 0 or length <= 0:
        raise OSError('invalid evidence pack reference: ' + str(path))
    return index.parent / pack, offset, length, checksum


def object_exists(path):
    return Path(path).is_file() or packed_reference(path) is not None


@contextmanager
def open_gzip_object(path):
    """Prefer a loose file, including surfacing corruption in a loose override."""
    path = Path(path)
    try:
        source = path.open('rb')
    except FileNotFoundError:
        ref = packed_reference(path)
        if ref is None:
            raise
        pack, offset, length, checksum = ref
        with pack.open('rb') as stream:
            stream.seek(offset)
            encoded = stream.read(length)
        if len(encoded) != length or hashlib.sha256(encoded).hexdigest() != checksum:
            raise OSError('evidence pack checksum differs: ' + str(path))
        source = io.BytesIO(encoded)
    with source:
        with gzip.GzipFile(fileobj=source, mode='rb') as stream:
            yield stream


def read_gzip_object(path, limit=-1):
    with open_gzip_object(path) as stream:
        return stream.read(limit)
