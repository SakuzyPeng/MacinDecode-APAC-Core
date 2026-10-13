"""Strict, fail-closed evidence validation shared by the producer and CI gate."""
import json
import math
import subprocess
import tempfile
from pathlib import Path

from ci_catalog import CELLS, REFERENCE, digest, group, load
from validate import require
from validate_portable import source_digest
from validate_replay import command, sha256_file

SCHEMA = 'apac-ci-evidence-v1'
# Exact keys only. Unknown/new fields participate in the comparison by default.
METADATA = {'created_at', 'started_utc', 'finished_utc', 'platform', 'architecture',
            'python', 'tested_worktree_dirty', 'failure_directory', 'compiler',
            'debug_assertions', 'binary_sha256', 'tool_sha256', 'presence_binary_sha256', 'mode'}
DIAGNOSTICS = {'metrics', 'pcm_metrics', 'spectral_metrics', 'spectral_summary',
               'spectral_metric_summary', 'portable_metrics', 'native_metrics',
               'max_absolute_error', 'max_relative_error', 'max_ulp', 'failed_samples', 'first_failure'}


def clean(value, root=True):
    if isinstance(value, dict):
        excluded = DIAGNOSTICS | (METADATA if root else {'compiler', 'debug_assertions'})
        return {k: clean(v, root=False) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [clean(v, root=False) for v in value]
    return value


def health(value):
    if isinstance(value, dict):
        require(value.get('passed') is not False, 'failed case or mathematical metric')
        require(value.get('failed_samples', 0) == 0, 'failed mathematical samples')
        require(value.get('first_failure') is None, 'mathematical failure coordinate')
        for child in value.values():
            health(child)
    elif isinstance(value, list):
        for child in value:
            health(child)
    elif isinstance(value, float):
        require(math.isfinite(value), 'nonfinite evidence')


def coverage(report, entry):
    require(report.get('passed') is True and not report.get('errors') and not report.get('failures'), 'validator did not pass')
    health(report)
    for key, expected in entry['groups'].items():
        rows = report.get(key)
        require(isinstance(rows, list), 'missing case group: ' + key)
        require(group(rows, expected['keys']) == expected, 'incomplete/changed case identities: ' + key)
    rejected = [r['index'] for r in report.get('cases', []) if r.get('expected') == 'rejection']
    require(sorted(rejected) == entry['rejections'], 'unsupported-case coverage differs')
    if 'presence' in entry:
        actual = report.get('presence', {})
        require(actual.get('passed') is True and actual.get('cases') == entry['presence']['cases']
                and actual.get('vector_manifest_sha256') == entry['presence']['sha256'], 'incomplete exhaustive presence evidence')


def commit():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def probe(binary, target, build_profile):
    """Read compiler/build identity from the executable, not its filename."""
    from spectrum_vectors import bundle, frame
    binary = binary.resolve(strict=True)
    fingerprint = sha256_file(binary)
    with tempfile.TemporaryDirectory(prefix='apac-ci-probe-') as tmp:
        root = Path(tmp)
        bundle(root / 'packets', [frame({})[0]], 48000)
        result = command(binary, 'decode-sq', root / 'packets', '--out', root / 'pcm')
        impl = result['pcm']['decoder_settings']['implementation']['value']
    compiler = impl['compiler']
    require(compiler.startswith('rustc 1.98.0 ') and 'host: ' + target in compiler, 'unexpected compiler/target: ' + compiler)
    require(impl['debug_assertions'] is (build_profile == 'debug'), 'executable build profile differs')
    require((target, build_profile) in CELLS, 'unknown build cell')
    require(sha256_file(binary) == fingerprint, 'binary changed during probe')
    return dict(target=target, build_profile=build_profile, binary_sha256=fingerprint,
                compiler=compiler, debug_assertions=impl['debug_assertions'],
                implementation=clean(impl, root=False), code_commit=commit(), source_sha256=source_digest())


def envelope(report, profile, name, mode, build):
    catalog = load()
    entry = catalog['profiles'][profile][name]
    coverage(report, entry)
    require(report.get('code_commit') == build['code_commit'], 'report commit differs')
    require(report.get('source_sha256', build['source_sha256']) == build['source_sha256'], 'report source differs')
    require(report.get('binary_sha256', report.get('tool_sha256')) == build['binary_sha256'], 'report binary differs')
    payload = clean(report)
    return dict(schema=SCHEMA, passed=True, profile=profile, validator=name, mode=mode,
                catalog_sha256=digest(catalog), entry_sha256=digest(entry), build=build,
                payload=payload, payload_sha256=digest(payload))


def check_evidence(evidence, profile, name, expected_commit, expected_source, catalog):
    require(evidence.get('schema') == SCHEMA and evidence.get('passed') is True, 'unqualified evidence')
    require(evidence.get('profile') == profile and evidence.get('validator') == name, 'suite/validator differs')
    require(evidence.get('catalog_sha256') == digest(catalog), 'catalog differs')
    entry = catalog['profiles'][profile][name]
    require(evidence.get('entry_sha256') == digest(entry), 'coverage profile differs')
    build = evidence['build']
    cell = (build['target'], build['build_profile'])
    require(cell in CELLS, 'unexpected build cell')
    require(build['code_commit'] == expected_commit and build['source_sha256'] == expected_source, 'wrong commit/source')
    require(build['compiler'].startswith('rustc 1.98.0 ') and 'host: ' + cell[0] in build['compiler'], 'wrong compiler/target')
    require(build['debug_assertions'] is (cell[1] == 'debug'), 'wrong optimization profile')
    require(evidence['mode'] == ('math' if cell == REFERENCE else 'collect'), 'wrong mathematical qualification')
    require(evidence['payload_sha256'] == digest(evidence['payload']), 'damaged payload')
    require(evidence['payload'].get('code_commit') == expected_commit, 'wrong payload commit')
    require(evidence['payload'].get('source_sha256', expected_source) == expected_source, 'wrong payload source')
    require(evidence['payload'].get('ci_profile') == profile, 'wrong payload coverage profile')
    coverage(evidence['payload'], entry)
    return cell
