#!/usr/bin/env python3
"""Prepare or verify small CI executable artifacts; never transfer target caches."""
import argparse
import json
from pathlib import Path
import shutil

from ci_catalog import TARGETS
from ci_evidence import commit, probe
from validate import require
from validate_portable import source_digest
from validate_replay import sha256_file


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('prepare', 'verify'))
    p.add_argument('--target', choices=TARGETS, required=True)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    suffix = '.exe' if 'windows' in a.target else ''
    source = source_digest()
    for profile in ('debug', 'release'):
        directory = a.root / profile
        cli, presence = directory / ('mapac' + suffix), directory / ('layout_presence' + suffix)
        if a.operation == 'prepare':
            require(not directory.exists(), 'refusing to overwrite tool artifact')
            directory.mkdir(parents=True)
            built = Path('target') / a.target / profile
            shutil.copy2(built / cli.name, cli)
            shutil.copy2(built / 'examples' / presence.name, presence)
            build = probe(cli, a.target, profile)
            manifest = dict(schema='apac-ci-tools-v1', build=build, presence_sha256=sha256_file(presence))
            (directory / 'tools.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        else:
            manifest = json.loads((directory / 'tools.json').read_text(encoding='utf-8'))
            build = manifest['build']
            require(manifest['schema'] == 'apac-ci-tools-v1', 'wrong tools schema')
            require((build['target'], build['build_profile']) == (a.target, profile), 'wrong artifact target/profile')
            require(build['code_commit'] == commit() and build['source_sha256'] == source, 'artifact commit/source differs')
            require(sha256_file(cli) == build['binary_sha256'] and sha256_file(presence) == manifest['presence_sha256'], 'artifact binary checksum differs')
            if suffix == '':
                cli.chmod(0o755)
                presence.chmod(0o755)
            require(probe(cli, a.target, profile) == build, 'artifact executable identity differs')
    require(source_digest() == source, 'source changed while handling tools')


if __name__ == '__main__':
    main()
