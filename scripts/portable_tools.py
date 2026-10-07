"""Portable executable discovery for CLI acceptance tests."""
import os
from pathlib import Path


def default_binary():
    override = os.environ.get('MAPAC_BINARY')
    if override:
        return Path(override).resolve()
    name = 'mapac.exe' if os.name == 'nt' else 'mapac'
    return Path(__file__).resolve().parents[1] / 'target/debug' / name


def required_binary():
    binary = default_binary()
    if not binary.is_file():
        raise FileNotFoundError('build mapac or set MAPAC_BINARY: ' + str(binary))
    return binary


def assert_hoa_configuration(test, options, rate=48000):
    """Encode a matching silent payload for an expanded HOA configuration.

    Changing only a cookie can change descriptor widths or field presence;
    an old payload is then malformed, not evidence of an unsupported config.
    """
    import json
    from hoa_shared_vectors import bundle, packet
    spec = dict(components=[dict(type=2, options=options)], rate=rate)
    root = test.path()
    bundle(root, [packet(dict(components=[{}]), **spec)[0]], **spec)
    parsed = test.run_tool('parse-packets', root, '--depth', 'hoa', '--output', root/'parsed')
    test.assertEqual(parsed.returncode, 0, parsed.stderr)
    report = json.loads((root/'parsed').read_text())['report']
    test.assertTrue(report['packet_complete'])
    destination = test.path()
    result = test.run_tool('decode-sq', root, '--out', destination)
    test.assertEqual(result.returncode, 0, result.stderr)
    decoded = json.loads(result.stdout)
    channels = (options.get('order', 3) + 1)**2
    test.assertEqual(decoded['saved_frames'], 1024)
    test.assertEqual(decoded['pcm']['channels'], channels)
    test.assertEqual(decoded['pcm']['sample_rate'], rate)
    test.assertEqual((destination/'pcm.f32le').read_bytes(), bytes(1024 * channels * 4))
    return report


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
