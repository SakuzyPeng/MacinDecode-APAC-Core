#!/usr/bin/env python3
"""Require complete six-build evidence, qualified math, and exact stage equality."""
import argparse
import json
import os
from pathlib import Path

from ci_catalog import CELLS, REFERENCE, digest, load
from ci_evidence import check_evidence, commit
from validate import require
from validate_portable import source_digest
from validate_replay import sha256_file


def tool_builds(root, expected_commit, expected_source):
    builds = {}
    for path in root.rglob('tools.json'):
        manifest = json.loads(path.read_text(encoding='utf-8'))
        require(manifest['schema'] == 'apac-ci-tools-v1', 'unknown tool artifact schema')
        build = manifest['build']
        cell = (build['target'], build['build_profile'])
        require(cell in CELLS and cell not in builds, 'unexpected/duplicate tool artifact')
        require(build['code_commit'] == expected_commit and build['source_sha256'] == expected_source, 'wrong tool artifact revision')
        suffix = '.exe' if 'windows' in cell[0] else ''
        require(sha256_file(path.parent / ('mapac' + suffix)) == build['binary_sha256'], 'tool artifact checksum differs')
        require(sha256_file(path.parent / ('layout_presence' + suffix)) == manifest['presence_sha256'], 'presence artifact checksum differs')
        builds[cell] = build
    require(set(builds) == set(CELLS), 'missing tool artifacts')
    return builds


def upstream(needs, full):
    expected = {'build': 'success', 'full_math': 'success' if full else 'skipped',
                'full_collect': 'success' if full else 'skipped'}
    require(set(needs) == set(expected), 'missing/extra upstream jobs')
    for name, result in expected.items():
        require(needs[name]['result'] == result, name + ' did not complete as required')


def check(documents, profiles, expected_commit, expected_source, catalog=None):
    catalog = catalog or load()
    wanted = {(profile, name, cell) for profile in profiles
              for name in catalog['profiles'][profile] for cell in CELLS}
    found, builds = {}, {}
    for doc in documents:
        profile, name = doc['profile'], doc['validator']
        require(profile in profiles and name in catalog['profiles'][profile], 'unexpected suite/validator')
        cell = check_evidence(doc, profile, name, expected_commit, expected_source, catalog)
        key = (profile, name, cell)
        require(key not in found, 'duplicate build/validator evidence: ' + str(key))
        found[key] = doc
        if cell in builds:
            require(builds[cell] == doc['build'], 'binary or compiler changed within build cell')
        builds[cell] = doc['build']
    require(set(found) == wanted, 'missing evidence: ' + str(sorted(wanted - set(found)))[:2000])
    for profile in profiles:
        for name in catalog['profiles'][profile]:
            reference = found[profile, name, REFERENCE]['payload']
            for cell in CELLS:
                candidate = found[profile, name, cell]['payload']
                if digest(candidate) != digest(reference):
                    from compare_reports import diff
                    differences = []
                    diff(reference, candidate, '$', differences, 8)
                    raise AssertionError(f'{profile}/{name}/{cell}: ' + '\n'.join(differences))
    return dict(passed=True, profiles=profiles, builds=len(builds), comparisons=len(found), code_commit=expected_commit)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reports', type=Path, required=True)
    p.add_argument('--full', action='store_true')
    p.add_argument('--commit', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    require(a.commit == commit(), 'checkout does not match requested commit')
    if 'CI_NEEDS' in os.environ:
        upstream(json.loads(os.environ['CI_NEEDS']), a.full)
    documents = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(a.reports.rglob('evidence.json'))]
    source = source_digest()
    builds = tool_builds(a.reports, a.commit, source)
    for doc in documents:
        cell = (doc['build']['target'], doc['build']['build_profile'])
        require(doc['build'] == builds[cell], 'report does not describe the uploaded executable')
    result = check(documents, ['ci-fast', 'ci-full'] if a.full else ['ci-fast'], a.commit, source)
    summaries = [json.loads(path.read_text(encoding='utf-8')) for path in a.reports.rglob('summary.json')]
    require(summaries and all(s.get('schema') == 'apac-ci-suite-v1' and s.get('passed') is True for s in summaries), 'failed/missing suite summary')
    result['timings'] = [{k: s[k] for k in ('profile', 'mode', 'shard', 'seconds')} |
                         {k: s['build'][k] for k in ('target', 'build_profile')} for s in summaries]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
