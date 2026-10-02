#!/usr/bin/env python3
"""Paired release end-to-end timings; full input verification is mandatory."""
import argparse,json,platform,statistics,subprocess,time,shutil
from pathlib import Path
from datetime import datetime,timezone
from channel_vectors import cookie,packet,sequences,LAYOUTS
from access_vectors import PROFILE
from caf_vectors import encode as caf_encode,sha,digest
from mp4_vectors import encode as mp4_encode
from validate import require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file
from validate_access import state_identity

def controls():
    n=8
    dense=[]
    for typ in LAYOUTS[n][2]:
        bands={b:(7,[1,-1],160) for b in range(14)}
        dense.append(dict(left=bands,right=bands,cac_gain=26) if typ==1 else dict(bands=bands))
    yield 'bounded',dict(scene=True,drc=False,rich=False),[dict(elements=dense)]
    joint=next(c for c in sequences(n) if c[0]=='joint_tools');yield 'numeric_tools',joint[1],[joint[2][1]]
    meta=next(c for c in sequences(n) if c[0]=='metadata_updates');yield 'metadata_updates',meta[1],meta[2][:-2]

def paired(binary,source,root,start,frames,trials,channels,packets,*,profile=PROFILE):
    times={m:[] for m in ('sequential','fast')};phases={m:[] for m in times};stable=None;implementation=None
    for trial in range(trials+1):
        for mode in (('sequential','fast') if trial%2==0 else ('fast','sequential')):
            out=root/f'{trial}-{mode}';before=time.perf_counter()
            report=command(binary,'decode-sq',source,'--out',out,'--access',mode,'--start-frame',start,'--frames',frames)
            elapsed=time.perf_counter()-before
            impl=report['pcm']['decoder_settings']['implementation']['value'];require(not impl['debug_assertions'],'benchmark requires release build')
            if implementation is None:implementation=impl
            require(impl==implementation and report['access']['profile']==profile,'benchmark implementation/access changed')
            state_identity(report)
            pcm=(out/'pcm.f32le').read_bytes();identity=(report['saved_frames'],sha(pcm),report['access']['metadata_before_output_sha256'],report['access']['metadata_after_processing_sha256'])
            require(report['complete'] and report['input']['consistency_verified'] and report['integrity_checked_packets']==packets,'benchmark reduced input verification')
            require(len(pcm)==report['saved_frames']*channels*4 and sha(pcm)==report['pcm']['sha256'],'benchmark PCM mismatch')
            if stable is None:stable=identity
            require(identity==stable,'benchmark modes differ')
            if mode=='fast':require(report['warmup_packets']<=1 and report['access']['prefix_scanned_packets']>=packets//2,'benchmark did not exercise long prefix')
            if trial:times[mode].append(elapsed);phases[mode].append(report['access']['timings_seconds'])
            shutil.rmtree(out)
    medians={mode:statistics.median(rows) for mode,rows in times.items()}
    return dict(passed=medians['fast']<medians['sequential'],samples_seconds=times,median_seconds=medians,
                speed_ratio=medians['sequential']/medians['fast'],phase_samples_seconds=phases,
                implementation=implementation,frames=stable[0],pcm_sha256=stable[1],metadata_before_output_sha256=stable[2],metadata_after_processing_sha256=stable[3])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--prefix-packets',type=int,default=2048);p.add_argument('--trials',type=int,default=5);args=p.parse_args()
    require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists');require(args.prefix_packets>=512 and args.trials>=3,'insufficient benchmark conditions')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),
        prefix_packets=args.prefix_packets,trials=args.trials,initial_pair_discarded=True,controls=[],errors=[],failure_directory=str(args.report.with_suffix('.failures')))
    try:
        for kind,options,seq in controls():
            cfg=cookie(8,48000,**options);cycle=[packet(c,8,48000,**options)[0] for c in seq];payloads=[cycle[i%len(cycle)] for i in range(args.prefix_packets+8)]
            for container in ('caf','mp4'):
                with workspace(report,kind+'-'+container) as root:
                    raw=caf_encode(cfg,payloads,channels=8)[0] if container=='caf' else mp4_encode(cfg,payloads,channels=8,variant=7)[0]
                    source=root/'input.audio';source.write_bytes(raw)
                    result=paired(args.binary.resolve(),source,root,args.prefix_packets*1024+17,1027,args.trials,8,len(payloads))
                    result.update(kind=kind,container=container,input_sha256=sha(raw));report['controls'].append(result)
                    require(result['passed'],'fast median did not improve: '+kind+'/'+container)
                print(kind,container,'speed_ratio',result['speed_ratio'],flush=True)
        require(len(report['controls'])==6,'missing benchmark');require(source_digest()==report['source_sha256'] and sha256_file(args.binary)==report['binary_sha256'],'benchmark source/binary changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['conditions_sha256']=digest([dict(kind=r['kind'],container=r['container'],input_sha256=r['input_sha256']) for r in report['controls']])
    write_json(args.report,report);print(json.dumps(dict(passed=report['passed'],errors=report['errors'])));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
