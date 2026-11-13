"""Explicit CI execution context; ordinary research validators retain their defaults.

Only ci_validate configures this module, in a fresh process per validator. A
collection executes the real decoder and structural checks, but no Decimal PCM
oracle. Its report is never eligible as an independent mathematical reference.
"""
import json
import subprocess


def requires_bwe2(truth):
    """Read activity from independently generated wire truth, including preroll."""
    if isinstance(truth, dict):
        data = truth.get('bwe2')
        if isinstance(data, dict) and any(channel.get('active') for channel in data.get('channels', [])):
            return True
        return any(requires_bwe2(value) for value in truth.values()
                   if isinstance(value, (dict, list, tuple)))
    if isinstance(truth, list) and truth and not isinstance(truth[0], (dict, list, tuple)):
        return False
    if isinstance(truth, (list, tuple)):
        return any(requires_bwe2(value) for value in truth if isinstance(value, (dict, list, tuple)))
    return False


def reject_bwe2_case(binary, source, root, report, identity, generated, group='cases'):
    """Verify the first active BWE2 packet and retain the case's frozen identity."""
    first = next((i for i, (_, truth) in enumerate(generated) if requires_bwe2(truth)), None)
    if first is None:
        return False
    destination = root / 'bwe2-unavailable'
    process = subprocess.run([str(binary), 'decode-sq', str(source), '--out', str(destination)],
                             capture_output=True, text=True, encoding='utf-8', timeout=120)
    if process.returncode != 1:
        raise AssertionError('active BWE2 must be rejected with exit 1')
    error = json.loads(process.stdout or process.stderr)['error']
    if ('bwe2-unavailable' not in error.get('message', '')
            or type(error.get('packet_index')) is not int or error.get('packet_index') != first
            or type(error.get('bit_offset')) is not int):
        raise AssertionError('unexpected BWE2 rejection: ' + str(error))
    if (destination / 'pcm.json').exists() or not (destination / '.incomplete.json').is_file():
        raise AssertionError('failed BWE2 output was marked complete')
    stable = {key: error[key] for key in ('operation', 'message', 'packet_index', 'bit_offset')}
    report[group].append(dict(identity, passed=True, bwe2_unavailable=True, error=stable))
    return True


def partition_bwe2_batch(binary, root, report, batch, generated, identity, bundle):
    """Keep supported sequences batched and check every unavailable sequence."""
    supported = []
    packets = []
    cursor = 0
    for entry in batch:
        sequence = generated[cursor:cursor + len(entry[-1])]
        cursor += len(entry[-1])
        if any(requires_bwe2(truth) for _, truth in sequence):
            directory = root / ('unavailable-' + str(entry[0]))
            directory.mkdir()
            bundle(directory / 'packets', [raw for raw, _ in sequence])
            if not reject_bwe2_case(binary, directory / 'packets', directory, report,
                                    identity(entry), sequence, group='sequences'):
                raise AssertionError('BWE2 activity changed during partitioning')
        else:
            supported.append(entry)
            packets.extend(sequence)
    if cursor != len(generated):
        raise AssertionError('batch does not cover its generated packets')
    return supported, packets

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
    error = require_hoa_limit_rejection(binary, directory, root)
    report['cases'].append(dict(identity, passed=True, expected='rejection', error=error))
    return True


def require_hoa_limit_rejection(binary, directory, root):
    """Check the product support boundary for a caller-declared high-order input."""
    result = subprocess.run([str(binary), 'decode-sq', str(directory), '--out', str(root / 'rejected')],
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode != 1:
        raise AssertionError('expected unsupported HOA rejection, exit=' + str(result.returncode))
    error = json.loads(result.stdout or result.stderr)['error']
    if error.get('operation') not in ('parse-cookie', 'SQ decoder') or not any(
            text in error.get('message', '') for text in (
                'HOA order exceeds the verified 0..10 range', 'HOA coefficient count exceeds 121')):
        raise AssertionError('unexpected rejection: ' + str(error))
    pcm = root / 'rejected' / 'pcm.f32le'
    if pcm.exists() and pcm.stat().st_size:
        raise AssertionError('unsupported HOA produced PCM')
    return error
