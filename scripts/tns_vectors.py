"""Artificial APAC TNS payloads; expected parameters come directly from the writer."""
import copy
import random

from cac_vectors import packet as cac_packet, windows
from spectrum_vectors import TABLES, bits, pack


def channel_payload(spec, ics, origin, rate, channel_index):
    short = ics['block_type'] == 2
    n, full, limit = (128, 14, 14) if short else (1024, 49, 40 if rate == 48000 else 42)
    offsets = TABLES['short_offsets' if short else 'long_offsets']
    payload = '0' if spec is None else '1'
    result = dict(channel_index=channel_index, present=spec is not None, start_bit_offset=origin, windows=[])
    if spec is not None:
        for w in range(8 if short else 1):
            entry = spec.get(w, {})
            filters, r = entry.get('filters', []), entry.get('resolution', 3)
            start = origin+len(payload)
            payload += bits(len(filters), 1 if short else 2)
            if filters:
                payload += bits(r-3, 1)
            records, top = [], full
            for f in filters:
                at = origin+len(payload)
                length, q = f['length'], list(f.get('q', []))
                order = len(q)
                payload += bits(length, 4 if short else 6)+bits(order, 3 if short else 5)
                direction = f.get('direction', False) if order else None
                compression = f.get('compression', False) if order else None
                width = r-int(compression) if order else None
                if order:
                    payload += bits(int(direction), 1)+bits(int(compression), 1)
                    for value in q:
                        if not -(1 << (width-1)) <= value < 1 << (width-1):
                            raise ValueError('coefficient outside signed width')
                        payload += bits(value % (1 << width), width)
                bottom = max(0, top-length)
                active = min(limit, ics['max_sfb'])
                records.append(dict(length=length, order=order, direction=direction, compression=compression,
                                    coefficient_width=width, quantized=q, top_band=top, bottom_band=bottom,
                                    start_line=w*n+offsets[min(bottom, active)], end_line=w*n+offsets[min(top, active)],
                                    start_bit_offset=at, end_bit_offset=origin+len(payload)))
                top = bottom
            result['windows'].append(dict(window_index=w, resolution=r if filters else None, filters=records,
                                           start_bit_offset=start, end_bit_offset=origin+len(payload)))
    result['end_bit_offset'] = origin+len(payload)
    return payload, result


def packet(case, rate=48000):
    raw, truth = cac_packet(case)
    end = truth['cac']['end_bit_offset'] if truth['shared_ics'] else truth['channels'][-1]['end_bit_offset']
    payload = ''.join(bits(v, 8) for v in raw)[:end]
    truth['tns'] = []
    for i, side in enumerate(('left_tns', 'right_tns')):
        encoded, expected = channel_payload(case.get(side), truth['channels'][i]['ics'], len(payload), rate, i)
        payload += encoded
        truth['tns'].append(expected)
    truth['tns_end_bit_offset'] = len(payload)
    return pack(payload+case.get('bwe_flags', '00'))+b'\0', truth


def filter_spec(q, length=49, direction=False, resolution=4, compression=False, window=0):
    return {window: dict(resolution=resolution, filters=[dict(length=length, q=list(q),
                         direction=direction, compression=compression)])}


def boundary_cases():
    for block, grouping in windows():
        short = block == 2
        full = 14 if short else 49
        base = dict(block=block, grouping=grouping, max_sfb=full, left={0:(1,[1,0,0,0],100)}, gain=100)
        yield dict(base, kind='absent')
        yield dict(base, kind='zero_filters', left_tns={}, right_tns={})
        for maximum in (0, 1, full-1, full):
            for length in (1, full, (1 << (4 if short else 6))-1):
                for order in (0, 1):
                    q = [1]*order
                    spec = filter_spec(q, length, resolution=3, window=7 if short else 0)
                    yield dict(base, kind='boundary', left={} if maximum==0 else base['left'], max_sfb=maximum,
                               left_tns=spec, right_tns=spec)
        if not short:
            for count in (2, 3):
                filters = [dict(length=15, q=[]), dict(length=15, q=[1])][:count]
                if count==3: filters.append(dict(length=63, q=[-1], direction=True))
                yield dict(base, kind='multiple_filters', left_tns={0:dict(filters=filters)})


def cases():
    yield from boundary_cases()
    for block, grouping in windows():
        short = block == 2
        full, maximum = (14, 7) if short else (49, 12)
        # All 36 encoded coefficients, both traversals and both channels.
        # The lowest band confines the dense response and is independent of grouping.
        for r in (3, 4):
            for compression in (False, True):
                for q in range(-(1 << (r-int(compression)-1)), 1 << (r-int(compression)-1)):
                    for direction in (False, True):
                        for side in ('left', 'right'):
                            yield dict(kind='first_order', block=block, grouping=grouping, gain=100,
                                       **{side:{0:(1,[1,0,0,0],100)}, side+'_tns':filter_spec([q], full, direction, r, compression)})
        for order in range(1, maximum+1):
            for position in range(order):
                q = [0]*order; q[position] = 1 if position%2 else -1
                for direction in (False, True):
                    yield dict(kind='sparse_order', block=block, grouping=grouping, gain=100, max_sfb=6,
                               left={0:(1,[1,0,0,0],100)}, right={0:(1,[0,1,0,0],100)},
                               left_tns=filter_spec(q,full,direction), right_tns=filter_spec(q,full,not direction))
        for sfb in range(full):
            for direction in (False, True):
                spec = filter_spec([1], full-sfb, direction)
                if short: spec = {w:copy.deepcopy(spec[0]) for w in range(8)}
                yield dict(kind='band', block=block, grouping=grouping, gain=100, cac_gain=9,
                           left={sfb:(1,[1,0,0,0],100)}, left_tns=spec, right_tns=spec)
        for w in range(8 if short else 1):
            yield dict(kind='window', block=block, grouping=grouping, gain=100,
                       left={0:(1,[1,0,0,0],100)}, left_tns=filter_spec([1],full,window=w),
                       right_tns=filter_spec([-1],full,window=w))
        rng = random.Random(0x544e53)
        for q in ([7]*maximum, [-8]*maximum, [-8 if j%2 else 7 for j in range(maximum)],
                  [rng.randrange(-8,8) for _ in range(maximum)]):
            for gain in (100, 255):
                for direction in (False, True):
                    yield dict(kind='dense_stress', block=block, grouping=grouping, gain=gain, cac_gain=26,
                               left={0:(11,[8191,-8190],gain)}, right={0:(11,[8190,8191],gain)},
                               left_tns=filter_spec(q,full,direction),right_tns=filter_spec(q,full,not direction),
                               max_sfb=full)


def sequences():
    for case in cases():
        block = case.get('block', 0)
        if block == 0:
            sequence = [{}, case, case, {}, {}]
        else:
            sequence = [{}, dict(block=1), dict(block=2), dict(block=3), {}, {}]
            sequence[block] = case
        yield case['kind'], sequence
    for direction in (False, True):
        shared = dict(cac_gain=9, left={0:(1,[1,0,0,0],100)}, gain=100,
                      left_tns=filter_spec([1],direction=direction))
        independent = dict(shared, independent=True)
        yield 'header_switch', [shared, independent, shared, independent, {}, {}]
