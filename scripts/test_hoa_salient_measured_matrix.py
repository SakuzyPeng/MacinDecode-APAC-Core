"""Qualified matrix source, repair boundaries and shared-width provenance."""
import copy
import json
import unittest

from generate_hoa_salient_measured import load_measurements as load_books, replace_book
from generate_hoa_salient_measured_groups import load_measurements as load_groups, replace_group
from generate_hoa_salient_measured_matrix import (
    from_candidate, load_measurement, load_measurements, measured_matrix, regenerate, replace_matrix,
)
from hoa_salient_format import DATA, format_for, format_name, shared_name
from hoa_packed_tables import unpack_matrix


def inputs():
    return ({q: json.loads((DATA / format_name(3, q)).read_text()) for q in range(6, 10)},
            json.loads((DATA / shared_name(3)).read_text()))


class MeasuredMatrixTests(unittest.TestCase):
    def test_all_four_widths_share_the_qualified_matrix(self):
        stored, shared = inputs()
        for measurement in load_measurements():
            cluster = measurement['cluster']
            words = measured_matrix(measurement)
            self.assertEqual(len(words), 256)
            index = shared['modes'][4]['matrix_indices'][cluster]
            self.assertEqual(unpack_matrix(shared['matrices_f32'][index], 256), words)
            for precision in range(6, 10):
                value = format_for(3, precision)
                self.assertEqual(value['modes'][4]['matrices_f32'][cluster], words)
                self.assertEqual(replace_matrix(value, measurement), value)
                self.assertIs(value['modes'][4]['matrices_f32'][cluster], format_for(3, 6)['modes'][4]['matrices_f32'][cluster])
        rebuilt, common = regenerate(stored, shared, load_measurements())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)

    def test_target_copy_can_be_repaired_without_using_its_previous_bits(self):
        stored, shared = inputs()
        broken = copy.deepcopy(shared)
        index = broken['modes'][4]['matrix_indices'][0]
        broken['matrices_f32'][index] = 'not-a-matrix'
        rebuilt, common = regenerate(stored, broken, load_measurement())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)
        other = broken['modes'][4]['matrix_indices'][1]
        broken['matrices_f32'][other] = 'not-a-matrix'
        with self.assertRaises(ValueError):
            regenerate(stored, broken, load_measurement())

    def test_all_selected_matrices_can_be_repaired_together(self):
        stored, shared = inputs()
        broken = copy.deepcopy(shared)
        for index in broken['modes'][4]['matrix_indices']:
            broken['matrices_f32'][index] = 'not-a-matrix'
        rebuilt, common = regenerate(stored, broken, load_measurements())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)
        broken['modes'][2]['signs'] = not broken['modes'][2]['signs']
        with self.assertRaisesRegex(ValueError, 'digest'):
            regenerate(stored, broken, load_measurements())

    def test_scope_and_aliases_cannot_expand_the_replacement(self):
        stored, shared = inputs()
        alias = copy.deepcopy(shared)
        alias['modes'][4]['matrix_indices'][1] = alias['modes'][4]['matrix_indices'][0]
        with self.assertRaisesRegex(ValueError, 'aliased'):
            regenerate(stored, alias, load_measurement())
        del stored[9]
        with self.assertRaisesRegex(ValueError, 'four'):
            regenerate(stored, shared, load_measurement())
        with self.assertRaisesRegex(ValueError, 'scope'):
            replace_matrix(format_for(2, 6), load_measurement())

    def test_candidate_must_be_the_qualified_frozen_file(self):
        for raw in (b'{}', b'{"status":"precision_unresolved","float32_bits":null}'):
            with self.assertRaisesRegex(ValueError, 'unverified qualified'):
                from_candidate(raw)

    def test_finite_but_changed_coefficients_are_rejected(self):
        for cluster in range(4):
            value = load_measurement(cluster)
            value['matrix_f32'][0], value['matrix_f32'][1] = value['matrix_f32'][1], value['matrix_f32'][0]
            with self.subTest(cluster=cluster), self.assertRaisesRegex(ValueError, 'digest'):
                measured_matrix(value)

    def test_malformed_matrix_and_wrong_provenance_are_rejected(self):
        for change, message in (
            (lambda m: m.update(cluster=4), 'scope'),
            (lambda m: m.update(rows=15), 'scope'),
            (lambda m: m.update(storage='column-major'), 'scope'),
            (lambda m: m['matrix_f32'].pop(), '256'),
            (lambda m: m['source'].update(architecture='x86_64'), 'source'),
            (lambda m: m['matrix_f32'].__setitem__(0, 0x7fc00000), 'nonfinite'),
            (lambda m: m['matrix_f32'].__setitem__(0, 0x7f800000), 'nonfinite'),
            (lambda m: m['matrix_f32'].__setitem__(0, -1), 'invalid'),
        ):
            value = load_measurement()
            change(value)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                measured_matrix(value)

    def test_a_matrix_cannot_be_relabelled_as_another_cluster(self):
        for cluster in range(4):
            value = load_measurement(cluster)
            value['cluster'] = (cluster + 1) % 4
            with self.subTest(cluster=cluster), self.assertRaisesRegex(ValueError, 'source'):
                measured_matrix(value)

    def test_matrix_and_codebook_sources_preserve_each_other(self):
        current = format_for(3, 6)
        initial = copy.deepcopy(current)
        initial['source'] = initial['source']['original_observation']
        matrices = load_measurements()
        books = load_books()
        a = initial
        for matrix in reversed(matrices):
            a = replace_matrix(a, matrix)
        for book in books:
            a = replace_book(a, book)
        b = initial
        for book in books:
            b = replace_book(b, book)
        for matrix in matrices:
            b = replace_matrix(b, matrix)
        for group in load_groups():
            a = replace_group(a, group)
            b = replace_group(b, group)
        self.assertEqual(a, current)
        self.assertEqual(b, current)


if __name__ == '__main__':
    unittest.main()
