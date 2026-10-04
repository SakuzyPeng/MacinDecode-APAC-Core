"""Native compatibility metrics are separate from independent mathematical truth."""
import hashlib
import struct

from validate import require
from validate_portable import compare_pcm


def compare(record, candidate, reference, key='pcm_metrics'):
    # Shape, finite samples and timing are never exempted as native roundoff.
    require(len(candidate) == len(reference) and len(candidate) > 0 and len(candidate) % 4 == 0,
            'native PCM length mismatch')
    count = len(candidate) // 4
    record[key] = compare_pcm(struct.unpack(f'<{count}f', candidate),
                              struct.unpack(f'<{count}f', reference))
    record['pcm_sha256'] = hashlib.sha256(candidate).hexdigest()
    record['native_pcm_sha256'] = hashlib.sha256(reference).hexdigest()


def finalize(report, records, key='pcm_metrics'):
    """Keep structural success, observed compatibility and strict exit status distinct.

    Call after structural/coverage checks set `passed`. This tool never claims
    an independent math pass; that requires the portable Decimal validators.
    """
    metrics = [r[key] for r in records if key in r]
    failed = sum(not m['passed'] for m in metrics)
    report['native_pcm_comparison'] = dict(
        profile='apac-native-pcm-diagnostic-v1', atol=1e-6, rtol=1e-5,
        compared=len(metrics), failed=failed,
        passed=bool(metrics) and failed == 0,
        required=report.get('require_native_pcm', False))
    report['independent_math_verified'] = False
    report['qualification'] = 'native_structure_and_state'
    report['structural_passed'] = report['passed']
    if not report['structural_passed']:
        return 1
    if report.get('require_native_pcm') and not report['native_pcm_comparison']['passed']:
        report['passed'] = False
        return 2
    return 0
