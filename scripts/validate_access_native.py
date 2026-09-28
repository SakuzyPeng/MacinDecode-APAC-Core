#!/usr/bin/env python3
"""Qualified native containers, identical fast/sequential state and media windows."""
import argparse,json,subprocess,shutil,statistics
from pathlib import Path
from datetime import datetime,timezone
from mp4_native_reference import verify
from mp4_vectors import encode,sha
from validate_mp4_native import check_input,check_media_windows
from benchmark_access import paired
from validate import require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file
from native_frame_trace import COMPONENT_SHA256
from access_vectors import PROFILE
from validate_access import state_identity

def compare_window(binary,source,root,start,frames,evidence=None):
    results=[]
    for mode in ('sequential','fast'):
        out=root/mode;r=command(binary,'decode-sq',source,'--out',out,'--access',mode,'--start-frame',start,'--frames',frames)
        if evidence is not None:check_input(r,evidence)
        require(r['complete'] and r['input']['consistency_verified'],'unverified input')
        require(r['drc_processing']==r['loudness_normalization']=='off','processing enabled')
        state_identity(r)
        raw=(out/'pcm.f32le').read_bytes();require(sha(raw)==r['pcm']['sha256'],'PCM file digest differs');results.append((r,raw));shutil.rmtree(out)
    a,b=results;require(a[1]==b[1] and a[0]['saved_frames']==b[0]['saved_frames'],'fast/native-control range PCM differs')
    for key in ('metadata_before_output_sha256','metadata_after_processing_sha256'):require(a[0]['access'][key]==b[0]['access'][key],'DRC history/provenance differs')
    require(b[0]['warmup_packets']<=1 and a[0]['integrity_checked_packets']==b[0]['integrity_checked_packets'],'warmup or input verification differs')
    return dict(passed=True,start=start,frames=b[0]['saved_frames'],pcm_sha256=sha(b[1]),
        access={k:v for k,v in b[0]['access'].items() if k!='timings_seconds'})

def controls(binary,report):
    report['controls']=[]
    for n,preset in ((1,'mono'),(2,'stereo'),(6,'surround51'),(8,'surround71')):
        for rate in (48000,44100):
            for profile in ('default','none','music','speech','movie','capture'):
                for signal in ('noise','impulse'):
                    label=f'{n}-{rate}-{profile}-{signal}'
                    with workspace(report,label) as root:
                        extra=[] if profile=='default' else ['--drc-configuration',profile]
                        command(binary,'fixture','--out',root/'fixture','--layout',preset,'--sample-rate',rate,'--duration','0.125','--signals',signal,*extra)
                        folder=root/'fixture'/signal;caf=folder/'encoded.caf';fixture=json.loads((folder/'manifest.json').read_text(encoding='utf-8'));actual=fixture['actual_encoder_settings']['cdrc']
                        require(actual['error'] is None and actual['value']=={'default':4294967295,'none':0,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder readback differs')
                        command(binary,'dump',caf,'--out',root/'bundle','--packets',150)
                        bundle=root/'bundle';m=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'));table=m['file']['packet_table']['value'];audio=(bundle/'packets.bin').read_bytes()
                        rows=[json.loads(l) for l in (bundle/'packets.jsonl').read_text(encoding='utf-8').splitlines()]
                        packets=[audio[r['export_offset']:r['export_offset']+r['bytes']] for r in rows];config=(bundle/'cookie.bin').read_bytes()
                        mp4=root/'input.mp4';mp4.write_bytes(encode(config,packets,rate,n,table['priming_frames'],table['remainder_frames'],len(report['controls']))[0])
                        evidence=verify(mp4);record=dict(channels=n,rate=rate,profile=profile,signal=signal,encoder=actual,windows=[])
                        for name,start,count in [('head',0,31),('middle',table['valid_frames']//2,1027),('tail',max(0,table['valid_frames']-1001),2048),('eof',table['valid_frames'],1)]:
                            results=[]
                            for kind,src in [('caf',caf),('mp4',mp4)]:
                                work=root/(kind+'-'+name);work.mkdir();results.append(compare_window(binary,src,work,start,count,evidence if kind=='mp4' else None));shutil.rmtree(work)
                            require(results[0]==results[1],'CAF/MP4 state or PCM differs');record['windows'].append(dict(kind=name,**results[0]))
                        record['passed']=True;report['controls'].append(record)
                    print('access control',label,flush=True)
    require(len(report['controls'])==96,'missing controls')

def media(binary,report,collection,prior,trials):
    baseline=json.loads(prior.read_text(encoding='utf-8'));require(baseline['passed'] and len(baseline['media'])==13,'missing qualified MP4 media baseline')
    wanted={r['source_sha256']:r for r in baseline['media']};require(len(wanted)==13,'duplicate baseline source')
    for r in wanted.values():
        require(r.get('passed') is True,'unqualified baseline source')
        check_media_windows(r['windows'])
    index=json.loads(collection.read_text(encoding='utf-8'));sources={s['source']:s for c in index['configs'] for s in c['sources'] if s['format']['channels']==8}
    require(len(sources)==13,'missing original sources');report['media']=[]
    for source,info in sorted(sources.items()):
        source=Path(source);identity=sha256_file(source);require(identity in wanted,'original source changed');evidence=verify(source)
        record=dict(source_sha256=identity,packets=evidence['packets'],windows=[],passed=True)
        for old in wanted[identity]['windows']:
            name=old['kind']
            with workspace(report,identity[:12]+'-'+name) as root:
                result=compare_window(binary,source,root,old['start'],old['frames'],evidence)
                require(result['pcm_sha256']==old['pcm_sha256'] and result['frames']==old['frames'],'qualified window identity changed');record['windows'].append(dict(kind=name,**result))
                if name=='tail':record['performance']=paired(binary,source,root,old['start'],old['frames'],trials,8,evidence['packets'])
            print('access media',len(report['media']),name,flush=True)
        check_media_windows(record['windows']);require(sha256_file(source)==identity,'source changed');report['media'].append(record)
    require(len(report['media'])==13 and sum(r['packets'] for r in report['media'])==207879,'missing source packet evidence')
    report['media_speed_ratio_median']=statistics.median(r['performance']['speed_ratio'] for r in report['media'])
    require(report['media_speed_ratio_median']>1.,'median real-window total time did not improve')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--collection',type=Path,required=True);p.add_argument('--mp4-reference',type=Path,required=True);p.add_argument('--trials',type=int,default=3)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists');require(args.trials>=3,'insufficient performance trials')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),component_sha256=sha256_file(component),reference_sha256=sha256_file(args.mp4_reference),trials=args.trials,controls=[],media=[],errors=[],failure_directory=str(args.report.with_suffix('.failures')))
    try:
        require(report['component_sha256']==COMPONENT_SHA256,'unqualified native component');controls(args.binary.resolve(),report);media(args.binary.resolve(),report,args.collection,args.mp4_reference,args.trials)
        require(source_digest()==report['source_sha256'] and sha256_file(args.binary)==report['binary_sha256'] and sha256_file(component)==COMPONENT_SHA256,'validation identity changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    write_json(args.report,report);print(json.dumps(dict(passed=report['passed'],controls=len(report['controls']),media=len(report['media']),errors=report['errors'])));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
