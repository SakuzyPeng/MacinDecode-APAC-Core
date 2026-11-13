#!/usr/bin/env python3
"""Collect the license and copyright notices of every locked external Rust crate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
NOTICE = 'LICENSES/Rust-dependencies.txt'
LICENSE_NAME = re.compile(r'^(?:LICEN[CS]ES?|COPYING|COPYRIGHT|NOTICES?|UNLICENSE|AUTHORS?)(?:$|[._-])', re.I)


def notice_header(root):
    digest = hashlib.sha256((Path(root) / 'Cargo.lock').read_bytes()).hexdigest()
    return f'Rust dependency notices\n\nCargo.lock SHA-256 {digest}\n\n'


def verify_notice(root):
    root = Path(root)
    if not (root / NOTICE).read_text(encoding='utf-8').startswith(notice_header(root)):
        raise ValueError('Rust dependency notices differ from Cargo.lock; run scripts/generate_dependency_licenses.py')


def render_notices(root, metadata):
    root = Path(root)
    lock = tomllib.loads((root / 'Cargo.lock').read_text(encoding='utf-8'))
    expected = {(p['name'], p['version'], p['source']) for p in lock['package'] if p.get('source')}
    packages = sorted((p for p in metadata['packages'] if p.get('source')),
                      key=lambda p: (p['name'], p['version'], p['source']))
    actual = {(p['name'], p['version'], p['source']) for p in packages}
    if actual != expected or len(actual) != len(packages):
        raise ValueError('Cargo metadata must cover every locked external dependency exactly once')
    parts = [notice_header(root),
             'Includes all locked external workspace dependencies across supported targets,\n'
             'including build and test dependencies. Each package retains its own license terms.\n\n']
    for package in packages:
        directory = Path(package['manifest_path']).parent.resolve()
        paths = set()
        for entry in directory.iterdir():
            if LICENSE_NAME.match(entry.name):
                paths.update(entry.rglob('*') if entry.is_dir() else [entry])
        if package.get('license_file'):
            paths.add(directory / package['license_file'])
        paths = sorted((p for p in paths if p.is_file()),
                       key=lambda p: p.relative_to(directory).as_posix())
        if not paths:
            raise ValueError(f'License files are missing for {package["name"]} {package["version"]}')
        parts.append(f'Package {package["name"]} {package["version"]}\n'
                     f'License expression {package.get("license") or "See the included license files"}\n'
                     f'Source {package["source"]}\n\n')
        for path in paths:
            if not path.resolve().is_relative_to(directory):
                raise ValueError(f'License file escapes the crate directory for {package["name"]}')
            parts.append(f'--- {path.relative_to(directory).as_posix()} ---\n\n')
            parts.append(path.read_text(encoding='utf-8').rstrip('\n') + '\n\n')
    return ''.join(parts).rstrip('\n') + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    metadata = json.loads(subprocess.check_output(
        ['cargo', 'metadata', '--locked', '--format-version', '1'], cwd=ROOT, text=True))
    rendered = render_notices(ROOT, metadata)
    path = ROOT / NOTICE
    if args.check:
        if path.read_text(encoding='utf-8') != rendered:
            raise SystemExit('Rust dependency notices need regeneration')
    else:
        path.write_text(rendered, encoding='utf-8', newline='\n')
    print(json.dumps({'notice': NOTICE, 'packages': sum(bool(p.get('source')) for p in metadata['packages']),
                      'checked': args.check}))


if __name__ == '__main__':
    main()
