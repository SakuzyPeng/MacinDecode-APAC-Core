"""Check measurement provenance and migration without native decoder access."""
import copy
import json
import unittest

from generate_hoa_salient_measured import (
    BOOK_SHA256, from_candidate, load_measurement, load_measurements, measured_book, measured_group, regenerate, replace_book,
)
from hoa_salient_format import DATA, format_for, format_name, shared_name


class MeasuredCodebookTests(unittest.TestCase):
    def test_all_supported_order_codebooks_have_measured_sources(self):
        books = [(1, 0), (2, 0), (2, 1), (3, 0)] + [(4, c) for c in range(4)]
        self.assertEqual(set(BOOK_SHA256), {(o, p, m, b) for o in (1, 2, 3) for p in (6, 7, 8, 9) for m, b in books})

    def test_measurement_matches_generated_packed_copy(self):
        for order, precision, mode, book in BOOK_SHA256:
            current = format_for(order, precision)
            with self.subTest(precision=precision, mode=mode, book=book):
                measurement = load_measurement(mode, book, precision, order)
                self.assertEqual(measurement['book_sha256'], BOOK_SHA256[order, precision, mode, book])
                self.assertEqual(measured_book(measurement), current['modes'][mode]['codebooks'][book])
                self.assertEqual(replace_book(current, measurement), current)

    def test_replacement_restores_book_and_preserves_every_other_table(self):
        for order, precision, mode, book in BOOK_SHA256:
            original = format_for(order, precision)
            with self.subTest(precision=precision, mode=mode, book=book):
                changed = copy.deepcopy(original)
                entries = changed['modes'][mode]['codebooks'][book]
                entries[0], entries[1] = entries[1], entries[0]
                result = replace_book(changed, load_measurement(mode, book, precision, order))
                self.assertEqual(result, original)
                self.assertNotEqual(changed, original)
                with self.assertRaisesRegex(ValueError, 'scope'):
                    replace_book(dict(format_for(order, precision), order=4), load_measurement(mode, book, precision, order))
                with self.assertRaisesRegex(ValueError, 'scope'):
                    replace_book(format_for(order, 15-precision), load_measurement(mode, book, precision, order))

    def test_replacements_compose_without_losing_the_other_source(self):
        for order, precision in ((o,p) for o in (1,2,3) for p in (6,7,8,9)):
            current = format_for(order, precision)
            original = copy.deepcopy(current)
            del original['source']['codebook_replacements']
            measurements = load_measurements(precision, order)
            for sequence in (measurements, list(reversed(measurements))):
                result = original
                for measurement in sequence:
                    result = replace_book(result, measurement)
                self.assertEqual(result, current)

    def test_candidate_requires_the_frozen_file(self):
        for raw in (b'{}', b'{"mode":4,"cluster":0,"rows":16,"columns":16}'):
            with self.assertRaisesRegex(ValueError, 'unverified frozen candidate'):
                from_candidate(raw)

    def test_regeneration_does_not_depend_on_the_previous_packed_book(self):
        for order, precision in ((o,p) for o in (1,2,3) for p in (6,7,8,9)):
            shared = json.loads((DATA / shared_name(order)).read_text())
            stored = json.loads((DATA / format_name(order, precision)).read_text())
            broken = copy.deepcopy(stored)
            for measurement in load_measurements(precision, order):
                broken['modes'][measurement['mode']]['codebooks'][measurement['book']] = 'not-a-codebook'
            rebuilt, common = regenerate(broken, shared, load_measurements(precision, order))
            self.assertEqual(rebuilt, stored)
            self.assertEqual(common, shared)
            # A partial repair must still reject any unselected damaged book.
            with self.assertRaises(ValueError):
                regenerate(broken, shared, [load_measurement(1, 0, precision, order)])

    def test_measurements_never_replace_the_shared_matrices(self):
        for order, precision in ((o,p) for o in (1,2,3) for p in (6,7,8,9)):
            shared = json.loads((DATA / shared_name(order)).read_text())
            changed = copy.deepcopy(shared)
            words = bytearray.fromhex(changed['matrices_f32'][0])
            words[0] ^= 0x80
            changed['matrices_f32'][0] = words.hex()
            stored = json.loads((DATA / format_name(order, precision)).read_text())
            with self.assertRaisesRegex(ValueError, 'digest'):
                regenerate(stored, changed, load_measurements(precision, order))

    def test_valid_but_relabelled_tree_is_not_the_measurement(self):
        for order, precision, mode, book in BOOK_SHA256:
            measurement = load_measurement(mode, book, precision, order)
            a, b = measurement['entries'][:2]
            a['codeword'], b['codeword'] = b['codeword'], a['codeword']
            a['bit_length'], b['bit_length'] = b['bit_length'], a['bit_length']
            with self.subTest(precision=precision, mode=mode, book=book), self.assertRaisesRegex(ValueError, 'digest'):
                measured_book(measurement)

    def test_incomplete_symbols_and_changed_provenance_are_rejected(self):
        for order, precision, mode, book in BOOK_SHA256:
            for change, message in (
                (lambda m: m['entries'].pop(), f'{1 << precision} symbols'),
                (lambda m: m['entries'][1].update(symbol=0), 'ordered and unique'),
                (lambda m: m['entries'][0].update(codeword='x'*9), 'codeword'),
                (lambda m: m.update(mode=5), 'scope'),
                (lambda m: m.update(book=4), 'scope'),
                (lambda m: m['source'].update(architecture='x86_64'), 'source'),
            ):
                measurement = load_measurement(mode, book, precision, order)
                change(measurement)
                with self.subTest(precision=precision, mode=mode, book=book, message=message), self.assertRaisesRegex(ValueError, message):
                    measured_book(measurement)

    def test_a_measurement_cannot_be_relabelled_as_another_cluster(self):
        for order, precision in ((o,p) for o in (1,2,3) for p in (6,7,8,9)):
            shared = json.loads((DATA / shared_name(order)).read_text())
            for cluster in range(4):
                value = load_measurement(4, cluster, precision, order)
                value['book'] = (cluster + 1) % 4
                with self.subTest(precision=precision, cluster=cluster), self.assertRaisesRegex(ValueError, 'source'):
                    measured_book(value)

    def test_precisions_cannot_be_relabelled_or_mixed_in_one_dictionary(self):
        for order, precision in ((o,p) for o in (1,2,3) for p in (6,7,8,9)):
            shared = json.loads((DATA / shared_name(order)).read_text())
            value = load_measurement(1, 0, precision, order)
            value['quantization_bits'] = 15-precision
            with self.assertRaisesRegex(ValueError, 'source'):
                measured_book(value)
            stored = json.loads((DATA / format_name(order, precision)).read_text())
            with self.assertRaisesRegex(ValueError, 'precision scope'):
                regenerate(stored, shared, load_measurements(15-precision, order))
        stored = json.loads((DATA / format_name(3, 8)).read_text())
        with self.assertRaisesRegex(ValueError, 'scope'):
            regenerate(stored, shared, load_measurements(7))

    def test_wider_sources_reuse_geometry_without_replacing_its_source(self):
        for value in (value for order in (1,2,3) for precision in (7,8,9) for value in load_measurements(precision, order)):
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
