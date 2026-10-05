#!/usr/bin/env python3
"""Resumable HOA black-box batches (orders 1–3, q6–q9, modes 1 through 4).

Only public native replay is used. This command never changes production data.
Candidates and complete lossless evidence remain under the selected output.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from hoa_blackbox_lib.common import (
    ROOT, TARGETS, DEFAULT_LIMITS, BudgetStop, EvidenceError, ExperimentError, IdentityError,
    canonical, digest, install_discovery_guard, now, require, tool_fingerprint,
)
from hoa_blackbox_lib.store import Store, atomic_file, writer_lock


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('run', 'resume', 'status', 'import-evidence', 'compare'):
        s = sub.add_parser(command)
        s.add_argument('--out', type=Path, required=True)
        if command in ('run', 'resume', 'import-evidence'):
            s.add_argument('--binary', type=Path)
            s.add_argument('--max-evidence-mib', type=int)
            s.add_argument('--max-native-calls', type=int)
            s.add_argument('--min-free-mib', type=int)
            s.add_argument('--jobs', type=int, choices=range(1, 5),
                           help='concurrent native captures (default 1; resume keeps its saved value)')
        if command in ('run', 'import-evidence', 'compare'):
            s.add_argument('--targets', nargs='+', choices=TARGETS)
        if command in ('run', 'import-evidence'):
            s.add_argument('--order', type=int, choices=(1, 2, 3),
                           help='HOA order for a new batch (default 3)')
            s.add_argument('--quantization-bits', type=int, choices=(6, 7, 8, 9),
                           help='quantization width for a new batch (default 6)')
            s.add_argument('--prior-evidence', type=Path, nargs='+',
                           help='qualified six-bit geometry batches for this order; no codewords are exported')
        if command == 'import-evidence':
            s.add_argument('--evidence', type=Path, nargs='+', required=True)
        if command == 'resume':
            s.add_argument('--retry-failed', action='store_true')
    return p


def progress(event):
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


def limits(args):
    result = dict(DEFAULT_LIMITS)
    for arg, key, scale in (('max_evidence_mib', 'max_bytes', 1024**2),
                            ('max_native_calls', 'max_calls', 1), ('min_free_mib', 'min_free', 1024**2)):
        value = getattr(args, arg, None)
        if value is not None:
            require(value > 0, 'limits must be positive')
            require(arg != 'min_free_mib' or value >= 1024, 'at least 1024 MiB free space must be retained')
            result[key] = value * scale
    return result


def initialize(args):
    from hoa_blackbox_lib.native import NativeBackend
    require(args.binary is not None, '--binary is required to create a batch')
    precision = args.quantization_bits or 6
    order = args.order or 3
    backend = NativeBackend(args.binary, quantization_bits=precision, order=order)
    selected = set(args.targets or TARGETS)
    if selected & {'mode2:0', 'mode2:1'}:
        selected.update(('mode2:0', 'mode2:1'))
    targets = [t for t in TARGETS if t in selected]
    priors = None
    require(precision > 6 or args.prior_evidence is None, 'six-bit recovery cannot use geometry priors')
    if precision > 6 and (order == 3 or args.prior_evidence):
        command = [sys.executable, '-B', '-m', 'hoa_blackbox_lib.priors', '--order', str(order)]
        if args.prior_evidence:
            command += ['--evidence', *[str(path.resolve()) for path in args.prior_evidence]]
        process = subprocess.run(command,
                                 cwd=ROOT/'scripts', capture_output=True, text=True, timeout=30)
        require(process.returncode == 0, 'qualified prior export failed: '+process.stderr[-1600:], EvidenceError)
        priors = json.loads(process.stdout)
        require(priors['component_sha256'] == backend.identity['component_sha256']
                and priors['architecture'] == backend.identity['architecture']
                and priors.get('order', 3) == order, 'prior native component or order differs', IdentityError)
    if precision > 6:
        require(priors is not None or selected <= {'mode1'}, 'qualified six-bit geometry evidence is required')
        for target in selected - {'mode1'}:
            group = '3:0' if target == 'mode3' else target.removeprefix('mode')
            require((target[-1] in priors['matrices'] if target.startswith('mode4:') else group in priors['groups']),
                    'missing qualified geometry for '+target)
    config = dict(schema_version=1, created_utc=now(), targets=targets, binary=str(backend.binary),
                  native_jobs=args.jobs or 1,
                  quantization_bits=precision, order=order,
                  prior_sha256=digest(canonical(priors)) if priors else None,
                  native_identity=backend.identity, tool_fingerprint=tool_fingerprint(), limits=limits(args),
                  code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
    store = Store.create(args.out, config)
    sources = [ROOT / 'scripts/hoa_blackbox.py', ROOT / 'data/sq-codebooks.json']
    sources += sorted((ROOT / 'scripts/hoa_blackbox_lib').glob('*.py'))
    snapshot = {str(path.relative_to(ROOT)): store.blob(path.read_bytes()) for path in sources}
    store.set_meta('source_snapshot', snapshot)
    if priors is not None:
        store.save_stage('_shared', 'priors', priors)
    store.close()


def check_store(store):
    require(store.config['schema_version'] == 1, 'unsupported batch schema', EvidenceError)
    require(store.config['tool_fingerprint'] == tool_fingerprint(),
            'tool or analysis fingerprint changed; create a new batch and import raw evidence', IdentityError)


def update_limits(store, args):
    updates = {}
    for arg, key, scale in (('max_evidence_mib', 'max_bytes', 1024**2),
                            ('max_native_calls', 'max_calls', 1), ('min_free_mib', 'min_free', 1024**2)):
        value = getattr(args, arg, None)
        if value is not None:
            require(value > 0, 'limits must be positive')
            require(arg != 'min_free_mib' or value >= 1024, 'at least 1024 MiB free space must be retained')
            updates[key] = value * scale
    if updates:
        store.set_limits(**updates)
    if getattr(args, 'jobs', None) is not None:
        store.config['native_jobs'] = args.jobs
        store.set_meta('config', store.config)
        atomic_file(store.out / 'manifest.json', canonical(store.config))


def discover(args, targets):
    from hoa_blackbox_lib.engine import Engine
    from hoa_blackbox_lib.native import NativeBackend, Runner
    with writer_lock(args.out):
        store = Store(args.out)
        try:
            check_store(store)
            update_limits(store, args)
            backend = NativeBackend(args.binary or store.config['binary'], store.config.get('quantization_bits', 6), store.config.get('order', 3))
            require(backend.identity == store.config['native_identity'], 'batch native environment differs', IdentityError)
            store.recover()
            store.set_meta('batch_status', 'running')
            engine = Engine(store, Runner(store, backend, jobs=store.config.get('native_jobs', 1)), progress)
            engine.run(targets=targets, retry_failed=getattr(args, 'retry_failed', False))
            store.set_meta('batch_status', 'discovery_complete')
        except BudgetStop:
            store.set_meta('batch_status', 'budget_exhausted')
            raise
        except KeyboardInterrupt:
            store.set_meta('batch_status', 'interrupted')
            raise
        except Exception:
            store.set_meta('batch_status', 'stopped')
            raise
        finally:
            store.close()


def compare_subprocess(out, targets):
    if not targets:
        return
    command = [sys.executable, '-B', str(Path(__file__).resolve()), 'compare', '--out', str(out), '--targets', *targets]
    result = subprocess.run(command, capture_output=True, text=True)
    require(result.returncode in (0, 1), 'comparison process failed: ' + result.stderr[-1600:], EvidenceError)
    progress(dict(stage='compare', targets=targets, result=json.loads(result.stdout)))


def main():
    args = parser().parse_args()
    if args.command in ('run', 'resume', 'import-evidence'):
        install_discovery_guard()
    try:
        require(not (args.command == 'import-evidence' and args.out.exists() and args.prior_evidence),
                'cannot replace priors in an existing batch; create a new batch')
        if args.command == 'status':
            store = Store(args.out, readonly=True)
            try:
                print(json.dumps(store.summary(), ensure_ascii=False))
            finally:
                store.close()
            return 0
        if args.command == 'run':
            initialize(args)
        elif args.command == 'import-evidence' and not args.out.exists():
            initialize(args)
        if args.command == 'import-evidence':
            from hoa_blackbox_lib.native import NativeBackend, import_batch, import_legacy
            with writer_lock(args.out):
                store = Store(args.out)
                try:
                    check_store(store)
                    update_limits(store, args)
                    require(args.quantization_bits is None or args.quantization_bits == store.config.get('quantization_bits', 6),
                            'cannot change an existing batch quantization width', IdentityError)
                    require(args.order is None or args.order == store.config.get('order', 3),
                            'cannot change an existing batch HOA order', IdentityError)
                    backend = NativeBackend(args.binary or store.config['binary'], store.config.get('quantization_bits', 6), store.config.get('order', 3))
                    require(backend.identity == store.config['native_identity'], 'native identity differs', IdentityError)
                    counts = {}
                    for path in args.evidence:
                        require(path.resolve() != store.out, 'cannot import a batch into itself')
                        counts[str(path)] = (import_batch if (path / 'state.sqlite3').exists() else import_legacy)(store, path)
                    print(json.dumps(dict(imported=counts, summary=store.summary()), ensure_ascii=False))
                finally:
                    store.close()
            return 0
        if args.command == 'compare':
            from hoa_blackbox_lib.reference import compare
            with writer_lock(args.out):
                store = Store(args.out)
                try:
                    check_store(store)
                    targets = args.targets or store.config['targets']
                    require(set(targets) <= set(store.config['targets']), 'comparison target not in batch')
                    store.audit_evidence()
                    compare(store, targets)
                    summary = store.summary()
                    print(json.dumps(summary, ensure_ascii=False))
                    return 0 if all(j['status'] == 'passed' for j in summary['targets'] if j['target'] in targets) else 1
                finally:
                    store.close()
        store = Store(args.out, readonly=True)
        targets = store.config['targets']
        order = store.config.get('order', 3)
        store.close()
        regression = [t for t in targets if t == 'mode1' or order == 3 and t == 'mode4:0']
        remaining = [t for t in targets if t not in regression]
        if regression:
            discover(args, regression)
            compare_subprocess(args.out, regression)
            store = Store(args.out, readonly=True)
            passed = all(j['status'] == 'passed' for j in store.summary()['targets'] if j['target'] in regression)
            store.close()
            if not passed:
                with writer_lock(args.out):
                    store = Store(args.out)
                    store.set_meta('batch_status', 'regression_failed')
                    store.close()
                raise ExperimentError('known-target regression failed; new clusters were not started')
        if remaining:
            discover(args, remaining)
            compare_subprocess(args.out, remaining)
        with writer_lock(args.out):
            store = Store(args.out)
            try:
                summary = store.summary()
                complete = all(j['status'] == 'passed' for j in summary['targets'])
                store.set_meta('batch_status', 'complete' if complete else 'needs_review')
                summary = store.summary()
                atomic_file(store.out / 'summary.json', canonical(summary))
                print(json.dumps(summary, ensure_ascii=False))
                return 0 if complete else 1
            finally:
                store.close()
    except (ExperimentError, OSError, ValueError) as error:
        print(json.dumps(dict(status='budget_exhausted' if isinstance(error, BudgetStop) else 'stopped', error=str(error)), ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='interrupted')))
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
