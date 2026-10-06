"""Production HOA order limits across the CLI and measured-source generators."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from caf_vectors import encode
from portable_tools import required_binary

ROOT = Path(__file__).resolve().parents[1]


class HoaSupportLimitTests(unittest.TestCase):
    def setUp(self):
        self.binary = required_binary()
        self.temporary = tempfile.TemporaryDirectory(prefix='apac-hoa-support-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def decode(self, row, channels, label, packet_field='packet'):
        cookie = bytes.fromhex(row['cookie'])
        packet = bytes.fromhex(row[packet_field])
        raw, _ = encode(cookie, [packet], channels=channels, layout_tag=(190 << 16) | channels)
        source = self.root / (label + '.caf')
        source.write_bytes(raw)
        destination = self.root / (label + '-pcm')
        result = subprocess.run([str(self.binary), 'decode-sq', str(source), '--out', str(destination)],
                                capture_output=True, text=True, timeout=30)
        return result, destination

    def test_supported_orders_still_decode_and_high_orders_remain_inspectable(self):
        data = json.loads((ROOT/'data/hoa-expanded-orders-state-v1.json').read_text())
        for i, row in enumerate(data['boundaries']):
            order = row['order']
            with self.subTest(order=order, index=i):
                cookie = self.root / f'cookie-{i}.bin'
                cookie.write_bytes(bytes.fromhex(row['cookie']))
                parsed = subprocess.run([str(self.binary), 'parse-cookie', str(cookie)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
                result, output = self.decode(row, (order+1)**2, f'complete-{i}')
                if order <= 3:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((output/'pcm.f32le').stat().st_size, 1024*(order+1)**2*4)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('HOA implementation supports orders 0..3', result.stderr)
                    self.assertFalse((output/'pcm.f32le').exists())

    def test_explicit_and_dynamic_domains_cannot_bypass_the_limit(self):
        partial = json.loads((ROOT/'data/hoa-partial-state-v1.json').read_text())
        for n in (17, 121):
            row = next(r for r in partial['boundaries'] if r['coefficients'] == n)
            result, output = self.decode(row, n, f'partial-{n}')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('HOA implementation supports orders 0..3', result.stderr)
            self.assertFalse((output/'pcm.f32le').exists())
        dynamic = json.loads((ROOT/'data/hoa-dynamic-domains-state-v1.json').read_text())
        row = next(r for r in dynamic['fixtures'] if r['name'] == 'third-fourth')
        result, output = self.decode(row, 25, 'dynamic-third-fourth', 'first')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HOA output coefficient domain 25', result.stderr)
        self.assertFalse((output/'pcm.f32le').exists())

    def test_source_generators_default_to_only_enabled_orders(self):
        results = {}
        for kind, script in (
            ('books', 'generate_hoa_salient_measured.py'),
            ('matrices', 'generate_hoa_salient_measured_matrix.py'),
            ('groups', 'generate_hoa_salient_measured_groups.py'),
        ):
            result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts'/script), '--check'],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            results[kind] = json.loads(result.stdout)
            refused = subprocess.run([sys.executable, '-B', str(ROOT/'scripts'/script), '--write', '--order', '10'],
                                     capture_output=True, text=True, timeout=30)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('production HOA support ends at order 3', refused.stderr)
        self.assertEqual(results['books']['books'], 96)
        self.assertEqual(results['books']['symbols'], 23040)
        self.assertEqual(len(results['matrices']['matrix_sha256']), 12)
        self.assertEqual(results['matrices']['coefficients'], 1412)
        self.assertEqual(results['groups']['orders'], [1, 2, 3])
        self.assertEqual(results['groups']['groups'], 9)


if __name__ == '__main__':
    unittest.main()
