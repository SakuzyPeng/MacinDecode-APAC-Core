"""Artificial shared-ICS packets with explicit integer and CAC index truth."""
import copy
import itertools
import json
from pathlib import Path

from spectrum_vectors import bits, channel, pack, window_groups

BOOKS = json.loads((Path(__file__).resolve().parents[1] / 'data/cac-codebooks.json').read_text())


def code(name, value):
    book = BOOKS[name]
    return bits(book['codes'][value], book['bits'][value])


def encode_runs(indices):
    runs = []
    start = 0
    while start < len(indices):
        end = start+1
        while end < len(indices) and indices[end] == indices[start] and end-start < 43:
            end += 1
        repeat = 43 if end == len(indices) else end-start-1
        runs.append((indices[start], repeat))
        start = end
    return runs


def frame(case,rate=48000):
    block, grouping, gain = (case.get(k, v) for k, v in [('block', 0), ('grouping', 0), ('gain', 160)])
    left, right = copy.deepcopy(case.get('left', {})), copy.deepcopy(case.get('right', {}))
    required = max([*left, *right], default=-1)+1
    max_sfb = case.get('max_sfb', required)
    from shared_config_tables import offsets
    from spectrum_vectors import TABLES
    if not required <= max_sfb < len(offsets(rate,block==2,TABLES)):
        raise ValueError('shared max_sfb does not contain both streams')
    for side in (left, right):
        if max_sfb:
            side.setdefault(max_sfb-1, (0, (), 0))
    a, expected_a = channel(left, block, grouping, gain,rate)
    b, expected_b = channel(right, block, grouping, gain,rate)
    header = expected_b.pop('header_bits')
    body = b[header:]
    origin = 5+len(a)
    expected_a.update(channel_index=0, stream_bit_offset=4+expected_a.pop('header_bits'),
                      spectral_bit_offset=4+expected_a.pop('spectral_relative_offset'), end_bit_offset=4+len(a))
    expected_b.update(channel_index=1, stream_bit_offset=origin,
                      spectral_bit_offset=origin+expected_b.pop('spectral_relative_offset')-header,
                      end_bit_offset=origin+len(body))
    groups = window_groups(grouping, block == 2)
    indices = case.get('indices', [case.get('cac_gain', 0)]*(len(groups)*max_sfb))
    if len(indices) != len(groups)*max_sfb or any(not 0 <= v <= 34 for v in indices):
        raise ValueError('invalid CAC index truth')
    pairs = case.get('raw_runs', encode_runs(indices))
    payload, runs = '', []
    start = origin+len(body)
    for value, repeat in pairs:
        bit_offset = start+len(payload)
        word = code('gain', value)+code('repeat', repeat)
        runs.append(dict(gain_index=value, repeat_code=repeat, bit_offset=bit_offset, bit_length=len(word)))
        payload += word
    end = start+len(payload)
    expected = dict(channels=[expected_a, expected_b], shared_ics=True,
                    cac=dict(start_bit_offset=start, end_bit_offset=end, runs=runs,
                             gain_indices=[list(indices[g*max_sfb:(g+1)*max_sfb]) for g in range(len(groups))]))
    return pack('0110'+a+'1'+body+payload+'0000')+b'\0', expected


def packet(case,rate=48000):
    if case.get('independent'):
        from spectrum_vectors import frame as independent_frame
        data, channels = independent_frame(case,rate)
        return data, dict(channels=channels, shared_ics=False, cac=None)
    return frame(case,rate)


def windows():
    return [(0, 0), (1, 0), (3, 0), (2, 0), (2, 0x55), (2, 0x7f)]


def cases():
    for gain in range(35):
        for block, grouping in windows():
            for x, y in [(1, 0), (0, 1), (1, 1), (1, -1)]:
                yield dict(kind='matrix_basis', block=block, grouping=grouping, gain=100,
                           left={0:(1,[x,0,0,0],100)}, right={0:(1,[y,0,0,0],100)}, cac_gain=gain)
    for block, grouping in windows():
        for sfb in range(14 if block == 2 else 49):
            for side in ['left', 'right']:
                cb = sfb % 11 + 1
                values = [1,-1,0,1] if cb <= 4 else [16,-17] if cb == 11 else [1,-1]
                count = len(window_groups(grouping, block == 2))*(sfb+1)
                yield dict(kind='band', block=block, grouping=grouping,
                           **{side:{sfb:(cb,values,160)}}, indices=[i % 35 for i in range(count)])
    for repeat in range(43):
        yield dict(kind='repeat', max_sfb=49, left={0:(1,[1,0,0,0],160)},
                   indices=[9]*(repeat+1)+[26]*(48-repeat))
    for count in [43, 44]:
        yield dict(kind='terminal_capacity', max_sfb=count, cac_gain=9, raw_runs=[(9,43)],
                   left={count-1:(1,[1,0,0,0],160)})
    yield dict(kind='terminal_capacity', max_sfb=49, indices=[9]*5+[26]*44,
               raw_runs=[(9,4),(26,43)], left={48:(1,[1,0,0,0],160)})
    for block, grouping in windows():
        yield dict(kind='zero_max_sfb', block=block, grouping=grouping)
        yield dict(kind='zero_bands', block=block, grouping=grouping,
                   max_sfb=14 if block == 2 else 49, cac_gain=34)
    for cac_gain in range(35):
        for gain in [0, 100, 160, 255]:
            yield dict(kind='gain_pressure', gain=gain, cac_gain=cac_gain,
                       left={0:(11,[8191,-8190],gain)}, right={0:(11,[8190,8191],gain)})
    for value in [15,16,17,31,32,33,63,64,127,128,255,256,511,512,1023,1024,2047,2048,4095,4096,8190,8191]:
        yield dict(kind='escape', cac_gain=18+value%17,
                   left={0:(11,[value,-value],160)}, right={0:(11,[-value,value],160)})
    for signs in itertools.product([-1, 1], repeat=4):
        yield dict(kind='cancellation', gain=255, cac_gain=9 if signs[0]>0 else 26,
                   left={0:(1,list(signs),255)}, right={0:(1,list(signs),255)})
    yield dict(kind='negative_scale', gain=0, cac_gain=34,
               left={i:(1,[1,0,-1,0],sf) for i,sf in enumerate([-60,-120,-180,-240,-256])})
    yield dict(kind='scale_carry', block=2, grouping=0x55, cac_gain=17,
               left={0:(1,[1,0,-1,0],155),3:(3,[1,-1,0,1],170)}, right={0:(11,[16,-17],160)})


def sequences():
    for case in cases():
        block = case.get('block', 0)
        if block == 0:
            sequence = [{}, case, case, {}, {}]
        else:
            sequence = [{}, dict(block=1), dict(block=2), dict(block=3), {}, {}]
            sequence[block] = case
        yield case['kind'], sequence
    for gain in range(35):
        independent = dict(independent=True, left={0:(1,[1,0,0,0],160)})
        shared = dict(cac_gain=gain, left={0:(1,[1,0,0,0],160)})
        yield 'header_switch', [independent, shared, independent, shared, {}, {}]
    yield 'window_header_switch', [{},dict(independent=True,block=1,left={0:(1,[1,0,0,0],160)}),
                                  dict(block=2,grouping=0x55,cac_gain=26,left={0:(11,[8191,-17],160)}),
                                  dict(independent=True,block=3),{},{}]
