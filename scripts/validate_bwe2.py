#!/usr/bin/env python3
"""BWE2 wire, independent Decimal mathematics, and exact portable acceptance."""
import argparse
import copy
from datetime import datetime,timezone
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

from generate_bwe2_math import PROFILE, DESTINATION, document
from bwe2_vectors import cases, packet, sequences
from bwe2_oracle import Decoder, expanded, conditioned_transform, FORMAT
from spectrum_vectors import bundle, cookie
from native_frame_trace import COMPONENT_SHA256, trace_bundle
from validate import require, write_json
from validate_cac import digest, native_real
from validate_tns import (check_expected as check_tns, fingerprints as tns_fingerprints,
                          exact as tns_exact, spectral_metrics, native_metrics)
from validate_portable import ROOT, LIMIT, bytes_of, float32, compare_pcm, source_digest
from validate_replay import command, sha256_file
from validate_spectra import coverage
from validate_synthesis import batch_cases

COUNTS=dict(spectra=8218,pcm=8218)
MANIFEST=json.loads((ROOT/'data/bwe2-vectors-v2.json').read_text())
HARD_NATIVE={'boundary','reuse_shape','right_only','left_only','gain_control','window_control','zero_source'}


def inspect(binary,directory,root,count):
    path=root/'bwe2.jsonl'
    summary=command(binary,'parse-packets',directory,'--depth','bwe2','--packets',count,'--output',path,allowed=(1,2))
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    failures=[dict(packet_index=r['packet_index'],error=r['error']) for r in rows if r['status']=='error']
    require(not failures,'BWE2 parser error: '+json.dumps(failures[:2]))
    require(summary['complete'] and summary['errors']==0 and len(rows)==count,'incomplete BWE2 reports')
    require(not path.with_name(path.name+'.incomplete').exists(),'BWE2 failure marker')
    for row in rows:coverage(row['report'])
    return summary,rows


def check_expected(report,truth,math=True):
    previous=dict(report,stop_bit_offset=truth['tns_end_bit_offset'])
    check_tns(previous,truth,math)
    require(report['bwe2_complete'] and report['bwe2_numeric_profile']==PROFILE,'wrong BWE2 completion/profile')
    require(report['bwe2']==truth['bwe2'],'BWE2 parameters, origins or boundaries differ')
    require(report['stop_bit_offset']==truth['bwe2']['end_bit_offset'],'BWE2 stop is not core alignment')
    require(report['bwe2_format_sha256']==FORMAT['tables_sha256'],'BWE2 format dictionary differs')
    require(report['bwe2_stage']=='scaled_after_bwe2_before_synthesis','wrong BWE2 stage')
    require([c['channel_index'] for c in report['channels_after_bwe2']]==[0,1],'BWE2 output channels differ')
    values=[float32(c['scaled']) for c in report['channels_after_bwe2']]
    for ch,(c,ranges) in enumerate(zip(report['channels_after_bwe2'],truth['bwe2_regions'])):
        require(c['regions']==ranges,'BWE2 copy geometry differs')
        affected={i for r in ranges for i in range(r['target_start_line'],r['target_end_line'])}
        before=float32(report['channels_after_tns'][ch]['scaled'])
        require(bytes_of([v for i,v in enumerate(values[ch]) if i not in affected],'f')==
                bytes_of([v for i,v in enumerate(before) if i not in affected],'f'),'BWE2 changed an unmodified frequency')
        active=truth['bwe2']['channels'][ch]['active']
        size=128 if truth['channels'][ch]['ics']['block_type']==2 else 1024
        cutoff=ranges[0]['target_start_line'] if ranges else 0
        energy=any(v!=0 for w in range(1024//size) for v in before[w*size:w*size+cutoff])
        require(c['processing_applied']==bool(active and energy),'BWE2 zero-energy processing differs')
        require((c['analysis'] is not None)==c['processing_applied'],'BWE2 analysis completion differs')
    if math:
        metrics=spectral_metrics(values,expanded(truth),truth)
        require(metrics['passed'],'BWE2 mathematics differs: '+json.dumps(metrics))
        return metrics
    return None


def fingerprints(reports):
    return dict(**tns_fingerprints(reports),bwe2_sha256=digest([r['bwe2'] for r in reports]),
                bwe2_analysis_sha256=digest([[{k:v for k,v in c.items() if k!='scaled'} for c in r['channels_after_bwe2']] for r in reports]),
                expanded_sha256=hashlib.sha256(bytes_of([v for r in reports for c in r['channels_after_bwe2'] for v in float32(c['scaled'])],'f')).hexdigest())


def exact(actual,expected):
    tns_exact(actual,expected)
    for key in ('bwe2_sha256','bwe2_analysis_sha256','expanded_sha256'):
        require(actual[key]==expected[key],'BWE2 fingerprint mismatch: '+key)


def validate_reference(reference,report):
    require(reference.get('passed') is True and reference.get('mode')=='independent_math','requires successful BWE2 mathematics')
    require(reference.get('counts')==COUNTS and reference.get('errors')==[],'incomplete BWE2 reference')
    for key in ('schema_version','code_commit','source_sha256','numeric_profile','tables_sha256','format_sha256','vector_manifest_sha256','upstream_constants','atol','rtol'):
        require(reference[key]==report[key],'BWE2 reference identity differs: '+key)
    for stage,count in COUNTS.items():
        require(len(reference[stage])==count and all(r['passed'] for r in reference[stage]),'missing/failed BWE2 cases')
        require([(r['rate'],r['index']) for r in reference[stage]]==[(rate,i) for rate in (48000,44100) for i in range(count//2)],
                'reordered/duplicated/missing BWE2 cases')


def portable(binary, report, reference):
    for stage in COUNTS:
        for rate in (48000,44100):
            first=0
            for batch in batch_cases(cases() if stage=='spectra' else sequences(),16):
                try:
                    with tempfile.TemporaryDirectory(prefix='bwe2-math-') as tmp:
                        root=Path(tmp)
                        groups=[(c['kind'],[c]) for c in batch] if stage=='spectra' else batch
                        generated=[packet(c,rate) for _,seq in groups for c in seq]
                        bundle(root/'packets',[p for p,_ in generated],rate)
                        summary,rows=inspect(binary,root/'packets',root,len(generated))
                        require(summary['bwe2_complete_packets']==len(generated),'missing complete BWE2 stages')
                        metrics=[check_expected(row['report'],truth,reference is None) for row,(_,truth) in zip(rows,generated)]
                        pcm=None
                        if stage=='pcm':
                            result=command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                            require(result['complete'] and result['experimental'] and not result['native_apis_used'],'PCM export identity')
                            require(result['bwe2_numeric_profile']==PROFILE,'PCM BWE2 profile differs')
                            meta=json.loads((root/'pcm/pcm.json').read_text(encoding='utf-8'))
                            implementation=meta['decoder_settings']['implementation']['value']
                            if report['implementation'] is None:report['implementation']=implementation
                            require(report['implementation']==implementation,'candidate implementation changed')
                            require(implementation['bwe2_tables_sha256']==report['constant_model_sha256'],'candidate BWE2 constants differ')
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
                            frozen=MANIFEST[stage][len(report[stage])]
                            require(frozen['packets']==n and all(record[k]==frozen[k] for k in ('rate','index','kind','input_sha256')),'frozen BWE2 input identity differs')
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
                if first%160==0:print('BWE2',stage,rate,first,flush=True,file=sys.stderr)


def native_conditioned_lsf(indices):
    def f32(v):return struct.unpack('<f',struct.pack('<f',v))[0]
    a,b=(FORMAT['lsf_codebooks_f32'][i][j] for i,j in enumerate(indices))
    values=[f32(struct.unpack('<f',x.to_bytes(4,'little'))[0]+struct.unpack('<f',y.to_bytes(4,'little'))[0]) for x,y in zip(a,b)]
    minimum=50.
    for i in range(16):values[i]=max(values[i],minimum);minimum=f32(values[i]+50.)
    if values[-1]>11950:
        maximum=11950.
        for i in range(15,-1,-1):values[i]=min(values[i],maximum);maximum=f32(values[i]-50.)
    return values


def locate(metrics,ics):
    if metrics.get('first_failure'):
        f=metrics['first_failure'];size=128 if ics['block_type']==2 else 1024
        f['window'],f['frequency_line']=divmod(f['coefficient_index'],size)
    return metrics


def check_native(rows,trace,diagnostic=False):
    require(not trace['errors'] and trace['process_exit_code'] in ((0,1) if diagnostic else (0,))
            and trace['packet_calls']==len(rows),'native BWE2 trace incomplete')
    reads={r['sequence']:r for r in trace['bwe2_reads']}
    applies={(r['sequence'],r['channel_index']):r for r in trace['bwe2_apply']}
    prior={(r['sequence'],r['channel_index']):r for r in trace['tns_apply']}
    boundaries={r['sequence']:r for r in trace['bwe_entries']}
    required={i for i,row in enumerate(rows) if row['report']['bwe2_complete']}
    require(set(reads)==required and len(reads)==len(trace['bwe2_reads']),'native BWE2 reader coverage differs')
    expected={(i,c['channel_index']) for i,row in enumerate(rows) for c in (row['report']['bwe2'] or {}).get('channels',[]) if c['active']}
    require(set(applies)==expected and len(applies)==len(trace['bwe2_apply']),'native BWE2 active-channel coverage differs')
    results=[]
    for i,row in enumerate(rows):
        r=row['report']
        if not r['bwe2_complete']:continue
        b=r['bwe2'];read=reads[i]
        require(read['packet_sha256']==r['packet_sha256'] and boundaries[i]['packet_sha256']==r['packet_sha256'] and boundaries[i]['bit_offset']==b['start_bit_offset'],
                'native TNS/BWE2 identity or boundary differs')
        require((read['start_bit_offset'],read['end_bit_offset'])==(b['start_bit_offset'],b['end_bit_offset']),
                'native BWE2 payload endpoints differ')
        require(read['data']['active']==[c['active'] for c in b['channels']],'native BWE2 active flags differ')
        inputs=[];outputs=[];native_inputs=[];native_outputs=[];lpc_metrics=[];conditioned=[];unavailable=[]
        for ch,c in enumerate(b['channels']):
            ics=r['channels'][ch]['ics'];before=prior[i,ch]['after']
            require(prior[i,ch]['packet_sha256']==r['packet_sha256'],'native TNS snapshot identity differs')
            after=before;conditioned_output=before
            if c['active']:
                p=c['parameters'];event=applies[i,ch]
                require(event['packet_sha256']==r['packet_sha256'] and event['bit_offset']==b['end_bit_offset'], 'native BWE2 apply identity differs')
                require(event['ics']['block_type']==ics['block_type'] and event['ics']['max_sfb']==ics['max_sfb']
                        and event['ics']['active_window_groups']==ics['window_groups'],'native BWE2 ICS mapping differs')
                require(bytes_of(event['before'],'f')==bytes_of(before,'f'),'native BWE2 input is not TNS output')
                require(read['data']['lsf_indices'][ch]==p['lsf_indices'],'native BWE2 LSF indices differ')
                require(read['data']['gain_indices'][ch][:len(p['gain_indices'])]==p['gain_indices'],'native BWE2 group gains differ')
                after=event['after']
                size=128 if ics['block_type']==2 else 1024;cutoff=r['channels_after_bwe2'][ch]['regions'][0]['target_start_line']
                processed=any(v!=0 for w in range(1024//size) for v in before[w*size:w*size+cutoff])
                if processed:
                    require(bytes_of(event['conditioned_lsf'],'f')==bytes_of(native_conditioned_lsf(p['lsf_indices']),'f'),
                            'native LSF dictionary/conditioning mapping differs')
                analysis=r['channels_after_bwe2'][ch]['analysis']
                if analysis and processed:
                    lpc_metrics.append({name:native_metrics(analysis[name],event[name],ch) for name in ('source_lpc','target_lpc')})
                if processed:
                    if all(math.isfinite(v) for array in (before,event['source_lpc'],event['target_lpc']) for v in array):
                        try:
                            conditioned_output=conditioned_transform(before,ics,p,event['source_lpc'],event['target_lpc'],cutoff)
                        except ArithmeticError as error:
                            conditioned_output=None;unavailable.append(dict(channel=ch,reason=str(error)))
                    else:
                        conditioned_output=None;unavailable.append(dict(channel=ch,reason='nonfinite_native_input_or_lpc'))
            native_inputs.append(before);native_outputs.append(after)
            inputs.append(locate(native_metrics(float32(r['channels_after_tns'][ch]['scaled']),before,ch),ics))
            outputs.append(locate(native_metrics(float32(r['channels_after_bwe2'][ch]['scaled']),after,ch),ics))
            if conditioned_output is not None:
                conditioned.append(locate(native_metrics(after,conditioned_output,ch),ics))
        isolated=[];reason=None
        if all(math.isfinite(v) for values in native_inputs for v in values):
            wanted=expanded(r,native_inputs)
            isolated=[locate(native_metrics(native_outputs[ch],wanted[ch],ch),r['channels'][ch]['ics']) for ch in (0,1)]
        else:reason='nonfinite_upstream_input'
        results.append(dict(sequence=i,packet_sha256=r['packet_sha256'],integer_boundary_passed=True,
                            active=[c['active'] for c in b['channels']],input_metrics=inputs,output_metrics=outputs,
                            isolated_bwe2_metrics=isolated,isolated_unavailable_reason=reason,lpc_metrics=lpc_metrics,
                            conditioned_transform_metrics=conditioned,conditioned_unavailable=unavailable,
                            conditioned_transform_passed=len(conditioned)==2 and all(m['passed'] for m in conditioned),
                            numeric_passed=all(m['passed'] for m in outputs),
                            isolated_numeric_passed=all(m['passed'] for m in isolated) if isolated else None,
                            native_replay_exit_code=trace['process_exit_code'],native_batch_packets=trace['packet_calls']))
    return results


def native_artificial(binary,report):
    for rate in (48000,44100):
        first=0
        # Ordinary index/copy scans can share a process. Bound high-gain/combined
        # pressure to one replay block; retain any unexpectedly partial trace and
        # retry its full batch in blocks of eight, without dropping any case.
        batches=(batch for pressure,group in itertools.groupby(cases(),key=lambda c:c['kind'] in ('escape_pressure','combined_pressure','lpc_conditioning'))
                 for batch in batch_cases(group,8 if pressure else 64))
        for batch in batches:
            with tempfile.TemporaryDirectory(prefix='bwe2-native-') as tmp:
                root=Path(tmp);generated=[packet(c,rate) for c in batch]
                bundle(root/'packets',[p for p,t in generated],rate);_,rows=inspect(binary,root/'packets',root,len(batch))
                try:
                    trace=trace_bundle(binary,root/'packets',root,True,cac=True,tns=True,bwe2=True,allow_replay_failure=True)
                    trace_sha=sha256_file(root/'native-boundaries.json')
                    checked=[]
                    if trace['packet_calls']!=len(batch) and trace['process_exit_code']==1 and len(batch)>8:
                        saved=ROOT/'reports'/('bwe2-native-partial-'+report['started_utc'].replace(':','')+f'-{rate}-{first}.json')
                        require((root/'native-boundaries.json').stat().st_size<LIMIT,'partial native trace exceeds budget')
                        saved.write_bytes((root/'native-boundaries.json').read_bytes())
                        for offset in range(0,len(batch),8):
                            retry=root/f'retry-{offset}';retry.mkdir()
                            segment=generated[offset:offset+8];bundle(retry/'packets',[p for p,t in segment],rate)
                            _,retry_rows=inspect(binary,retry/'packets',retry,len(segment))
                            retraced=trace_bundle(binary,retry/'packets',retry,True,cac=True,tns=True,bwe2=True,allow_replay_failure=True)
                            verified=check_native(retry_rows,retraced,True)
                            for record in verified:record['native_trace_sha256']=sha256_file(retry/'native-boundaries.json')
                            checked.extend(verified)
                    else:
                        checked=check_native(rows,trace,True)
                    require(len(checked)==len(batch),'missing BWE2 native cases')
                    for j,(case,result) in enumerate(zip(batch,checked)):
                        result.update(rate=rate,index=first+j,kind=case['kind'])
                        result.setdefault('native_trace_sha256',trace_sha)
                        if case['kind'] in HARD_NATIVE:
                            require(result['conditioned_transform_passed'],'bounded native-LPC-conditioned BWE2 mismatch: '+json.dumps(result))
                        report['native_artificial'].append(result)
                except Exception:
                    path=root/'native-boundaries.json'
                    if path.exists():
                        target=ROOT/'reports'/('bwe2-native-failure-'+report['started_utc'].replace(':','')+f'-{rate}-{first}.json')
                        require(path.stat().st_size<LIMIT,'native snapshot exceeds budget');target.write_bytes(path.read_bytes())
                    raise
            first+=len(batch)
            print('BWE2 native',rate,first,flush=True,file=sys.stderr)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference-report',type=Path)
    parser.add_argument('--native',action='store_true');parser.add_argument('--native-only',action='store_true')
    parser.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing to overwrite report')
    if (args.native or args.native_only) and sys.platform!='darwin':parser.error('native BWE2 snapshots require macOS')
    binary=args.binary.resolve(strict=True)
    report=dict(schema_version=1,numeric_profile=PROFILE,code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,encoding='utf-8').strip(),
                tested_worktree_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,encoding='utf-8').strip()),
                source_sha256=source_digest(),tables_sha256=sha256_file(DESTINATION),format_sha256=sha256_file(ROOT/'data/bwe2-format-v1.json'),
                constant_model_sha256=json.loads(DESTINATION.read_text())['tables_sha256'],
                vector_manifest_sha256=sha256_file(ROOT/'data/bwe2-vectors-v2.json'),
                upstream_constants={name:sha256_file(ROOT/'data'/name) for name in ('sq-math-v1.json','cac-math-v1.json','tns-math-v1.json','sq-codebooks.json','cac-codebooks.json')},
                tool_sha256=sha256_file(binary),platform=platform.platform(),architecture=platform.machine(),python=sys.version,
                mode='native_diagnostic' if args.native_only else 'bit_exact_replay' if args.reference_report else 'independent_math',
                implementation=None,atol=1e-6,rtol=1e-5,spectra=[],pcm=[],errors=[],native_artificial=[],real=[],started_utc=datetime.now(timezone.utc).isoformat())
    reference=None
    try:
        require(MANIFEST['counts']==COUNTS,'BWE2 frozen counts differ')
        require(json.loads(DESTINATION.read_text())==document(),'BWE2 constants do not match high-precision generation')
        if args.reference_report:
            require(args.reference_report.stat().st_size<=LIMIT,'reference exceeds output budget')
            report['reference_report_sha256']=sha256_file(args.reference_report)
            reference=json.loads(args.reference_report.read_text(encoding='utf-8'));validate_reference(reference,report)
        if not args.native_only:portable(binary,report,reference)
        if args.native or args.native_only:
            report['component_sha256']=COMPONENT_SHA256;native_artificial(binary,report)
            native_real(binary,args.replay_baseline,report,inspect_fn=inspect,check_fn=check_native,tns=True,bwe2=True)
            require(len(report['native_artificial'])==COUNTS['spectra'] and len(report['real'])==30,'incomplete native BWE2 matrix')
        require(source_digest()==report['source_sha256'] and sha256_file(binary)==report['tool_sha256'],'sources or binary changed during acceptance')
        if args.reference_report:require(sha256_file(args.reference_report)==report['reference_report_sha256'],'reference report changed')
    except Exception as error:report['errors'].append(dict(stage='validation',error=str(error)))
    report['counts']={stage:len(report[stage]) for stage in COUNTS}
    report['passed']=not report['errors'] and (args.native_only or report['counts']==COUNTS) and all(r['passed'] for stage in COUNTS for r in report[stage]) and all(r['passed'] for r in report['real'])
    report['pcm_metrics']=dict(max_absolute_error=max((r.get('max_absolute_error',0.) for r in report['pcm']),default=0),max_ulp=max((r.get('max_ulp',0) for r in report['pcm']),default=0),failed_samples=sum(r.get('failed_samples',0) for r in report['pcm'])) if reference is None else None
    report['native_float_failures']=sum(not r['numeric_passed'] for r in report['native_artificial'])
    report['native_float_policy']='User-approved layered gate: all integer/boundary checks and well-excited controls using identical captured LPC inputs are required at the original tolerance. Full native and input-isolated float differences remain separate; portable truth never consumes native values.'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode())<LIMIT,'report exceeds 128 MiB');write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('passed','counts','pcm_metrics','native_float_failures','errors')}))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
