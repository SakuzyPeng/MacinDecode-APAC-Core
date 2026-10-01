"""Bit-exact storage codecs for the verified HOA Huffman trees and matrices."""
import math
import struct

CODEBOOK_ENCODING = 'preorder-tree-msb-hex-v1'
MATRIX_ENCODING = 'micro21-msb-hex-v1'


class _Writer:
    def __init__(self):
        self.data = bytearray()
        self.value = self.bits = 0

    def put(self, value, width):
        if not 0 <= value < 1 << width:
            raise ValueError('packed table value outside bit width')
        self.value = (self.value << width) | value
        self.bits += width
        while self.bits >= 8:
            self.bits -= 8
            self.data.append((self.value >> self.bits) & 255)
        self.value &= (1 << self.bits) - 1

    def hex(self):
        tail = bytes([self.value << (8 - self.bits)]) if self.bits else b''
        return (self.data + tail).hex()


class _Reader:
    def __init__(self, encoded, bit_length):
        if (not isinstance(encoded, str) or len(encoded) != 2 * ((bit_length + 7) // 8)
                or any(c not in '0123456789abcdef' for c in encoded)):
            raise ValueError('invalid packed table hex or length')
        self.data = bytes.fromhex(encoded)
        self.position = 0
        self.end = bit_length
        if bit_length % 8 and self.data[-1] & ((1 << (8 - bit_length % 8)) - 1):
            raise ValueError('nonzero packed table padding')

    def take(self, width):
        if self.position + width > self.end:
            raise ValueError('truncated packed table')
        value = 0
        for _ in range(width):
            value = (value << 1) | ((self.data[self.position // 8] >> (7 - self.position % 8)) & 1)
            self.position += 1
        return value

    def finish(self):
        if self.position != self.end:
            raise ValueError('unused packed table bits')


def _symbol_count(precision):
    if type(precision) is not int or precision not in range(6, 10):
        raise ValueError('packed codebook precision must be 6..9')
    return 1 << precision


def pack_codebook(book, precision):
    count = _symbol_count(precision)
    if len(book) != count:
        raise ValueError('packed codebook symbol count differs')
    tree = {}
    for symbol, (length, code) in enumerate(book):
        if (type(length) is not int or type(code) is not int
                or not 1 <= length <= 32 or not 0 <= code < 1 << length):
            raise ValueError('invalid Huffman codeword')
        node = tree
        for bit in range(length - 1, -1, -1):
            if 'symbol' in node:
                raise ValueError('Huffman prefix collision')
            node = node.setdefault((code >> bit) & 1, {})
        if node:
            raise ValueError('Huffman prefix collision')
        node['symbol'] = symbol
    writer = _Writer()

    def visit(node):
        if 'symbol' in node:
            writer.put(1, 1)
            writer.put(node['symbol'], precision)
        else:
            if set(node) != {0, 1}:
                raise ValueError('incomplete Huffman tree')
            writer.put(0, 1)
            visit(node[0])
            visit(node[1])

    visit(tree)
    return writer.hex()


def unpack_codebook(encoded, precision):
    count = _symbol_count(precision)
    reader = _Reader(encoded, count * (precision + 2) - 1)
    book = [None] * count

    def visit(code=0, depth=0):
        if reader.take(1):
            if depth == 0:
                raise ValueError('zero-length Huffman codeword')
            symbol = reader.take(precision)
            if book[symbol] is not None:
                raise ValueError('duplicate Huffman symbol')
            book[symbol] = [depth, code]
        else:
            if depth >= 32:
                raise ValueError('Huffman depth exceeds 32')
            visit(code << 1, depth + 1)
            visit((code << 1) | 1, depth + 1)

    visit()
    reader.finish()
    if any(word is None for word in book):
        raise ValueError('missing Huffman symbol')
    return book


def _matrix_word(value):
    magnitude = (value & 0xfffff) / 1_000_000
    word = struct.unpack('<I', struct.pack('<f', magnitude))[0]
    return word | ((value >> 20) << 31)


def pack_matrix(words):
    writer = _Writer()
    for word in words:
        if type(word) is not int or not 0 <= word <= 0xffffffff:
            raise ValueError('invalid Float32 matrix word')
        value = struct.unpack('<f', struct.pack('<I', word))[0]
        if not math.isfinite(value):
            raise ValueError('nonfinite matrix word')
        magnitude = round(abs(value) * 1_000_000)
        packed = ((word >> 31) << 20) | magnitude
        if magnitude >= 1 << 20 or _matrix_word(packed) != word:
            raise ValueError('matrix word has no exact micro21 representation')
        writer.put(packed, 21)
    return writer.hex()


def unpack_matrix(encoded, count):
    reader = _Reader(encoded, count * 21)
    words = [_matrix_word(reader.take(21)) for _ in range(count)]
    reader.finish()
    return words
