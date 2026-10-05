"""Measured coefficient order, alias preservation and source composition."""
import copy
import itertools
import json
import unittest

from generate_hoa_salient_measured import load_measurements as load_books, measured_group, replace_book
from generate_hoa_salient_measured_matrix import load_measurements as load_matrices, replace_matrix
from generate_hoa_salient_measured_groups import GROUP_USERS, load_measurements, regenerate, replace_group
from hoa_salient_format import DATA, format_for, format_name, shared_name


def inputs():
    return ({q: json.loads((DATA / format_name(3, q)).read_text()) for q in range(6, 10)},
            json.loads((DATA / shared_name(3)).read_text()))


class MeasuredGroupTests(unittest.TestCase):
    def test_every_shared_user_keeps_the_measured_order_at_all_widths(self):
        stored, shared = inputs()
        measurements = load_measurements()
        for measurement in measurements:
            group = measured_group(measurement)
            key = (measurement['mode'], measurement['book'])
            for q in range(6, 10):
                value = format_for(3, q)
                self.assertEqual(replace_group(value, measurement), value)
                for mode, index in GROUP_USERS[key]:
                    self.assertEqual(value['modes'][mode]['groups'][index], group)
                    self.assertIs(value['modes'][mode]['groups'][index],
                                  format_for(3, 6)['modes'][key[0]]['groups'][key[1]])
        rebuilt, common = regenerate(stored, shared, measurements)
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)

    def test_groups_can_be_repaired_without_masking_other_data_damage(self):
        stored, shared = inputs()
        broken = copy.deepcopy(shared)
        broken['groups'] = [[] for _ in broken['groups']]
        rebuilt, common = regenerate(stored, broken, load_measurements())
        self.assertEqual(rebuilt, stored)
        self.assertEqual(common, shared)
        with self.assertRaisesRegex(ValueError, 'digest'):
            regenerate(stored, broken, load_measurements()[:1])
        broken['matrices_f32'][0] = 'not-a-matrix'
        with self.assertRaises(ValueError):
            regenerate(stored, broken, load_measurements())
        damaged = copy.deepcopy(stored)
        damaged[7]['modes'][2]['codebooks'][0] = 'not-a-codebook'
        with self.assertRaises(ValueError):
            regenerate(damaged, shared, load_measurements())

    def test_scope_aliases_and_duplicate_selection_are_rejected(self):
        stored, shared = inputs()
        broken = copy.deepcopy(shared)
        broken['modes'][2]['group_indices'][1] = broken['modes'][2]['group_indices'][0]
        with self.assertRaisesRegex(ValueError, 'aliases'):
            regenerate(stored, broken, load_measurements())
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            regenerate(stored, shared, load_measurements()*2)
        with self.assertRaisesRegex(ValueError, 'four'):
            regenerate({6: stored[6]}, shared, load_measurements())
        with self.assertRaisesRegex(ValueError, 'scope'):
            replace_group(format_for(4, 6), load_measurements()[0])

    def test_relabelled_or_malformed_groups_are_not_accepted_as_measurements(self):
        for measurement in load_measurements():
            for change in ('swap', 'duplicate', 'range', 'missing', 'hash'):
                value = copy.deepcopy(measurement)
                group = value['coefficient_group']
                if change == 'swap':group[0], group[1] = group[1], group[0]
                elif change == 'duplicate':group[0] = group[1]
                elif change == 'range':group[0] = 16
                elif change == 'missing':del value['coefficient_group']
                else:value['group_sha256'] = 'wrong'
                with self.subTest(mode=measurement['mode'], book=measurement['book'], change=change), self.assertRaises(ValueError):
                    measured_group(value)

    def test_codebook_matrix_and_group_updates_commute(self):
        for precision in (6, 7):
            current = format_for(3, precision)
            original = copy.deepcopy(current)
            original['source'] = original['source']['original_observation']
            operations = [(load_books(precision), replace_book), (load_matrices(), replace_matrix), (load_measurements(), replace_group)]
            for order in itertools.permutations(operations):
                value = original
                for measurements, replace in order:
                    for measurement in measurements:
                        value = replace(value, measurement)
                self.assertEqual(value, current)


if __name__ == '__main__':
    unittest.main()
