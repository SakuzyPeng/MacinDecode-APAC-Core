"""SQ Huffman codebook provenance: structural checks and the vo-aacenc extraction."""
import copy
import json
import unittest

from generate_sq_codebooks import DESTINATION, LONG, SHORT, check, check_book, document, encode


def c_array(name, values, shape=''):
    return f'const int {name}{shape}={{\n  {{{", ".join(hex(v) for v in values)}}}\n}};\n'


def synthetic_source(tables):
    """An aac_rom.c-shaped file holding the committed tables, as vo-aacenc packs them."""
    books = tables['spectral']
    parts = ['#if defined (ARMV5E)\nconst int cossintab[2] = {1, 2};\n#endif\n']
    for odd in (1, 3, 5, 7, 9):
        packed = [high << 8 | low for high, low in zip(books[odd - 1]['bits'], books[odd]['bits'])]
        parts.append(c_array(f'huff_ltab{odd}_{odd + 1}', packed, '[3][3]'))
    parts.append(c_array('huff_ltab11', books[10]['bits'], '[17][17]'))
    parts += [c_array(f'huff_ctab{book}', value['codes'], '[9][9]') for book, value in enumerate(books, 1)]
    parts.append(c_array('huff_ltabscf', tables['scalefactor']['bits'], '[121]'))
    parts.append(c_array('huff_ctabscf', tables['scalefactor']['codes'], '[121]'))
    for name, offsets, section in (('sfBandTabLong', tables['long_offsets'], LONG),
                                   ('sfBandTabShort', tables['short_offsets'], SHORT)):
        parts.append('/* rows */ ' + c_array(name, [7] * section.start + offsets + [9], '[400]'))
    return '\n'.join(parts)


class SqCodebookTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads(DESTINATION.read_text())

    def test_committed_books_are_complete_prefix_codes(self):
        check(self.tables)
        self.assertEqual(DESTINATION.read_bytes(), encode(self.tables))

    def test_extraction_unpacks_book_pairs_and_selects_48_khz_offsets(self):
        self.assertEqual(document(synthetic_source(self.tables)), self.tables)

    def test_incomplete_overlapping_or_reordered_tables_are_rejected(self):
        broken = copy.deepcopy(self.tables)
        broken['spectral'][0]['bits'][0] += 1
        with self.assertRaisesRegex(AssertionError, 'not a complete code'):
            check(broken)
        with self.assertRaisesRegex(AssertionError, 'not prefix-free'):
            check_book('overlap', dict(codes=[0b0, 0b01, 0b11], bits=[1, 2, 2]), 3)
        broken = copy.deepcopy(self.tables)
        broken['long_offsets'][3], broken['long_offsets'][4] = broken['long_offsets'][4], broken['long_offsets'][3]
        with self.assertRaisesRegex(AssertionError, 'long_offsets'):
            check(broken)


if __name__ == '__main__':
    unittest.main()
