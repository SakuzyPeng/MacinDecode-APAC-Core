"""HOA format constants against their independent rules, and the vo-aacenc shared tables."""
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

import generate_hoa_shared_config_format as shared
import hoa_rules_oracle as oracle

FILES = ['hoa-dynamic-format-v1.json', 'hoa-dynamic-format-v2.json', 'hoa-salient-subbands-format-v1.json',
         'hoa-salient-subbands-format-v2.json', 'sq-codebooks.json', 'hoa-static-ambient-tables-v1.json',
         'hoa-source-layout-format-v1.json'] + [f'hoa-salient-order{n}-shared-v1.json' for n in range(1, 11)]


class HoaRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hoa-rules-')
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        for name in FILES:
            shutil.copy(oracle.DATA / name, self.data / name)
        original = oracle.DATA
        oracle.DATA = self.data
        self.addCleanup(setattr, oracle, 'DATA', original)

    def edit(self, name, change):
        path = self.data / name
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def test_committed_constants_follow_the_rules(self):
        self.assertEqual(oracle.check_subbands(), dict(grids=48, exceptions=1))
        self.assertEqual(oracle.check_salient_groups(), dict(orders=10))
        self.assertEqual(oracle.check_static_ambient(), dict(tables=3))
        layouts = oracle.check_source_layouts()
        self.assertEqual((layouts['full_rank'], layouts['rank_deficient']), (18, 18))
        self.assertLess(layouts['max_relative_error'], 5e-7)

    def test_rules_reproduce_known_values(self):
        # Equal width rounds to a long-window line first: 1024*2/9 -> 228 -> 28.5 -> 29.
        self.assertEqual(oracle.subband_ends(2, 9, None)[1], 29)
        self.assertEqual(oracle.subband_ends(0, 4, None), [4, 10, 27, 128])
        stereo = oracle.layout_matrix(1, [(30, 0), (-30, 0)])
        for got, want in zip(stereo[0], [4 / 13, 2 / 3 ** 0.5, 0, 6 / 13]):
            self.assertAlmostEqual(got, want, places=12)
        self.assertIsNone(oracle.layout_matrix(1, [(30, 0), (-30, 0), (110, 0), (-110, 0)]))

    def test_perturbed_tables_are_rejected(self):
        self.edit('hoa-salient-subbands-format-v2.json', lambda v: v['tables'][0]['short_ends'].__setitem__(0, 4))
        with self.assertRaises(AssertionError):
            oracle.check_subbands()
        self.edit('hoa-salient-order3-shared-v1.json', lambda v: v['groups'].reverse())
        with self.assertRaisesRegex(AssertionError, 'order 3'):
            oracle.check_salient_groups()
        self.edit('hoa-static-ambient-tables-v1.json', lambda v: v['encoder_signs'][1].reverse())
        with self.assertRaisesRegex(AssertionError, 'Sylvester'):
            oracle.check_static_ambient()

        def nudge(value):
            key = next(layout['matrix_id'] for layout in value['layouts'] if layout['matrix_rows'] == 2)
            first = struct.unpack('<f', struct.pack('<I', value['matrices'][key][0]))[0]
            value['matrices'][key][0] = struct.unpack('<I', struct.pack('<f', first * (1 + 1e-4)))[0]
        self.edit('hoa-source-layout-format-v1.json', nudge)
        with self.assertRaisesRegex(AssertionError, 'relative error'):
            oracle.check_source_layouts()

    def test_shared_configuration_rebuilds_from_aac_tables(self):
        committed = json.loads(shared.PATH.read_text())
        legacy = json.loads((oracle.DATA / 'sq-codebooks.json').read_text())
        arrays = dict(committed['offset_arrays'], **{'legacy-long': legacy['long_offsets'],
                                                     'legacy-short': legacy['short_offsets']})
        rows = [r for r in committed['rates'] if r['sample_rate'] != 7350]
        sfb = [dict(sample_rate=r['sample_rate'], long_offsets=arrays[r['long']], short_offsets=arrays[r['short']])
               for r in rows]
        tns = [dict(rate=r['sample_rate'], long_limit=r['tns_long_limit'], short_limit=r['tns_short_limit']) for r in rows]
        self.assertEqual(shared.generate(sfb, tns, committed['profiles']), committed)


if __name__ == '__main__':
    unittest.main()
