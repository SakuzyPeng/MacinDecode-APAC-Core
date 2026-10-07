#!/usr/bin/env python3
"""Refuse release assets built from a different commit than an existing tag."""
import argparse
import re
import subprocess
import sys


def verify_release_tag(tag, commit, remote='origin', *, require_existing=False):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('expected a full Git commit SHA-1')
    ref = 'refs/tags/' + tag
    subprocess.run(['git', 'check-ref-format', ref], check=True)
    # A successful empty response means the tag does not exist yet. Transport
    # failures must propagate, rather than being treated as permission to publish.
    output = subprocess.check_output(
        ['git', 'ls-remote', '--tags', remote, ref, ref + '^{}'], text=True)
    refs = {name: sha for sha, name in (line.split() for line in output.splitlines())}
    # Annotated tags have their own object SHA; compare the peeled commit.
    actual = refs.get(ref + '^{}', refs.get(ref))
    if actual is None and require_existing:
        raise ValueError(f'{ref} no longer exists; refusing to modify release assets')
    if actual is not None and actual != commit:
        raise ValueError(f'{ref} points to {actual}, but the packages were built from {commit}; '
                         'refusing to modify release assets')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--remote', default='origin')
    parser.add_argument('--require-existing', action='store_true',
                        help='require the tag to still exist for a tag-push build')
    args = parser.parse_args()
    try:
        actual = verify_release_tag(args.tag, args.commit, args.remote,
                                    require_existing=args.require_existing)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f'Release tag verification failed: {error}', file=sys.stderr)
        return 1
    print(f'{args.tag}: ' + ('matches build commit' if actual else 'no existing tag'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
