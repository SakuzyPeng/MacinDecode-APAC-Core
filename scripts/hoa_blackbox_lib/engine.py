"""Deterministic discovery stages. No recorded salient table is imported."""
import math
import random
import struct
import time

from .common import (GAINS, SYMBOLS, MAX_DEPTH, REPEAT_EPS, POLICY_VERSION,
                     ExperimentError, EvidenceError, IdentityError, BudgetStop,
                     canonical, digest, require, target_parts)
from .maths import dot, norm, vmul, subtract, inverse, direction_residual, infer_tree, matrix_entry
from .wire import vector
from .direct import DirectRecovery


class Engine(DirectRecovery):
    def __init__(self, store, runner, progress=lambda event: None):
        self.store, self.runner = store, runner
        self.writer = runner.writer
        self.progress = progress
        self.refs = {}
        self.last_progress = 0.

    def capture(self, target, stage, label, payload, independent=False):
        replicate = f'{target}/{stage}/{label}' if independent else ''
        result = self.runner.probe(target, stage, label, payload, replicate)
        if time.monotonic() - self.last_progress > 5:
            self.progress(dict(target=target, stage=stage, probes=self.store.summary()['native_calls']))
            self.last_progress = time.monotonic()
        return result

    def calibrate(self):
        saved = self.store.stage('_shared', 'calibration')
        if saved is not None:
            return saved
        responses = {}
        for line in (0, 1):
            for gain in GAINS:
                evidence, waves = {}, {}
                symbols = range(64) if line == 0 else (0, 16, 32, 48, 63)
                for q in symbols:
                    key, pcm = self.capture('_shared', 'calibration', f'{gain}:{line}:{q}', self.writer.fixed(vector(q), gain, line))
                    evidence[str(q)], waves[q] = key, pcm
                require(not any(waves[32]), 'mode-0 zero is not zero')
                refs = [waves[48][k::16] for k in range(16)]
                energy = [dot(v, v) for v in refs]
                require(all(v > 0 for v in energy), 'calibration carrier unobservable')
                errors = [0.] * 16
                estimated = {}
                for q, pcm in waves.items():
                    values = [0.5 * dot(pcm[k::16], refs[k]) / energy[k] for k in range(16)]
                    estimated[str(q)] = values
                    errors = [max(e, abs(y - (q - 32) / 32)) for e, y in zip(errors, values)]
                require(max(errors) < 1e-6, 'calibration inaccurate')
                if line == 0:
                    require(all(estimated[str(q+1)][k] > estimated[str(q)][k] for q in range(63) for k in range(16)),
                            'calibration symbols not distinct and ordered')
                responses[f'{gain}:{line}'] = dict(reference=evidence['48'], evidence=evidence,
                    max_coefficient_error=errors, estimated_coefficients=estimated)
        columns = []
        for k in range(16):
            a, b = (responses[f'{g}:0']['max_coefficient_error'][k] for g in GAINS)
            wa = b*b/(a*a+b*b) if a*a+b*b else 0.5
            columns.append(dict(weights=[wa, 1-wa], calibration_bounds=[a, b],
                                propagated_bound=wa*a+(1-wa)*b))
        policy = dict(profile=POLICY_VERSION, responses=responses, weight_columns=columns,
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
            refs = [pcm[k::16] for k in range(16)]
            self.refs[key] = (response, refs, [dot(v, v) for v in refs])
        return self.refs[key]

    def estimate(self, pcm, gain, line=0):
        response, refs, energy = self.reference(gain, line)
        values = []
        for k in range(16):
            v = pcm[k::16]
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
            require(not any(pcm[:1024*16]), 'mode-3 preparation frame is not silent')
            pcm = pcm[1024*16:]
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
        for j in range(2, 16):
            observations.append(self.observe(target, 'bootstrap', f'row:{j}', '0'*(j*length)+'1'))
        rows = [subtract(o['coefficients'], base['coefficients']) for o in observations]
        inv, condition, residual = inverse(rows)
        coordinates = vmul(base['coefficients'], inv)
        require(max(abs(v-coordinates[0]) for v in coordinates) <= REPEAT_EPS, 'zero stream coordinates not uniform')
        repeats = []
        for i, pattern in enumerate(('', '1', '0'*length+'1', '01', '10', '0'*(15*length)+'1')):
            trials = []
            for gain, extra in ((128, 0), (129, 0), (128, 256)):
                obs = self.observe(target, 'bootstrap', f'repeat:{i}:{gain}:{extra}', pattern, gain, extra, True)
                obs['coordinates'] = vmul(obs['coefficients'], inv)
                trials.append(obs)
            deviation = max(abs(t['coordinates'][k]-trials[0]['coordinates'][k]) for t in trials[1:] for k in range(16))
            require(deviation <= REPEAT_EPS, 'coordinates changed with amplitude or padding')
            repeats.append(dict(deviation=deviation, trials=trials))
        result = dict(zero_code_length=length, baseline=base, scan=scans, row_observations=observations,
                      scaled_matrix=rows, inverse=inv, condition_inf=condition, inverse_residual_inf=residual, repeats=repeats)
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
        boot = self.bootstrap(target) if mode == 4 else None
        def query(pattern):
            obs = self.observe(target, 'codebook', pattern, pattern)
            if boot:
                result = vmul(obs['coefficients'], boot['inverse'])[0]
            else:
                result = round((obs['coefficients'][0]+1)*32)
                require(0 <= result < 64 and abs(obs['coefficients'][0]-(result-32)/32) < 1/(8*32),
                        'mode-1 symbol is ambiguous')
            return result, obs['evidence']
        entries, decisions, scale = infer_tree(query, coordinate=mode == 4)
        if boot:
            def first(raw): return next(e['symbol'] for e in entries if raw.startswith(e['codeword']))
            require(first('1'+'0'*32)-first('0'*32) == scale, 'coordinate scale and symbol labels disagree')
            require(next(e['bit_length'] for e in entries if e['symbol'] == first('0'*32)) == boot['zero_code_length'],
                    'reconstructed zero code length disagrees')
        result = dict(schema_version=1, profile='hoa-blackbox-codebook-v1', order=3, quantization_bits=6,
                      mode=mode, book=cluster if cluster is not None else 0, entries=entries, decisions=decisions,
                      signed_coordinate_scale=scale, policy_sha256=digest(canonical(self.calibration)),
                      old_dictionary_consulted=False)
        self.store.save_stage(target, 'codebook', result)
        return result

    def matrix(self, target, book):
        saved = self.store.stage(target, 'matrix')
        if saved is not None:
            return saved
        _, cluster = target_parts(target)
        words = {e['symbol']: e['codeword'] for e in book['entries']}
        observations = {}
        for gain in GAINS:
            for row in range(16):
                key, pcm = self.capture(target, 'matrix', f'{gain}:{row}',
                    self.writer.coded(4, cluster, vector(0, row), words, gain))
                observations[f'{gain}:{row}'] = dict(evidence=key, values=[-v for v in self.estimate(pcm, gain)])
        entries = []
        for row in range(16):
            for column in range(16):
                p = self.calibration['weight_columns'][column]
                estimates = [observations[f'{g}:{row}']['values'][column] for g in GAINS]
                entries.append(dict(row=row, column=column,
                    **matrix_entry(estimates, p['weights'], p['calibration_bounds'])))
        result = dict(schema_version=1, profile='hoa-blackbox-matrix-v1', order=3, mode=4, cluster=cluster,
                      rows=16, columns=16, entries=entries, observations=observations,
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
        boot = self.store.stage(target, 'bootstrap') if mode == 4 else None
        checks, normal = [], {}
        if matrix:
            entries = matrix['entries']
            m = [[struct.unpack('<f', struct.pack('<I', e['float32_bits']))[0]
                  if e['float32_bits'] is not None else e['estimate'] for e in entries[j*16:(j+1)*16]] for j in range(16)]
            u = [[e['empirical_half_width'] for e in entries[j*16:(j+1)*16]] for j in range(16)]
        def check(values, gain, label, line=0, padding=0):
            key, pcm = self.capture(target, 'validation', label,
                self.writer.coded(mode, cluster, values, words, gain, line, padding), True)
            obs = self.estimate(pcm, gain, line)
            item = dict(label=label, evidence=key, gain=gain, line=line)
            if matrix:
                x = [(q-32)/32 for q in values[:16]]
                expected = vmul(x, m)
                errors = [abs(a-b) for a, b in zip(obs, expected)]
                cal = self.reference(gain, line)[0]['max_coefficient_error']
                limits = [sum(abs(x[j])*u[j][k] for j in range(16)) + max(8*cal[k], 5e-8)*max(1, sum(abs(v) for v in x)) for k in range(16)]
                require(all(e <= limit for e, limit in zip(errors, limits)), 'matrix forward validation failed: '+label)
                z = vmul(obs, boot['inverse'])
                coordinate_error = max(abs(a-(q-32)/book['signed_coordinate_scale']) for a, q in zip(z, values[:16]))
                require(coordinate_error <= REPEAT_EPS, 'normal-length codebook validation failed: '+label)
                item.update(max_error=max(errors), max_error_to_bound_ratio=max(e/l for e, l in zip(errors, limits)),
                            coordinate_error=coordinate_error)
            else:
                for column, q in enumerate(values[:16]):
                    ref = self.runner.pcm(self.calibration['responses'][f'{gain}:{line}']['evidence'][str(q)])
                    require(pcm[column::16].tobytes() == ref[column::16].tobytes(), 'mode-1 PCM differs from calibration')
                item['pcm_bit_identical_to_mode0'] = True
            checks.append(item)
            return digest(pcm.tobytes())
        if matrix:
            for gain in GAINS:
                for row in range(16):
                    for q in (16, 48):
                        check(vector(q, row), gain, f'half:{gain}:{row}:{q}')
        for gain in GAINS:
            for q in range(64):
                normal[gain, q] = check(vector(q, 0 if matrix else None), gain, f'symbol:{gain}:{q}')
        rng = random.Random(0x484f4134)
        mixtures = [[rng.randrange(64) for _ in range(SYMBOLS)] for _ in range(8)]
        for i, values in enumerate(mixtures):
            for gain in GAINS:
                check(values, gain, f'mixed:{gain}:{i}')
        for gain in GAINS:
            for q in (0, 31, 32, 63):
                value = check(vector(q, 0 if matrix else None), gain, f'padding:{gain}:{q}', padding=128)
                require(value == normal[gain, q], 'padding or fresh converter changed PCM')
                checks[-1]['padding_bit_identical'] = True
        if matrix:
            for row in range(16):
                for q in (0, 48):
                    check(vector(q, row), 128, f'line1:{row}:{q}', line=1)
            for i, values in enumerate(mixtures[:4]):
                check(values, 129, f'line1-mixed:{i}', line=1)
            for gain in GAINS:
                check([32]*SYMBOLS, gain, f'zero:{gain}')
        qualified = sum(e['float32_bits'] is not None for e in matrix['entries']) if matrix else None
        result = dict(status='passed', codebook_sha256=digest(canonical(book)), checks=checks,
                      matrix_sha256=digest(canonical(matrix)) if matrix else None,
                      matrix_qualified=qualified, old_dictionary_consulted=False)
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
                matrix = self.matrix(target, book) if target.startswith('mode4:') else None
                validation = self.validate(target, book, matrix)
                status = 'validated' if matrix is None or validation['matrix_qualified'] == 256 else 'partial'
                self.store.job(target, status)
                self.runner.backend.check(force=True)
                self.progress(dict(target=target, status=status, matrix_qualified=validation['matrix_qualified']))
            except (BudgetStop, EvidenceError, IdentityError):
                raise
            except ExperimentError as error:
                self.store.job(target, 'failed', str(error))
                self.progress(dict(target=target, status='failed', error=str(error)))
