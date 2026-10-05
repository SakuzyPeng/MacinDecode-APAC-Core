"""Recover grouped and signed-delta books from public PCM, without dictionaries."""
import random

from .common import GAINS, MAX_DEPTH, SYMBOLS, canonical, digest, require, target_parts
from .maths import infer_tree


def quantized(value, signed=False):
    coordinate = abs(value)*32 if signed else (value+1)*32
    symbol = round(coordinate)
    require(0 <= symbol < 64 and abs(coordinate-symbol) < 1/8,
            'direct symbol classification is ambiguous')
    return symbol


def affected(before, after):
    channels = [i for i, (a, b) in enumerate(zip(before, after)) if abs(a-b) > 1/(8*32)]
    require(len(channels) <= 1, 'bit perturbation changed multiple coefficients')
    return channels[0] if channels else None


class DirectRecovery:
    def mode2_partition(self):
        saved = self.store.stage('_mode2', 'partition')
        if saved is not None:
            return saved
        baseline = self.observe('mode2:0', 'partition', 'zero', '')
        observations = {}

        def flip(offset):
            if offset not in observations:
                obs = self.observe('mode2:0', 'partition', f'flip:{offset}', '0'*offset+'1')
                obs['channel'] = affected(baseline['coefficients'], obs['coefficients'])
                observations[offset] = obs
            return observations[offset]['channel']

        positions, offset = [], 0
        for position in range(16):
            channel = flip(offset)
            require(channel is not None and channel not in [p['channel'] for p in positions],
                    'mode-2 coefficient order is not observable')
            for length in range(1, MAX_DEPTH+1):
                successor = flip(offset+length)
                if successor != channel:
                    require((successor is None) == (position == 15), 'mode-2 descriptor boundary differs')
                    positions.append(dict(channel=channel, offset=offset, zero_length=length))
                    offset += length
                    break
            else:
                require(False, 'mode-2 zero code exceeds depth limit')
        require(sorted(p['channel'] for p in positions) == list(range(16)), 'mode-2 partition misses channels')
        result = dict(baseline=baseline, positions=positions,
                      observations={str(k): v for k, v in observations.items()}, old_dictionary_consulted=False)
        self.store.save_stage('_mode2', 'partition', result)
        return result

    def direct_book(self, target, entries, decisions, group, layout):
        mode, index = target_parts(target)
        result = dict(schema_version=1, profile='hoa-blackbox-codebook-v1', order=3, quantization_bits=6,
                      mode=mode, book=index or 0, entries=entries, decisions=decisions,
                      coefficient_group=group, layout_sha256=digest(canonical(layout)),
                      policy_sha256=digest(canonical(self.calibration)), old_dictionary_consulted=False)
        self.store.save_stage(target, 'codebook', result)
        return result

    def mode2_books(self):
        partition = self.mode2_partition()
        positions = partition['positions']

        def recover(index, position):
            def query(pattern):
                obs = self.observe(f'mode2:{index}', 'codebook', pattern, '0'*position['offset']+pattern)
                return quantized(obs['coefficients'][position['channel']]), obs['evidence']
            entries, decisions, _ = infer_tree(query)
            return entries, decisions

        entries0, decisions0 = recover(0, positions[0])
        zero = next(e for e in entries0 if set(e['codeword']) == {'0'})
        require(zero['bit_length'] == positions[0]['zero_length'], 'mode-2 zero code length differs')
        membership, boundary = [], None
        for i, position in enumerate(positions[1:], 1):
            base_q = quantized(partition['baseline']['coefficients'][position['channel']])
            record = dict(position=i, probes=[])
            matches = base_q == zero['symbol'] and position['zero_length'] == zero['bit_length']
            if matches:
                # All 64 leaves, rather than a guessed subset, distinguish the
                # first book from a different complete code tree.
                for entry in entries0:
                    pattern = '0'*position['offset'] + entry['codeword'].ljust(MAX_DEPTH, '0')
                    obs = self.observe('mode2:0', 'membership', f'{i}:{entry["symbol"]}', pattern)
                    observed = quantized(obs['coefficients'][position['channel']])
                    record['probes'].append(dict(symbol=entry['symbol'], observed=observed, evidence=obs['evidence']))
                    if observed != entry['symbol']:
                        matches = False
                        break
            record['matches_first_book'] = matches
            membership.append(record)
            if not matches:
                boundary = i
                break
        require(boundary is not None, 'mode-2 books cannot be separated by PCM')
        entries1, decisions1 = recover(1, positions[boundary])
        for entries, selected in ((entries0, positions[:boundary]), (entries1, positions[boundary:])):
            zero = next(e for e in entries if set(e['codeword']) == {'0'})
            require(all(p['zero_length'] == zero['bit_length'] and
                        quantized(partition['baseline']['coefficients'][p['channel']]) == zero['symbol']
                        for p in selected), 'mode-2 partition is inconsistent with recovered books')
        groups = [[p['channel'] for p in selected] for selected in (positions[:boundary], positions[boundary:])]
        layout = dict(groups=groups, boundary=boundary, partition_sha256=digest(canonical(partition)),
                      membership=membership, old_dictionary_consulted=False)
        self.store.save_stage('_mode2', 'layout', layout)
        return [self.direct_book(f'mode2:{i}', entries, decisions, groups[i], layout)
                for i, (entries, decisions) in enumerate(((entries0, decisions0), (entries1, decisions1)))]

    def mode3_book(self):
        baseline = self.observe('mode3', 'bootstrap', 'zero', '')
        flip = self.observe('mode3', 'bootstrap', 'flip', '1')
        channel = affected(baseline['coefficients'], flip['coefficients'])
        require(channel is not None, 'mode-3 first symbol is not observable')
        repeats = []
        for pattern in ('', '1', '01', '10'):
            trials = [self.observe('mode3', 'bootstrap', f'{pattern}:{gain}:{extra}', pattern, gain, extra, True)
                      for gain, extra in ((128, 0), (129, 0), (128, 256))]
            symbols = [quantized(t['coefficients'][channel], signed=True) for t in trials]
            require(len(set(symbols)) == 1, 'mode-3 symbol changes with amplitude or padding')
            repeats.append(dict(pattern=pattern, trials=trials))

        def query(pattern):
            obs = self.observe('mode3', 'codebook', pattern, pattern)
            return quantized(obs['coefficients'][channel], signed=True), obs['evidence']

        entries, decisions, _ = infer_tree(query)
        words = {e['symbol']: e['codeword'] for e in entries}
        order, evidence = [], []
        for position in range(16):
            values = [0]*SYMBOLS
            values[position] = 48
            packet = self.writer.coded(3, None, values, words, signs=[True]*SYMBOLS)
            key, pcm = self.capture('mode3', 'layout', str(position), self.mode3_program(packet))
            require(not any(pcm[:1024*16]), 'mode-3 preparation frame is not silent')
            coefficients = self.estimate(pcm[1024*16:], 128)
            found = affected([0.]*16, coefficients)
            require(found is not None and abs(coefficients[found]-1.5) < 1/(8*32),
                    'mode-3 sign or initialized history differs')
            order.append(found)
            evidence.append(dict(evidence=key, coefficients=coefficients))
        require(sorted(order) == list(range(16)) and order[0] == channel, 'mode-3 coefficient order is ambiguous')
        layout = dict(groups=[order], observations=evidence, bootstrap=dict(baseline=baseline, flip=flip, repeats=repeats),
                      history_preparation='silent mode-0 frame with all coefficients zero', old_dictionary_consulted=False)
        self.store.save_stage('mode3', 'layout', layout)
        return self.direct_book('mode3', entries, decisions, order, layout)

    def mode3_program(self, packet, seed=32, history_packet=None):
        packets = [self.writer.fixed([seed]*SYMBOLS, active=False)]
        if history_packet is not None:
            packets.append(history_packet)
        return packets + [packet, self.writer.fixed([32]*SYMBOLS, active=False)]

    def validate_direct(self, target, book):
        mode, index = target_parts(target)
        words = {e['symbol']: e['codeword'] for e in book['entries']}
        if mode == 2:
            books = [self.store.stage(f'mode2:{i}', 'codebook') for i in range(2)]
            if any(b is None for b in books):
                books = self.mode2_books()
            words = [{e['symbol']: e['codeword'] for e in b['entries']} for b in books]
            groups = self.store.stage('_mode2', 'layout')['groups']
        else:
            groups = [book['coefficient_group']]
        checks, normal = [], {}

        def values_for(q):
            values = [32 if mode == 2 else 0]*SYMBOLS
            for channel in groups[index or 0]:
                values[channel] = q
            return values

        def check(values, gain, label, positive=True, signs=None, padding=0, line=0, seed=32, history=None):
            signs = signs if signs is not None else [positive]*SYMBOLS
            packet = self.writer.coded(mode, index, values, words, gain, line, padding, groups, signs)
            leading = 0
            if mode == 3:
                history_packet = None
                if history:
                    previous, previous_signs = history
                    history_packet = self.writer.coded(3, None, previous, words, groups=groups,
                                                       signs=previous_signs, active=False)
                packet = self.mode3_program(packet, seed, history_packet)
                leading = 1 + (history_packet is not None)
            key, pcm = self.capture(target, 'validation', label, packet, True)
            require(not any(pcm[:leading*1024*16]), 'history preparation emitted nonzero PCM')
            output = pcm[leading*1024*16:]
            observed = self.estimate(output, gain, line)
            expected = [(q-32)/32 for q in values[:16]]
            if mode == 3:
                expected = [(seed-32)/32 + q/32*(1 if signs[i] else -1) for i, q in enumerate(values[:16])]
                if history:
                    expected = [v + previous[i]/32*(1 if previous_signs[i] else -1) for i, v in enumerate(expected)]
            errors = [abs(a-b) for a, b in zip(observed, expected)]
            calibration = self.reference(gain, line)[0]['max_coefficient_error']
            limits = [max(8*e, 5e-7)*max(1, abs(v)) for e, v in zip(calibration, expected)]
            require(all(e < min(limit, 1/(8*32)) for e, limit in zip(errors, limits)),
                    'direct normal-length validation failed: '+label)
            checks.append(dict(label=label, evidence=key, gain=gain, line=line, preparation_frames=leading,
                               expected=expected, max_error=max(errors)))
            return digest(output.tobytes())

        for gain in GAINS:
            for q in range(64):
                for positive in ((True, False) if mode == 3 else (True,)):
                    normal[gain, q, positive] = check(values_for(q), gain, f'symbol:{gain}:{q}:{positive}', positive)
        rng = random.Random(0x484f4132+mode)
        mixtures = [[rng.randrange(64) for _ in range(SYMBOLS)] for _ in range(8)]
        for i, values in enumerate(mixtures):
            signs = [bool(rng.randrange(2)) for _ in range(SYMBOLS)]
            for gain in GAINS:
                check(values, gain, f'mixed:{gain}:{i}', signs=signs)
        for gain in GAINS:
            for q in (0, 31, 32, 63):
                result = check(values_for(q), gain, f'padding:{gain}:{q}', padding=128)
                require(result == normal[gain, q, True], 'fresh decoder or padding changed PCM')
                checks[-1]['padding_bit_identical'] = True
        for i, values in enumerate(mixtures[:4]):
            for gain in GAINS:
                check(values, gain, f'line1:{gain}:{i}', line=1)
        if mode == 3:
            for seed in (16, 48, 63):
                for gain in GAINS:
                    for i in range(2):
                        check(mixtures[i], gain, f'seed:{seed}:{gain}:{i}', seed=seed,
                              signs=[bool((j+i)%2) for j in range(SYMBOLS)])
                        check(mixtures[i], gain, f'history:{seed}:{gain}:{i}', seed=seed,
                              signs=[bool((j+i)%2) for j in range(SYMBOLS)],
                              history=(mixtures[i+2], [bool(j%3) for j in range(SYMBOLS)]))
            markers = [0]*SYMBOLS
            markers[groups[0][1]] = 17
            a = check(markers, 128, 'zero-sign:positive')
            signs = [True]*SYMBOLS
            signs[groups[0][0]] = False
            b = check(markers, 128, 'zero-sign:negative', signs=signs)
            require(a == b, 'zero sign changed a following coefficient')
        result = dict(status='passed', codebook_sha256=digest(canonical(book)), checks=checks,
                      layout_sha256=book['layout_sha256'], matrix_sha256=None, matrix_qualified=None,
                      old_dictionary_consulted=False)
        self.store.save_stage(target, 'validation', result)
        return result
