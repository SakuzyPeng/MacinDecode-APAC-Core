"""Shared identities and bounded experiment definitions; no target tables."""
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ('mode1', 'mode2:0', 'mode2:1', 'mode3', 'mode4:0', 'mode4:1', 'mode4:2', 'mode4:3')
POLICY_VERSION = 'hoa-blackbox-order3-q6-q7-qualified-priors-v4'
GAINS = (128, 129)
N = 16
SYMBOLS = 320
MAX_DEPTH = 32
REPEAT_EPS = 1 / (32 * 63)
LEAF_EPS = 1 / (8 * 63)
PCM_BYTES = 2048 * 16 * 4
DEFAULT_LIMITS = dict(max_bytes=512 * 1024**2, max_calls=4096, min_free=1024**3)


class ExperimentError(Exception):
    """A target-specific observability or validation failure."""


class EvidenceError(ExperimentError):
    """Stop the whole batch: evidence is missing or inconsistent."""


class IdentityError(EvidenceError):
    pass


class BudgetStop(ExperimentError):
    pass


def require(condition, message, error=ExperimentError):
    if not condition:
        raise error(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def tool_fingerprint():
    paths = [ROOT / 'scripts/hoa_blackbox.py', ROOT / 'data/sq-codebooks.json']
    paths += sorted(Path(__file__).parent.glob('*.py'))
    return digest(canonical(dict(files={str(p.relative_to(ROOT)): file_digest(p) for p in paths},
                                 policy=POLICY_VERSION, python=sys.version)))


def target_parts(target):
    require(target in TARGETS, 'unsupported target: ' + target)
    if target in ('mode1', 'mode3'):
        return int(target[-1]), None
    return int(target[4]), int(target[-1])


def pcm_samples(raw):
    require(len(raw) in (PCM_BYTES, 3 * 1024 * 16 * 4, 4 * 1024 * 16 * 4),
            'wrong PCM byte count', EvidenceError)
    values = array('f')
    values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    require(all(math.isfinite(v) for v in values), 'nonfinite PCM', EvidenceError)
    return values


def install_discovery_guard():
    """Reject recorded dictionaries and debugger helpers, including imports."""
    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes)):
            value = args[0].decode() if isinstance(args[0], bytes) else args[0]
            path = Path(value).absolute()
            if ('/data/hoa-salient' in str(path) or path.name in (
                    'hoa_salient_format.py', 'hoa_measured_batch_sources.py',
                    'generate_hoa_salient_measured.py', 'generate_hoa_salient_measured_matrix.py',
                    'verify_hoa_salient_format.py')
                    or path.name.startswith('native_') and path.name.endswith('_trace.py')
                    or path.name == 'comparison.json'):
                raise EvidenceError('reference data is forbidden in discovery: ' + str(path))
    sys.addaudithook(audit)
