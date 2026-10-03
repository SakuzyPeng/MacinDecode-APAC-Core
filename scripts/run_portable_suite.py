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
                        help='layout_presence example binary (required for validate_layouts)')
    parser.add_argument('--out', type=Path, required=True, help='new report directory')
    parser.add_argument('--only', nargs='*', help='validator names to run (default: all)')
    parser.add_argument('--fast', action='store_true', help='run only the quick subset')
    parser.add_argument('--skip', nargs='*', default=[], help='validator names to leave out')
    parser.add_argument('--jobs', type=int, default=1, help='validators run concurrently')
    args = parser.parse_args()
    names = [n for n in (args.only or (FAST if args.fast else list(SUITE))) if n not in args.skip]
    if 'validate_layouts' in names:
        if not args.presence_binary:
            parser.error('--presence-binary is required for validate_layouts; '
                         'use --skip validate_layouts to omit it explicitly')
        if not args.presence_binary.is_file():
            parser.error('--presence-binary is not a file: ' + str(args.presence_binary))
    if args.out.exists():
        raise SystemExit('output directory exists: ' + str(args.out))
    args.out.mkdir(parents=True)
    binary = args.binary.resolve()

    def run(name):
        flag, extra = SUITE[name]
        report = (args.out / (name + '.json')).resolve()
        command = [sys.executable, '-B', str(ROOT / 'scripts' / (name + '.py')),
                   '--binary', str(binary), flag, str(report)]
        if 'presence' in extra:
            command += ['--presence-binary', str(args.presence_binary.resolve())]
        start = time.monotonic()
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        elapsed = round(time.monotonic() - start, 1)
        print(f'{name}: exit {done.returncode} in {elapsed}s', flush=True)
        return dict(name=name, returncode=done.returncode, seconds=elapsed,
                    stderr_tail=done.stderr[-2000:])

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        summary = list(pool.map(run, names))
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if all(r['returncode'] == 0 for r in summary) else 1


if __name__ == '__main__':
    raise SystemExit(main())
