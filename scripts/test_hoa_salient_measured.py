"""Check measurement provenance and migration without native decoder access."""
import copy
import json
import unittest

from generate_hoa_salient_measured import (
    BOOK_SHA256, from_candidate, load_measurement, measured_book, regenerate, replace_book,
)
from hoa_salient_format import DATA, format_for, format_name, shared_name


class MeasuredCodebookTests(unittest.TestCase):
    def test_measurement_matches_generated_packed_copy(self):
        measurement = load_measurement()
        self.assertEqual(measurement['book_sha256'], BOOK_SHA256)
        current = format_for(3, 6)
        self.assertEqual(measured_book(measurement), current['modes'][1]['codebooks'][0])
        self.assertEqual(replace_book(current, measurement), current)

    def test_replacement_restores_book_and_preserves_every_other_table(self):
        original = format_for(3, 6)
        changed = copy.deepcopy(original)
        entries = changed['modes'][1]['codebooks'][0]
        entries[0], entries[1] = entries[1], entries[0]
        result = replace_book(changed, load_measurement())
        self.assertEqual(result, original)
        self.assertNotEqual(changed, original)
        with self.assertRaisesRegex(ValueError, 'scope'):
            replace_book(format_for(2, 6), load_measurement())

    def test_candidate_requires_the_frozen_file(self):
        with self.assertRaisesRegex(ValueError, 'unverified frozen candidate'):
            from_candidate(b'{}')

    def test_regeneration_does_not_depend_on_the_previous_packed_book(self):
        stored = json.loads((DATA / format_name(3, 6)).read_text())
        shared = json.loads((DATA / shared_name(3)).read_text())
        broken = copy.deepcopy(stored)
        broken['modes'][1]['codebooks'][0] = 'not-a-codebook'
        rebuilt, common = regenerate(broken, shared, load_measurement())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)
        # Damage to another dictionary must not be hidden by the replacement.
        broken['modes'][2]['codebooks'][0] = 'not-a-codebook'
        with self.assertRaises(ValueError):
            regenerate(broken, shared, load_measurement())

    def test_valid_but_relabelled_tree_is_not_the_measurement(self):
        measurement = load_measurement()
        a, b = measurement['entries'][:2]
        a['codeword'], b['codeword'] = b['codeword'], a['codeword']
        a['bit_length'], b['bit_length'] = b['bit_length'], a['bit_length']
        with self.assertRaisesRegex(ValueError, 'digest'):
            measured_book(measurement)

    def test_incomplete_symbols_and_changed_provenance_are_rejected(self):
        for change, message in (
            (lambda m: m['entries'].pop(), '64 symbols'),
            (lambda m: m['entries'][1].update(symbol=0), 'ordered and unique'),
            (lambda m: m['entries'][0].update(codeword='x'*9), 'codeword'),
            (lambda m: m.update(mode=4), 'scope'),
            (lambda m: m['source'].update(architecture='x86_64'), 'source'),
        ):
            measurement = load_measurement()
            change(measurement)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                measured_book(measurement)


if __name__ == '__main__':
    unittest.main()
