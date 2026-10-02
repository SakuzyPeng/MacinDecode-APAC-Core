"""Portable executable discovery for CLI acceptance tests."""
import os
from pathlib import Path


def default_binary():
    override = os.environ.get('APAC_TOOL_BINARY')
    if override:
        return Path(override).resolve()
    name = 'apac-tool.exe' if os.name == 'nt' else 'apac-tool'
    return Path(__file__).resolve().parents[1] / 'target/debug' / name


def required_binary():
    binary = default_binary()
    if not binary.is_file():
        raise FileNotFoundError('build apac-tool or set APAC_TOOL_BINARY: ' + str(binary))
    return binary


def assert_hoa_fast(test, source, destination):
    """A qualified legacy HOA file now accepts fast with identical PCM/state."""
    import json
    fast = test.run_tool('decode-sq', source, '--out', destination, '--access', 'fast')
    test.assertEqual(fast.returncode, 0, fast.stderr)
    sequential_path = test.path()
    sequential = test.run_tool('decode-sq', source, '--out', sequential_path, '--access', 'sequential')
    test.assertEqual(sequential.returncode, 0, sequential.stderr)
    a, b = json.loads(fast.stdout), json.loads(sequential.stdout)
    test.assertEqual(a['access']['profile'], 'apac-hoa-access-v1')
    test.assertTrue(a['input']['consistency_verified'])
    test.assertEqual(a['saved_frames'], b['saved_frames'])
    test.assertEqual((destination / 'pcm.f32le').read_bytes(), (sequential_path / 'pcm.f32le').read_bytes())
    for key in ('metadata_before_output_sha256', 'metadata_after_processing_sha256'):
        test.assertEqual(a['access'][key], b['access'][key])
