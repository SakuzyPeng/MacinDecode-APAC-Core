#!/usr/bin/env python3
"""Validate Rust SQ integer spectra, bit boundaries and pre-tool float spectra."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from spectrum_vectors import band_cases, bundle, frame, matrix_cases
from validate import require, write_json
from validate_replay import command, sha256_file
from validate_frames import control_specs
from native_frame_trace import COMPONENT_SHA256, trace_bundle


def ulp(a, b):
    def ordered(x):
        word = struct.unpack('<i', struct.pack('<f', x))[0]
        return word if word >= 0 else -0x80000000-word
    return abs(ordered(a)-ordered(b))


def compare(actual, expected, metrics):
    require(len(actual) == len(expected) == 1024, 'wrong spectrum size')
    for line, (a, b) in enumerate(zip(actual, expected)):
        a = struct.unpack("<f", struct.pack("<f", a))[0]  # JSON carries a Float32 value.
        require(math.isfinite(a) and math.isfinite(b), 'nonfinite spectrum')
        error = abs(a-b)
        require(error <= 1e-6 + 1e-5*abs(b), f'spectrum coefficient {line} differs: {a} vs {b}')
        metrics['max_absolute_error'] = max(metrics['max_absolute_error'], error)
        metrics['max_ulp'] = max(metrics['max_ulp'], ulp(a,b))
        if b:
            metrics['max_relative_error'] = max(metrics.get('max_relative_error', 0.0), error/abs(b))
        metrics['coefficients'] += 1


def coverage(report):
    spans = [(f['bit_offset'],f['bit_length']) for f in report['fields']]
    spans += [(r['bit_offset'],r['bit_length']) for r in report['unknown_ranges']]
    position = 0
    for start,length in sorted(spans):
        require(start == position and length > 0, 'bit coverage gap/overlap')
        position += length
    require(position == report['packet_bytes']*8, 'missing packet bits')
    require(report['status'] == 'partial' and report['component_end_bit_offset'] is None, 'false frame completion')
    require(report['spectral_stage'] == 'scaled_before_cac_tns', 'wrong spectral stage')


def check_expected(report, expected, metrics):
    coverage(report)
    require(report['spectrum_complete'] and len(report['channels']) == 2, 'incomplete independent SQ streams')
    for actual,wanted in zip(report['channels'], expected):
        for key in ['quantized','sections','scale_factors','ics','global_gain','channel_index',
                    'stream_bit_offset','spectral_bit_offset','end_bit_offset']:
            require(actual[key] == wanted[key], 'integer/structure mismatch: '+key)
        compare(actual['scaled'], wanted['scaled'], metrics)
    require(report['stop_bit_offset'] == expected[-1]['end_bit_offset'], 'wrong spectral stop')
    require(report['payload_bit_offset'] == expected[0]['stream_bit_offset'], 'lost prefix entry')


def inspect(binary, path, root, count):
    output = root/'spectra.jsonl'
    summary = command(binary, 'parse-packets', path, '--depth','spectrum','--packets',count,'--output',output,allowed=(2,))
    require(summary['complete'] and summary['errors'] == 0, 'parser error')
    require(not output.with_name(output.name+'.incomplete').exists(), 'incomplete report')
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    require(len(rows) == count == summary['actual_packets'], 'missing packets')
    manifest = json.loads((path/'manifest.json').read_text())
    index = [json.loads(line) for line in (path/'packets.jsonl').read_text().splitlines()]
    require(len(index) == count, 'index count differs')
    with (path/'packets.bin').open('rb') as data:
        for row, packet in zip(rows, index):
            report = row['report']
            require(row['packet_index'] == packet['packet_index'] and row['export_offset'] == packet['export_offset'], 'packet coordinate mismatch')
            data.seek(packet['export_offset'])
            payload = data.read(packet['bytes'])
            require(report['packet_bytes'] == len(payload) and report['packet_sha256'] == hashlib.sha256(payload).hexdigest(), 'packet fingerprint mismatch')
            require(report['cookie_sha256'] == manifest['file']['cookie']['value']['sha256'], 'cookie fingerprint mismatch')
            coverage(report)
    return summary,rows


def check_native(rows, trace, metrics):
    require(not trace['errors'] and trace['process_exit_code'] == 0, 'native trace error')
    require(trace['packet_calls'] == len(rows), 'native packet count mismatch')
    require(len(trace['events']) == len(rows), 'native prefix boundary count mismatch')
    entries = {}
    for event in trace['spectra']:
        key = (event['sequence'],event['channel_index'])
        require(key not in entries, 'duplicate native channel')
        entries[key] = event
    checked = 0
    for i,row in enumerate(rows):
        report = row['report']
        prefix = trace['events'][i]
        require(prefix['sequence'] == i and prefix['packet_sha256'] == report['packet_sha256'], 'native prefix identity differs')
        absent = report['stop_reason'] == 'cpe_absent'
        require(prefix['boundary'] == ('cpe_absent' if absent else 'sq_left_channel_stream'), 'native prefix kind differs')
        require(prefix['native_bit_offset'] == (report['stop_bit_offset'] if absent else report['payload_bit_offset']), 'native prefix location differs')
        for channel in report['channels']:
            event = entries.get((i,channel['channel_index']))
            require(event is not None and event['packet_sha256'] == report['packet_sha256'], 'missing native channel identity')
            for key in ['stream_bit_offset','end_bit_offset']:
                require(event[key] == channel[key], f'native boundary differs: packet {i} channel {channel["channel_index"]} {key}')
            compare(channel['scaled'],event['scaled'],metrics)
            checked += 1
    return checked


def chunks(items, size=96):
    iterator = iter(items)
    while True:
        batch = list(itertools.islice(iterator,size))
        if not batch:
            break
        yield batch


def artificial(binary, cases, rate, native, records, metrics, native_metrics=None):
    for batch_index,cases in enumerate(chunks(cases)):
        record = dict(rate=rate,batch=batch_index,cases=len(cases),native=native,passed=False)
        records.append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='apac-spectra-vectors-') as tmp:
                root=Path(tmp)
                generated=[frame(case) for case in cases]
                bundle(root/'packets',[p for p,_ in generated],rate)
                summary,rows=inspect(binary,root/'packets',root,len(cases))
                for case_index,(row,(_,expected)) in enumerate(zip(rows,generated)):
                    try:
                        check_expected(row['report'],expected,metrics)
                    except Exception as error:
                        raise AssertionError(f'case {batch_index*96+case_index}: {error}') from error
                record['spectrum_sha256']=hashlib.sha256(json.dumps([r['report']['channels'] for r in rows],sort_keys=True).encode()).hexdigest()
                if native:
                    trace=trace_bundle(binary,root/'packets',root,True)
                    record['native_channels']=check_native(rows,trace,native_metrics)
                record['passed']=True
        except Exception as error:
            record['error']=str(error)
        if batch_index%10==0 or native:
            print(f'spectrum vectors: rate={rate} batch={batch_index} native={native} passed={record["passed"]}',file=sys.stderr,flush=True)


def native_cases():
    # Every gain/escape boundary, all windows/groups, every band and codebook,
    # both channel positions. Exhaustive tuple signs are checked portably.
    for case in matrix_cases():
        if case['kind'] != 'signed_tuple':
            yield case
    for block in range(4):
        for band in range(14 if block==2 else 49):
            for side in ['left','right']:
                cb=1+band%11
                values=[1,-1,0,1] if cb<=4 else [16,-17] if cb==11 else [1,-1]
                yield dict(kind='native_band',block=block,grouping=[0,0x55,0x7f][band%3],**{side:{band:(cb,values,160)}})
    for case in band_cases():
        if case['kind'] != 'band':
            yield case


def real_cases(binary, baseline, report, metrics):
    baseline=json.loads(baseline.read_text())
    require(baseline['passed'] and len(baseline['representatives'])==15,'requires successful replay baseline')
    for base in baseline['representatives']:
        record=dict(kind='representative',channels=base['channels'],range=base['range'],passed=False)
        report.append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='apac-spectra-real-') as tmp:
                root=Path(tmp);window=base['dump']['replay_window']
                result=command(binary,'dump',base['source'],'--out',root/'packets','--with-preroll',
                    '--start-packet',window['requested_start_packet'],'--packets',window['requested_packets'])
                record['summary'],rows=inspect(binary,root/'packets',root,result['actual_packets'])
                if base['channels']==2:
                    record['native_channels']=check_native(rows,trace_bundle(binary,root/'packets',root,True),metrics)
                else:
                    require(all(not r['report']['channels'] and not r['report']['spectrum_complete'] for r in rows),'unsupported context acquired spectra')
                record['passed']=True
        except Exception as error:
            record['error']=str(error)
        print('spectrum representative:',record['channels'],record['range'],record['passed'],file=sys.stderr,flush=True)
    for name,signal,extra in control_specs():
        record=dict(kind='control',name=name,passed=False);report.append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='apac-spectra-control-') as tmp:
                root=Path(tmp)
                command(binary,'fixture','--out',root/'fixture','--signals',signal,'--duration',2,'--seed',1,'--max-output-mib',8,*extra)
                case=root/'fixture'/signal
                fixture=json.loads((case/'manifest.json').read_text())
                record['encoder']={k:fixture[k] for k in ['requested','actual_encoder_settings','drc_configuration_verified']}
                if 'none' in extra:
                    require(fixture['drc_configuration_verified'] is True,'DRC none not verified')
                for option,key in [('--quality','cdqu'),('--bitrate','brat')]:
                    if option in extra:
                        setting=fixture['actual_encoder_settings'][key]
                        require(setting['error'] is None and setting['value']==int(extra[extra.index(option)+1]),'encoder control not verified')
                result=command(binary,'dump',case/'encoded.caf','--out',root/'packets','--with-preroll')
                (case/'encoded.caf').unlink()
                record['summary'],rows=inspect(binary,root/'packets',root,result['actual_packets'])
                record['native_channels']=check_native(rows,trace_bundle(binary,root/'packets',root,True),metrics)
                record['passed']=True
        except Exception as error:
            record['error']=str(error)
        print('spectrum control:',name,record['passed'],file=sys.stderr,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=Path('target/debug/apac-tool'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--native',action='store_true',help='Also compare Apple pre-tool snapshots and real/control windows (macOS arm64).')
    p.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=p.parse_args()
    if args.output.exists(): p.error('refusing to overwrite report')
    if args.native and sys.platform!='darwin': p.error('native snapshots require macOS')
    binary=args.binary.resolve()
    metrics=dict(max_absolute_error=0.0,max_relative_error=0.0,max_ulp=0,coefficients=0)
    native_metrics=dict(metrics)
    report=dict(schema_version=1,started_utc=datetime.now(timezone.utc).isoformat(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        tool_sha256=sha256_file(binary),table_sha256=sha256_file(Path(__file__).resolve().parents[1]/'data/sq-codebooks.json'),
        architecture=platform.machine(),atol=1e-6,rtol=1e-5,artificial=[],native_artificial=[],real=[],
        scope='SQ integer and scaled spectra before CAC/TNS; no independent PCM')
    for rate in [48000,44100]:
        artificial(binary,itertools.chain(matrix_cases(),band_cases()),rate,False,report['artificial'],metrics)
    if args.native:
        report['component_sha256']=COMPONENT_SHA256
        report['system']=subprocess.check_output(['sw_vers'],text=True).strip()
        for rate in [48000,44100]:
            artificial(binary,native_cases(),rate,True,report['native_artificial'],metrics,native_metrics)
        real_cases(binary,args.replay_baseline,report['real'],native_metrics)
    report['failures']=[r for k in ['artificial','native_artificial','real'] for r in report[k] if not r['passed']]
    report.update(passed=not report['failures'],portable_metrics=metrics,native_metrics=native_metrics,
        artificial_cases=sum(r['cases'] for r in report['artificial']),
        native_artificial_cases=sum(r['cases'] for r in report['native_artificial']),
        native_channels=sum(r.get('native_channels',0) for k in ['native_artificial','real'] for r in report[k]),
        finished_utc=datetime.now(timezone.utc).isoformat())
    totals=Counter()
    for r in report['real']:
        for k in ['actual_packets','left_spectrum_packets','right_spectrum_packets','spectrum_complete_packets','cpe_absent_packets']:
            totals[k]+=r.get('summary',{}).get(k,0)
    report['real_totals']=dict(totals)
    require(len(json.dumps(report).encode()) < 128*1024*1024,'report exceeds output limit')
    write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ['passed','artificial_cases','native_artificial_cases','native_channels','portable_metrics','native_metrics','real_totals','failures']}))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
