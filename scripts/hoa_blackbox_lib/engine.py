"""Deterministic discovery stages. No recorded salient table is imported."""
import math
import random
import struct
import time
from contextlib import contextmanager

from .common import (GAINS, MAX_DEPTH, POLICY_VERSION, geometry,
                     ExperimentError, EvidenceError, IdentityError, BudgetStop,
                     canonical, digest, require, target_parts)
from .maths import dot, norm, vmul, subtract, inverse, direction_residual, infer_tree, matrix_entry, heldout_rms_error
from .wire import vector
from .direct import DirectRecovery


class Engine(DirectRecovery):
    def __init__(self, store, runner, progress=lambda event: None):
        self.store, self.runner = store, runner
        self.writer = runner.writer
        self.order = store.config.get('order', 3)
        g = geometry(self.order)
        self.n, self.symbols = g['channels'], g['symbols']
        self.precision = store.config.get('quantization_bits', 6)
        self.levels = 1 << self.precision
        self.zero = self.levels // 2
        self.negative_half, self.positive_half = self.zero//2, 3*self.zero//2
        self.repeat_eps = 1/(32*(self.levels-1))
        self.priors = None
        self.prior_matrices = {}
        if store.config.get('prior_sha256') is not None:
            self.priors = store.stage('_shared', 'priors')
            require(self.priors is not None and digest(canonical(self.priors)) == store.config.get('prior_sha256')
                    and self.priors['huffman_words_included'] is False
                    and self.priors.get('order', 3) == self.order, 'qualified priors are missing or changed', EvidenceError)
            require(self.priors['component_sha256'] == store.config['native_identity']['component_sha256']
                    and self.priors['architecture'] == store.config['native_identity']['architecture'],
                    'prior native component differs', IdentityError)
        elif self.precision > 6:
            require(set(store.config['targets']) <= {'mode1'}, 'qualified geometry priors are required', EvidenceError)
        self.progress = progress
        self.refs = {}
        self.last_progress = 0.

    def vector(self, q, row=None):
        return vector(q, row, self.zero, self.order)

    def prior_matrix(self, cluster):
        if cluster not in self.prior_matrices:
            source = self.priors['matrices'][str(cluster)]
            values = [struct.unpack('<f', struct.pack('<I', word))[0] for word in source['matrix_f32']]
            rows = [values[i:i+self.n] for i in range(0, self.n*self.n, self.n)]
            policy = {}
            inv, condition, residual = inverse(rows, order=self.order, diagnostics=policy)
            self.prior_matrices[cluster] = dict(inverse=inv, condition_inf=condition,
                                               inverse_residual_inf=residual, inversion_policy=policy)
        return self.prior_matrices[cluster]

    def reused_matrix(self, cluster):
        source = self.priors['matrices'][str(cluster)]
        return dict(reused=True, prior_sha256=self.store.config['prior_sha256'],
                    entries=[dict(row=i//self.n, column=i%self.n, float32_bits=word,
                                  empirical_half_width=source['empirical_half_width'])
                             for i, word in enumerate(source['matrix_f32'])])

    def capture(self, target, stage, label, payload, independent=False):
        replicate = f'{target}/{stage}/{label}' if independent else ''
        result = self.runner.probe(target, stage, label, payload, replicate)
        self.capture_progress(target, stage)
        return result

    def capture_progress(self, target, stage):
        if time.monotonic() - self.last_progress > 5:
            self.progress(dict(target=target, stage=stage, probes=self.store.summary()['native_calls']))
            self.last_progress = time.monotonic()

    @contextmanager
    def capture_batch(self, target, stage, cases, independent=False):
        requests = [(target, stage, label, payload, f'{target}/{stage}/{label}' if independent else '')
                    for label, payload in cases]
        with self.runner.batch(requests) as outputs:
            def results():
                for result in outputs:
                    self.capture_progress(target, stage)
                    yield result
            yield results()

    def calibrate(self):
        saved = self.store.stage('_shared', 'calibration')
        if saved is not None:
            return saved
        responses = {}
        for line in (0, 1):
            for gain in GAINS:
                evidence = {}
                symbols = range(self.levels) if line == 0 else (0, self.negative_half, self.zero, self.positive_half, self.levels-1)
                cases = [(f'{gain}:{line}:{q}', self.writer.fixed(self.vector(q), gain, line)) for q in symbols]
                with self.capture_batch('_shared', 'calibration', cases) as outputs:
                    for q, (key, pcm) in zip(symbols, outputs):
                        evidence[str(q)] = key
                        if q == self.zero:
                            require(not any(pcm), 'mode-0 zero is not zero')
                reference_pcm = self.runner.pcm(evidence[str(self.positive_half)])
                refs = [reference_pcm[k::self.n] for k in range(self.n)]
                del reference_pcm
                energy = [dot(v, v) for v in refs]
                require(all(v > 0 for v in energy), 'calibration carrier unobservable')
                errors = [0.] * self.n
                estimated = {}
                for q in symbols:
                    pcm = self.runner.pcm(evidence[str(q)])
                    values = [0.5 * dot(pcm[k::self.n], refs[k]) / energy[k] for k in range(self.n)]
                    estimated[str(q)] = values
                    errors = [max(e, abs(y - (q - self.zero) / self.zero)) for e, y in zip(errors, values)]
                require(max(errors) < 1e-6, 'calibration inaccurate')
                if line == 0:
                    require(all(estimated[str(q+1)][k] > estimated[str(q)][k] for q in range(self.levels-1) for k in range(self.n)),
                            'calibration symbols not distinct and ordered')
                responses[f'{gain}:{line}'] = dict(reference=evidence[str(self.positive_half)], evidence=evidence,
                    max_coefficient_error=errors, estimated_coefficients=estimated)
        columns = []
        for k in range(self.n):
            a, b = (responses[f'{g}:0']['max_coefficient_error'][k] for g in GAINS)
            wa = b*b/(a*a+b*b) if a*a+b*b else 0.5
            columns.append(dict(weights=[wa, 1-wa], calibration_bounds=[a, b],
                                propagated_bound=wa*a+(1-wa)*b))
        policy = dict(profile=POLICY_VERSION, responses=responses, weight_columns=columns,
                      quantization_bits=self.precision, order=self.order,
                      native_identity=self.store.config['native_identity'], tool_fingerprint=self.store.config['tool_fingerprint'],
                      safety_factor=8, min_half_width=5e-8, precision_limit=2.5e-7,
                      matrix_training='minus-one unit basis only; half-amplitude probes held out',
                      old_dictionary_consulted=False)
        self.store.save_stage('_shared', 'calibration', policy)
        return policy

    def reference(self, gain, line):
        key = f'{gain}:{line}'
        if key not in self.refs:
            response = self.calibration['responses'][key]
            pcm = self.runner.pcm(response['reference'])
            refs = [pcm[k::self.n] for k in range(self.n)]
            self.refs[key] = (response, refs, [dot(v, v) for v in refs])
        return self.refs[key]

    def preflight(self):
        """Verify every output channel independently, using only mode 0."""
        previous = self.store.stage('_shared', 'preflight')
        if previous is not None:
            return previous
        self.calibration = self.calibrate()
        cases = [(f'{gain}:{row}', self.writer.fixed(self.vector(self.positive_half, row), gain))
                 for gain in GAINS for row in range(self.n)]
        checks = []
        with self.capture_batch('_shared', 'preflight', cases, independent=True) as outputs:
            for (label, _), (key, pcm) in zip(cases, outputs):
                gain, row = map(int, label.split(':'))
                values = self.estimate(pcm, gain)
                error = max(abs(v-(0.5 if k == row else 0.)) for k,v in enumerate(values))
                require(error < 1e-6, 'mode-0 basis channel is not observable')
                require(any(pcm[row:1024*self.n:self.n]) and any(pcm[1024*self.n+row::self.n]),
                        'basis onset or overlap is not observable')
                checks.append(dict(evidence=key, gain=gain, channel=row, max_error=error))
        result = dict(status='passed', order=self.order, channels=self.n, checks=checks,
                      calibration_sha256=digest(canonical(self.calibration)))
        self.store.save_stage('_shared', 'preflight', result)
        return result

    def estimate(self, pcm, gain, line=0):
        response, refs, energy = self.reference(gain, line)
        values = []
        for k in range(self.n):
            v = pcm[k::self.n]
            y = 0.5 * dot(v, refs[k]) / energy[k]
            residual = norm([a - 2*y*b for a, b in zip(v, refs[k])]) / math.sqrt(energy[k]) / 2
            require(residual <= max(5e-7, 16*response['max_coefficient_error'][k]) * max(1, abs(y)),
                    'PCM is not a scaled calibration response')
            values.append(y)
        return values

    def observe(self, target, stage, label, pattern, gain=128, extra=0, independent=False):
        mode, cluster = target_parts(target)
        payload = self.writer.padded(mode, cluster, pattern, gain, extra)
        if mode == 3:
            payload = self.mode3_program(payload)
        key, pcm = self.capture(target, stage, label, payload, independent)
        if mode == 3:
            require(not any(pcm[:1024*self.n]), 'mode-3 preparation frame is not silent')
            pcm = pcm[1024*self.n:]
        return dict(evidence=key, coefficients=self.estimate(pcm, gain))

    def bootstrap(self, target):
        saved = self.store.stage(target, 'bootstrap')
        if saved is not None:
            return saved
        base = self.observe(target, 'bootstrap', 'zero', '')
        first = self.observe(target, 'bootstrap', 'flip:0', '1')
        d0 = subtract(first['coefficients'], base['coefficients'])
        scans = []
        length = None
        for t in range(1, MAX_DEPTH+1):
            found = self.observe(target, 'bootstrap', f'flip:{t}', '0'*t+'1')
            residual = direction_residual(subtract(found['coefficients'], base['coefficients']), d0)
            scans.append(dict(bit_offset=t, direction_residual=residual, **found))
            if residual > 1e-4:
                length, second = t, found
                break
        require(length is not None, 'zero code length not identified')
        observations = [first, second]
        for j in range(2, self.n):
            observations.append(self.observe(target, 'bootstrap', f'row:{j}', '0'*(j*length)+'1'))
        rows = [subtract(o['coefficients'], base['coefficients']) for o in observations]
        policy = {}
        inv, condition, residual = inverse(rows, order=self.order, diagnostics=policy)
        coordinates = vmul(base['coefficients'], inv)
        require(max(abs(v-coordinates[0]) for v in coordinates) <= self.repeat_eps, 'zero stream coordinates not uniform')
        repeats = []
        for i, pattern in enumerate(('', '1', '0'*length+'1', '01', '10', '0'*((self.n-1)*length)+'1')):
            trials = []
            for gain, extra in ((128, 0), (129, 0), (128, 256)):
                obs = self.observe(target, 'bootstrap', f'repeat:{i}:{gain}:{extra}', pattern, gain, extra, True)
                obs['coordinates'] = vmul(obs['coefficients'], inv)
                trials.append(obs)
            deviation = max(abs(t['coordinates'][k]-trials[0]['coordinates'][k]) for t in trials[1:] for k in range(self.n))
            require(deviation <= self.repeat_eps, 'coordinates changed with amplitude or padding')
            repeats.append(dict(deviation=deviation, trials=trials))
        result = dict(zero_code_length=length, baseline=base, scan=scans, row_observations=observations,
                      scaled_matrix=rows, inverse=inv, condition_inf=condition, inverse_residual_inf=residual,
                      inversion_policy=policy, repeats=repeats)
        self.store.save_stage(target, 'bootstrap', result)
        return result

    def codebook(self, target):
        saved = self.store.stage(target, 'codebook')
        if saved is not None:
            return saved
        mode, cluster = target_parts(target)
        if mode == 2:
            return self.mode2_books()[cluster]
        if mode == 3:
            return self.mode3_book()
        reused = mode == 4 and self.priors is not None
        boot = (self.prior_matrix(cluster) if reused else self.bootstrap(target)) if mode == 4 else None
        def query(pattern):
            obs = self.observe(target, 'codebook', pattern, pattern)
            if boot and not reused:
                result = vmul(obs['coefficients'], boot['inverse'])[0]
            else:
                value = vmul(obs['coefficients'], boot['inverse'])[0] if reused else obs['coefficients'][0]
                result = self.quantize(value)
            return result, obs['evidence']
        entries, decisions, scale = infer_tree(query, coordinate=mode == 4 and not reused, symbols=self.levels)
        if boot and not reused:
            def first(raw): return next(e['symbol'] for e in entries if raw.startswith(e['codeword']))
            require(first('1'+'0'*32)-first('0'*32) == scale, 'coordinate scale and symbol labels disagree')
            require(next(e['bit_length'] for e in entries if e['symbol'] == first('0'*32)) == boot['zero_code_length'],
                    'reconstructed zero code length disagrees')
        result = dict(schema_version=1, profile='hoa-blackbox-codebook-v1', order=self.order, quantization_bits=self.precision,
                      mode=mode, book=cluster if cluster is not None else 0, entries=entries, decisions=decisions,
                      signed_coordinate_scale=scale, policy_sha256=digest(canonical(self.calibration)),
                      old_dictionary_consulted=False)
        if reused:
            result.update(prior_sha256=self.store.config['prior_sha256'], matrix_reused=True,
                          matrix_condition_inf=boot['condition_inf'], matrix_inverse_residual_inf=boot['inverse_residual_inf'],
                          matrix_inversion_policy=boot['inversion_policy'])
        self.store.save_stage(target, 'codebook', result)
        return result

    def matrix(self, target, book):
        saved = self.store.stage(target, 'matrix')
        if saved is not None:
            return saved
        _, cluster = target_parts(target)
        words = {e['symbol']: e['codeword'] for e in book['entries']}
        observations = {}
        indices = [(gain, row) for gain in GAINS for row in range(self.n)]
        cases = [(f'{gain}:{row}', self.writer.coded(4, cluster, self.vector(0, row), words, gain)) for gain, row in indices]
        with self.capture_batch(target, 'matrix', cases) as outputs:
            for (gain, row), (key, pcm) in zip(indices, outputs):
                observations[f'{gain}:{row}'] = dict(evidence=key, values=[-v for v in self.estimate(pcm, gain)])
        entries = []
        for row in range(self.n):
            for column in range(self.n):
                p = self.calibration['weight_columns'][column]
                estimates = [observations[f'{g}:{row}']['values'][column] for g in GAINS]
                entries.append(dict(row=row, column=column,
                    **matrix_entry(estimates, p['weights'], p['calibration_bounds'])))
        result = dict(schema_version=1, profile='hoa-blackbox-matrix-v1', order=self.order, mode=4, cluster=cluster,
                      rows=self.n, columns=self.n, entries=entries, observations=observations,
                      codebook_sha256=digest(canonical(book)), policy_sha256=digest(canonical(self.calibration)),
                      old_matrix_consulted=False)
        self.store.save_stage(target, 'matrix', result)
        return result

    def validate(self, target, book, matrix=None):
        saved = self.store.stage(target, 'validation')
        if saved is not None:
            return saved
        mode, cluster = target_parts(target)
        if mode in (2, 3):
            return self.validate_direct(target, book)
        words = {e['symbol']: e['codeword'] for e in book['entries']}
        reused = bool(matrix and matrix.get('reused'))
        boot = (self.prior_matrix(cluster) if reused else self.store.stage(target, 'bootstrap')) if mode == 4 else None
        checks, hashes, cases = [], {}, []
        if matrix:
            entries = matrix['entries']
            m = [[struct.unpack('<f', struct.pack('<I', e['float32_bits']))[0]
                  if e['float32_bits'] is not None else e['estimate'] for e in entries[j*self.n:(j+1)*self.n]] for j in range(self.n)]
            u = [[e['empirical_half_width'] for e in entries[j*self.n:(j+1)*self.n]] for j in range(self.n)]
        def add(values, gain, label, line=0, padding=0, equal_to=None):
            cases.append(dict(values=values, gain=gain, label=label, line=line, equal_to=equal_to,
                              payload=self.writer.coded(mode, cluster, values, words, gain, line, padding)))

        def check(case, key, pcm):
            values, gain, label, line = (case[k] for k in ('values', 'gain', 'label', 'line'))
            obs = self.estimate(pcm, gain, line)
            item = dict(label=label, evidence=key, gain=gain, line=line)
            if matrix:
                x = [(q-self.zero)/self.zero for q in values[:self.n]]
                expected = vmul(x, m)
                errors = [abs(a-b) for a, b in zip(obs, expected)]
                cal = self.reference(gain, line)[0]['max_coefficient_error']
                limits = [sum(abs(x[j])*u[j][k] for j in range(self.n)) + max(8*cal[k], 5e-8)*max(1, sum(abs(v) for v in x)) for k in range(self.n)]
                require(all(e <= limit for e, limit in zip(errors, limits)), 'matrix forward validation failed: '+label)
                z = vmul(obs, boot['inverse'])
                scale = self.zero if reused else book['signed_coordinate_scale']
                coordinate_error = max(abs(a-(q-self.zero)/scale) for a, q in zip(z, values[:self.n]))
                require(coordinate_error <= self.repeat_eps, 'normal-length codebook validation failed: '+label)
                item.update(max_error=max(errors), max_error_to_bound_ratio=max(e/l for e, l in zip(errors, limits)),
                            coordinate_error=coordinate_error)
            else:
                bit_identical=True;heldout=[]
                columns_by_symbol = {}
                for column, q in enumerate(values[:self.n]):
                    columns_by_symbol.setdefault(q, []).append(column)
                for q, columns in columns_by_symbol.items():
                    ref = self.runner.pcm(self.calibration['responses'][f'{gain}:{line}']['evidence'][str(q)])
                    for column in columns:
                        actual,control=pcm[column::self.n],ref[column::self.n]
                        equal=actual.tobytes()==control.tobytes();bit_identical &= equal
                        if line==0:
                            require(equal, 'mode-1 PCM differs from calibration')
                        else:
                            response,half,_=self.reference(gain,line)
                            heldout.append(heldout_rms_error(actual,control,half[column],self.zero,response['max_coefficient_error'][column]))
                item['pcm_bit_identical_to_mode0'] = bit_identical
                if heldout:
                    item.update(max_heldout_rms_to_limit_ratio=max(error/limit for error,limit in heldout),
                                max_heldout_rms_error=max(error for error,_ in heldout),
                                min_heldout_rms_limit=min(limit for _,limit in heldout))
            checks.append(item)
            return digest(pcm.tobytes())
        if matrix:
            for gain in GAINS:
                for row in range(self.n):
                    for q in (self.negative_half, self.positive_half):
                        add(self.vector(q, row), gain, f'half:{gain}:{row}:{q}')
        for gain in GAINS:
            for q in range(self.levels):
                add(self.vector(q, 0 if matrix else None), gain, f'symbol:{gain}:{q}')
        rng = random.Random(0x484f4134)
        mixtures = [[rng.randrange(self.levels) for _ in range(self.symbols)] for _ in range(8)]
        for i, values in enumerate(mixtures):
            for gain in GAINS:
                add(values, gain, f'mixed:{gain}:{i}')
        for gain in GAINS:
            for q in (0, self.zero-1, self.zero, self.levels-1):
                add(self.vector(q, 0 if matrix else None), gain, f'padding:{gain}:{q}', padding=128,
                    equal_to=f'symbol:{gain}:{q}')
        if matrix:
            for row in range(self.n):
                for q in (0, self.positive_half):
                    add(self.vector(q, row), 128, f'line1:{row}:{q}', line=1)
            for i, values in enumerate(mixtures[:4]):
                add(values, 129, f'line1-mixed:{i}', line=1)
            for gain in GAINS:
                add([self.zero]*self.symbols, gain, f'zero:{gain}')
        else:
            # The held-out carrier has five mode-0 calibration controls. Use
            # those values for both uniform and independently mixed inputs.
            heldout_symbols = (0, self.negative_half, self.zero, self.positive_half, self.levels-1)
            for gain in GAINS:
                for q in heldout_symbols:
                    add(self.vector(q), gain, f'line1-symbol:{gain}:{q}', line=1)
            heldout_rng = random.Random(0x484f4131)
            for i in range(4):
                values = [heldout_rng.choice(heldout_symbols) for _ in range(self.symbols)]
                add(values, 129, f'line1-mixed:{i}', line=1)
        with self.capture_batch(target, 'validation', [(c['label'], c['payload']) for c in cases], True) as outputs:
            for case, (key, pcm) in zip(cases, outputs):
                value = check(case, key, pcm)
                hashes[case['label']] = value
                if case['equal_to'] is not None:
                    require(value == hashes[case['equal_to']], 'padding or fresh converter changed PCM')
                    checks[-1]['padding_bit_identical'] = True
        qualified = sum(e['float32_bits'] is not None for e in matrix['entries']) if matrix and not reused else None
        result = dict(status='passed', codebook_sha256=digest(canonical(book)), checks=checks,
                      matrix_sha256=digest(canonical(matrix)) if matrix and not reused else None,
                      matrix_qualified=qualified, old_dictionary_consulted=False)
        if reused:
            result.update(prior_sha256=self.store.config['prior_sha256'], matrix_reused=True)
        self.store.save_stage(target, 'validation', result)
        return result

    def run(self, targets=None, retry_failed=False):
        self.runner.backend.check(force=True)
        self.store.audit_evidence()
        self.calibration = self.calibrate()
        for target in targets or self.store.config['targets']:
            state = self.store.db.execute('SELECT status FROM jobs WHERE target=?', (target,)).fetchone()[0]
            if state == 'failed' and not retry_failed:
                continue
            self.store.job(target, 'running')
            try:
                book = self.codebook(target)
                matrix = (self.reused_matrix(target_parts(target)[1]) if self.priors else self.matrix(target, book)) if target.startswith('mode4:') else None
                validation = self.validate(target, book, matrix)
                status = 'validated' if matrix is None or matrix.get('reused') or validation['matrix_qualified'] == self.n*self.n else 'partial'
                self.store.job(target, status)
                self.runner.backend.check(force=True)
                self.progress(dict(target=target, status=status, matrix_qualified=validation['matrix_qualified']))
            except (BudgetStop, EvidenceError, IdentityError):
                raise
            except ExperimentError as error:
                self.store.job(target, 'failed', str(error))
                self.progress(dict(target=target, status='failed', error=str(error)))
