"""Mean-experiment geometry and bundles, independent of high-order campaigns."""
from array import array
import math
import sys

from hoa_blackbox_lib.common import EvidenceError, canonical, digest, require
from hoa_blackbox_lib.wire import SIGNATURE, bits, pack


def geometry(order=10):
    require(type(order) is int and 1 <= order <= 10, 'unsupported HOA order')
    n = (order+1)**2
    profile, level = ((5, 0) if n <= 16 else (5, 1) if n <= 36
                      else (5, 2) if n <= 49 else (0, 0))
    return dict(order=order, channels=n, components=min(5, n), bands=4,
                layout_tag=(190 << 16) | n, profile=profile, level=level)


def pcm_samples(raw, channels=121):
    require(channels in tuple((o+1)**2 for o in range(1, 11))
            and len(raw) in tuple(frames*channels*4 for frames in (2048, 3072, 4096)),
            'wrong PCM byte count', EvidenceError)
    values = array('f')
    values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    require(all(math.isfinite(v) for v in values), 'nonfinite PCM', EvidenceError)
    return values


def escaped(value, widths):
    require(type(value) is int and value >= 0, 'invalid escaped integer')
    result = ''
    for i, width in enumerate(widths):
        maximum = (1 << width)-1
        if value < maximum or i == len(widths)-1:
            return result+bits(value, width)
        result += bits(maximum, width)
        value -= maximum
    raise AssertionError('unreachable escaped integer')


def cookie(quantization_bits=9, order=10):
    require(quantization_bits == 9, 'mean experiment requires nine-bit mode 0')
    g = geometry(order)
    n, components = g['channels'], g['components']
    fields = [(0, 32), (int.from_bytes(b'dapa', 'big'), 32), (0, 32), (0x800, 16),
              (g['profile'], 6), (g['level'], 4), (0, 1), (3, 6), (0, 6), (n, 8),
              (2, 8), (0, 1), (1, 3), (0, 8), (2, 3)]
    wire = ''.join(bits(v, w) for v, w in fields)
    wire += ('1100110'+bits(1, 2)+bits(0, 2)+bits(3, 2)+bits(order, 4)
             +bits(components, 4)+bits(0, (n-1).bit_length()))
    wire += ((bits(3, 4)+bits(order, order.bit_length()))*components
             +'0'+escaped(n, (5, 10, 16))+'000'*n)
    wire += '0'+bits(190, 16)+bits(n, 16)+'0'+'0'+bits(0, 3)+bits(0, 2)+'000000'
    raw = pack(wire)
    return len(raw).to_bytes(4, 'big')+raw[4:]


def write_bundle(path, packets, quantization_bits=9, order=10):
    g = geometry(order)
    n = g['channels']
    path.mkdir()
    raw, cfg = b''.join(packets), cookie(quantization_bits, order)
    def known(v):
        return dict(value=v, error=None)
    info = dict(schema_version=1, source='blackbox-synthetic.caf', file_bytes=len(raw), modified_unix_seconds=None,
        environment=dict(tool_version='hoa-blackbox-batch-v1', os='synthetic', architecture='portable', system_version='synthetic'),
        container=known('caff'), format=dict(sample_rate=48000, format_id=int.from_bytes(b'apac', 'big'), format_fourcc='apac',
        flags=0, bytes_per_packet=0, frames_per_packet=1024, bytes_per_frame=0, channels=n, bits_per_channel=0),
        layout=known(dict(tag=g['layout_tag'], bitmap=0, descriptions=[], ambisonic_order=order,
                         ambisonic_channel_order='ACN', ambisonic_normalization='SN3D')),
        packet_count=known(len(packets)), max_packet_bytes=known(max(map(len, packets))),
        packet_table=known(dict(priming_frames=0, valid_frames=1024*len(packets), remainder_frames=0)),
        cookie=known(dict(bytes=len(cfg), sha256=digest(cfg))), restricts_random_access=known(False))
    manifest = dict(schema_version=1, complete=True, file=info, start_packet=0,
        requested_packets=len(packets), actual_packets=len(packets), packet_data_file='packets.bin',
        packet_index_file='packets.jsonl', cookie_file='cookie.bin', packet_data_bytes=len(raw), packet_data_sha256=digest(raw))
    offset = 0
    with (path/'packets.jsonl').open('xb') as stream:
        for i, packet in enumerate(packets):
            stream.write(canonical(dict(schema_version=1, packet_index=i, export_offset=offset, bytes=len(packet), frames=1024,
                sha256=digest(packet), raw_frame_position=known(i*1024),
                dependency=known(dict(independently_decodable=True, preroll_packet_count=0)), roll_distance=known(0))))
            offset += len(packet)
    (path/'manifest.json').write_bytes(canonical(manifest))
    (path/'packets.bin').write_bytes(raw)
    (path/'cookie.bin').write_bytes(cfg)
