"""Recover grouped and signed-delta books from public PCM, without dictionaries."""
import random

from .common import GAINS, MAX_DEPTH, canonical, digest, require, target_parts
from .maths import infer_tree


def quantized(value, signed=False, zero=32):
    coordinate = abs(value)*zero if signed else (value+1)*zero
    symbol = round(coordinate)
    require(0 <= symbol < 2*zero and abs(coordinate-symbol) < 1/8,
            'direct symbol classification is ambiguous')
    return symbol


def affected(before, after, zero=32):
    channels = [i for i, (a, b) in enumerate(zip(before, after)) if abs(a-b) > 1/(8*zero)]
    require(len(channels) <= 1, 'bit perturbation changed multiple coefficients')
    return channels[0] if channels else None


class DirectRecovery:
    def quantize(self, value, signed=False):
        return quantized(value, signed, self.zero)

    def affected(self, before, after):
        return affected(before, after, self.zero)

    def mode2_partition(self):
        saved = self.store.stage('_mode2', 'partition')
        if saved is not None:
            return saved
        baseline = self.observe('mode2:0', 'partition', 'zero', '')
        observations = {}

        def flip(offset):
            if offset not in observations:
                obs = self.observe('mode2:0', 'partition', f'flip:{offset}', '0'*offset+'1')
                obs['channel'] = self.affected(baseline['coefficients'], obs['coefficients'])
                observations[offset] = obs
            return observations[offset]['channel']

        positions, offset = [], 0
        for position in range(self.n):
            channel = flip(offset)
            require(channel is not None and channel not in [p['channel'] for p in positions],
                    'mode-2 coefficient order is not observable')
            for length in range(1, MAX_DEPTH+1):
                successor = flip(offset+length)
                if successor != channel:
                    require((successor is None) == (position == self.n-1), 'mode-2 descriptor boundary differs')
                    positions.append(dict(channel=channel, offset=offset, zero_length=length))
                    offset += length
                    break
            else:
                require(False, 'mode-2 zero code exceeds depth limit')
        require(sorted(p['channel'] for p in positions) == list(range(self.n)), 'mode-2 partition misses channels')
        result = dict(baseline=baseline, positions=positions,
                      observations={str(k): v for k, v in observations.items()}, old_dictionary_consulted=False)
        self.store.save_stage('_mode2', 'partition', result)
        return result

    def direct_book(self, target, entries, decisions, group, layout):
        mode, index = target_parts(target)
        result = dict(schema_version=1, profile='hoa-blackbox-codebook-v1', order=self.order, quantization_bits=self.precision,
                      mode=mode, book=index or 0, entries=entries, decisions=decisions,
                      coefficient_group=group, layout_sha256=digest(canonical(layout)),
                      policy_sha256=digest(canonical(self.calibration)), old_dictionary_consulted=False)
        if self.priors:
            result.update(prior_sha256=self.store.config['prior_sha256'], group_reused=True)
        self.store.save_stage(target, 'codebook', result)
        return result

    def mode2_books(self):
        if self.priors:
            return self.mode2_prior_books()
        partition = self.mode2_partition()
        positions = partition['positions']

        def recover(index, position):
            def query(pattern):
                obs = self.observe(f'mode2:{index}', 'codebook', pattern, '0'*position['offset']+pattern)
                return self.quantize(obs['coefficients'][position['channel']]), obs['evidence']
            entries, decisions, _ = infer_tree(query, symbols=self.levels)
            return entries, decisions

        entries0, decisions0 = recover(0, positions[0])
        zero = next(e for e in entries0 if set(e['codeword']) == {'0'})
        require(zero['bit_length'] == positions[0]['zero_length'], 'mode-2 zero code length differs')
        membership, boundary = [], None
        for i, position in enumerate(positions[1:], 1):
            base_q = self.quantize(partition['baseline']['coefficients'][position['channel']])
            record = dict(position=i, probes=[])
            matches = base_q == zero['symbol'] and position['zero_length'] == zero['bit_length']
            if matches:
                # All leaves, rather than a guessed subset, distinguish the
                # first book from a different complete code tree.
                for entry in entries0:
                    pattern = '0'*position['offset'] + entry['codeword'].ljust(MAX_DEPTH, '0')
                    obs = self.observe('mode2:0', 'membership', f'{i}:{entry["symbol"]}', pattern)
                    observed = self.quantize(obs['coefficients'][position['channel']])
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
                        self.quantize(partition['baseline']['coefficients'][p['channel']]) == zero['symbol']
                        for p in selected), 'mode-2 partition is inconsistent with recovered books')
        groups = [[p['channel'] for p in selected] for selected in (positions[:boundary], positions[boundary:])]
        layout = dict(groups=groups, boundary=boundary, partition_sha256=digest(canonical(partition)),
                      membership=membership, old_dictionary_consulted=False)
        self.store.save_stage('_mode2', 'layout', layout)
        return [self.direct_book(f'mode2:{i}', entries, decisions, groups[i], layout)
                for i, (entries, decisions) in enumerate(((entries0, decisions0), (entries1, decisions1)))]

    def mode2_prior_books(self):
        groups = [self.priors['groups'][f'2:{i}']['indices'] for i in range(2)]
        recovered, preceding = [], ''
        for index in range(2):
            def query(pattern):
                obs = self.observe(f'mode2:{index}', 'codebook', pattern, preceding+pattern)
                return self.quantize(obs['coefficients'][groups[index][0]]), obs['evidence']
            entries, decisions, _ = infer_tree(query, symbols=self.levels)
            recovered.append((entries, decisions))
            if index == 0:
                word = next(e['codeword'] for e in entries if e['symbol'] == self.zero)
                preceding = word * len(groups[0])
        layout = dict(groups=groups, prior_sha256=self.store.config['prior_sha256'],
                      group_reused=True, second_book_prefix=preceding, old_dictionary_consulted=False)
        self.store.save_stage('_mode2', 'layout', layout)
        return [self.direct_book(f'mode2:{i}', entries, decisions, groups[i], layout)
                for i, (entries, decisions) in enumerate(recovered)]

    def mode3_book(self):
        baseline = self.observe('mode3', 'bootstrap', 'zero', '')
        flip = self.observe('mode3', 'bootstrap', 'flip', '1')
        channel = self.affected(baseline['coefficients'], flip['coefficients'])
        require(channel is not None, 'mode-3 first symbol is not observable')
        repeats = []
        for pattern in ('', '1', '01', '10'):
            trials = [self.observe('mode3', 'bootstrap', f'{pattern}:{gain}:{extra}', pattern, gain, extra, True)
                      for gain, extra in ((128, 0), (129, 0), (128, 256))]
            symbols = [self.quantize(t['coefficients'][channel], signed=True) for t in trials]
            require(len(set(symbols)) == 1, 'mode-3 symbol changes with amplitude or padding')
            repeats.append(dict(pattern=pattern, trials=trials))

        def query(pattern):
            obs = self.observe('mode3', 'codebook', pattern, pattern)
            return self.quantize(obs['coefficients'][channel], signed=True), obs['evidence']

        entries, decisions, _ = infer_tree(query, symbols=self.levels)
        words = {e['symbol']: e['codeword'] for e in entries}
        order, evidence, cases = [], [], []
        for position in range(self.n):
            values = [0]*self.symbols
            values[position] = self.positive_half
            packet = self.writer.coded(3, None, values, words, signs=[True]*self.symbols)
            cases.append((str(position), self.mode3_program(packet)))
        with self.capture_batch('mode3', 'layout', cases) as outputs:
            for key, pcm in outputs:
                require(not any(pcm[:1024*self.n]), 'mode-3 preparation frame is not silent')
                coefficients = self.estimate(pcm[1024*self.n:], 128)
                found = self.affected([0.]*self.n, coefficients)
                require(found is not None and abs(coefficients[found]-1.5) < 1/(8*self.zero),
                        'mode-3 sign or initialized history differs')
                order.append(found)
                evidence.append(dict(evidence=key, coefficients=coefficients))
        require(sorted(order) == list(range(self.n)) and order[0] == channel, 'mode-3 coefficient order is ambiguous')
        layout = dict(groups=[order], observations=evidence, bootstrap=dict(baseline=baseline, flip=flip, repeats=repeats),
                      history_preparation='silent mode-0 frame with all coefficients zero', old_dictionary_consulted=False)
        if self.priors:
            require(order == self.priors['groups']['3:0']['indices'], 'native coefficient order differs from qualified prior')
            layout.update(prior_sha256=self.store.config['prior_sha256'], group_reused=True)
        self.store.save_stage('mode3', 'layout', layout)
        return self.direct_book('mode3', entries, decisions, order, layout)

    def mode3_program(self, packet, seed=None, history_packet=None):
        seed = self.zero if seed is None else seed
        packets = [self.writer.fixed([seed]*self.symbols, active=False)]
        if history_packet is not None:
            packets.append(history_packet)
        return packets + [packet, self.writer.fixed([self.zero]*self.symbols, active=False)]

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
        checks, hashes, cases = [], {}, []

        def values_for(q):
            values = [self.zero if mode == 2 else 0]*self.symbols
            # Observe both books in one frame. Every output coefficient is
            # checked, and separate basis/mixed cases verify the partition.
            for channel in (range(self.n) if mode == 2 else groups[index or 0]):
                values[channel] = q
            return values

        def add(values, gain, label, positive=True, signs=None, padding=0, line=0, seed=None, history=None, equal_to=None):
            seed = self.zero if seed is None else seed
            signs = signs if signs is not None else [positive]*self.symbols
            packet = self.writer.coded(mode, None if mode == 2 else index, values, words, gain, line, padding, groups, signs)
            leading = 0
            if mode == 3:
                history_packet = None
                if history:
                    previous, previous_signs = history
                    history_packet = self.writer.coded(3, None, previous, words, groups=groups,
                                                       signs=previous_signs, active=False)
                packet = self.mode3_program(packet, seed, history_packet)
                leading = 1 + (history_packet is not None)
            cases.append(dict(values=values, gain=gain, label=label, signs=signs, line=line, seed=seed,
                              history=history, leading=leading, payload=packet, equal_to=equal_to))

        def check(case, key, pcm):
            values, gain, label, signs, line, seed, history, leading = (
                case[k] for k in ('values', 'gain', 'label', 'signs', 'line', 'seed', 'history', 'leading'))
            require(not any(pcm[:leading*1024*self.n]), 'history preparation emitted nonzero PCM')
            output = pcm[leading*1024*self.n:]
            observed = self.estimate(output, gain, line)
            expected = [(q-self.zero)/self.zero for q in values[:self.n]]
            if mode == 3:
                expected = [(seed-self.zero)/self.zero + q/self.zero*(1 if signs[i] else -1) for i, q in enumerate(values[:self.n])]
                if history:
                    previous, previous_signs = history
                    expected = [v + previous[i]/self.zero*(1 if previous_signs[i] else -1) for i, v in enumerate(expected)]
            errors = [abs(a-b) for a, b in zip(observed, expected)]
            calibration = self.reference(gain, line)[0]['max_coefficient_error']
            limits = [max(8*e, 5e-7)*max(1, abs(v)) for e, v in zip(calibration, expected)]
            require(all(e < min(limit, 1/(8*self.zero)) for e, limit in zip(errors, limits)),
                    'direct normal-length validation failed: '+label)
            checks.append(dict(label=label, evidence=key, gain=gain, line=line, preparation_frames=leading,
                               expected=expected, max_error=max(errors)))
            return digest(output.tobytes())

        for gain in GAINS:
            for q in range(self.levels):
                for positive in ((True, False) if mode == 3 else (True,)):
                    add(values_for(q), gain, f'symbol:{gain}:{q}:{positive}', positive)
        rng = random.Random(0x484f4132+mode)
        mixtures = [[rng.randrange(self.levels) for _ in range(self.symbols)] for _ in range(8)]
        for i, values in enumerate(mixtures):
            signs = [bool(rng.randrange(2)) for _ in range(self.symbols)]
            for gain in GAINS:
                add(values, gain, f'mixed:{gain}:{i}', signs=signs)
        for gain in GAINS:
            for q in (0, self.zero-1, self.zero, self.levels-1):
                add(values_for(q), gain, f'padding:{gain}:{q}', padding=128, equal_to=f'symbol:{gain}:{q}:True')
        for i, values in enumerate(mixtures[:4]):
            for gain in GAINS:
                add(values, gain, f'line1:{gain}:{i}', line=1)
        if self.priors or mode == 2:
            for channel in range(self.n):
                values = [self.zero if mode == 2 else 0]*self.symbols
                values[channel] = self.positive_half
                add(values, 128, f'prior-group-basis:{channel}')
        if mode == 3:
            for seed in (self.negative_half, self.positive_half, self.levels-1):
                for gain in GAINS:
                    for i in range(2):
                        add(mixtures[i], gain, f'seed:{seed}:{gain}:{i}', seed=seed,
                              signs=[bool((j+i)%2) for j in range(self.symbols)])
                        add(mixtures[i], gain, f'history:{seed}:{gain}:{i}', seed=seed,
                              signs=[bool((j+i)%2) for j in range(self.symbols)],
                              history=(mixtures[i+2], [bool(j%3) for j in range(self.symbols)]))
            markers = [0]*self.symbols
            markers[groups[0][1]] = 17
            add(markers, 128, 'zero-sign:positive')
            signs = [True]*self.symbols
            signs[groups[0][0]] = False
            add(markers, 128, 'zero-sign:negative', signs=signs, equal_to='zero-sign:positive')
        capture_target = '_mode2' if mode == 2 else target
        with self.capture_batch(capture_target, 'validation', [(c['label'], c['payload']) for c in cases], True) as outputs:
            for case, (key, pcm) in zip(cases, outputs):
                result = check(case, key, pcm)
                hashes[case['label']] = result
                if case['equal_to'] is not None:
                    require(result == hashes[case['equal_to']], 'independent repeated PCM differs: '+case['label'])
                    if case['label'].startswith('padding:'):
                        checks[-1]['padding_bit_identical'] = True
        result = dict(status='passed', codebook_sha256=digest(canonical(book)), checks=checks,
                      layout_sha256=book['layout_sha256'], matrix_sha256=None, matrix_qualified=None,
                      old_dictionary_consulted=False)
        if mode == 2:
            result.update(joint_codebooks_sha256=[digest(canonical(b)) for b in books],
                          observation_scope='both mode-2 books jointly; every output coefficient checked')
        if self.priors:
            result.update(prior_sha256=self.store.config['prior_sha256'], group_reused=True)
        self.store.save_stage(target, 'validation', result)
        return result
