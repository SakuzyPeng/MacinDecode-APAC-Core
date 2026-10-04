#!/usr/bin/env python3
"""Independent TNS LPC/IMDCT mathematics and exact portable stage acceptance."""
import argparse
import copy
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

from generate_tns_math import PROFILE, DESTINATION, document
from tns_vectors import cases, packet, sequences
from tns_oracle import Decoder, filtered, reflection
from spectrum_vectors import bundle, cookie
from native_frame_trace import COMPONENT_SHA256, trace_bundle
from validate import require, write_json
from validate_cac import (check_expected as check_cac, fingerprints as cac_fingerprints,
                          exact as cac_exact, compare_spectra, digest, native_real)
from validate_portable import ROOT, LIMIT, bytes_of, float32, compare_pcm, source_digest
from validate_replay import command, sha256_file
from validate_spectra import coverage
from validate_synthesis import batch_cases

COUNTS = dict(spectra=4326, pcm=4330)


def before_tns(report):
    earlier = dict(report)
    earlier['stop_bit_offset'] = (report['cac']['end_bit_offset'] if report['shared_ics']
                                  else report['channels'][-1]['end_bit_offset'])
    return earlier


def inspect(binary, directory, root, count):
    output = root/'tns.jsonl'
    summary = command(binary,'parse-packets',directory,'--depth','tns','--packets',count,
                      '--output',output,allowed=(1,2))
    rows = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
    errors = [dict(packet_index=r['packet_index'],error=r['error']) for r in rows if r['status']=='error']
    require(not errors,'TNS parsing failed: '+json.dumps(errors[:2]))
    require(summary['complete'] and summary['errors']==0 and len(rows)==count,'incomplete TNS reports')
    require(not output.with_name(output.name+'.incomplete').exists(),'TNS failure marker')
    for row in rows:
        coverage(row['report'])
    return summary,rows


def spectral_metrics(actual, expected, truth):
    result = compare_spectra([v for c in actual for v in c],[v for c in expected for v in c])
    if result['first_failure']:
        failure=result['first_failure']; channel=failure['channel']
        n=128 if truth['channels'][channel]['ics']['block_type']==2 else 1024
        failure['window'],failure['frequency_line']=divmod(failure['coefficient_index'],n)
    return result


def check_expected(report, truth, math=True):
    check_cac(before_tns(report),truth)
    require(report['tns_complete'] and report['tns_numeric_profile']==PROFILE,'wrong TNS completion/profile')
    require(report['tns_stage']=='scaled_after_tns_before_bwe2','wrong TNS output stage')
    require(report['stop_bit_offset']==truth['tns_end_bit_offset'],'wrong BWE2 entry')
    actual=copy.deepcopy(report['tns'])
    for c in actual:
        for w in c['windows']:
            for f in w['filters']:
                ks=f.pop('reflection')
                require(bytes_of(ks,'d')==bytes_of([float(reflection(q,w['resolution'])) for q in f['quantized']],'d'),
                        'TNS reflection constant mismatch')
    require(actual==truth['tns'],'TNS wire parameters/ranges/offsets differ')
    require([c['channel_index'] for c in report['channels_after_tns']]==[0,1],'TNS output channel order')
    values=[float32(c['scaled']) for c in report['channels_after_tns']]
    if math:
        result=spectral_metrics(values,filtered(truth),truth)
        require(result['passed'],'TNS mathematical spectrum differs: '+json.dumps(result))
        return result
    return None


def fingerprints(reports):
    return dict(**cac_fingerprints(reports),tns_sha256=digest([r['tns'] for r in reports]),
                filtered_sha256=hashlib.sha256(bytes_of([v for r in reports for c in r['channels_after_tns']
                                                         for v in float32(c['scaled'])],'f')).hexdigest())


def exact(actual, expected):
    cac_exact(actual, expected)
    for key in ('tns_sha256','filtered_sha256'):
        require(actual[key]==expected[key],'TNS fingerprint mismatch: '+key)


def validate_reference(reference, report, regression=False):
    require(reference.get('passed') is True and reference.get('mode')=='independent_math','requires complete independent TNS reference')
    require(reference.get('counts')==COUNTS and reference.get('errors')==[],'incomplete TNS reference')
    keys=('schema_version','numeric_profile','tables_sha256','upstream_constants','atol','rtol')
    if not regression:keys+=('code_commit','source_sha256')
    for key in keys:
        require(reference[key]==report[key],'TNS reference identity differs: '+key)
    for stage,count in COUNTS.items():
        require(len(reference[stage])==count and all(r['passed'] for r in reference[stage]),'missing/failed TNS cases')
        require([(r['rate'],r['index']) for r in reference[stage]]==[(rate,i) for rate in (48000,44100) for i in range(count//2)],
                'TNS reference reordered/duplicated/missing cases')


def portable(binary, report, reference):
    for stage in COUNTS:
        for rate in (48000,44100):
            first=0
            for batch in batch_cases(cases() if stage=='spectra' else sequences(),16):
                try:
                    with tempfile.TemporaryDirectory(prefix='tns-math-') as tmp:
                        root=Path(tmp)
                        groups=[(c['kind'],[c]) for c in batch] if stage=='spectra' else batch
                        generated=[packet(c,rate) for _,seq in groups for c in seq]
                        bundle(root/'packets',[p for p,_ in generated],rate)
                        summary,rows=inspect(binary,root/'packets',root,len(generated))
                        require(summary['tns_complete_packets']==len(generated),'missing complete TNS stages')
                        metrics=[check_expected(row['report'],truth,reference is None) for row,(_,truth) in zip(rows,generated)]
                        pcm=None
                        if stage=='pcm':
                            result=command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                            require(result['complete'] and result['experimental'] and not result['native_apis_used'],'PCM export identity')
                            require(result['tns_numeric_profile']==PROFILE,'PCM TNS profile differs')
                            meta=json.loads((root/'pcm/pcm.json').read_text(encoding='utf-8'))
                            implementation=meta['decoder_settings']['implementation']['value']
                            if report['implementation'] is None:report['implementation']=implementation
                            require(report['implementation']==implementation,'candidate implementation changed')
                            require(implementation['tns_tables_sha256']==report['constant_model_sha256'],'candidate TNS constants differ')
                            pcm=(root/'pcm/pcm.f32le').read_bytes()
                            require(meta['frames']==len(generated)*1024 and meta['channels']==2 and meta['sample_rate']==rate
                                    and meta['all_finite'] and len(pcm)==len(generated)*8192 and hashlib.sha256(pcm).hexdigest()==meta['sha256'],
                                    'invalid PCM accounting')
                        cursor=0
                        for i,(kind,seq) in enumerate(groups):
                            n=len(seq);selected=[r['report'] for r in rows[cursor:cursor+n]]
                            identity=hashlib.sha256(cookie(rate))
                            for data,_ in generated[cursor:cursor+n]:
                                identity.update(len(data).to_bytes(8,'little'));identity.update(data)
                            record=dict(rate=rate,index=first+i,kind=kind,passed=True,input_sha256=identity.hexdigest(),**fingerprints(selected))
                            if reference is None:
                                record['spectral_metrics']=metrics[cursor:cursor+n]
                            if pcm is not None:
                                raw=pcm[cursor*8192:(cursor+n)*8192]
                                values=float32(struct.unpack('<'+str(len(raw)//4)+'f',raw))
                                record.update(frames=n*1024,pcm_sha256=hashlib.sha256(raw).hexdigest())
                                if reference is None:
                                    decoder=Decoder()
                                    wanted=[v for _,truth in generated[cursor:cursor+n] for v in decoder.decode(truth)]
                                    record.update(compare_pcm(values,wanted))
                            if reference:exact(record,reference[stage][len(report[stage])])
                            report[stage].append(record);cursor+=n
                except Exception as error:
                    report['errors'].append(dict(stage=stage,rate=rate,first_case=first,error=str(error)))
                first+=len(batch)
                if first%160==0:print('TNS',stage,rate,first,flush=True,file=sys.stderr)


def native_metrics(actual, expected, channel):
    bad=[(i,a,b) for i,(a,b) in enumerate(zip(actual,expected)) if not math.isfinite(a) or not math.isfinite(b)]
    if bad:
        i,a,b=bad[0]
        return dict(passed=False, nonfinite_samples=len(bad), first_failure=dict(channel=channel,coefficient_index=i,
                    candidate_bits=bytes_of([a],'f').hex(),reference_bits=bytes_of([b],'f').hex()))
    return compare_spectra(actual,expected,channel)


def check_native(rows, trace, diagnostic=False):
    require(not trace['errors'] and trace['process_exit_code'] in ((0,1) if diagnostic else (0,))
            and trace['packet_calls']==len(rows),'native TNS trace incomplete')
    reads={(r['sequence'],r['channel_index']):r for r in trace['tns']}
    applies={(r['sequence'],r['channel_index']):r for r in trace['tns_apply']}
    boundaries={r['sequence']:r for r in trace['bwe_entries']}
    streams={(r['sequence'],r['channel_index']):r for r in trace['spectra']}
    require(len(reads)==len(trace['tns']) and len(applies)==len(trace['tns_apply']) and len(boundaries)==len(trace['bwe_entries']),
            'duplicate native TNS events')
    expected_keys={(i,c['channel_index']) for i,row in enumerate(rows) for c in row['report']['tns']}
    require(set(applies)==expected_keys and set(reads)=={(i,c['channel_index']) for i,row in enumerate(rows)
            for c in row['report']['tns'] if c['present']},'native TNS coverage differs')
    require(set(boundaries)=={i for i,row in enumerate(rows) if row['report']['tns_complete']},'native BWE2 coverage differs')
    records=[]
    for i,row in enumerate(rows):
        report=row['report']
        if not report['tns_complete']:continue
        end=boundaries[i]
        require(end['packet_sha256']==report['packet_sha256'] and end['bit_offset']==report['stop_bit_offset'],'BWE2 boundary mismatch')
        inputs=[];outputs=[];isolated=[]
        for channel,c in enumerate(report['tns']):
            event=applies[i,channel]
            require(event['packet_sha256']==report['packet_sha256'] and event['bit_offset']==report['stop_bit_offset'],'native TNS identity/entry mismatch')
            stream=streams[i,channel]
            require(stream['end_bit_offset']==report['channels'][channel]['end_bit_offset'],'upstream stream endpoint differs')
            short=report['channels'][channel]['ics']['block_type']==2
            require(event['full_band_count']==(14 if short else 49),'native full-band cursor differs')
            require(event['data']['present']==c['present'],'native TNS presence differs')
            if c['present']:
                read=reads[i,channel]
                require((read['start_bit_offset'],read['end_bit_offset'])==(c['start_bit_offset']+1,c['end_bit_offset']),'native TNS reader boundaries differ')
                require(read['data']==event['data'],'native TNS data changed before filter')
                require(len(c['windows'])==len(read['data']['windows']),'native window count differs')
                for wanted,actual in zip(c['windows'],read['data']['windows']):
                    require(len(wanted['filters'])==len(actual['filters']),'native filter count differs')
                    for f,g in zip(wanted['filters'],actual['filters']):
                        for key in ('length','order','direction','quantized'):require(f[key]==g[key],'native TNS parameter differs: '+key)
                        if f['order']:require(g['resolution']==wanted['resolution'],'native resolution differs')
            inputs.append(compare_spectra(float32(report['channels_after_cac'][channel]['scaled']),event['before'],channel))
            outputs.append(native_metrics(float32(report['channels_after_tns'][channel]['scaled']),event['after'],channel))
        # Isolate native TNS from upstream CAC rounding: filter the captured input
        # through the independent LPC oracle; never use this as portable truth.
        expected=filtered(report,[applies[i,c]['before'] for c in (0,1)])
        for c in (0,1):isolated.append(native_metrics(applies[i,c]['after'],expected[c],c))
        records.append(dict(sequence=i,packet_sha256=report['packet_sha256'],integer_boundary_passed=True,
                            tns_present=[c['present'] for c in report['tns']],input_metrics=inputs,output_metrics=outputs,
                            isolated_tns_metrics=isolated,numeric_passed=all(m['passed'] for m in outputs),
                            isolated_numeric_passed=all(m['passed'] for m in isolated),native_replay_exit_code=trace['process_exit_code'],
                            native_batch_packets=trace['packet_calls']))
    return records


def native_artificial(binary, report):
    for rate in (48000,44100):
        first=0
        # Replay validates PCM in blocks of eight packets. Bound dense stress to
        # one such block: all eight TNS/BWE2 returns are captured even if PCM is
        # nonfinite. A missing return is still a hard failure, never a skipped case.
        batches=(batch for dense,group in itertools.groupby(cases(),key=lambda c:c['kind']=='dense_stress')
                 for batch in batch_cases(group,8 if dense else 64))
        for batch in batches:
            with tempfile.TemporaryDirectory(prefix='tns-native-') as tmp:
                root=Path(tmp);generated=[packet(c,rate) for c in batch]
                bundle(root/'packets',[p for p,_ in generated],rate)
                _,rows=inspect(binary,root/'packets',root,len(batch))
                try:
                    diagnostic=batch[0]['kind']=='dense_stress'
                    trace=trace_bundle(binary,root/'packets',root,True,cac=True,tns=True,allow_replay_failure=diagnostic)
                    checked=check_native(rows,trace,diagnostic)
                    require(len(checked)==len(batch),'missing native artificial TNS')
                    for i,(case,record) in enumerate(zip(batch,checked)):
                        record.update(rate=rate,index=first+i,kind=case['kind'])
                        record['native_trace_sha256']=sha256_file(root/'native-boundaries.json')
                        if case['kind']!='dense_stress':
                            require(record['numeric_passed'] and record['isolated_numeric_passed'],'bounded native TNS differs: '+json.dumps(record))
                        report['native_artificial'].append(record)
                except Exception:
                    # Keep the exact original snapshot on failure, bounded by the
                    # same 128 MiB artifact budget; do not silently drop cases.
                    path=root/'native-boundaries.json'
                    if path.exists():
                        failure=ROOT/'reports'/('tns-native-failure-'+report['started_utc'].replace(':','')+f'-{rate}-{first}.json')
                        require(path.stat().st_size<LIMIT,'native failure artifact exceeds budget')
                        failure.write_bytes(path.read_bytes())
                    raise
            first+=len(batch)
            print('TNS native',rate,first,flush=True,file=sys.stderr)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    references=parser.add_mutually_exclusive_group()
    references.add_argument('--reference-report',type=Path)
    references.add_argument('--regression-report',type=Path)
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--native-only',action='store_true',help='diagnostic native run; never qualifies as portable acceptance')
    parser.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing to overwrite report')
    if (args.native or args.native_only) and sys.platform!='darwin':parser.error('native snapshots require macOS')
    binary=args.binary.resolve(strict=True)
    report=dict(schema_version=1,numeric_profile=PROFILE,code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,encoding='utf-8').strip(),
                tested_worktree_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,encoding='utf-8').strip()),
                source_sha256=source_digest(),tables_sha256=sha256_file(DESTINATION),
                constant_model_sha256=json.loads(DESTINATION.read_text())['tables_sha256'],
                upstream_constants={name:sha256_file(ROOT/'data'/name) for name in ('sq-math-v2.json','cac-math-v1.json','sq-codebooks.json','cac-codebooks.json')},
                tool_sha256=sha256_file(binary),platform=platform.platform(),architecture=platform.machine(),python=sys.version,
                mode='native_diagnostic' if args.native_only else 'bit_exact_replay' if args.reference_report else 'independent_math',
                implementation=None,atol=1e-6,rtol=1e-5,spectra=[],pcm=[],errors=[],native_artificial=[],real=[],
                started_utc=datetime.now(timezone.utc).isoformat())
    reference=None
    reference_path=args.reference_report or args.regression_report
    if args.regression_report:report['mode']='regression_replay'
    try:
        require(json.loads(DESTINATION.read_text())==document(),'TNS constants do not match high precision generation')
        if reference_path:
            require(reference_path.stat().st_size<=LIMIT,'reference report exceeds budget')
            report['reference_report_sha256']=sha256_file(reference_path)
            reference=json.loads(reference_path.read_text(encoding='utf-8'));validate_reference(reference,report,bool(args.regression_report))
            report['reference_code_commit']=reference['code_commit']
        if not args.native_only:portable(binary,report,reference)
        if args.native or args.native_only:
            report['component_sha256']=COMPONENT_SHA256
            native_artificial(binary,report)
            native_real(binary,args.replay_baseline,report,inspect_fn=inspect,check_fn=check_native,tns=True)
            require(len(report['native_artificial'])==COUNTS['spectra'] and len(report['real'])==30,'incomplete native TNS coverage')
        require(source_digest()==report['source_sha256'] and sha256_file(binary)==report['tool_sha256'],'sources or binary changed during acceptance')
        if reference_path:require(sha256_file(reference_path)==report['reference_report_sha256'],'reference report changed')
    except Exception as error:report['errors'].append(dict(stage='validation',error=str(error)))
    report['counts']={stage:len(report[stage]) for stage in COUNTS}
    report['passed']=not report['errors'] and (args.native_only or report['counts']==COUNTS) and all(r['passed'] for stage in COUNTS for r in report[stage]) and all(r['passed'] for r in report['real'])
    report['pcm_metrics']=dict(max_absolute_error=max((r.get('max_absolute_error',0.) for r in report['pcm']),default=0),max_ulp=max((r.get('max_ulp',0) for r in report['pcm']),default=0),failed_samples=sum(r.get('failed_samples',0) for r in report['pcm'])) if reference is None else None
    report['native_float_failures']=sum(not r['numeric_passed'] for r in report['native_artificial'])
    report['native_float_policy']='All integer/boundary checks and bounded unit/boundary floats are required. Dense high-order stress remains a separate diagnostic.'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode())<LIMIT,'report exceeds 128 MiB')
    write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('passed','counts','pcm_metrics','native_float_failures','errors')}))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
