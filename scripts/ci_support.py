"""Explicit CI execution context; ordinary research validators retain their defaults.

Only ci_validate configures this module, in a fresh process per validator. A
collection executes the real decoder and structural checks, but no Decimal PCM
oracle. Its report is never eligible as an independent mathematical reference.
"""
import json
import subprocess

CONTEXT = None


def checked_reference(reference):
    if reference is None:
        return None
    if not isinstance(reference, dict) or reference.get('passed') is not True:
        raise ValueError('reference report must be an object with passed=true')
    if reference.get('mode') == 'fingerprint_collection' or reference.get('ci_profile') == 'ci-fast':
        raise ValueError('collection/partial CI report is not a complete mathematical reference')
    return reference


def mathematics(reference=None):
    return reference is None and (CONTEXT is None or CONTEXT['mode'] == 'math')


def selected(stage, items):
    indices = CONTEXT.get('selection', {}).get(stage) if CONTEXT else None
    if indices is None:
        yield from items
    else:
        wanted = set(indices)
        for index, item in enumerate(items):
            if index in wanted:
                yield item


def case_index(stage, offset):
    indices = CONTEXT.get('selection', {}).get(stage) if CONTEXT else None
    return offset if indices is None else indices[offset]


def case_counts(counts, validator):
    return {stage: 2 * len(CONTEXT['selection'][stage]) for stage in counts} if CONTEXT and CONTEXT.get('validator') == validator and CONTEXT.get('selection') else counts


def rejection(binary, directory, root, report, identity):
    """Check an explicitly catalogued unsupported input, never infer from output."""
    if CONTEXT is None or identity['index'] not in CONTEXT['rejections']:
        return False
    result = subprocess.run([str(binary), 'decode-sq', str(directory), '--out', str(root / 'rejected')],
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode != 1:
        raise AssertionError('expected unsupported HOA rejection, exit=' + str(result.returncode))
    error = json.loads(result.stdout or result.stderr)['error']
    if error.get('operation') != 'SQ decoder' or not any(text in error.get('message', '') for text in (
            'HOA implementation supports orders 0..3', 'order-3 implementation limit')):
        raise AssertionError('unexpected rejection: ' + str(error))
    pcm = root / 'rejected' / 'pcm.f32le'
    if pcm.exists() and pcm.stat().st_size:
        raise AssertionError('unsupported HOA produced PCM')
    report['cases'].append(dict(identity, passed=True, expected='rejection', error=error))
    return True
