#!/usr/bin/env python3
"""Run every portable CLI validator against one binary and record outcomes.

Each validator writes its own report under OUT; a summary.json records the
command, exit code, elapsed seconds and stderr tail. Native, media and
baseline-dependent validators (and validate_synthesis, which needs
Apple reference PCM) are excluded because they need macOS or
explicit local inputs. Compare two summaries/report trees with
compare_reports.py.
"""
import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name -> (output flag, extra arguments)
SUITE = {
    'validate_portable': ('--output', []),
    'validate_tns': ('--output', []),
    'validate_cac': ('--output', []),
    'validate_bwe2': ('--output', []),
    'validate_packets': ('--output', []),
    'validate_spectra': ('--output', []),
    'validate_access': ('--report', []),
    'validate_caf': ('--report', []),
    'validate_mp4': ('--report', []),
    'validate_channels': ('--report', []),
    'validate_layouts': ('--report', ['presence']),
    'validate_surround916': ('--report', ['presence']),
    'validate_drc': ('--report', []),
    'validate_drc_pcm': ('--report', []),
    'validate_hoa': ('--report', []),
    'validate_hoa_access': ('--report', []),
    'validate_hoa_additive': ('--report', []),
    'validate_hoa_ambient_counts': ('--report', []),
    'validate_hoa_asp': ('--report', []),
    'validate_hoa_component_orders': ('--report', []),
    'validate_hoa_controls': ('--report', []),
    'validate_hoa_dynamic': ('--report', []),
    'validate_hoa_dynamic_domains': ('--report', []),
    'validate_hoa_dynamic_subbands': ('--report', []),
    'validate_hoa_expanded_orders': ('--report', []),
    'validate_hoa_mixed': ('--report', []),
    'validate_hoa_order1': ('--report', []),
    'validate_hoa_orders': ('--report', []),
    'validate_hoa_partial': ('--report', []),
    'validate_hoa_quantization': ('--report', []),
    'validate_hoa_remapping': ('--report', []),
    'validate_hoa_salient': ('--report', []),
    'validate_hoa_salient_counts': ('--report', []),
    'validate_hoa_salient_partition': ('--report', []),
    'validate_hoa_salient_subbands': ('--report', []),
    'validate_hoa_shared': ('--report', []),
    'validate_hoa_source_layouts': ('--report', []),
    'validate_hoa_static_ambient': ('--report', []),
    'validate_hoa_transports': ('--report', []),
}

# Validators that finished within about two minutes on a 4-core Linux container;
# the others (portable, tns, cac, bwe2, layouts, access, mp4, packets, channels)
# take 4 to 25 minutes each and run at phase boundaries.
FAST = [name for name in SUITE if name.startswith('validate_hoa') or name in (
    'validate_drc', 'validate_drc_pcm', 'validate_caf', 'validate_spectra')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--presence-binary', type=Path,
                        help='layout_presence example binary (required for fixed-layout validators)')
    parser.add_argument('--out', type=Path, required=True, help='new report directory')
    parser.add_argument('--only', nargs='*', help='validator names to run (default: all)')
    parser.add_argument('--fast', action='store_true', help='run only the quick subset')
    parser.add_argument('--skip', nargs='*', default=[], help='validator names to leave out')
    parser.add_argument('--jobs', type=int, default=1, help='validators run concurrently')
    parser.add_argument('--profile', choices=('ci-fast', 'ci-full'), help='fixed CI coverage; incompatible with --only/--fast/--skip')
    parser.add_argument('--collect-only', action='store_true', help='collect candidate fingerprints without qualifying independent mathematics')
    parser.add_argument('--target', help='native Rust target for CI build identity')
    parser.add_argument('--build-profile', choices=('debug', 'release'))
    parser.add_argument('--shard', type=int, choices=range(9), help='one complete-math shard (0..8)')
    args = parser.parse_args()
    if args.profile:
        if args.only is not None or args.fast or args.skip:
            parser.error('CI profiles cannot be combined with --only/--fast/--skip')
        if not args.target or not args.build_profile:
            parser.error('CI profiles require --target and --build-profile')
        if not 1 <= args.jobs <= 2:
            parser.error('CI --jobs must be 1 or 2')
        if args.shard is not None and (args.profile != 'ci-full' or args.collect_only):
            parser.error('--shard requires ci-full mathematical validation')
        from ci_catalog import load
        catalog = load()
        names = catalog['shards'][args.shard] if args.shard is not None else list(catalog['profiles'][args.profile])
    else:
        if args.collect_only or args.shard is not None or args.target or args.build_profile:
            parser.error('CI options require --profile')
        names = [n for n in (args.only or (FAST if args.fast else list(SUITE))) if n not in args.skip]
    if not names or any(n not in SUITE for n in names):
        parser.error('empty suite or unknown validator')
    presence_validators = [name for name in names if 'presence' in SUITE[name][1]]
    if presence_validators:
        if not args.presence_binary:
            parser.error('--presence-binary is required for ' + ', '.join(presence_validators)
                         + '; omit those validators explicitly with --skip')
        if not args.presence_binary.is_file():
            parser.error('--presence-binary is not a file: ' + str(args.presence_binary))
    if args.out.exists():
        raise SystemExit('output directory exists: ' + str(args.out))
    args.out.mkdir(parents=True)
    binary = args.binary.resolve()
    started = time.monotonic()
    if args.profile:
        from ci_evidence import probe
        build = probe(binary, args.target, args.build_profile)
        build_info = (args.out / 'build.json').resolve()
        build_info.write_text(json.dumps(build, indent=2) + '\n', encoding='utf-8')

    def run(name):
        flag, extra = SUITE[name]
        report = (args.out / (name + '.json')).resolve()
        command = [sys.executable, '-B', str(ROOT / 'scripts' / (name + '.py')),
                   '--binary', str(binary), flag, str(report)]
        if 'presence' in extra:
            command += ['--presence-binary', str(args.presence_binary.resolve())]
        if args.profile:
            command = [sys.executable, '-B', str(ROOT / 'scripts/ci_validate.py'),
                       '--validator', name, '--profile', args.profile,
                       '--mode', 'collect' if args.collect_only else 'math',
                       '--build-info', str(build_info), '--binary', str(binary),
                       '--out', str((args.out / name).resolve())]
            if 'presence' in extra:
                command += ['--presence-binary', str(args.presence_binary.resolve())]
        start = time.monotonic()
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        elapsed = round(time.monotonic() - start, 1)
        print(f'{name}: exit {done.returncode} in {elapsed}s', flush=True)
        if args.profile:
            (args.out / (name + '.log')).write_text(done.stdout + done.stderr, encoding='utf-8')
        return dict(name=name, returncode=done.returncode, seconds=elapsed,
                    stderr_tail=done.stderr[-2000:])

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        summary = list(pool.map(run, names))
    passed = all(r['returncode'] == 0 for r in summary)
    result = dict(schema='apac-ci-suite-v1', profile=args.profile, build=build,
                  mode='collect' if args.collect_only else 'math', shard=args.shard,
                  passed=passed, seconds=round(time.monotonic() - started, 1), validators=summary) if args.profile else summary
    (args.out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
