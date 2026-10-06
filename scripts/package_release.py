#!/usr/bin/env python3
"""Package a CI executable and public documentation without local research data."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'x86_64-unknown-linux-gnu': ('apac-tool', '.tar.gz'),
    'x86_64-pc-windows-msvc': ('apac-tool.exe', '.zip'),
    'aarch64-apple-darwin': ('apac-tool', '.tar.gz'),
}


def create_package(binary, target, out, commit, *, binary_version, rust_version, root=ROOT):
    if target not in TARGETS:
        raise ValueError('unsupported release target: ' + target)
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('commit must be a full Git SHA-1')
    executable, extension = TARGETS[target]
    binary = Path(binary)
    root, out = Path(root), Path(out)
    name = f'apac-tool-{target}-{commit[:12]}'
    archive = out/(name + extension)
    checksum = out/(archive.name + '.sha256')
    if archive.exists() or checksum.exists():
        raise FileExistsError('release output already exists: ' + str(archive))

    raw = binary.read_bytes()
    metadata = dict(binary=executable, binary_version=binary_version,
                    binary_sha256=hashlib.sha256(raw).hexdigest(), target=target,
                    commit=commit, rust_version=rust_version)
    files = {executable: (raw, 0o755),
             'build-info.json': ((json.dumps(metadata, sort_keys=True, indent=2) + '\n').encode(), 0o644)}
    sources = [root/name for name in ('README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY.md')]
    sources += sorted((root/'LICENSES').glob('*.txt'))
    sources += sorted((root/'guide').glob('*.md'))
    if not (root/'LICENSES/Apache-2.0.txt').is_file():
        raise FileNotFoundError('required Apache-2.0 license is missing')
    for source in sources:
        if source.is_symlink():
            raise ValueError('release documentation must not be a symlink: ' + str(source))
        files[source.relative_to(root).as_posix()] = (source.read_bytes(), 0o644)

    out.mkdir(parents=True, exist_ok=True)
    if extension == '.zip':
        with zipfile.ZipFile(archive, 'x') as bundle:
            for relative, (data, mode) in sorted(files.items()):
                item = zipfile.ZipInfo(f'{name}/{relative}', date_time=(1980, 1, 1, 0, 0, 0))
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | mode) << 16
                bundle.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    else:
        with archive.open('xb') as stream:
            with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w') as bundle:
                    for relative, (data, mode) in sorted(files.items()):
                        item = tarfile.TarInfo(f'{name}/{relative}')
                        item.size, item.mode = len(data), mode
                        bundle.addfile(item, io.BytesIO(data))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    with checksum.open('x', encoding='ascii', newline='\n') as stream:
        stream.write(f'{sha}  {archive.name}\n')
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=TARGETS, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--binary', type=Path, help='default: target/TARGET/release/apac-tool[.exe]')
    args = parser.parse_args()
    executable = TARGETS[args.target][0]
    binary = (args.binary or ROOT/'target'/args.target/'release'/executable).resolve()
    version = subprocess.check_output([str(binary), '--version'], text=True).strip()
    subprocess.run([str(binary), '--help'], check=True, stdout=subprocess.DEVNULL)
    rust_version = subprocess.check_output(['rustc', '--version'], text=True).strip()
    archive, checksum = create_package(binary, args.target, args.out, args.commit,
                                      binary_version=version, rust_version=rust_version)
    print(json.dumps(dict(archive=str(archive), checksum=str(checksum), binary_version=version)))


if __name__ == '__main__':
    main()
