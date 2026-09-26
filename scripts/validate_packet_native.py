#!/usr/bin/env python3
"""Hash-gated native state/boundary controls and no-DRC encoder packet windows."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile

from native_packet_trace import trace_bundle
from native_frame_trace import COMPONENT_SHA256
from packet_vectors import sequences,packet,bundle,cookie,identity,window
from validate import require,write_json
from validate_packets import check_expected,coverage,PROFILE
from validate_portable import ROOT,LIMIT,source_digest,compare_pcm
from validate_replay import command,sha256_file
from validate_cac import digest,pcm_stop


@contextmanager
def workspace(report,label):
    with tempfile.TemporaryDirectory(prefix='apac-native-state-') as tmp:
        root=Path(tmp)
        try:yield root
        except Exception:
            target=report['failure_directory']/label;target.mkdir(parents=True)
            selected=[root/name for name in ('packets','native-state.json','native-pcm','rust-pcm') if (root/name).exists()]
            size=sum(p.stat().st_size for item in selected for p in (item.rglob('*') if item.is_dir() else [item]) if p.is_file())
            require(size<=LIMIT,'native failure snapshot exceeds retention budget')
            for item in selected:
                if item.is_dir():shutil.copytree(item,target/item.name)
                else:shutil.copyfile(item,target/item.name)
            raise


def encoded_absence(report):
    return next(f['value'] for f in report['fields'] if f['name']=='components[0].tce[0].present') is False


def inspect(binary,directory,root,count):
    path=root/'packet-report.jsonl'
    result=command(binary,'parse-packets',directory,'--depth','packet','--packets',count,'--output',path)
    require(result['packet_complete_packets']==count and result['errors']==0,'qualified packet did not complete')
    rows=[json.loads(line)['report'] for line in path.read_text(encoding='utf-8').splitlines()]
    require(len(rows)==count,'packet report count')
    for row in rows:coverage(row)
    return rows


def native_checks(rows,trace):
    require(not trace['errors'] and trace['process_exit_code']==0 and not trace['pending_returns'],'incomplete native state trace')
    require(len(rows)==len(trace['packets']),'native source-packet count differs')
    events=trace['events'];expected={};records=[]
    for i,r in enumerate(rows):
        require(trace['packets'][i]['packet_sha256']==r['packet_sha256'] and trace['packets'][i]['status']==0,'native packet identity/status differs')
        expected[i,'current']=r
        if r['embedded_preroll'] is not None:expected[i,'preroll']=r['embedded_preroll']['report']
    groups=defaultdict(list)
    for e in events:groups[e['sequence'],e['role']].append(e)
    require(set(groups)==set(expected),'missing/unassociated native frame events')
    overlap={}
    for e in events:
        if e['kind']!='synthesis':continue
        channel=e['channel'];before=e['overlap_before']
        require(all(math.isfinite(v) for key in ('input','output','overlap_before','overlap_after') for v in e[key]),'nonfinite native state control')
        if channel in overlap:require(before==overlap[channel],'native overlap was unexpectedly reset or replaced')
        else:require(not any(before),'native decoder did not begin with cleared overlap')
        overlap[channel]=e['overlap_after']
    for key,r in expected.items():
        own=groups[key]
        def one(kind):
            found=[e for e in own if e['kind']==kind]
            require(len(found)==1,'missing/duplicate native '+kind)
            return found[0]
        core=one('core');parsed=one('deserialize');tools=one('core_tools');tail=one('ancillary')
        require(core['start']['buffer_sha256']==r['packet_sha256'],'native reader belongs to wrong current/internal frame')
        require(core['start']['relative_bit_offset']==r['derived']['core_frame_start_bit'],'native core start differs')
        require(parsed['end']['relative_bit_offset']==r['packet_tail']['core_end_bit_offset'],'native core end differs')
        require(tail['start']['relative_bit_offset']==r['packet_tail']['ancillary_start_bit_offset']
                and tail['end']['relative_bit_offset']==r['packet_tail']['ancillary_end_bit_offset'],'native ancillary boundary differs')
        absent=encoded_absence(r)
        payload_end=(next(f['bit_offset']+f['bit_length'] for f in r['fields'] if f['name']=='components[0].tce[0].present')
                     if absent else r['bwe2']['end_bit_offset'])
        require(tools['start']['relative_bit_offset']==tools['end']['relative_bit_offset']==payload_end,'unexpected core extension bits')
        bwe=[e for e in own if e['kind']=='bwe2']
        require(len(bwe)==(0 if absent else 1),'BWE2 called for wrong element presence')
        if not absent:
            require(bwe[0]['start']['relative_bit_offset']==r['bwe2']['start_bit_offset']
                    and bwe[0]['end']['relative_bit_offset']==r['bwe2']['end_bit_offset'],'native BWE2 stream boundary differs')
        resets=[e for e in own if e['kind']=='cpe_reset']
        require(len(resets)==int(absent),'wrong CPE reset count')
        if absent:
            require(all(not any(c['scaled']) and c['ics']==dict(block_type=0,max_sfb=0,active_group_count=0,active_window_groups=[])
                        for c in resets[0]['after']),'native CPE reset left stale data')
        synth=[e for e in own if e['kind']=='synthesis'];require([e['channel'] for e in synth]==[0,1],'native synthesis channel order/count')
        inputs=[]
        for channel,e in enumerate(synth):
            require(e['status']==0 and e['block']==(0 if absent else r['channels'][channel]['ics']['block_type']),'native synthesis type/status differs')
            if absent:
                require(not any(e['input']) and not any(e['overlap_after']) and e['output']==e['overlap_before'],'absent element lost or repeated its overlap')
            else:inputs.append(compare_pcm(e['input'],r['channels_after_bwe2'][channel]['scaled']))
        scene=[e for e in own if e['kind']=='scene_read']
        require(len(scene)==int(r['packet_tail']['neutral_scene_restatement']),'native scene restatement count differs')
        if scene:
            present=next(f for f in r['fields'] if f['name']=='ancillary.audio_scenes_update_present')
            trim=next(f for f in r['fields'] if f['name']=='ancillary.trimming_present')
            require(scene[0]['start']['relative_bit_offset']==present['bit_offset']+1 and scene[0]['end']['relative_bit_offset']==trim['bit_offset'],'scene update bounds differ')
        require(all(e['before']==e['after'] for e in own if e['kind']=='scene'),'qualified scene changed PCM')
        records.append(dict(sequence=key[0],role=key[1],cpe_absent=absent,boundary_state_passed=True,
                            input_metrics=inputs,input_numeric_passed=all(m['passed'] for m in inputs)))
    return records


def artificial(binary,report):
    required=[]
    for rate in (48000,44100):
        buckets=defaultdict(list)
        for index,(kind,opts,seq) in enumerate(sequences()):
            # Existing high-amplitude BWE/TNS differences remain in their
            # original pressure reports and portable mathematical matrices.
            # This hard state gate uses all state cases and moderate joint tools.
            if kind=='joint_tools' and seq[1]['gain']==255:continue
            required.append((rate,index));buckets[json.dumps(opts,sort_keys=True)].append((index,kind,opts,seq))
        for group in buckets.values():
            for first in range(0,len(group),8):
                batch=group[first:first+8]
                with workspace(report,f'artificial-{rate}-{batch[0][0]}') as root:
                    opts=batch[0][2]
                    generated=[packet(c,rate,opts.get('scene',False)) for _,_,_,seq in batch for c in seq]
                    bundle(root/'packets',[p for p,_ in generated],rate,**opts)
                    rows=inspect(binary,root/'packets',root,len(generated))
                    for r,(_,truth) in zip(rows,generated):check_expected(r,truth,False)
                    trace=trace_bundle(binary,root/'packets',root)
                    checked=native_checks(rows,trace)
                    command(binary,'decode-sq',root/'packets','--out',root/'rust-pcm')
                    raw=(root/'native-pcm/pcm.f32le').read_bytes();candidate=(root/'rust-pcm/pcm.f32le').read_bytes()
                    require(len(raw)==len(candidate)==8192*len(rows),'internal frame leaked onto PCM timeline')
                    cursor=0
                    for index,kind,_,seq in batch:
                        n=len(seq);samples=raw[cursor*8192:(cursor+n)*8192];other=candidate[cursor*8192:(cursor+n)*8192]
                        metrics=compare_pcm(struct.unpack('<'+str(len(samples)//4)+'f',samples),struct.unpack('<'+str(len(other)//4)+'f',other))
                        relevant=[e for e in checked if cursor<=e['sequence']<cursor+n]
                        if kind!='joint_tools':require(metrics['passed'],'native basis/state control exceeds original tolerance: '+json.dumps(metrics))
                        report['artificial'].append(dict(rate=rate,index=index,kind=kind,passed=True,
                            input_sha256=identity(cookie(rate,**opts),[p for p,_ in generated[cursor:cursor+n]]),
                            native_trace_sha256=sha256_file(root/'native-state.json'),state_checks=relevant,native_float_metrics=metrics))
                        cursor+=n
                print('native packet states',rate,len(report['artificial']),file=sys.stderr,flush=True)
    require(len(required)==2220 and {(r['rate'],r['index']) for r in report['artificial']}==set(required),'native state control coverage differs')


def controls(binary,report):
    specs=[(rate,signal,[]) for rate in (48000,44100) for signal in ('silence','impulse','sine','sweep','noise','channel-solo')]
    specs += [(rate,'sine',extra) for rate in (48000,44100) for extra in (['--quality','96'],['--bitrate','256000'])]
    for rate,signal,extra in specs:
        item=dict(rate=rate,signal=signal,settings=extra,passed=False);report['controls'].append(item)
        with workspace(report,f'control-{rate}-{signal}-{len(report["controls"])}') as root:
            command(binary,'fixture','--out',root/'fixture','--signals',signal,'--sample-rate',rate,'--duration',2,
                    '--seed',1,'--drc-configuration','none',*extra)
            generated=root/'fixture'/signal;encoder=json.loads((generated/'manifest.json').read_text())
            require(encoder['drc_configuration_verified'] is True and encoder['actual_encoder_settings']['cdrc']['value']==0,'DRC none was not verified')
            item['encoder']={k:encoder[k] for k in ('requested','actual_encoder_settings','drc_configuration_verified')}
            dumped=command(binary,'dump',generated/'encoded.caf','--out',root/'packets','--with-preroll','--packets',150)
            # Subsequent native and Rust decoding must need only the bundle.
            (generated/'encoded.caf').unlink()
            rows=inspect(binary,root/'packets',root,dumped['actual_packets'])
            trace=trace_bundle(binary,root/'packets',root)
            item['native_state_checks']=native_checks(rows,trace)
            item['native_trace_sha256']=sha256_file(root/'native-state.json')
            decoded=command(binary,'decode-sq',root/'packets','--out',root/'rust-pcm')
            full=(root/'rust-pcm/pcm.f32le').read_bytes();raw=(root/'native-pcm/pcm.f32le').read_bytes()
            require(len(full)==len(raw),'native/Rust valid-frame duration differs')
            item['native_float_metrics']=compare_pcm(struct.unpack('<'+str(len(raw)//4)+'f',raw),struct.unpack('<'+str(len(full)//4)+'f',full))
            item['pcm_sha256']=hashlib.sha256(full).hexdigest();item['pcm']=decoded
            refresh=next((i for i,r in enumerate(rows) if r['embedded_preroll'] is not None),None)
            targets=[('start',0),('refresh',refresh if refresh is not None else len(rows)//2),('tail',max(0,len(rows)-4))]
            index=[json.loads(line) for line in (root/'packets/packets.jsonl').read_text().splitlines()]
            item['windows']=[]
            for label,target in targets:
                # Reuse exported dependency directions; source zero is always a valid fallback.
                roll=index[target]['roll_distance']['value'] if target else 0;require(isinstance(roll,int) and roll>=0,'missing target roll')
                start=target-min(target,roll)
                while start and (not index[start]['dependency']['value']['independently_decodable']
                        or index[start]['dependency']['value']['preroll_packet_count']>target-start):start-=1
                end=min(len(rows),target+4)
                window(root/'packets',root/label,start,target,end)
                result=command(binary,'decode-sq',root/label,'--out',root/(label+'-pcm'))
                pcm=(root/(label+'-pcm')/'pcm.f32le').read_bytes();first=result['range']['start_frame'];count=result['saved_frames']
                require(pcm==full[first*8:(first+count)*8],'random-access PCM differs from continuous decode')
                item['windows'].append(dict(kind=label,stored_start=start,target=target,frames=count,pcm_sha256=hashlib.sha256(pcm).hexdigest(),passed=True))
            item.update(packets=len(rows),embedded_preroll=sum(r['embedded_preroll'] is not None for r in rows),
                        cpe_absent=sum(encoded_absence(r) for r in rows),passed=True)
        print('native media',rate,signal,extra,'passed',file=sys.stderr,flush=True)
    require(len(report['controls'])==16 and all(r['passed'] for r in report['controls']),'missing required encoder controls')


def representatives(binary,report,baseline):
    previous=json.loads(baseline.read_text(encoding='utf-8'))
    require(previous['passed'] and len(previous['representatives'])==15,'requires verified replay representatives')
    for index,spec in enumerate(previous['representatives']):
        with workspace(report,f'representative-{index}') as root:
            target=spec['dump']['replay_window']
            command(binary,'dump',spec['source'],'--out',root/'packets','--with-preroll',
                    '--start-packet',target['requested_start_packet'],'--packets',target['requested_packets'])
            result=command(binary,'decode-sq',root/'packets','--out',root/'rust-pcm',allowed=(0,1))
            reason=None if result.get('complete') else pcm_stop(result)
            if spec['channels']==2 and reason=='configuration':
                require('ancillary.loudness_drc_present=true' in result['error']['message'],'stereo representative stopped for an unexplained configuration')
            report['representatives'].append(dict(channels=spec['channels'],range=spec['range'],
                target_packet=target['requested_start_packet'],pcm_stop_reason=reason,result=result,passed=True))
    require(len(report['representatives'])==15,'representative windows missing')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--replay-baseline',type=Path,default=Path('reports/replay-validation-a76d2f4.json'))
    args=parser.parse_args()
    if sys.platform!='darwin':parser.error('native packet validation requires macOS')
    if args.output.exists():parser.error('refusing to overwrite report')
    binary=args.binary.resolve(strict=True)
    report=dict(schema_version=1,mode='native_state_diagnostic',state_profile=PROFILE,component_sha256=COMPONENT_SHA256,
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,encoding='utf-8').strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True,encoding='utf-8').strip()),
        source_sha256=source_digest(),tool_sha256=sha256_file(binary),artificial=[],controls=[],representatives=[],errors=[],
        failure_directory=args.output.with_suffix('.failures'),started_utc=datetime.now(timezone.utc).isoformat())
    try:
        artificial(binary,report);controls(binary,report);representatives(binary,report,args.replay_baseline)
        require(source_digest()==report['source_sha256'] and sha256_file(binary)==report['tool_sha256'],'sources or binary changed during native acceptance')
    except Exception as error:report['errors'].append(str(error))
    report['failure_directory']=str(report['failure_directory'])
    report['passed']=not report['errors'] and len(report['artificial'])==2220 and len(report['controls'])==16 and len(report['representatives'])==15
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode())<=LIMIT,'native report exceeds 128 MiB');write_json(args.output,report)
    print(json.dumps(dict(passed=report['passed'],artificial=len(report['artificial']),controls=len(report['controls']),errors=report['errors'])))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
