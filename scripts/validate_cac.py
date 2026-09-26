#!/usr/bin/env python3
"""CAC integer/boundary, independent mathematics, and exact portable acceptance."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from cac_oracle import Decoder, coupled
from cac_vectors import cases, packet, sequences
from generate_cac_math import PROFILE
from native_frame_trace import COMPONENT_SHA256, trace_bundle
from spectrum_vectors import bundle, cookie
from sq_oracle import scaled_channel
from validate import require, write_json
from validate_frames import control_specs
from validate_portable import (ROOT, LIMIT, bytes_of, compare_pcm, float32, match_record,
                               source_digest, spectra_fingerprints)
from validate_replay import command, sha256_file
from validate_spectra import coverage
from validate_synthesis import batch_cases

COUNTS = dict(spectra=2912, pcm=2984)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def compare_spectra(actual, expected, channel=None):
    result=compare_pcm(actual,expected)
    failure=result['first_failure']
    if failure:
        position=failure.pop('sample')
        failure['channel']=position//1024 if channel is None else channel
        failure['coefficient_index']=position%1024 if channel is None else position
    return result


def inspect(binary, directory, root, count):
    path = root/'cac.jsonl'
    summary = command(binary, 'parse-packets', directory, '--depth', 'cac', '--packets', count,
                      '--output', path, allowed=(1, 2))
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    failures = [dict(packet_index=r['packet_index'], error=r['error']) for r in rows if r['status']=='error']
    require(not failures, 'CAC parser error: '+json.dumps(failures[:2]))
    require(summary['complete'] and summary['errors']==0 and len(rows)==count, 'incomplete CAC report')
    require(not path.with_name(path.name+'.incomplete').exists(), 'CAC report has a failure marker')
    for row in rows:
        coverage(row['report'])
    return summary, rows


def check_expected(report, truth):
    require(report['cac_complete'] and report['spectrum_complete'], 'incomplete shared channel pair')
    require(report['shared_ics']==truth['shared_ics'] and report['cac']==truth['cac'], 'CAC parameters or boundaries differ')
    require(report['cac_numeric_profile']==PROFILE and report['numeric_profile']=='apac-sq-math-v1', 'numerical profile differs')
    require(report['output_stage']=='scaled_after_cac_before_tns', 'wrong CAC output stage')
    require(len(report['channels'])==len(report['channels_after_cac'])==2, 'wrong channel count')
    for actual, expected in zip(report['channels'], truth['channels']):
        for key in ('channel_index','ics','global_gain','sections','scale_factors','quantized',
                    'stream_bit_offset','spectral_bit_offset','end_bit_offset'):
            require(actual[key]==expected[key], 'raw CAC stream differs: '+key)
        require(bytes_of(actual['scaled'],'f')==bytes_of(scaled_channel(expected),'f'), 'raw spectrum rounding differs')
    expected_end = truth['cac']['end_bit_offset'] if truth['shared_ics'] else truth['channels'][-1]['end_bit_offset']
    require(report['stop_bit_offset']==expected_end, 'CAC stop is not the TNS flag boundary')
    wanted = coupled(truth)
    actual = [float32(c['scaled']) for c in report['channels_after_cac']]
    require([c['channel_index'] for c in report['channels_after_cac']]==[0,1], 'wrong output channel order')
    metrics = compare_spectra([v for c in actual for v in c], [v for c in wanted for v in c])
    require(metrics['passed'], 'CAC matrix differs: '+json.dumps(metrics))
    require(bytes_of([v for c in actual for v in c],'f')==bytes_of([v for c in wanted for v in c],'f'),
            'CAC matrix does not follow the prescribed separate rounding')
    return metrics


def fingerprints(reports):
    return dict(**spectra_fingerprints([c for r in reports for c in r['channels']]),
                cac_sha256=digest([dict(shared_ics=r['shared_ics'],cac=r['cac']) for r in reports]),
                coupled_sha256=hashlib.sha256(bytes_of([v for r in reports for c in r['channels_after_cac']
                                                        for v in float32(c['scaled'])], 'f')).hexdigest())


def exact(actual, expected):
    match_record(actual, expected)
    for key in ('cac_sha256', 'coupled_sha256'):
        require(actual[key]==expected[key], 'CAC fingerprint mismatch: '+key)


def validate_reference(reference, report, regression=False):
    require(reference.get('passed') is True and reference.get('mode')=='independent_math', 'requires successful CAC mathematical report')
    require(reference.get('counts')==COUNTS and reference.get('errors')==[], 'incomplete CAC reference')
    keys = ('schema_version','numeric_profile','format_sha256','tables_sha256','atol','rtol')
    if not regression: keys += ('code_commit','source_sha256')
    for key in keys:
        require(reference[key]==report[key], 'CAC reference identity differs: '+key)
    for stage,count in COUNTS.items():
        require(len(reference[stage])==count and all(r['passed'] for r in reference[stage]), 'missing or failed CAC reference cases')
        require([(r['rate'],r['index']) for r in reference[stage]]==[(rate,i) for rate in (48000,44100) for i in range(count//2)],
                'CAC reference cases are duplicated, reordered or missing')


def portable(binary, report, reference):
    for stage in COUNTS:
        for rate in (48000,44100):
            first = 0
            items = cases() if stage=='spectra' else sequences()
            for batch in batch_cases(items, 32):
                try:
                    with tempfile.TemporaryDirectory(prefix='cac-math-') as tmp:
                        root = Path(tmp)
                        groups = [(c['kind'],[c]) for c in batch] if stage=='spectra' else batch
                        generated = [packet(c) for _,seq in groups for c in seq]
                        bundle(root/'packets', [p for p,_ in generated], rate)
                        summary, rows = inspect(binary, root/'packets', root, len(generated))
                        require(summary['cac_complete_packets']==len(generated), 'missing complete CAC stages')
                        for row,(_,truth) in zip(rows,generated):
                            check_expected(row['report'],truth)
                        pcm = None
                        if stage=='pcm':
                            result = command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                            require(result['complete'] and not result['native_apis_used'] and result['experimental'], 'wrong PCM completion')
                            require(result['cac_numeric_profile']==PROFILE, 'PCM CAC profile differs')
                            meta = json.loads((root/'pcm/pcm.json').read_text(encoding='utf-8'))
                            implementation = meta['decoder_settings']['implementation']['value']
                            if report['implementation'] is None:report['implementation']=implementation
                            require(implementation==report['implementation'], 'candidate changed implementation')
                            require(implementation['cac_tables_sha256']==report['constant_model_sha256'], 'candidate CAC constants differ')
                            pcm = (root/'pcm/pcm.f32le').read_bytes()
                            require(meta['frames']==len(generated)*1024 and meta['channels']==2 and meta['sample_rate']==rate
                                    and meta['all_finite'] and len(pcm)==len(generated)*8192
                                    and hashlib.sha256(pcm).hexdigest()==meta['sha256'], 'invalid PCM accounting')
                        cursor = 0
                        for i,(kind,seq) in enumerate(groups):
                            n = len(seq); selected = [r['report'] for r in rows[cursor:cursor+n]]
                            identity = hashlib.sha256(cookie(rate))
                            for data,_ in generated[cursor:cursor+n]:
                                identity.update(len(data).to_bytes(8,'little'));identity.update(data)
                            record = dict(rate=rate,index=first+i,kind=kind,passed=True,input_sha256=identity.hexdigest(),
                                          **fingerprints(selected))
                            if pcm is not None:
                                raw = pcm[cursor*8192:(cursor+n)*8192]
                                values = float32(struct.unpack('<'+str(len(raw)//4)+'f',raw))
                                record.update(frames=n*1024,pcm_sha256=hashlib.sha256(raw).hexdigest())
                                if reference is None:
                                    oracle = Decoder()
                                    wanted = [v for _,truth in generated[cursor:cursor+n] for v in oracle.decode(truth)]
                                    record.update(compare_pcm(values,wanted))
                            if reference:
                                exact(record,reference[stage][len(report[stage])])
                            report[stage].append(record);cursor += n
                except Exception as error:
                    report['errors'].append(dict(stage=stage,rate=rate,first_case=first,error=str(error)))
                first += len(batch)
                if first%320==0:print('CAC',stage,rate,first,flush=True,file=sys.stderr)


def check_native(rows, trace):
    require(not trace['errors'] and trace['process_exit_code']==0 and trace['packet_calls']==len(rows), 'native CAC trace incomplete')
    streams = {(s['sequence'],s['channel_index']):s for s in trace['spectra']}
    coupled_events = {c['sequence']:c for c in trace['cac']}
    require(len(streams)==len(trace['spectra']), 'duplicate native stream event')
    require(set(streams)=={(i,c['channel_index']) for i,r in enumerate(rows) for c in r['report']['channels']},
            'native stream coverage differs from CAC report')
    require(len(coupled_events)==len(trace['cac']), 'duplicate CAC native event')
    require(set(coupled_events)=={i for i,r in enumerate(rows) if r['report']['shared_ics'] and r['report']['cac_complete']},
            'native CAC event coverage differs')
    records = []
    for i,row in enumerate(rows):
        r = row['report']
        if not r['cac_complete']:
            require(r['stop_reason']=='cpe_absent' or r['status']=='unsupported' or not r['prefix_complete'], 'supported nonempty CAC did not finish')
            require(not r['channels_after_cac'], 'unsupported CAC acquired output')
            continue
        numeric = []
        for channel in r['channels']:
            event = streams.get((i,channel['channel_index']))
            require(event is not None and event['packet_sha256']==r['packet_sha256'], 'native raw stream identity mismatch')
            for key in ('stream_bit_offset','end_bit_offset'):
                require(event[key]==channel[key], 'native raw boundary mismatch: '+key)
            raw_metrics = compare_spectra(float32(channel['scaled']),event['scaled'],channel['channel_index'])
            require(raw_metrics['passed'], 'native raw spectrum differs: '+json.dumps(raw_metrics))
        if r['shared_ics']:
            event = coupled_events.get(i)
            require(event is not None and event['packet_sha256']==r['packet_sha256'], 'missing native CAC identity')
            require(event['start_bit_offset']==r['cac']['start_bit_offset'] and event['end_bit_offset']==r['cac']['end_bit_offset']
                    and event['tns_start_bit_offset']==r['stop_bit_offset'], 'native CAC/TNS boundary mismatch')
            require(event['runs']==[{k:run[k] for k in ('gain_index','repeat_code')} for run in r['cac']['runs']], 'native CAC integer parameters differ')
            for index,channel in enumerate(r['channels_after_cac']):
                require(event['before'][index]==streams[(i,index)]['scaled'], 'CAC input snapshot moved beyond the raw stream')
                numeric.append(compare_spectra(float32(channel['scaled']),event['after'][index],index))
        records.append(dict(packet_index=row['packet_index'],packet_sha256=r['packet_sha256'],shared_ics=r['shared_ics'],
                            structural_passed=True,numeric_passed=all(m['passed'] for m in numeric),metrics=numeric))
    return records


def pcm_stop(result):
    error=result.get('error',{})
    require(error.get('operation')=='SQ decoder','unexpected PCM failure: '+json.dumps(error))
    message=error.get('message','')
    if message.startswith('unsupported configuration: '):return 'configuration'
    exact_messages={
        'requires verified stereo configuration without ancillary data, extensions or remapping':'configuration',
        'decode from source packet zero; random-access state is not implemented':'nonzero_origin',
        'ASP refresh/preroll is not implemented':'asp_preroll',
        'unsupported frame: cpe_absent':'cpe_absent',
        'unparsed trailing bytes':'unknown_tail',
    }
    if message in exact_messages:return exact_messages[message]
    packet_stops={
        'nonzero ancillary trimming is unsupported':'ancillary trimming',
        'nonzero core alignment is unsupported':'core alignment',
        'nonzero packet alignment is unsupported':'ancillary alignment',
        'unparsed trailing bytes':'unknown_tail',
        'embedded_preroll_incomplete':'embedded_preroll',
        'ASP reconfiguration is unsupported':'asp_reconfiguration',
        'unverified ASP frame type 3':'asp_type',
        'lrvq_prefix_deferred':'lrvq',
    }
    if message.startswith('unsupported frame: '):
        reason=message.removeprefix('unsupported frame: ')
        if reason.startswith('non-neutral audio scene update: '):return 'non_neutral_scene'
        if reason in packet_stops:
            require(isinstance(error.get('bit_offset'),int) and isinstance(error.get('packet_index'),int),'packet stop lacks packet/bit position')
            return packet_stops[reason]
    for tool in ('left TNS','right TNS','left BWE2','right BWE2','core alignment','ancillary trimming','ancillary alignment'):
        if message=='unsupported nonzero '+tool:
            require(isinstance(error.get('bit_offset'),int) and isinstance(error.get('packet_index'),int), 'tool stop lacks packet/bit position')
            return tool
    raise AssertionError('unexpected PCM stop: '+json.dumps(error))


def native_artificial(binary, report):
    for rate in (48000,44100):
        first = 0
        for batch in batch_cases(cases(),32):
            with tempfile.TemporaryDirectory(prefix='cac-native-') as tmp:
                root = Path(tmp);generated=[packet(c) for c in batch]
                bundle(root/'packets',[p for p,_ in generated],rate)
                _,rows=inspect(binary,root/'packets',root,len(batch))
                checked=check_native(rows,trace_bundle(binary,root/'packets',root,True,cac=True))
                require(len(checked)==len(batch),'native artificial CAC count differs')
                for i,(case,record) in enumerate(zip(batch,checked)):
                    record.update(rate=rate,index=first+i,kind=case['kind'])
                    if case['kind']=='matrix_basis':require(record['numeric_passed'],'native CAC unit basis differs')
                    report['native_artificial'].append(record)
            first+=len(batch)
            if first%320==0:print('CAC native',rate,first,flush=True,file=sys.stderr)


def native_real(binary, baseline, report, inspect_fn=inspect, check_fn=check_native, tns=False, bwe2=False):
    previous=json.loads(baseline.read_text(encoding='utf-8'))
    require(previous['passed'] and len(previous['representatives'])==15,'requires verified replay representatives')
    specs=[('representative',base,None) for base in previous['representatives']]
    specs += [('control',item,None) for item in control_specs()]
    for kind,spec,_ in specs:
        record=dict(kind=kind,passed=False);report['real'].append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='cac-real-') as tmp:
                root=Path(tmp)
                if kind=='representative':
                    window=spec['dump']['replay_window'];record.update(channels=spec['channels'],range=spec['range'])
                    dumped=command(binary,'dump',spec['source'],'--out',root/'packets','--with-preroll',
                                   '--start-packet',window['requested_start_packet'],'--packets',window['requested_packets'])
                else:
                    name,signal,extra=spec;record['name']=name
                    command(binary,'fixture','--out',root/'fixture','--signals',signal,'--duration',2,'--seed',1,'--max-output-mib',8,*extra)
                    generated=root/'fixture'/signal
                    settings=json.loads((generated/'manifest.json').read_text(encoding='utf-8'))
                    record['encoder']={k:settings[k] for k in ('requested','actual_encoder_settings','drc_configuration_verified')}
                    if 'none' in extra:require(settings['drc_configuration_verified'] is True,'DRC none was not verified')
                    for flag,key in [('--quality','cdqu'),('--bitrate','brat')]:
                        if flag in extra:require(settings['actual_encoder_settings'][key]['error'] is None and settings['actual_encoder_settings'][key]['value']==int(extra[extra.index(flag)+1]),'encoder control differs')
                    dumped=command(binary,'dump',generated/'encoded.caf','--out',root/'packets','--with-preroll')
                summary,rows=inspect_fn(binary,root/'packets',root,dumped['actual_packets']);record['summary']=summary
                if summary['context']['channels']==2:
                    record['native_bwe2_checks' if bwe2 else 'native_tns_checks' if tns else 'native_cac_checks']=check_fn(rows,trace_bundle(binary,root/'packets',root,True,cac=True,tns=tns,bwe2=bwe2))
                    if bwe2:require(summary['bwe2_complete_packets']==summary['tns_complete_packets'],'real BWE2 stage incomplete')
                    if tns:require(summary['tns_complete_packets']==summary['cac_complete_packets'],'real TNS stage incomplete')
                    require(summary['cac_complete_packets']+summary['cpe_absent_packets']==summary['actual_packets'],
                            'supported real/control packets did not complete CAC')
                else:require(summary['cac_complete_packets']==0,'unsupported context acquired CAC')
                result=subprocess.run([str(binary),'decode-sq',str(root/'packets'),'--out',str(root/'pcm')],capture_output=True,text=True,encoding='utf-8',timeout=120)
                record['pcm_exit_code']=result.returncode
                record['pcm_result']=json.loads(result.stdout or result.stderr)
                require(result.returncode in (0,1),'unexpected PCM exit code')
                if result.returncode:
                    record['pcm_stop_reason']=pcm_stop(record['pcm_result'])
                    if (root/'pcm').exists():
                        require((root/'pcm/.incomplete.json').exists() and not (root/'pcm/pcm.json').exists(),
                                'failed PCM export acquired a completion artifact')
                else:
                    require(record['pcm_result']['complete'] and not record['pcm_result']['native_apis_used'], 'invalid independent PCM result')
                    record['pcm_stop_reason']=None
                record['passed']=True
        except Exception as error:record['error']=str(error)
        print('CAC',kind,record.get('name',record.get('range')),record['passed'],flush=True,file=sys.stderr)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--reference-report',type=Path)
    group.add_argument('--regression-report',type=Path)
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing to overwrite report')
    if args.native and sys.platform!='darwin':parser.error('native CAC snapshots require macOS')
    binary=args.binary.resolve(strict=True)
    report=dict(schema_version=1,numeric_profile=PROFILE,code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,encoding='utf-8').strip(),
                tested_worktree_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,encoding='utf-8').strip()),
                source_sha256=source_digest(),format_sha256=sha256_file(ROOT/'data/cac-codebooks.json'),tables_sha256=sha256_file(ROOT/'data/cac-math-v1.json'),
                constant_model_sha256=json.loads((ROOT/'data/cac-math-v1.json').read_text())['tables_sha256'],
                tool_sha256=sha256_file(binary),platform=platform.platform(),architecture=platform.machine(),python=sys.version,
                mode='bit_exact_replay' if args.reference_report else 'independent_math',implementation=None,atol=1e-6,rtol=1e-5,
                spectra=[],pcm=[],errors=[],native_artificial=[],real=[],started_utc=datetime.now(timezone.utc).isoformat())
    reference=None
    reference_path=args.reference_report or args.regression_report
    if args.regression_report: report['mode']='regression_replay'
    try:
        if reference_path:
            require(reference_path.stat().st_size<=LIMIT,'reference exceeds 128 MiB')
            report['reference_report_sha256']=sha256_file(reference_path)
            reference=json.loads(reference_path.read_text(encoding='utf-8'));validate_reference(reference,report,bool(args.regression_report))
            report['reference_code_commit']=reference['code_commit']
        portable(binary,report,reference)
        if args.native:
            report['component_sha256']=COMPONENT_SHA256
            native_artificial(binary,report);native_real(binary,args.replay_baseline,report)
            require(len(report['native_artificial'])==COUNTS['spectra'] and len(report['real'])==30,'incomplete native CAC coverage')
        require(source_digest()==report['source_sha256'] and sha256_file(binary)==report['tool_sha256'],'sources or binary changed during acceptance')
        if reference_path:require(sha256_file(reference_path)==report['reference_report_sha256'],'reference report changed')
    except Exception as error:report['errors'].append(dict(stage='validation',error=str(error)))
    report['counts']={stage:len(report[stage]) for stage in COUNTS}
    report['passed']=not report['errors'] and report['counts']==COUNTS and all(r['passed'] for stage in COUNTS for r in report[stage]) and all(r['passed'] for r in report['real'])
    report['pcm_metrics']=dict(max_absolute_error=max((r.get('max_absolute_error',0.) for r in report['pcm']),default=0),max_ulp=max((r.get('max_ulp',0) for r in report['pcm']),default=0),failed_samples=sum(r.get('failed_samples',0) for r in report['pcm'])) if reference is None else None
    report['native_float_failures']=sum(not r['numeric_passed'] for r in report['native_artificial'])
    report['native_float_comparison_passed']=all(r['numeric_passed'] for r in report['native_artificial']) if args.native else None
    report['native_float_policy']='Unit basis is required; all other native float differences remain separate diagnostics, without tolerance changes.'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode())<LIMIT,'report exceeds 128 MiB')
    write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('passed','counts','pcm_metrics','native_float_failures','errors')}))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
