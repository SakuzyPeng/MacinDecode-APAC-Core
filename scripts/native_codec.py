"""Bounded public AudioConverter observations through the existing SDK bridge."""
import argparse
import ctypes as c
import hashlib
import json
import math
from pathlib import Path

from native_component import identity, sha256_file, unchanged


UInt = c.c_uint32
Byte = c.c_uint8
Pointer = c.POINTER
Void = c.c_void_p
LIMIT = 128 * 1024 * 1024


class Config(c.Structure):
    _fields_ = [('rate', c.c_double), ('fields', UInt * 7), ('has_layout', UInt),
                ('tag', UInt), ('bitmap', UInt), ('description_count', UInt),
                ('descriptions', Void), ('cookie', Pointer(Byte)),
                ('cookie_bytes', UInt), ('defer_cookie', UInt)]


class Packet(c.Structure):
    _fields_ = [('offset', UInt), ('bytes', UInt), ('frames', UInt)]


Callback = c.CFUNCTYPE(c.c_int32, Void, UInt, Pointer(Pointer(Byte)), Pointer(UInt),
                      Pointer(Pointer(Packet)), Pointer(UInt))


def decode(bridge, bundle, destination):
    component = identity()
    bridge_sha = sha256_file(bridge)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    fmt = manifest['file']['format']
    channels, rate = fmt['channels'], fmt['sample_rate']
    cookie = (bundle / 'cookie.bin').read_bytes()
    raw = (bundle / 'packets.bin').read_bytes()
    records = [json.loads(line) for line in (bundle / 'packets.jsonl').read_text().splitlines()]
    if not 1 <= channels <= 255 or not 0 < len(cookie) <= 8 * 1024 * 1024 or len(raw) > LIMIT:
        raise ValueError('native observation exceeds input bounds')
    offset = 0
    for index, row in enumerate(records):
        size = row['bytes']
        packet = raw[offset:offset + size]
        if (row['packet_index'] != index or row['export_offset'] != offset or row['frames'] != 1024
                or len(packet) != size or hashlib.sha256(packet).hexdigest() != row['sha256']):
            raise ValueError('native packet index or bytes differ')
        offset += size
    if offset != len(raw) or hashlib.sha256(raw).hexdigest() != manifest['packet_data_sha256']:
        raise ValueError('native packet payload differs from manifest')
    if hashlib.sha256(cookie).hexdigest() != manifest['file']['cookie']['value']['sha256']:
        raise ValueError('native cookie differs from manifest')
    library = c.CDLL(str(bridge))
    signatures = {
        'create': [Pointer(Config), Callback, Void, Pointer(Void)],
        'read': [Void, Pointer(c.c_float), Pointer(UInt)],
        'reset': [Void], 'close': [Void], 'set_cookie': [Void, Pointer(Byte), UInt],
        'set_off_property': [Void, UInt], 'property': [Void, UInt, Pointer(UInt), UInt],
    }
    for name, arguments in signatures.items():
        function = getattr(library, 'apac_replay_' + name)
        function.argtypes = arguments
        function.restype = c.c_int32
    library.apac_last_operation.restype = c.c_char_p
    cookie_buffer = (Byte * len(cookie)).from_buffer_copy(cookie)
    data = (Byte * len(raw)).from_buffer_copy(raw)
    description = Packet()
    state = dict(index=0, offset=0)

    @Callback
    def supply(context, wanted, pointer, byte_count, packets, count):
        if state['index'] == len(records):
            count[0], byte_count[0] = 0, 0
            pointer[0], packets[0] = Pointer(Byte)(), Pointer(Packet)()
            return 0
        if not wanted:
            return -50
        size = records[state['index']]['bytes']
        description.offset, description.bytes, description.frames = 0, size, 1024
        pointer[0] = c.cast(c.byref(data, state['offset']), Pointer(Byte))
        byte_count[0], packets[0], count[0] = size, c.pointer(description), 1
        state['index'] += 1
        state['offset'] += size
        return 0

    destination.mkdir()
    marker = destination / '.incomplete.json'
    marker.write_text(json.dumps(component) + '\n')
    config = Config(rate, (UInt * 7)(int.from_bytes(b'apac', 'big'), 0, 0, 1024, 0, channels, 0),
                    0, 0, 0, 0, None, cookie_buffer, len(cookie), 1)
    handle, events, pcm = Void(), [], bytearray()

    def event(stage, status):
        record = dict(stage=stage, status=status,
                      operation=library.apac_last_operation().decode(), supplied=state['index'])
        events.append(record)
        return status

    status = event('create', library.apac_replay_create(c.byref(config), supply, None, c.byref(handle)))
    try:
        if not status:
            for property_name in (b'mdrc', b'^pro', b'^tlc'):
                status = event(property_name.decode(), library.apac_replay_set_off_property(
                    handle, int.from_bytes(property_name, 'big')))
                if status:
                    break
        if not status:
            status = event('cookie', library.apac_replay_set_cookie(handle, cookie_buffer, len(cookie)))
        if not status:
            status = event('reset', library.apac_replay_reset(handle))
        if not status:
            for _ in range(len(records) + 4):
                buffer, frames = (c.c_float * (1024 * channels))(), UInt(1024)
                status = event('decode', library.apac_replay_read(handle, buffer, c.byref(frames)))
                events[-1]['frames'] = frames.value
                if status:
                    break
                if frames.value > 1024 or len(pcm) + frames.value * channels * 4 > LIMIT:
                    raise ValueError('native output exceeds observation bound')
                if not all(math.isfinite(value) for value in buffer[:frames.value * channels]):
                    raise ValueError('native output contains nonfinite samples')
                pcm.extend(bytes(buffer)[:frames.value * channels * 4])
                if not frames.value:
                    break
            else:
                raise ValueError('native converter did not drain within observation bound')
    finally:
        if handle:
            close_status = event('close', library.apac_replay_close(handle))
            if close_status:
                raise ValueError('native converter close failed')
    unchanged(component)
    if bridge_sha != sha256_file(bridge):
        raise ValueError('native SDK bridge changed during observation')
    result = dict(**component, complete=True, status=status, channels=channels, sample_rate=rate,
                  cookie_sha256=hashlib.sha256(cookie).hexdigest(), packet_data_sha256=hashlib.sha256(raw).hexdigest(),
                  bridge_sha256=bridge_sha, frames=len(pcm) // (channels * 4),
                  pcm_sha256=hashlib.sha256(pcm).hexdigest(), events=events)
    (destination / 'pcm.f32le').write_bytes(pcm)
    (destination / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    marker.unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = decode(args.bridge.resolve(), args.bundle.resolve(), args.out.resolve())
    print(json.dumps(result))
    return int(result['status'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
