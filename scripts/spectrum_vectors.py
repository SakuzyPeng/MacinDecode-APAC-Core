"""Portable artificial APAC SQ writer with independent integer/position truth.

Only the selected two-channel configuration and independent ICS path are built.
The accepted zero ancillary tail is a fixture convention, not a general muxer.
"""
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct

TABLES = json.loads((Path(__file__).resolve().parents[1] / 'data/sq-codebooks.json').read_text())


def bits(n, width):
    if not 0 <= n < 1 << width:
        raise ValueError('value does not fit bit width')
    return format(n, f'0{width}b') if width else ''


def pack(s):
    s += '0' * (-len(s) % 8)
    return int(s, 2).to_bytes(len(s) // 8, 'big')


def shape(cb):
    if cb <= 4:
        return 4, 3, -1 if cb <= 2 else 0
    if cb <= 6:
        return 2, 9, -4
    return 2, {7: 8, 8: 8, 9: 13, 10: 13, 11: 17}[cb], 0


def tuple_values(cb, index):
    size, base, bias = shape(cb)
    return [index // base**i % base + bias for i in range(size-1, -1, -1)]


def encode(cb, values):
    size, base, bias = shape(cb)
    assert len(values) == size
    external = cb in [3, 4, 7, 8, 9, 10, 11]
    index = 0
    for v in values:
        digit = abs(v) if external else v
        if cb == 11:
            digit = min(digit, 16)
        assert 0 <= digit - bias < base
        index = index * base + digit - bias
    book = TABLES['spectral'][cb-1]
    out = bits(book['codes'][index], book['bits'][index])
    if external:
        out += ''.join('1' if v < 0 else '0' for v in values if v)
    if cb == 11:
        for v in values:
            v = abs(v)
            if v >= 16:
                width = v.bit_length()-1
                assert width <= 12
                out += '1'*(width-4) + '0' + bits(v-(1 << width), width)
    return out


def window_groups(mask, short):
    groups = [1]
    if short:
        for bit in bits(mask, 7):
            if bit == '1':
                groups[-1] += 1
            else:
                groups.append(1)
    return groups


def channel(bands, block=0, grouping=0, gain=160):
    """bands maps sfb to (codebook, tuple, sf); nonzero runs merge into sections."""
    short = block == 2
    offsets = TABLES['short_offsets' if short else 'long_offsets']
    groups = window_groups(grouping, short)
    max_sfb = max(bands, default=-1)+1
    head = bits(block, 2)+bits(max_sfb, 4 if short else 6)+(bits(grouping, 7) if short else '')
    header = head + bits(gain, 8)
    encoded = ''
    quantized = [0]*1024
    scaled = [0.0]*1024
    previous = gain
    first_window = 0
    expected_sections, factors = [], []
    for g, length in enumerate(groups):
        factors.append([None]*max_sfb)
        band = 0
        while band < max_sfb:
            cb = bands.get(band, (0, (), 0))[0]
            end = band+1
            while end < max_sfb and bands.get(end, (0, (), 0))[0] == cb:
                end += 1
            section_length = end-band
            width = 3 if short else 5
            sentinel = (1 << width)-1
            header += bits(cb, 4) + bits(sentinel, width)*(section_length//sentinel) + bits(section_length % sentinel, width)
            expected_sections.append(dict(group=g, start_band=band, end_band=end, codebook=cb))
            for sfb in range(band, end):
                if cb:
                    _, values, sf = bands[sfb]
                    delta = sf-previous
                    assert -60 <= delta <= 60
                    previous = sf
                    book = TABLES['scalefactor']
                    header += bits(book['codes'][delta+60], book['bits'][delta+60])
                    factors[g][sfb] = sf
                    coefficients = list(values) + [0]*(offsets[sfb+1]-offsets[sfb]-len(values))
                    size = shape(cb)[0]
                    for w in range(first_window, first_window+length):
                        for i in range(0, len(coefficients), size):
                            encoded += encode(cb, coefficients[i:i+size])
                        for i, q in enumerate(coefficients):
                            dest = w*(128 if short else 1024)+offsets[sfb]+i
                            quantized[dest] = q
                            scaled[dest] = math.copysign(abs(q)**(4/3), q)*2**((sf-100)/4) if q else 0.0
            band = end
        first_window += length
    return header+encoded, dict(quantized=quantized, scaled=scaled, sections=expected_sections,
        scale_factors=factors, ics=dict(block_type=block, max_sfb=max_sfb, window_groups=groups),
        global_gain=gain, header_bits=len(head), spectral_relative_offset=len(header))


def frame(case):
    block, grouping, gain = (case.get(k, v) for k, v in [('block', 0), ('grouping', 0), ('gain', 160)])
    left, a = channel(case.get('left', {}), block, grouping, gain)
    right, b = channel(case.get('right', {}), case.get('right_block', block), case.get('right_grouping', grouping), gain)
    a.update(channel_index=0, stream_bit_offset=4+a.pop('header_bits'), spectral_bit_offset=4+a.pop('spectral_relative_offset'), end_bit_offset=4+len(left))
    origin = 5+len(left)
    b.update(channel_index=1, stream_bit_offset=origin+b.pop('header_bits'), spectral_bit_offset=origin+b.pop('spectral_relative_offset'), end_bit_offset=origin+len(right))
    return pack('0110'+left+'0'+right+'0000') + b'\0', [a, b]


def matrix_cases():
    yield dict(kind='silence')
    for cb in range(1, 12):
        for i in range(len(TABLES['spectral'][cb-1]['codes'])):
            values = tuple_values(cb, i)
            variants = itertools.product(*[(v, -v) if v else (0,) for v in values]) if cb in [3, 4, 7, 8, 9, 10, 11] else [values]
            for values in variants:
                yield dict(kind='signed_tuple', left={0: (cb, values, 160)})
    for gain in range(256):
        yield dict(kind='gain', gain=gain, left={0: (1, [-1, 1, -1, 1], gain)})
    for value in [15, 16, 17, 31, 32, 33, 63, 64, 127, 128, 255, 256, 511, 512, 1023, 1024, 2047, 2048, 4095, 4096, 8190, 8191]:
        for pair in [(value, 0), (-value, 0), (0, value), (0, -value), (value, -value)]:
            yield dict(kind='escape_boundary', left={0: (11, pair, 160)})


def band_cases():
    for block in range(4):
        for mask in ([0, 0x55, 0x7f] if block == 2 else [0]):
            for band in range(14 if block == 2 else 49):
                for cb in range(1, 12):
                    values = [1, -1, 0, 1] if cb <= 4 else [1, -1]
                    for side in ['left', 'right']:
                        yield dict(kind='band', block=block, grouping=mask, **{side: {band: (cb, values, 160)}})
    for shift in range(8):
        yield dict(kind='multiple_sections', left={b: (1+i*2, [1,-1,0,1] if i<2 else [1,-1], 150+(i+shift)%7) for i,b in enumerate([0,1,9,30,31,48])},
            right={b: (2+i*2 if i<5 else 11, [1,-1,0,1] if i<2 else [1,-1], 150-(i+shift)%7) for i,b in enumerate([0,1,9,30,31,48])})
    yield dict(kind='mixed_windows', block=2, grouping=0x55, right_block=3, right_grouping=0,
               left={0:(1,[1,0,0,0],160)}, right={48:(11,[8191,-8191],160)})
    yield dict(kind='multi_band_section', left={i:(1,[1,0,-1,0],160+i) for i in range(5)})
    yield dict(kind='group_scale_carry', block=2, grouping=0x55,
               left={0:(1,[1,0,-1,0],155),3:(3,[1,-1,0,1],170)})
    yield dict(kind='negative_scale', gain=0, left={i:(1,[1,0,-1,0],-60*(i+1)) for i in range(4)})


def cookie(rate):
    fields = [(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(31,6),(0,4),(0,1),
        (3 if rate==48000 else 4,6),(0,6),(2,8),(2,8),(0,1),(1,3),(0,8),(0,3),(0,1),(1,5),(1,3),
        (101,16),(0,1),(0,1),(0,3),(0,2),(0,5),(0,1)]
    data = pack(''.join(bits(v,w) for v,w in fields))
    return len(data).to_bytes(4,'big')+data[4:]


def bundle(root, payloads, rate=48000):
    root.mkdir()
    data = b''.join(payloads)
    config = cookie(rate)
    sha = lambda b: hashlib.sha256(b).hexdigest()
    known = lambda v: dict(value=v, error=None)
    info = dict(schema_version=1, source='artificial-sq.caf', file_bytes=len(data), modified_unix_seconds=None,
        environment=dict(tool_version='spectrum-vectors', os='portable', architecture='portable', system_version='synthetic'),
        container=known('caff'), format=dict(sample_rate=rate, format_id=int.from_bytes(b'apac','big'),format_fourcc='apac',flags=0,
        bytes_per_packet=0,frames_per_packet=1024,bytes_per_frame=0,channels=2,bits_per_channel=0),
        layout=known(dict(tag=(101<<16)|2, bitmap=0, descriptions=[], ambisonic_order=None,ambisonic_channel_order=None,ambisonic_normalization=None)),
        packet_count=known(len(payloads)), max_packet_bytes=known(max(map(len,payloads))),
        packet_table=known(dict(priming_frames=0,valid_frames=len(payloads)*1024,remainder_frames=0)),
        cookie=known(dict(bytes=len(config),sha256=sha(config))),restricts_random_access=known(False))
    manifest = dict(schema_version=1, complete=True, file=info, start_packet=0, requested_packets=len(payloads), actual_packets=len(payloads),
        packet_data_file='packets.bin',packet_index_file='packets.jsonl',cookie_file='cookie.bin',packet_data_bytes=len(data),packet_data_sha256=sha(data))
    offset=0
    with (root/'packets.jsonl').open('x') as f:
        for i,p in enumerate(payloads):
            f.write(json.dumps(dict(schema_version=1,packet_index=i,export_offset=offset,bytes=len(p),frames=1024,sha256=sha(p),
                raw_frame_position=known(i*1024),dependency=known(dict(independently_decodable=True,preroll_packet_count=0)),roll_distance=known(0)))+'\n')
            offset+=len(p)
    (root/'packets.bin').write_bytes(data)
    (root/'cookie.bin').write_bytes(config)
    (root/'manifest.json').write_text(json.dumps(manifest))
