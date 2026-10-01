"""Shared storage must preserve every frozen dictionary word and reference."""
import copy
import json
import unittest
from hoa_salient_format import (
    DATA, expand_format, expand_shared, format_for, format_name, json_bytes, shared_name, split_format,
)
from hoa_packed_tables import pack_codebook, unpack_codebook, pack_matrix, unpack_matrix


class SharedFormatTests(unittest.TestCase):
    def test_all_forty_dictionaries_round_trip_without_duplicate_shared_tables(self):
        for order in range(1, 11):
            shared_path = DATA / shared_name(order)
            expected_shared = shared_path.read_bytes()
            base = format_for(order)
            shared = expand_shared(json.loads(expected_shared))
            self.assertEqual(len(shared['groups']), 3)
            self.assertEqual(len(shared['matrices_f32']), 4)
            for pool in ('groups', 'matrices_f32'):
                entries = [tuple(block) for block in shared[pool]]
                self.assertEqual(len(entries), len(set(entries)))
            for precision in range(6, 10):
                value = format_for(order, precision)  # Checks its historical SHA-256.
                stored, common = split_format(value)
                self.assertEqual(json_bytes(common), expected_shared)
                self.assertEqual(json_bytes(stored), (DATA / format_name(order, precision)).read_bytes())
                self.assertEqual(expand_format(stored, common), value)
                for mode, original in zip(value['modes'], base['modes']):
                    for key in ('groups', 'matrices_f32'):
                        for block, expected in zip(mode[key], original[key]):
                            self.assertIs(block, expected)

    def test_wrong_order_references_and_changed_table_words_are_rejected(self):
        stored, shared = split_format(format_for(1, 9))
        wrong = copy.deepcopy(shared)
        wrong['order'] = 2
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            expand_format(stored, wrong)
        for index in (-1, len(shared['groups'])):
            wrong = copy.deepcopy(shared)
            wrong['modes'][0]['group_indices'][0] = index
            with self.assertRaisesRegex(ValueError, 'index'):
                expand_format(stored, wrong)
        wrong = copy.deepcopy(shared)
        matrix = bytearray.fromhex(wrong['matrices_f32'][0])
        matrix[0] ^= 0x80  # Change the first matrix coefficient's sign.
        wrong['matrices_f32'][0] = matrix.hex()
        with self.assertRaisesRegex(ValueError, 'digest'):
            expand_format(stored, wrong)
        wrong = copy.deepcopy(stored)
        book = unpack_codebook(wrong['modes'][1]['codebooks'][0], 9)
        book[0], book[1] = book[1], book[0]
        wrong['modes'][1]['codebooks'][0] = pack_codebook(book, 9)
        with self.assertRaisesRegex(ValueError, 'digest'):
            expand_format(wrong, shared)

    def test_archived_shared_schema_still_expands_to_the_original_dictionary(self):
        value = format_for(2, 8)
        stored, shared = split_format(value)
        stored['schema_version'] = 2
        del stored['codebook_encoding']
        for mode, original in zip(stored['modes'], value['modes']):
            mode['codebooks'] = original['codebooks']
        self.assertEqual(expand_format(stored, expand_shared(shared)), value)


class PackedTableTests(unittest.TestCase):
    def test_tree_rejects_duplicate_symbols_padding_truncation_and_excess_depth(self):
        book = [[6, i] for i in range(64)]
        encoded = pack_codebook(book, 6)
        self.assertEqual(unpack_codebook(encoded, 6), book)
        duplicate = bytearray.fromhex(encoded)
        duplicate[2] &= ~0x10  # Second leaf's six-bit symbol becomes 0 instead of 1.
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            unpack_codebook(duplicate.hex(), 6)
        padding = bytearray.fromhex(encoded)
        padding[-1] |= 1
        for invalid in (encoded[:-2], encoded + '00', padding.hex(), '00' * 64, '80' + '00' * 63):
            with self.subTest(encoded=invalid):
                with self.assertRaises(ValueError):
                    unpack_codebook(invalid, 6)
        for invalid in ([[7, i] for i in range(64)], [[6, 0]] * 64):
            with self.assertRaises(ValueError):
                pack_codebook(invalid, 6)

    def test_matrix_round_trip_preserves_negative_zero_and_refuses_lossy_values(self):
        words = [0, 0x80000000, 0x3dcccccd, 0xbf800000]
        encoded = pack_matrix(words)
        self.assertEqual(unpack_matrix(encoded, len(words)), words)
        for invalid in (0x00000001, 0x7fc00000, 0x7f800000, 0x40000000):
            with self.subTest(word=invalid):
                with self.assertRaises(ValueError):
                    pack_matrix([invalid])
        padding = bytearray.fromhex(encoded)
        padding[-1] |= 1
        for invalid in (encoded[:-2], encoded + '00', padding.hex(), 'zz' * (len(encoded) // 2)):
            with self.assertRaises(ValueError):
                unpack_matrix(invalid, len(words))


if __name__ == '__main__':
    unittest.main()
