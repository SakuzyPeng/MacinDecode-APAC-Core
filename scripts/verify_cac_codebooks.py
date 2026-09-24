#!/usr/bin/env python3
"""Read-only, hash-gated comparison of CAC encoder and decoder wire constants.

This diagnostic is never used by the Rust build or the portable acceptance.
Only codeword/value/length triples are retained, not the native table layout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

COMPONENT_SHA256 = '826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd'
DEFAULT_COMPONENT = Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
DEFAULT_TABLE = Path(__file__).resolve().parents[1] / 'data/cac-codebooks.json'


def extract(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != COMPONENT_SHA256:
        raise ValueError('unverified AudioCodecs component; reverify CAC evidence')
    magic, count = struct.unpack_from('>II', raw)
    assert magic == 0xcafebabe
    for i in range(count):
        cpu, _, offset, size, _ = struct.unpack_from('>IIIII', raw, 8+20*i)
        if cpu == 0x1000007:
            break
    else:
        raise ValueError('verified x86_64 slice absent')
    data = memoryview(raw)[offset:offset+size]
    assert struct.unpack_from('<I', data)[0] == 0xfeedfacf
    position, segments = 32, []
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, length = struct.unpack_from('<II', data, position)
        if command == 0x19:
            _, va, _, file_offset, file_size = struct.unpack_from('<16sQQQQ', data, position+8)
            segments.append((va, file_offset, file_size))
        position += length

    def read(address, size):
        for va, file_offset, file_size in segments:
            if va <= address and address+size <= va+file_size:
                return bytes(data[file_offset+address-va:file_offset+address-va+size])
        raise ValueError('CAC table address outside verified file segments')

    result = dict(schema_version=1, source=dict(component='AudioCodecs 7.0',
                  system='macOS 27.0 / 26A428', component_sha256=COMPONENT_SHA256,
                  architecture='x86_64', method='encoder and decoder wire-codeword agreement'))
    for name, encoder, decoder, count in [('gain', 0x8a1200, 0x8a1444, 35),
                                          ('repeat', 0x8a128c, 0x8a133c, 44)]:
        encoded = [struct.unpack('<HH', read(encoder+4*i, 4)) for i in range(count)]
        decoded = []
        for i in range(count):
            entry = read(decoder+6*i, 6)
            decoded.append((entry[0], struct.unpack_from('<H', entry, 2)[0], entry[4]))
        assert sorted(decoded) == [(i, *pair) for i, pair in enumerate(encoded)]
        result[name] = dict(codes=[c for c, _ in encoded], bits=[b for _, b in encoded])
    assert (result['repeat']['codes'][43], result['repeat']['bits'][43]) == (0, 4)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', type=Path, default=DEFAULT_COMPONENT)
    parser.add_argument('--table', type=Path, default=DEFAULT_TABLE)
    parser.add_argument('--write', action='store_true', help='Write a new table; refuse overwrite.')
    args = parser.parse_args()
    result = extract(args.component)
    data = (json.dumps(result, indent=2) + '\n').encode()
    if args.write:
        with args.table.open('xb') as output:
            output.write(data)
    elif args.table.read_bytes() != data:
        raise AssertionError('CAC wire constants differ from verified encoder/decoder tables')
    print(json.dumps(dict(gain_entries=35, repeat_entries=44, sha256=hashlib.sha256(data).hexdigest())))


if __name__ == '__main__':
    main()
