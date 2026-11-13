"""Frozen BWE2 wire coverage and default-build rejection checks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import tempfile

from bwe2_vectors import cases, packet, sequences
from bwe2_oracle import Decoder
from ci_catalog import core_identity
from ci_support import mathematics, selected, case_index, requires_bwe2, reject_bwe2_case
from spectrum_vectors import bundle
from validate import require, write_json
from validate_portable import compare_pcm, source_digest
from validate_replay import command, sha256_file


def spectral_cases(binary, report):
    from validate_bwe2 import check_expected, fingerprints
    batch = []
    def check(rows):
        with tempfile.TemporaryDirectory(prefix='bwe2-wire-') as directory:
            root = Path(directory)
            generated = [packet(case, rate) for _, rate, case in rows]
            bundle(root / 'packets', [raw for raw, _ in generated], rows[0][1])
            path = root / 'wire.jsonl'
            command(binary, 'parse-packets', root / 'packets', '--depth', 'bwe2',
                    '--packets', len(rows), '--output', path, allowed=(1, 2))
            actual = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            require(len(actual) == len(rows), 'BWE2 wire reports are incomplete')
            for (index, rate, case), (_, truth), row in zip(rows, generated, actual):
                record = core_identity('validate_bwe2', 'spectra', rate, index, case)
                if requires_bwe2(truth):
                    error = row['error']
                    first = next(channel for channel in truth['bwe2']['channels'] if channel['active'])
                    require(error['kind'] == 'bwe2-unavailable', 'wrong BWE2 availability error')
                    require(error['bit_offset'] == truth['bwe2']['start_bit_offset'] + first['channel_index'],
                            'BWE2 rejection lost its control-bit position')
                    record.update(bwe2_unavailable=True, error=error)
                else:
                    require('report' in row, 'inactive BWE2 was rejected')
                    check_expected(row['report'], truth, mathematics())
                    record.update(fingerprints([row['report']]))
                report['spectra'].append(dict(record, passed=True))
    for rate in (48000, 44100):
        for offset, case in enumerate(selected('spectra', cases())):
            batch.append((case_index('spectra', offset), rate, case))
            if len(batch) == 32:
                check(batch)
                batch = []
        if batch:
            check(batch)
            batch = []


def pcm_cases(binary, report):
    for rate in (48000, 44100):
        for offset, item in enumerate(selected('pcm', sequences())):
            index = case_index('pcm', offset)
            kind, sequence = item
            generated = [packet(case, rate) for case in sequence]
            identity = core_identity('validate_bwe2', 'pcm', rate, index, item)
            with tempfile.TemporaryDirectory(prefix='bwe2-state-') as directory:
                root = Path(directory)
                bundle(root / 'packets', [raw for raw, _ in generated], rate)
                if reject_bwe2_case(binary, root / 'packets', root, report, identity, generated, group='pcm'):
                    continue
                output = command(binary, 'decode-sq', root / 'packets', '--out', root / 'pcm')
                raw = (root / 'pcm/pcm.f32le').read_bytes()
                record = dict(identity, passed=True, pcm_sha256=hashlib.sha256(raw).hexdigest())
                require(output['complete'] and output['saved_frames'] == 1024 * len(sequence), 'inactive BWE2 PCM is incomplete')
                if mathematics():
                    decoder = Decoder()
                    expected = [value for _, truth in generated for value in decoder.decode(truth)]
                    metric = compare_pcm(struct.unpack('<%df' % (len(raw) // 4), raw), expected)
                    require(metric['passed'], 'inactive BWE2 PCM mathematics differ')
                report['pcm'].append(record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.binary.is_file() and not args.output.exists(), 'binary missing or output already exists')
    binary = args.binary.resolve()
    report = dict(schema_version=1, profile='apac-bwe2-unavailable-v1', passed=False,
        created_at=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
        code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        source_sha256=source_digest(), binary_sha256=sha256_file(binary),
        qualification='inactive-path mathematics and active-path wire/rejection contracts',
        spectra=[], pcm=[], errors=[])
    try:
        spectral_cases(binary, report)
        pcm_cases(binary, report)
        require(sha256_file(binary) == report['binary_sha256'] and source_digest() == report['source_sha256'],
                'source or executable changed during validation')
        report['passed'] = True
    except Exception as error:
        report['errors'].append(str(error))
    write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ('passed', 'errors')}))
    return 0 if report['passed'] else 1
