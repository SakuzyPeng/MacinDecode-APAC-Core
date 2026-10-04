#!/usr/bin/env python3
"""DRC-off structure/selection/timing proof, with separate native PCM diagnostics."""
import argparse
from datetime import datetime,timezone
import hashlib,json,struct,subprocess,sys
from pathlib import Path
from native_drc_trace import trace_bundle
from native_frame_trace import COMPONENT_SHA256
from validate_drc_off import inspect as off_proof, GATE_PROFILE
from validate_drc import workspace
from validate_portable import ROOT,LIMIT,source_digest,compare_pcm
from validate_replay import command,sha256_file
from validate import require,write_json
from validate_cac import pcm_stop
from packet_vectors import window,basis
from native_pcm import compare as compare_native_pcm, finalize as finalize_native_pcm
from drc_vectors import packet,bundle,pack


def compare_encoder_pcm(record,actual,native):
    compare_native_pcm(record,actual,native)


def native_parameters(rows,trace):
    require(len(rows)==len(trace['packets']),'native outer packet count differs')
    parameters=[]
    def visit(row,index,role):
        if row['embedded_preroll']:visit(row['embedded_preroll']['report'],index,'preroll')
        found=[e for e in trace['events'] if e['kind']=='gain_read' and e['sequence']==index and e['role']==role]
        require(len(found)==1,'missing native DRC gain frame')
        e=found[0];d=row['drc'];require(e['start']['buffer_sha256']==row['packet_sha256'],'native DRC belongs to a different frame');require(e['status']==0 and len(e['sequences'])==1,'native DRC parser failed')
        require(e['native_timing_mode_word']==1,'unknown APAC DRC time mode')
        require(e['start']['relative_bit_offset']==d['header_end_bit_offset'] and e['end']['relative_bit_offset']==d['end_bit_offset'],'native gain boundary differs')
        require(e['sequences'][0]['coding_mode']==d['coding_mode'],'native DRC coding mode differs')
        if d['coding_mode']:require(e['sequences'][0]['frame_end']==d['frame_end'],'native DRC frame-end differs')
        require(e['sequences'][0]['nodes']==[dict(gain_db=n['gain_eighth_db']/8,slope=0.,time=n['time']) for n in d['nodes']],'native DRC integer nodes differ')
        ancillary=[e for e in trace['packet_events'] if e['kind']=='ancillary' and e['sequence']==index and e['role']==role]
        require(len(ancillary)==1 and ancillary[0]['status']==0,'native ancillary failed')
        tail=row['packet_tail'];require(ancillary[0]['start']['relative_bit_offset']==tail['ancillary_start_bit_offset'] and ancillary[0]['end']['relative_bit_offset']==tail['ancillary_end_bit_offset'],'native ancillary boundary differs')
        parameters.append(dict(sequence=index,role=role,sha256=row['packet_sha256'],drc=d,tail=tail))
    for i,row in enumerate(rows):
        require(trace['packets'][i]['packet_sha256']==row['packet_sha256'],'native packet identity differs')
        visit(row,i,'current')
    require(not any(e['kind']=='gain_reset' for e in trace['events']),'native concealed malformed DRC')
    return dict(frames=len(parameters),sha256=hashlib.sha256(json.dumps(parameters,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def inspect_bundle(binary,directory,root,frames,record=None,require_nonzero=True):
    if record is None:record={}
    command(binary,'parse-packets',directory,'--depth','packet','--packets',150,'--output',root/'packets.jsonl')
    rows=[json.loads(l)['report'] for l in (root/'packets.jsonl').read_text(encoding='utf-8').splitlines()]
    trace=trace_bundle(binary,directory,root/'native',frames=frames)
    native=root/'native/native-pcm';replay=json.loads((native/'replay.json').read_text(encoding='utf-8'));pcm=json.loads((native/'pcm.json').read_text(encoding='utf-8'))
    proof=off_proof(trace,replay,pcm,(native/'pcm.f32le').read_bytes(),'drc-off',require_nonzero=require_nonzero)
    require(proof['passed'],'explicit off kernel, selection or timing proof failed')
    require(all(e['before']==e['after'] for e in trace['packet_events'] if e['kind']=='scene'),'qualified scene changed PCM')
    record.update(off_proof=proof,native_parameters=native_parameters(rows,trace))
    decoded=command(binary,'decode-sq',directory,'--out',root/'rust-pcm','--frames',frames)
    full=(root/'rust-pcm/pcm.f32le').read_bytes()
    require(decoded['saved_frames']==pcm['frames'] and decoded['range']['start_frame']==pcm['start_frame'],'native PCM timeline differs')
    compare_encoder_pcm(record,full,(native/'pcm.f32le').read_bytes())
    record['distinct_gain_nodes']=sorted({n['gain_eighth_db'] for row in rows for n in row['drc']['nodes']})
    record['decoded']=decoded
    return record,rows,full


def controls(binary,report):
    for rate in (48000,44100):
        for profile in ('default','music','speech','movie','capture'):
            for signal in ('noise','impulse'):
                label=f'{rate}-{profile}-{signal}';record=dict(rate=rate,profile=profile,signal=signal,passed=False);report['controls'].append(record)
                try:
                    with workspace(report,label,retain=lambda: not record.get('pcm_metrics',{}).get('passed',True)) as root:
                        extra=[] if profile=='default' else ['--drc-configuration',profile]
                        command(binary,'fixture','--out',root/'fixture','--sample-rate',rate,'--duration','0.125','--signals',signal,*extra)
                        source=root/'fixture'/signal;fixture=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
                        actual=fixture['actual_encoder_settings']['cdrc'];record['encoder']=actual
                        require(actual['error'] is None and actual['value']=={'default':4294967295,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder readback differs')
                        if profile!='default':require(fixture['drc_configuration_verified'],'explicit encoder setting unverified')
                        command(binary,'dump',source/'encoded.caf','--out',root/'packets','--start-packet',0,'--packets',150)
                        manifest=json.loads((root/'packets/manifest.json').read_text(encoding='utf-8'));frames=manifest['file']['packet_table']['value']['valid_frames']
                        inspected,rows,full=inspect_bundle(binary,root/'packets',root,frames,record);record.update(inspected)
                        # Unmodified/default playback is a separate diagnostic, never the off proof.
                        default=command(binary,'replay',root/'packets','--out',root/'default','--frames',frames)
                        raw=(root/'default/pcm.f32le').read_bytes();record['implicit_default']=dict(pcm_sha256=hashlib.sha256(raw).hexdigest(),settings=default['decoder_settings'],metrics=compare_pcm(struct.unpack('<'+str(len(full)//4)+'f',full),struct.unpack('<'+str(len(raw)//4)+'f',raw)))
                        indices=[json.loads(l) for l in (root/'packets/packets.jsonl').read_text().splitlines()]
                        refresh=next((i for i,r in enumerate(rows) if i and r['embedded_preroll']),len(rows)//2)
                        record['windows']=[]
                        for name,target in [('start',0),('middle',len(rows)//2),('refresh',refresh),('tail',max(0,len(rows)-3))]:
                            roll=indices[target]['roll_distance']['value'] if target else 0
                            require(isinstance(roll,int) and roll>=0,'target dependency missing');start=target-min(target,roll)
                            while start and (not indices[start]['dependency']['value']['independently_decodable'] or indices[start]['dependency']['value']['preroll_packet_count']>target-start):start-=1
                            window(root/'packets',root/name,start,target,min(len(rows),target+3))
                            selected=command(binary,'decode-sq',root/name,'--out',root/(name+'-pcm'))
                            raw=(root/(name+'-pcm')/'pcm.f32le').read_bytes();first=selected['range']['start_frame']-record['decoded']['range']['start_frame'];count=selected['saved_frames']
                            require(raw==full[first*8:(first+count)*8],'DRC seek window differs from continuous PCM')
                            record['windows'].append(dict(kind=name,frames=count,pcm_sha256=hashlib.sha256(raw).hexdigest(),passed=True))
                        record['passed']=True
                except Exception as error:report['errors'].append(dict(control=label,error=str(error)))
                print('DRC native control',label,record['passed'],file=sys.stderr,flush=True)
    require(len(report['controls'])==20 and all(c['passed'] for c in report['controls']),'missing or failed encoder controls')
    require(any(len(c['distinct_gain_nodes'])>1 for c in report['controls']),'controls never exercised changing gains')


def state_controls(binary,report):
    from drc_pcm_vectors import sequences,packet as written_packet,bundle as written_bundle
    from packet_vectors import identity
    report['state_controls']=[]
    for rate in (48000,44100):
        prerolls=0
        for index,(kind,options,seq) in enumerate(sequences()):
            selected=kind=='metadata_updates' or (kind=='preroll' and options.get('rich') and prerolls<2)
            if not selected:continue
            prerolls+=int(kind=='preroll')
            item=dict(rate=rate,index=index,kind=kind,passed=False)
            with workspace(report,f'state-{rate}-{index}',retain=lambda: not item.get('pcm_metrics',{}).get('passed',True)) as root:
                payloads=[written_packet(c,rate,options)[0] for c in seq]
                written_bundle(root/'packets',payloads,rate,**options)
                item['input_sha256']=identity((root/'packets/cookie.bin').read_bytes(),payloads)
                report['state_controls'].append(item)
                inspect_bundle(binary,root/'packets',root,1024*len(seq),item)
                item['passed']=True
    require(len(report['state_controls'])==8,'metadata/preroll controls missing')


def recovery(binary,report):
    with workspace(report,'concealed-node-count') as root:
        good,_=packet(dict(basis((0,0),gain=220)))
        bad=pack('01000000'+'0'+'1'+'0'*256+'1'+'0'*16)
        end,_=packet(dict(absent=True));bundle(root/'packets',[good,bad,end],rich=True)
        trace=trace_bundle(binary,root/'packets',root/'native',frames=3072)
        failures=[e for e in trace['events'] if e['kind']=='gain_read' and e['status']!=0]
        require(len(failures)==1 and failures[0]['sequence']==1 and any(e['kind']=='gain_reset' and e['sequence']==1 for e in trace['events']),'native recovery control was not triggered')
        require(any(e['kind']=='payload' and e['sequence']==1 and e['status']==0 for e in trace['events']),'native wrapper did not conceal the gain error')
        native=root/'native/native-pcm';replay=json.loads((native/'replay.json').read_text());pcm=json.loads((native/'pcm.json').read_text())
        rejected=off_proof(trace,replay,pcm,(native/'pcm.f32le').read_bytes(),'drc-off')
        require(not rejected['passed'] and rejected['gain_resets']==1,'acceptance gate accepted native concealment')
        error=command(binary,'decode-sq',root/'packets','--out',root/'rust-pcm',allowed=(1,))
        require(error['error']['packet_index']==1 and 'drc-node-count' in error['error']['message'],'Rust did not reject malformed DRC at the correct packet')
        require((root/'rust-pcm/.incomplete.json').exists() and not (root/'rust-pcm/pcm.json').exists(),'failed PCM was marked complete')
        report['recovery_control']=dict(passed=True,native_gain_status=failures[0]['status'],native_gain_end=failures[0]['end'],native_wrapper_success=True,off_gate_rejected=True,rust_error=error['error'])


def representatives(binary,report,baseline):
    previous=json.loads(baseline.read_text(encoding='utf-8'));require(previous['passed'] and len(previous['representatives'])==15,'requires 15 verified representative ranges')
    for index,spec in enumerate(previous['representatives']):
        item={}
        with workspace(report,'representative-'+str(index),retain=lambda: not item.get('pcm_metrics',{}).get('passed',True)) as root:
            target=spec['dump']['replay_window']
            command(binary,'dump',spec['source'],'--out',root/'packets','--with-preroll','--start-packet',target['requested_start_packet'],'--packets',target['requested_packets'])
            manifest=json.loads((root/'packets/manifest.json').read_text());frames=max(1,(target['target_raw_end']-target['target_raw_start']))
            if spec['channels']==2:
                item,_,_=inspect_bundle(binary,root/'packets',root,frames,require_nonzero=False)
                item.update(channels=2,target_packet=target['requested_start_packet'],passed=True)
            else:
                decoded=command(binary,'decode-sq',root/'packets','--out',root/'unsupported',allowed=(1,))
                require(decoded['error']['operation']=='SQ decoder' and 'unsupported configuration' in decoded['error']['message'],'unexplained representative stop')
                item=dict(channels=spec['channels'],target_packet=target['requested_start_packet'],stop_reason=pcm_stop(decoded),error=decoded['error'],passed=True)
            report['representatives'].append(item)
        print('DRC representative',index,'passed',file=sys.stderr,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--require-native-pcm',action='store_true',help='Require native PCM compatibility as well; mismatch exits 2, without redefining independent math.')
    p.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=p.parse_args();require(sys.platform=='darwin' and args.binary.is_file() and not args.report.exists(),'requires macOS binary and fresh report')
    report=dict(schema_version=1,passed=False,mode='native_diagnostic',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),component_sha256=COMPONENT_SHA256,
                native_bridge_sha256=sha256_file(ROOT/'native/audio_toolbox.c'),rules_version='apac-native-drc-off-v2',gate_profile=GATE_PROFILE,require_native_pcm=args.require_native_pcm,
                started_at=datetime.now(timezone.utc).isoformat(),controls=[],representatives=[],errors=[],
                failure_directory=str(args.report.with_suffix('.failures')))
    try:
        controls(args.binary.resolve(),report);state_controls(args.binary.resolve(),report);recovery(args.binary.resolve(),report);representatives(args.binary.resolve(),report,args.replay_baseline)
        require(source_digest()==report['source_sha256'] and sha256_file(args.binary)==report['binary_sha256'],'source or binary changed during acceptance')
        require(len(report['representatives'])==15,'representative ranges missing');report['passed']=True
    except Exception as error:report['errors'].append(dict(error=str(error)))
    records=report['controls']+report.get('state_controls',[])+report['representatives']
    exit_code=finalize_native_pcm(report,records)
    require(len(json.dumps(report).encode())<=LIMIT,'native report exceeds 128 MiB');write_json(args.report,report)
    print(json.dumps(dict(passed=report['passed'],qualification=report['qualification'],independent_math_verified=False,structural_passed=report['structural_passed'],native_pcm_comparison=report['native_pcm_comparison'],controls=len(report['controls']),representatives=len(report['representatives']),errors=report['errors'])))
    return exit_code

if __name__=='__main__':raise SystemExit(main())
