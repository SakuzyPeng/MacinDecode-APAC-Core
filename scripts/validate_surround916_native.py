#!/usr/bin/env python3
"""9.1.6 public layout, native element boundaries, capacity and encoder controls."""
import argparse
import ctypes as C
import hashlib
import json
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from channel_vectors import WINDOWS, bundle, cookie, excitation, packet
from layout_vectors import sequences
from native_frame_trace import COMPONENT_SHA256
from validate import require, write_json
from validate_channels import check
from validate_channels_native import inspect
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command, sha256_file


def public_layout():
    library = C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox')
    library.AudioFormatGetPropertyInfo.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)]
    library.AudioFormatGetProperty.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p]
    tag = C.c_uint32((193 << 16) | 16)
    size = C.c_uint32()
    prop = int.from_bytes(b'cmpl', 'big')
    require(library.AudioFormatGetPropertyInfo(prop, 4, C.byref(tag), C.byref(size)) == 0
            and size.value == 12 + 20 * 16, 'native layout size differs')
    raw = C.create_string_buffer(size.value)
    require(library.AudioFormatGetProperty(prop, 4, C.byref(tag), C.byref(size), raw) == 0,
            'native layout property failed')
    labels = [struct.unpack_from('=I', raw.raw, 12 + 20 * i)[0] for i in range(16)]
    require(labels == [1, 2, 3, 4, 5, 6, 33, 34, 35, 36, 13, 15, 49, 51, 52, 54],
            'native 9.1.6 channel order differs')
    return dict(tag=tag.value, labels=labels, property_sha256=hashlib.sha256(raw.raw).hexdigest())


def probes():
    for rate in (48000, 44100):
        options = dict(scene=False, drc=False, rich=False)
        cases = [excitation(16, ch, window, gain=100) for window in WINDOWS for ch in range(16)]
        masks = [0, 511] + [1 << i for i in range(9)]
        cases += [dict(elements=[None if mask & (1 << i) else {} for i in range(9)]) for mask in masks]
        for first in range(0, len(cases), 4):
            yield f'unit-{rate}-{first}', rate, options, cases[first:first + 4], True
        # Nonzero CAC, TNS, BWE2, DRC and an embedded preroll. Strong-signal
        # floating-point differences are diagnostic, as for the older layouts.
        options, seq = next((opts, seq) for kind, opts, seq in sequences(16) if kind == 'joint_tools')
        yield f'tools-{rate}', rate, options, seq[:4], False


def artificial(binary, report):
    for name, rate, options, cases, hard in probes():
        with workspace(report, name) as root:
            generated = [packet(case, 16, rate, **options) for case in cases]
            bundle(root / 'packets', [raw for raw, _ in generated], 16, rate, **options)
            identity = hashlib.sha256(cookie(16, rate, **options))
            for raw, _ in generated:
                identity.update(len(raw).to_bytes(8, 'little'))
                identity.update(raw)
            result, rows, _ = inspect(binary, root / 'packets', root, 1024 * len(cases), hard_unit=hard)
            for row, (_, truth) in zip(rows, generated):
                check(row, truth, 16)
            report['probes'].append(dict(result, name=name, rate=rate, hard_unit=hard,
                                         input_sha256=identity.hexdigest()))
        print(name, flush=True)


def controls(binary, report):
    for rate in (48000, 44100):
        for policy in ('none', 'music'):
            for signal in ('noise', 'channel-solo'):
                name = f'encoder-{rate}-{policy}-{signal}'
                with workspace(report, name) as root:
                    command(binary, 'fixture', '--layout', 'surround916', '--sample-rate', rate,
                            '--duration', '0.125', '--signals', signal, '--drc-configuration', policy,
                            '--out', root / 'fixture')
                    encoded = root / 'fixture' / signal / 'encoded.caf'
                    command(binary, 'dump', encoded, '--out', root / 'packets', '--packets', 32)
                    manifest = json.loads((root / 'packets/manifest.json').read_text())
                    frames = manifest['file']['packet_table']['value']['valid_frames']
                    result, _, pcm = inspect(binary, root / 'packets', root, frames)
                    require(result['pcm_metrics']['passed'], 'encoded native PCM exceeds tolerance')
                    decoded = command(binary, 'decode-sq', encoded, '--out', root / 'caf')
                    require(decoded['channel_layout_profile'] == 'apac-channel-layout-v3', 'missing 9.1.6 profile')
                    require((root / 'caf/pcm.f32le').read_bytes() == pcm, 'CAF and bundle differ')
                    for start, count in ((0, 31), (frames // 2, 1027), (frames - 9, 99)):
                        out = root / f'range-{start}'
                        command(binary, 'decode-sq', encoded, '--access', 'fast', '--start-frame', start,
                                '--frames', count, '--out', out)
                        require((out / 'pcm.f32le').read_bytes() == pcm[start * 64:(start + count) * 64],
                                'native-encoded fast range differs')
                    report['controls'].append(dict(result, rate=rate, policy=policy, signal=signal,
                                                  cookie_sha256=manifest['file']['cookie']['value']['sha256']))
                print(name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    require(args.binary.is_file() and not args.report.exists(), 'binary missing or report exists')
    binary = args.binary.resolve()
    report = dict(schema_version=1, profile='apac-channel-layout-v3', passed=False,
                  created_at=datetime.now(timezone.utc).isoformat(),
                  code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  source_sha256=source_digest(), binary_sha256=sha256_file(binary),
                  component_sha256=COMPONENT_SHA256, probes=[], controls=[], errors=[],
                  failure_directory=str(args.report.with_suffix('.failures')))
    try:
        report['layout'] = public_layout()
        artificial(binary, report)
        controls(binary, report)
        require(len(report['probes']) == 56 and len(report['controls']) == 8, 'missing native cases')
        require(source_digest() == report['source_sha256'] and sha256_file(binary) == report['binary_sha256'],
                'source or binary changed during acceptance')
        report['passed'] = True
    except Exception as error:
        report['errors'].append(str(error))
    write_json(args.report, report)
    print(json.dumps({key: report[key] for key in ('passed', 'errors')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
