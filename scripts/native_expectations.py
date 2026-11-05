"""Version-specific public API expectations for two frozen synthetic inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from native_component import CURRENT_SHA256, LEGACY_SHA256, identity, unchanged


COOKIE_SHA256 = 'd320bcca35f96895b6617afc78021424ed7a7aef79a86e1f64e243b4bbad8c30'
PACKET_SHA256 = {
    'dimension-49-to-16': 'ad2ded47aeddc281a92267bd98b284ba0380e0905d42a7c75a2644ed6881a05c',
    'dynamic-prefix-49-to-16': '8f092ec4d1bdca0efb620b5a49a03238e3eda0d68c8994022caa4266981aae5d',
}
COOKIE_OPERATIONS = {
    'default': 'AudioConverterSetProperty(DecompressionMagicCookie) (create packet decoder)',
    'drc-off': 'AudioConverterSetProperty(DecompressionMagicCookie/replay) (set cookie after processing policy)',
}


def input_identity(bundle):
    cookie = hashlib.sha256((bundle / 'cookie.bin').read_bytes()).hexdigest()
    payload = hashlib.sha256((bundle / 'packets.bin').read_bytes()).hexdigest()
    manifest = json.loads((bundle / 'manifest.json').read_text())
    info = manifest['file']
    return dict(cookie_sha256=cookie, packet_data_sha256=payload,
                manifest_sha256=hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest(),
                packet_index_sha256=hashlib.sha256((bundle / 'packets.jsonl').read_bytes()).hexdigest(),
                channels=info['format']['channels'], sample_rate=info['format']['sample_rate'],
                frames=info['packet_table']['value']['valid_frames'],
                layout_tag=info['layout']['value']['tag'])


def expected_exit(component_sha256, inputs):
    if inputs['cookie_sha256'] != COOKIE_SHA256 or inputs['packet_data_sha256'] not in PACKET_SHA256.values():
        return None
    frames = 1024 if inputs['packet_data_sha256'] == PACKET_SHA256['dimension-49-to-16'] else 5120
    if any(inputs.get(key) != value for key, value in
           dict(channels=16, sample_rate=48000, frames=frames, layout_tag=12451856).items()):
        return None
    return {LEGACY_SHA256: 0, CURRENT_SHA256: 1}.get(component_sha256)


def validate_result(component_sha256, inputs, returncode, result, policy):
    expected = expected_exit(component_sha256, inputs)
    if expected is None or returncode != expected:
        raise ValueError('native acceptance differs from the qualified input/version expectation')
    if expected == 1:
        error = result.get('error', {})
        if (error.get('os_status') != 560226676 or error.get('os_status_fourcc') != '!dat'
                or policy not in COOKIE_OPERATIONS or error.get('operation') != COOKIE_OPERATIONS[policy]):
            raise ValueError('native rejection status or stage differs')
    elif not result.get('complete'):
        raise ValueError('legacy native acceptance is incomplete')


def collect(binary, bundle):
    binary_sha256 = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    component = identity()
    inputs = input_identity(bundle)
    if expected_exit(component['component_sha256'], inputs) is None:
        raise ValueError('input/version has no registered native expectation')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    frames = manifest['file']['packet_table']['value']['valid_frames']
    records = []
    for policy in ['default', 'drc-off']:
        with tempfile.TemporaryDirectory(prefix='apac-native-acceptance-') as temporary:
            command = [str(binary), 'replay', str(bundle), '--out', str(Path(temporary) / 'pcm'),
                       '--frames', str(frames), '--processing-policy', policy]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            observed = json.loads(result.stdout or result.stderr)
            validate_result(component['component_sha256'], inputs, result.returncode, observed, policy)
            records.append(dict(policy=policy, returncode=result.returncode, result=observed))
    unchanged(component)
    if hashlib.sha256(Path(binary).read_bytes()).hexdigest() != binary_sha256:
        raise ValueError('native reference executable changed during validation')
    if inputs != input_identity(bundle):
        raise ValueError('native acceptance input changed during validation')
    return dict(**component, **inputs, policies=records, passed=True,
                qualification='versioned_public_api_acceptance',
                binary_sha256=binary_sha256)


def validate_record(record, bundle):
    if not record.get('passed') or record.get('qualification') != 'versioned_public_api_acceptance':
        raise ValueError('incomplete native acceptance record')
    inputs = input_identity(bundle)
    if any(record.get(key) != value for key, value in inputs.items()):
        raise ValueError('native acceptance record is bound to a different input')
    records = record.get('policies', [])
    if len(records) != 2 or {row['policy'] for row in records} != {'default', 'drc-off'}:
        raise ValueError('native acceptance record is missing a processing policy')
    for row in records:
        validate_result(record['component_sha256'], inputs, row['returncode'], row['result'], row['policy'])
    return True


def cases():
    import hoa_dynamic_domains_vectors as vectors
    options = vectors.options(49, 16)
    # Frozen one-frame control produced by the original independent dimension writer.
    payload = bytes.fromhex('4816410a0000088208208208208208208208308208208208208208208208208208208208208208208208208000')
    yield 'dimension-49-to-16', options, [payload]
    for name, options, sequence in vectors.native_controls():
        if name == 'prefix':
            yield 'dynamic-prefix-49-to-16', options, [vectors.packet(case, **options)[0] for case in sequence]
