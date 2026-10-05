"""Check measurement provenance and migration without native decoder access."""
import copy
import json
import unittest

from generate_hoa_salient_measured import (
    BOOK_SHA256, from_candidate, load_measurement, load_measurements, measured_book, measured_group, regenerate, replace_book,
)
from hoa_salient_format import DATA, format_for, format_name, shared_name


class MeasuredCodebookTests(unittest.TestCase):
    def test_measurement_matches_generated_packed_copy(self):
        for precision, mode, book in BOOK_SHA256:
            current = format_for(3, precision)
            with self.subTest(precision=precision, mode=mode, book=book):
                measurement = load_measurement(mode, book, precision)
                self.assertEqual(measurement['book_sha256'], BOOK_SHA256[precision, mode, book])
                self.assertEqual(measured_book(measurement), current['modes'][mode]['codebooks'][book])
                self.assertEqual(replace_book(current, measurement), current)

    def test_replacement_restores_book_and_preserves_every_other_table(self):
        for precision, mode, book in BOOK_SHA256:
            original = format_for(3, precision)
            with self.subTest(precision=precision, mode=mode, book=book):
                changed = copy.deepcopy(original)
                entries = changed['modes'][mode]['codebooks'][book]
                entries[0], entries[1] = entries[1], entries[0]
                result = replace_book(changed, load_measurement(mode, book, precision))
                self.assertEqual(result, original)
                self.assertNotEqual(changed, original)
                with self.assertRaisesRegex(ValueError, 'scope'):
                    replace_book(format_for(2, precision), load_measurement(mode, book, precision))
                with self.assertRaisesRegex(ValueError, 'scope'):
                    replace_book(format_for(3, 13-precision), load_measurement(mode, book, precision))

    def test_replacements_compose_without_losing_the_other_source(self):
        for precision in (6, 7):
            current = format_for(3, precision)
            original = copy.deepcopy(current)
            del original['source']['codebook_replacements']
            measurements = load_measurements(precision)
            for order in (measurements, list(reversed(measurements))):
                result = original
                for measurement in order:
                    result = replace_book(result, measurement)
                self.assertEqual(result, current)

    def test_candidate_requires_the_frozen_file(self):
        for raw in (b'{}', b'{"mode":4,"cluster":0,"rows":16,"columns":16}'):
            with self.assertRaisesRegex(ValueError, 'unverified frozen candidate'):
                from_candidate(raw)

    def test_regeneration_does_not_depend_on_the_previous_packed_book(self):
        shared = json.loads((DATA / shared_name(3)).read_text())
        for precision in (6, 7):
            stored = json.loads((DATA / format_name(3, precision)).read_text())
            broken = copy.deepcopy(stored)
            for measurement in load_measurements(precision):
                broken['modes'][measurement['mode']]['codebooks'][measurement['book']] = 'not-a-codebook'
            rebuilt, common = regenerate(broken, shared, load_measurements(precision))
            self.assertEqual(rebuilt, stored)
            self.assertEqual(common, shared)
            # A partial repair must still reject any unselected damaged book.
            with self.assertRaises(ValueError):
                regenerate(broken, shared, [load_measurement(1, 0, precision)])

    def test_measurements_never_replace_the_shared_matrices(self):
        shared = json.loads((DATA / shared_name(3)).read_text())
        changed = copy.deepcopy(shared)
        words = bytearray.fromhex(changed['matrices_f32'][0])
        words[0] ^= 0x80
        changed['matrices_f32'][0] = words.hex()
        for precision in (6, 7):
            stored = json.loads((DATA / format_name(3, precision)).read_text())
            with self.assertRaisesRegex(ValueError, 'digest'):
                regenerate(stored, changed, load_measurements(precision))

    def test_valid_but_relabelled_tree_is_not_the_measurement(self):
        for precision, mode, book in BOOK_SHA256:
            measurement = load_measurement(mode, book, precision)
            a, b = measurement['entries'][:2]
            a['codeword'], b['codeword'] = b['codeword'], a['codeword']
            a['bit_length'], b['bit_length'] = b['bit_length'], a['bit_length']
            with self.subTest(precision=precision, mode=mode, book=book), self.assertRaisesRegex(ValueError, 'digest'):
                measured_book(measurement)

    def test_incomplete_symbols_and_changed_provenance_are_rejected(self):
        for precision, mode, book in BOOK_SHA256:
            for change, message in (
                (lambda m: m['entries'].pop(), f'{1 << precision} symbols'),
                (lambda m: m['entries'][1].update(symbol=0), 'ordered and unique'),
                (lambda m: m['entries'][0].update(codeword='x'*9), 'codeword'),
                (lambda m: m.update(mode=5), 'scope'),
                (lambda m: m.update(book=4), 'scope'),
                (lambda m: m['source'].update(architecture='x86_64'), 'source'),
            ):
                measurement = load_measurement(mode, book, precision)
                change(measurement)
                with self.subTest(precision=precision, mode=mode, book=book, message=message), self.assertRaisesRegex(ValueError, message):
                    measured_book(measurement)

    def test_a_measurement_cannot_be_relabelled_as_another_cluster(self):
        for precision in (6, 7):
            for cluster in range(4):
                value = load_measurement(4, cluster, precision)
                value['book'] = (cluster + 1) % 4
                with self.subTest(precision=precision, cluster=cluster), self.assertRaisesRegex(ValueError, 'source'):
                    measured_book(value)

    def test_precisions_cannot_be_relabelled_or_mixed_in_one_dictionary(self):
        shared = json.loads((DATA / shared_name(3)).read_text())
        for precision in (6, 7):
            value = load_measurement(1, 0, precision)
            value['quantization_bits'] = 13-precision
            with self.assertRaisesRegex(ValueError, 'source'):
                measured_book(value)
            stored = json.loads((DATA / format_name(3, precision)).read_text())
            with self.assertRaisesRegex(ValueError, 'precision scope'):
                regenerate(stored, shared, load_measurements(13-precision))
        stored = json.loads((DATA / format_name(3, 8)).read_text())
        with self.assertRaisesRegex(ValueError, 'scope'):
            regenerate(stored, shared, load_measurements(7))

    def test_seven_bit_sources_reuse_geometry_without_replacing_its_source(self):
        for value in load_measurements(7):
            self.assertNotIn('coefficient_group', value)
            self.assertNotIn('group_sha256', value)
            with self.assertRaisesRegex(ValueError, 'no recovered group'):
                measured_group(value)
            self.assertEqual(value['source']['matrix_reused'], value['mode']==4)
            self.assertEqual(value['source']['group_reused'], value['mode'] in (2,3))
            if value['mode'] != 1:
                self.assertEqual(value['source']['prior_quantization_bits'],6)
                self.assertEqual(len(value['source']['prior_sha256']),64)


if __name__ == '__main__':
    unittest.main()
