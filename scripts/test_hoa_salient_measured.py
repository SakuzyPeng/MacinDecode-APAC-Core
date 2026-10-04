"""Check measurement provenance and migration without native decoder access."""
import copy
import json
import unittest

from generate_hoa_salient_measured import (
    BOOK_SHA256, from_candidate, load_measurement, load_measurements, measured_book, regenerate, replace_book,
)
from hoa_salient_format import DATA, format_for, format_name, shared_name


class MeasuredCodebookTests(unittest.TestCase):
    def test_measurement_matches_generated_packed_copy(self):
        current = format_for(3, 6)
        for mode in (1, 4):
            with self.subTest(mode=mode):
                measurement = load_measurement(mode)
                self.assertEqual(measurement['book_sha256'], BOOK_SHA256[mode])
                self.assertEqual(measured_book(measurement), current['modes'][mode]['codebooks'][0])
                self.assertEqual(replace_book(current, measurement), current)

    def test_replacement_restores_book_and_preserves_every_other_table(self):
        original = format_for(3, 6)
        for mode in (1, 4):
            with self.subTest(mode=mode):
                changed = copy.deepcopy(original)
                entries = changed['modes'][mode]['codebooks'][0]
                entries[0], entries[1] = entries[1], entries[0]
                result = replace_book(changed, load_measurement(mode))
                self.assertEqual(result, original)
                self.assertNotEqual(changed, original)
                with self.assertRaisesRegex(ValueError, 'scope'):
                    replace_book(format_for(2, 6), load_measurement(mode))

    def test_replacements_compose_without_losing_the_other_source(self):
        current = format_for(3, 6)
        original = copy.deepcopy(current)
        original['source'] = original['source']['original_observation']
        for order in ((1, 4), (4, 1)):
            result = original
            for mode in order:
                result = replace_book(result, load_measurement(mode))
            self.assertEqual(result, current)

    def test_candidate_requires_the_frozen_file(self):
        for raw in (b'{}', b'{"mode":4,"cluster":0,"rows":16,"columns":16}'):
            with self.assertRaisesRegex(ValueError, 'unverified frozen candidate'):
                from_candidate(raw)

    def test_regeneration_does_not_depend_on_the_previous_packed_book(self):
        stored = json.loads((DATA / format_name(3, 6)).read_text())
        shared = json.loads((DATA / shared_name(3)).read_text())
        broken = copy.deepcopy(stored)
        for mode in (1, 4):
            broken['modes'][mode]['codebooks'][0] = 'not-a-codebook'
        rebuilt, common = regenerate(broken, shared, load_measurements())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)
        # Damage to another dictionary must not be hidden by the replacement.
        broken['modes'][4]['codebooks'][1] = 'not-a-codebook'
        with self.assertRaises(ValueError):
            regenerate(broken, shared, load_measurements())

    def test_measurements_never_replace_the_shared_matrices(self):
        stored = json.loads((DATA / format_name(3, 6)).read_text())
        shared = json.loads((DATA / shared_name(3)).read_text())
        changed = copy.deepcopy(shared)
        words = bytearray.fromhex(changed['matrices_f32'][0])
        words[0] ^= 0x80
        changed['matrices_f32'][0] = words.hex()
        with self.assertRaisesRegex(ValueError, 'digest'):
            regenerate(stored, changed, load_measurements())

    def test_valid_but_relabelled_tree_is_not_the_measurement(self):
        for mode in (1, 4):
            measurement = load_measurement(mode)
            a, b = measurement['entries'][:2]
            a['codeword'], b['codeword'] = b['codeword'], a['codeword']
            a['bit_length'], b['bit_length'] = b['bit_length'], a['bit_length']
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'digest'):
                measured_book(measurement)

    def test_incomplete_symbols_and_changed_provenance_are_rejected(self):
        for mode in (1, 4):
            for change, message in (
                (lambda m: m['entries'].pop(), '64 symbols'),
                (lambda m: m['entries'][1].update(symbol=0), 'ordered and unique'),
                (lambda m: m['entries'][0].update(codeword='x'*9), 'codeword'),
                (lambda m: m.update(mode=2), 'scope'),
                (lambda m: m.update(book=1), 'scope'),
                (lambda m: m['source'].update(architecture='x86_64'), 'source'),
            ):
                measurement = load_measurement(mode)
                change(measurement)
                with self.subTest(mode=mode, message=message), self.assertRaisesRegex(ValueError, message):
                    measured_book(measurement)


if __name__ == '__main__':
    unittest.main()
