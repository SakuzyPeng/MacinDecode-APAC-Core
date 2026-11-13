#!/usr/bin/env python3
"""Verify the distributed source manifest and reject excluded research material."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'data/source-provenance-v1.json'
NUMERICAL = {'qualified-measurement', 'qualified-derived', 'formula', 'public-licensed'}
PRIVATE_PATH = re.compile(
    r'^(?:docs|reports|artifacts|target)/|\.xcworkspace/|'
    r'(?:^|/)bwe2-format-v1\.json$|'
    r'^scripts/(?:native_component|verify_bwe2_format|native_.*_trace)\.py$|'
    r'(?:^|/)\.env(?:$|\.(?!example$))')
MEDIA = re.compile(r'\.(?:wav|flac|mp3|m4a|aiff?|caf|mp4|mov|dylib|dll|exe|so)$', re.I)
PATTERNS = {
    'private-home-path': re.compile(rb'/Users/(?!example/|user/|you/|USER/)[^/\s]+/(?:Music|Library|code|Desktop|Downloads)'),
    'github-token': re.compile(rb'(?:github_pat_[A-Za-z0-9_]{40,}|gh[pousr]_[A-Za-z0-9]{30,})'),
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws-access-key': re.compile(rb'AKIA[A-Z0-9]{16}'),
    'generated-lsf-table': re.compile(rb'static\s+BWE2_BOOK_[01]\s*:'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def contains_lsf_table(value):
    if isinstance(value, dict):
        return (isinstance(value.get('lsf_codebooks_f32'), list)
                or any(contains_lsf_table(item) for item in value.values()))
    if isinstance(value, list):
        if (len(value) == 2 and all(isinstance(book, list) and len(book) == 512
                and all(isinstance(row, list) and len(row) == 16 for row in book) for book in value)):
            return True
        return any(contains_lsf_table(item) for item in value)
    return False


def inspect_bytes(name, raw):
    findings = []
    if PRIVATE_PATH.search(name) or MEDIA.search(name):
        findings.append('excluded-path')
    for rule, pattern in PATTERNS.items():
        if pattern.search(raw):
            findings.append(rule)
    if name.endswith('.json') or raw.lstrip().startswith((b'{', b'[')):
        try:
            if contains_lsf_table(json.loads(raw)):
                findings.append('excluded-lsf-table')
        except (ValueError, UnicodeDecodeError):
            pass
    return findings


def audit(root=ROOT, history=False):
    findings = []
    manifest = json.loads((root / MANIFEST).read_bytes())
    if manifest.get('profile') != 'apac-core-distribution-sources-v1':
        raise ValueError('unexpected distribution manifest')
    listed = set()
    for record in manifest['files']:
        name = record['path']
        path = root / name
        if name in listed or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('duplicate or escaping manifest path')
        listed.add(name)
        if not path.is_file() or sha(path.read_bytes()) != record['sha256']:
            findings.append(dict(path=name, rule='source-digest-or-presence'))
        if record['role'] != 'test-input' and record['classification'] not in NUMERICAL:
            findings.append(dict(path=name, rule='unqualified-numerical-source'))
    for name, expected in manifest['numerical_consumers'].items():
        path = root / name
        if not path.is_file() or sha(path.read_bytes()) != expected:
            findings.append(dict(path=name, rule='zero-equivalence-consumer-changed'))
    names = set(git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')) - {''}
    for name in sorted(names):
        path = root / name
        if name.startswith('data/') and name.endswith('.json') and name != MANIFEST and name not in listed:
            findings.append(dict(path=name, rule='unlisted-data'))
        if path.is_file():
            findings.extend(dict(path=name, rule=rule) for rule in inspect_bytes(name, path.read_bytes()))
    objects_checked = 0
    commits_checked = 0
    if history:
        objects = git(root, 'rev-list', '--objects', '--all').decode().splitlines()
        names_by_object = {}
        for tree in set(git(root, 'log', '--all', '--format=%T').decode().splitlines()):
            for entry in git(root, 'ls-tree', '-rz', '--full-tree', tree).split(b'\0'):
                if entry:
                    header, name = entry.split(b'\t', 1)
                    names_by_object.setdefault(header.split()[2].decode(), set()).add(name.decode())
        with subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
            for line in objects:
                oid, _, fallback = line.partition(' ')
                process.stdin.write((oid + '\n').encode()); process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise ValueError('unreadable Git object')
                kind, length = header[1], int(header[2])
                raw = process.stdout.read(length)
                if len(raw) != length or process.stdout.read(1) != b'\n':
                    raise ValueError('incomplete Git object')
                objects_checked += 1
                commits_checked += kind == b'commit'
                if kind in (b'blob', b'commit'):
                    for name in sorted(names_by_object.get(oid, {fallback})):
                        findings.extend(dict(object=oid, path=name, rule=rule) for rule in inspect_bytes(name, raw))
            process.stdin.close()
            if process.wait():
                raise ValueError('Git object reader failed')
    return dict(profile='apac-core-distribution-audit-v1', passed=not findings,
                data_files=len(listed), history_checked=history, objects_checked=objects_checked,
                commits_checked=commits_checked, findings=findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = audit(history=args.history)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
