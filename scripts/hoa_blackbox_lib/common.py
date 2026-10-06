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
POLICY_VERSION = 'hoa-blackbox-orders1-10-q6-q9-campaign-v12'
GAINS = (128, 129)
N = 16
SYMBOLS = 320
MAX_DEPTH = 32
REPEAT_EPS = 1 / (32 * 63)
LEAF_EPS = 1 / (8 * 63)
PCM_BYTES = 2048 * 16 * 4
DEFAULT_LIMITS = dict(max_bytes=512 * 1024**2, max_calls=4096, min_free=1024**3)


def geometry(order=3):
    require(type(order) is int and 1 <= order <= 10, 'unsupported HOA order')
    channels = (order + 1)**2
    components = min(5, channels)
    profile, level = (5, 0) if channels <= 16 else (5, 1) if channels <= 36 else (5, 2) if channels <= 49 else (0, 0)
    return dict(order=order, channels=channels, components=components, bands=4,
                symbols=channels*components*4, layout_tag=(190 << 16) | channels,
                profile=profile, level=level)


class ExperimentError(Exception):
    """A target-specific observability or validation failure."""


class EvidenceError(ExperimentError):
    """Stop the whole batch: evidence is missing or inconsistent."""


class IdentityError(EvidenceError):
    pass


class BudgetStop(ExperimentError):
    pass


class ShardBoundary(BudgetStop):
    """A planned checkpoint, distinct from exhausting a resource limit."""


def pcm_byte_count(order, frames=2048):
    require(frames in (2048, 3072, 4096), 'unsupported PCM frame count', EvidenceError)
    return frames * geometry(order)['channels'] * 4


def capture_reservation(request):
    signature = request['signature']
    raw = pcm_byte_count(signature.get('order', 3), signature['frames'])
    require(signature['channels'] == geometry(signature.get('order', 3))['channels'],
            'capture geometry differs', EvidenceError)
    inputs = sum(p['bytes'] for p in request['packets'])
    # Raw output, a worst-case gzip copy, its verification buffer on disk,
    # input bundle, journal/sidecars and a safety margin. Preserve the old floor.
    return max(2 * 1024**2, 3*raw + 2*inputs + 256*1024)


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
    paths = producer_paths()
    return digest(canonical(dict(files={str(p.relative_to(ROOT)): file_digest(p) for p in paths},
                                 policy=POLICY_VERSION, python=sys.version)))


def producer_paths():
    return [ROOT / 'scripts/hoa_blackbox.py', ROOT / 'data/sq-codebooks.json',
            *sorted(Path(__file__).parent.glob('*.py'))]


def target_parts(target):
    require(target in TARGETS, 'unsupported target: ' + target)
    if target in ('mode1', 'mode3'):
        return int(target[-1]), None
    return int(target[4]), int(target[-1])


def pcm_samples(raw, channels=16):
    require(channels in tuple((o+1)**2 for o in range(1, 11))
            and len(raw) in tuple(frames*channels*4 for frames in (2048, 3072, 4096)),
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
