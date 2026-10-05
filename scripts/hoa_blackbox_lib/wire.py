"""Order-1/2/3 q6–q9 APAC inputs; reads only the public-source AAC tables."""
import json
from .common import ROOT, MAX_DEPTH, canonical, digest, geometry, require

SIGNATURE = dict(sample_rate=48000, channels=16, frames_per_packet=1024, packets=2,
                 frames=2048, layout_tag=(190 << 16) | 16, encoding='f32le',
                 input_batch_packets=1, start_frame=0, processing_policy='apac-native-drc-off-v1')


def bits(value, width):
    require(type(value) is int and 0 <= value < 1 << width, 'integer outside wire field')
    return format(value, f'0{width}b') if width else ''


def pack(wire):
    require(set(wire) <= {'0', '1'}, 'nonbinary input')
    wire += '0' * (-len(wire) % 8)
    return int(wire, 2).to_bytes(len(wire) // 8, 'big')


def cookie(quantization_bits=6, order=3):
    g = geometry(order)
    n, components = g["channels"], g["components"]
    require(quantization_bits in (6, 7, 8, 9), 'unsupported quantization width')
    fields = [(0, 32), (int.from_bytes(b'dapa', 'big'), 32), (0, 32), (0x800, 16),
              (5, 6), (0, 4), (0, 1), (3, 6), (0, 6), (n, 8), (2, 8), (0, 1),
              (1, 3), (0, 8), (2, 3)]
    wire = ''.join(bits(v, w) for v, w in fields)
    wire += '1100110' + bits(1, 2) + bits(0, 2) + bits(quantization_bits-6, 2) + bits(order, 4) + bits(components, 4) + bits(0, (n-1).bit_length())
    wire += (bits(3, 4) + bits(order, order.bit_length())) * components + '0' + bits(n, 5) + '000' * n
    wire += '0' + bits(190, 16) + bits(n, 16) + '0' + '0' + bits(0, 3) + bits(0, 2) + '000000'
    raw = pack(wire)
    return len(raw).to_bytes(4, 'big') + raw[4:]


class Writer:
    def __init__(self, quantization_bits=6, order=3):
        self.order = order
        g = geometry(order)
        self.n, self.symbols, self.descriptors = g["channels"], g["symbols"], g["components"]*g["bands"]
        require(quantization_bits in (6, 7, 8, 9), 'unsupported quantization width')
        self.quantization_bits = quantization_bits
        self.zero = 1 << (quantization_bits-1)
        self.aac = json.loads((ROOT / 'data/sq-codebooks.json').read_text())
        require(self.aac['long_offsets'][:2] == [0, 4], 'unexpected AAC first band')

    def carrier(self, gain, line):
        require(line in (0, 1), 'unsupported spectral line')
        values = [0] * 4
        values[line] = 1
        index = 0
        for value in values:
            index = index * 3 + value + 1
        sf, cb = self.aac['scalefactor'], self.aac['spectral'][0]
        return ('10' + bits(1, 6) + bits(gain, 8) + bits(1, 4) + bits(1, 5)
                + bits(sf['codes'][60], sf['bits'][60]) + bits(cb['codes'][index], cb['bits'][index]) + '00')

    def packet(self, mode, payload, gain=128, line=0, active=True, padding=0):
        require(mode in (0, 1, 2, 3, 4), 'unsupported coding mode')
        wire = '0100' + (self.carrier(gain, line) + '0' * (self.n-1) if active else '0' * self.n)
        wire += '1' + bits(mode, 3) + payload
        wire += '0' * (-len(wire) % 8) + '0'
        return pack(wire) + bytes(padding)

    def fixed(self, values, gain=128, line=0, active=True):
        require(len(values) == self.symbols, 'wrong descriptor count')
        return self.packet(0, ''.join(bits(q, self.quantization_bits) for q in values), gain, line, active)

    def padded(self, mode, cluster, pattern, gain=128, extra=0):
        require(len(pattern) <= self.n * (MAX_DEPTH + (mode == 3)) + 1, 'probe pattern too long')
        # A fixed total length makes equal extreme-prefix queries byte-identical.
        length = self.descriptors * (2 + self.n * (MAX_DEPTH + (mode == 3))) + 64 + extra
        payload = pattern.ljust(length, '0')
        if mode == 4:
            payload = bits(cluster, 2) + payload
        return self.packet(mode, payload, gain)

    def coded(self, mode, cluster, values, words, gain=128, line=0, padding=0,
              groups=None, signs=None, active=True):
        require(len(values) == self.symbols, 'wrong descriptor count')
        if mode == 1:
            payload = ''.join(words[q] for q in values)
        elif mode == 4:
            payload = ''.join(bits(cluster, 2) + ''.join(words[q] for q in values[i:i+self.n])
                              for i in range(0, self.symbols, self.n))
        else:
            require(mode in (2, 3), 'unsupported coded mode')
            groups = groups if groups is not None else [list(range(self.n))]
            require(sorted(j for group in groups for j in group) == list(range(self.n)), 'invalid coefficient partition')
            require(len(groups) == (2 if mode == 2 else 1), 'wrong group count')
            if mode == 3:
                require(signs is not None and len(signs) == self.symbols, 'missing mode-3 signs')
            payload = ''
            for start in range(0, self.symbols, self.n):
                for book, group in enumerate(groups):
                    for j in group:
                        q = values[start+j]
                        payload += (words[book] if mode == 2 else words)[q]
                        if mode == 3:
                            payload += bits(int(signs[start+j]), 1)
        return self.packet(mode, payload, gain, line, active=active, padding=padding)

    def frames(self, payload):
        if isinstance(payload, (list, tuple)):
            require(2 <= len(payload) <= 4 and all(isinstance(p, bytes) for p in payload), 'invalid packet program')
            return list(payload)
        return [payload, self.fixed([self.zero] * self.symbols, active=False)]


def vector(q, row=None, zero=32, order=3):
    g = geometry(order)
    n, count = g["channels"], g["symbols"]
    return [q] * n + [zero] * (count - n) if row is None else [q if i == row else zero for i in range(count)]


def request(identity, packets, replicate='', quantization_bits=6, order=3):
    g = geometry(order)
    signature = dict(SIGNATURE, channels=g["channels"], layout_tag=g["layout_tag"],
                     packets=len(packets), frames=len(packets)*1024)
    if order != 3:
        signature["order"] = order
    if quantization_bits != 6:
        signature['quantization_bits'] = quantization_bits
    value = dict(native_identity=identity, signature=signature, cookie_sha256=digest(cookie(quantization_bits, order)),
                 packets=[dict(sha256=digest(p), bytes=len(p), frames=1024) for p in packets], replicate=replicate)
    return digest(canonical(value)), value


def write_bundle(path, packets, quantization_bits=6, order=3):
    g = geometry(order)
    n = g["channels"]
    path.mkdir()
    raw, cfg = b''.join(packets), cookie(quantization_bits, order)
    known = lambda v: dict(value=v, error=None)
    info = dict(schema_version=1, source='blackbox-synthetic.caf', file_bytes=len(raw), modified_unix_seconds=None,
        environment=dict(tool_version='hoa-blackbox-batch-v1', os='synthetic', architecture='portable', system_version='synthetic'),
        container=known('caff'), format=dict(sample_rate=48000, format_id=int.from_bytes(b'apac', 'big'), format_fourcc='apac',
        flags=0, bytes_per_packet=0, frames_per_packet=1024, bytes_per_frame=0, channels=n, bits_per_channel=0),
        layout=known(dict(tag=g["layout_tag"], bitmap=0, descriptions=[], ambisonic_order=order,
                         ambisonic_channel_order='ACN', ambisonic_normalization='SN3D')),
        packet_count=known(len(packets)), max_packet_bytes=known(max(map(len, packets))),
        packet_table=known(dict(priming_frames=0, valid_frames=1024*len(packets), remainder_frames=0)),
        cookie=known(dict(bytes=len(cfg), sha256=digest(cfg))), restricts_random_access=known(False))
    manifest = dict(schema_version=1, complete=True, file=info, start_packet=0,
                    requested_packets=len(packets), actual_packets=len(packets),
                    packet_data_file='packets.bin', packet_index_file='packets.jsonl', cookie_file='cookie.bin',
                    packet_data_bytes=len(raw), packet_data_sha256=digest(raw))
    offset = 0
    with (path / 'packets.jsonl').open('xb') as stream:
        for i, packet in enumerate(packets):
            stream.write(canonical(dict(schema_version=1, packet_index=i, export_offset=offset, bytes=len(packet), frames=1024,
                sha256=digest(packet), raw_frame_position=known(i * 1024),
                dependency=known(dict(independently_decodable=True, preroll_packet_count=0)), roll_distance=known(0))))
            offset += len(packet)
    (path / 'manifest.json').write_bytes(canonical(manifest))
    (path / 'packets.bin').write_bytes(raw)
    (path / 'cookie.bin').write_bytes(cfg)
