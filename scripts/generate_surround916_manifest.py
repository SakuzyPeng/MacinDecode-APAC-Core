#!/usr/bin/env python3
"""Freeze independent 9.1.6 inputs without expanding earlier layout matrices."""
import argparse
import json
from pathlib import Path
from channel_vectors import state_fixtures
from layout_vectors import math_manifest, access_manifest, presence_manifest

PROFILE = 'apac-channel-layout-v3'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'data'
    for name, value in (
        ('surround916-vectors-v1.json', math_manifest((16,), PROFILE)),
        ('surround916-access-vectors-v1.json', access_manifest((16,), PROFILE)),
        ('surround916-presence-v1.json', presence_manifest((16,), PROFILE)),
        ('surround916-state-fixtures-v1.json', state_fixtures((16,))),
    ):
        path = root / name
        if args.check:
            if json.loads(path.read_text(encoding='utf-8')) != value:
                raise SystemExit(name + ' differs')
        else:
            path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        print(name, value.get('sha256', ''), flush=True)


if __name__ == '__main__':
    main()
