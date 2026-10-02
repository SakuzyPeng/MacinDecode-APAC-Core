#!/usr/bin/env python3
"""Compare two validation reports, JSON/JSONL files or report directories.

Volatile keys (timestamps, timings, commits, source/binary fingerprints,
compiler identity, environment and absolute paths) are removed before the
comparison; every remaining difference is printed with its JSON path. Exit
code 0 means equal after normalization, 1 means differences were found.
"""
import argparse
import json
import re
import sys
from pathlib import Path

VOLATILE = {
    'created_at', 'finished_at', 'started_at', 'started_utc', 'finished_utc',
    'tested_worktree_dirty', 'code_commit', 'source_sha256',
    'binary_sha256', 'presence_binary_sha256', 'test_binary_sha256', 'compiler',
    'environment', 'failure_directory', 'debug_assertions', 'hostname',
    'stderr_tail', 'seconds', 'elapsed', 'elapsed_seconds', 'timing', 'timings',
}
VOLATILE_SUFFIXES = ('_seconds', '_ms', '_ns', '_path', '_directory')
PATH_LIKE = re.compile(r'^(/|[A-Za-z]:\\)')


def normalize(value, extra):
    if isinstance(value, dict):
        return {k: normalize(v, extra) for k, v in value.items()
                if k not in VOLATILE and k not in extra and not k.endswith(VOLATILE_SUFFIXES)}
    if isinstance(value, list):
        return [normalize(v, extra) for v in value]
    if isinstance(value, str) and PATH_LIKE.match(value):
        return '<path>'
    return value


def load(path):
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def diff(a, b, where, out, limit):
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        out.append(f'{where}: type {type(a).__name__} != {type(b).__name__}')
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f'{where}.{key}: only in {"right" if key not in a else "left"}')
            else:
                diff(a[key], b[key], f'{where}.{key}', out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f'{where}: length {len(a)} != {len(b)}')
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f'{where}[{i}]', out, limit)
    elif a != b:
        out.append(f'{where}: {json.dumps(a)[:200]} != {json.dumps(b)[:200]}')


def files(path):
    if path.is_dir():
        return {p.relative_to(path).as_posix(): p for p in sorted(path.rglob('*'))
                if p.is_file() and p.suffix in ('.json', '.jsonl')}
    return {path.name: path}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--ignore', nargs='*', default=[], help='additional volatile keys')
    parser.add_argument('--limit', type=int, default=50, help='differences printed per file')
    args = parser.parse_args()
    extra = set(args.ignore)
    left, right = files(args.left), files(args.right)
    if args.left.is_file() and args.right.is_file():
        right = {args.left.name: args.right}
    failed = False
    for name in sorted(set(left) | set(right)):
        if name not in left or name not in right:
            print(f'{name}: only in {"right" if name not in left else "left"}')
            failed = True
            continue
        out = []
        diff(normalize(load(left[name]), extra), normalize(load(right[name]), extra), '$', out, args.limit)
        if out:
            failed = True
            print(f'== {name}')
            print('\n'.join(out))
    print('different' if failed else 'equal after normalization')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
