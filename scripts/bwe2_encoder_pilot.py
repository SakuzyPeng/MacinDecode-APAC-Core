#!/usr/bin/env python3
"""Reproducible public-encoder setting pilot; LSF references stay inaccessible."""
import argparse
import json
from pathlib import Path
import sys

import bwe2_blackbox
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_blackbox_capture import atomic
from bwe2_lsf_probe import Experiment,read_bwe_syntax


def cases():
    rows=[]
    for signal in ('vowel','bwe-shaped-noise','formant-noise'):
        for mode,rate in ((0,64000),(1,64000),(2,32000),(3,None)):
            row=dict(signal=signal,rate=48000,seconds=2.,channels=2,bitrate_mode=mode,quality=0,content_source=36,seed=20261007)
            if rate is not None:row['bitrate']=rate
            if mode==3:row['vbr_quality']=0
            rows.append(row)
    for source in (33,38,40,42):
        rows.append(dict(signal='bwe-shaped-noise',rate=48000,seconds=2.,channels=2,bitrate_mode=0,
                         bitrate=64000,quality=32,content_source=source,seed=20261007))
    return rows


def run(exp,selected):
    producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
              ('bwe2_encoder_pilot.py','bwe2_public_encoder.py','bwe2_encoder_settings.py','bwe2_blackbox_math.py','bwe2_lsf_probe.py','bwe2_blackbox.py')}
    snapshot=exp.out/'producers'/digest(canonical(producer));snapshot.mkdir(exist_ok=True,parents=True)
    for name in producer:
        if not (snapshot/name).exists():atomic(snapshot/name,(ROOT/'scripts'/name).read_bytes())
    # Freeze raw encoder requests separately from later syntax-reader fixes.
    # Existing captures keep their exact request bytes and identities.
    plan_path=exp.out/'capture-plan.json'
    if plan_path.exists():plan=json.loads(plan_path.read_bytes())
    else:
        previous=sorted((exp.out/'results').glob('encoder-v2-*.json')) if (exp.out/'results').exists() else []
        original=json.loads(previous[0].read_bytes())['encoder_producer'] if previous else producer
        plan=dict(cases=cases(),encoder_producer=original)
        atomic(plan_path,canonical(plan))
    if plan['cases']!=cases():raise RuntimeError('frozen encoder case list changed')
    for name,sha in plan['encoder_producer'].items():
        if name!='bwe2_encoder_pilot.py' and producer[name]!=sha:raise RuntimeError('frozen raw encoder producer changed')
    rows=[]
    for spec in selected:
        if any(digest((ROOT/'scripts'/n).read_bytes())!=h for n,h in producer.items()):raise RuntimeError('encoder producer changed')
        case=digest(canonical(dict(spec=spec,producer=plan['encoder_producer'])))[:16];folder=exp.evidence/('case-'+case)
        request=exp.evidence/('spec-'+case+'.json')
        if request.exists():
            if request.read_bytes()!=canonical(spec):raise RuntimeError('case specification changed')
        else:atomic(request,canonical(spec))
        result=exp.command('encode-'+case,['-B',str(ROOT/'scripts/bwe2_public_encoder.py'),'--spec',str(request),
            '--out',str(folder),'--tool-sha',producer['bwe2_public_encoder.py']],credits=1,executable=sys.executable)
        row=dict(case=case,spec=spec,encoder_returncode=result['returncode'])
        if (folder/'result.json').exists():row['encoder']=json.loads((folder/'result.json').read_bytes())
        if result['returncode']==0:
            packet_dir=exp.evidence/('packets-preroll-'+case)
            # Mid-stream packets avoid treating encoder startup as steady state.
            dumped=exp.command('dump-preroll-'+case,['dump',str(folder/'encoded.caf'),'--out',str(packet_dir),'--start-packet','32',
                                           '--packets','24','--with-preroll','--max-output-mib','2'])
            row['dump_returncode']=dumped['returncode']
            if dumped['returncode']==0:
                report=exp.evidence/('tns-preroll-'+case+'.jsonl')
                parsed=exp.command('syntax-preroll-'+case,['parse-packets',str(packet_dir),'--depth','tns','--packets','24',
                    '--output',str(report),'--max-output-mib','2'],allowed=(0,1,2))
                row['syntax_returncode']=parsed['returncode']
                if report.exists():
                    try:row['syntax']=read_bwe_syntax(packet_dir,report)
                    except Exception as error:row['syntax_error']=repr(error)
        rows.append(row)
        print(json.dumps(dict(case=case,spec=spec,returncode=row['encoder_returncode'],
            active=row.get('syntax',{}).get('active_channel_frames'),parsed=row.get('syntax',{}).get('parsed_frames'))),flush=True)
    return dict(kind='public-encoder-lsf-index-observability-v2',cases=rows,encoder_producer=producer,
                capture_plan_sha256=digest(plan_path.read_bytes()),eligible_lsf=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--observations',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--mount',type=Path);p.add_argument('--evidence',type=Path);p.add_argument('--cases',type=int,default=4)
    args=p.parse_args()
    if not 1<=args.cases<=len(cases()):p.error('case count exceeds the frozen setting list')
    exp=Experiment(args)
    try:exp.save('encoder-v2',run(exp,cases()[:args.cases]))
    finally:exp.close()
