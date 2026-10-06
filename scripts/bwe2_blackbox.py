#!/usr/bin/env python3
"""Public-PCM BWE2 observability and reconstruction experiments."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

# Limit numeric worker pools: native calls remain serial in this experiment.
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')


def forbid_reference(event,args):
    if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
        name=os.fsdecode(args[0])
        if Path(name).name in ('bwe2-format-v1.json','bwe2_oracle.py','validate_bwe2.py','verify_bwe2_format.py'):
            raise RuntimeError('reference target access is forbidden during reconstruction')


_guard_installed = False


def install_discovery_guard():
    """Enable process-wide reference isolation explicitly at command entry."""
    global _guard_installed
    if not _guard_installed:
        sys.addaudithook(forbid_reference)
        _guard_installed = True


import numpy as np
from bwe2_blackbox_wire import ROOT,canonical,digest,source
from bwe2_blackbox_capture import Capture,atomic,read_status
from bwe2_blackbox_math import affine_scale,basis,fit_envelope,pcm,spectrum
from bwe2_blackbox_results import freeze_gains,lsf_identifiability


def producer():
    paths=sorted((ROOT/'scripts').glob('bwe2_blackbox*.py'))
    entries={p.name:digest(p.read_bytes()) for p in paths}
    return dict(files=entries,sha256=digest(canonical(entries)),numpy=np.__version__,python=sys.version)


def log(message,**fields):
    print(json.dumps(dict(time=time.time(),message=message,**fields),ensure_ascii=False),flush=True)


def read_probe(cap,label,spec,replicate=''):
    key,raw=cap.probe(label,spec,replicate)
    return key,raw


def calibration(cap):
    key,raw=read_probe(cap,'calibration-tone0',dict(source='tone',line=0,gain=128))
    b=basis()[:,0]
    scale=float(pcm(raw)[:,0]@b/(128*(b@b)))
    checks=[]
    for gain,line in ((128,511),(129,0),(129,767),(128,1023)):
        ref,raw=read_probe(cap,f'calibration-tone-{gain}-{line}',dict(source='tone',line=line,gain=gain))
        estimate=spectrum(raw,scale)
        expected=2**((gain-100)/4)
        error=estimate.copy();error[line]-=expected
        checks.append(dict(key=ref,gain=gain,line=line,relative_max_error=float(np.max(abs(error))/expected)))
    if max(row['relative_max_error'] for row in checks)>2e-6:
        raise RuntimeError('PCM calibration failed')
    return dict(scale=scale,key=key,checks=checks)


def pilot(cap):
    cal=calibration(cap)
    entries=[]
    # Indices are fixed independently of all target values.
    pairs=[(0,0),(0,511),(511,0),(511,511),(127,257),(256,128),(73,319),(401,41)]
    for pair in pairs:
        for gain in (128,129):
            spec=dict(source='comb',seed=1,gain=gain,parameters=[*pair,63])
            key,raw=read_probe(cap,f'pilot-{pair}-{gain}',spec)
            values=spectrum(raw,cal['scale'])
            carrier=np.array(source('comb'))*2**((gain-100)/4)
            try:
                fit=fit_envelope(values,carrier)
                entries.append(dict(pair=pair,carrier_gain=gain,key=key,**fit))
            except ArithmeticError as error:
                entries.append(dict(pair=pair,carrier_gain=gain,key=key,error=str(error)))
        log('pilot pair',pair=pair,observations=cap.status()['observations'])
    return dict(kind='observability-pilot',calibration=cal,estimates=entries,status=cap.status())


def gain_sweep(cap,seeds):
    cal=calibration(cap)
    estimates=[]
    for seed in range(1,seeds+1):
        spec=dict(source='comb',seed=seed,gain=128)
        base_key,raw=read_probe(cap,f'gains-base-{seed}',spec)
        baseline=pcm(raw)[:,0]
        ref_key,raw=read_probe(cap,f'gains-reference-{seed}',dict(spec,parameters=[0,0,63]))
        reference=pcm(raw)[:,0]
        carrier=np.array(source('comb',seed))*128
        fit=fit_envelope(spectrum(raw,cal['scale']),carrier)
        rows=[]
        for index in range(64):
            key,raw=read_probe(cap,f'gains-{seed}-{index}',dict(spec,parameters=[0,0,index]))
            ratio,residual=affine_scale(pcm(raw)[:,0],baseline,reference)
            rows.append(dict(index=index,key=key,relative_gain=ratio,affine_relative_residual=residual))
        estimates.append(dict(seed=seed,baseline_key=base_key,reference_key=ref_key,absolute_reference=fit,rows=rows))
        log('gain sweep seed',seed=seed,observations=cap.status()['observations'])
    return dict(kind='gain-estimates',calibration=cal,estimates=estimates,status=cap.status())


def anchors(cap,seeds,high=False):
    rows=[]
    for pair in (((0,0),) if high else ((0,0),(256,128))):
        for seed in range(1,seeds+1):
            spec=dict(source='comb',seed=seed,gain=128)
            base_key,raw=read_probe(cap,f'anchor-base-{seed}',spec)
            baseline=pcm(raw)[:,0]
            ref_key,raw=read_probe(cap,f'anchor-reference-{pair}-{seed}',dict(spec,parameters=[*pair,63]))
            reference=pcm(raw)[:,0]
            entries=[]
            for index in ((57,58,59,60,61,62) if high else (16,24,32,40,48,56)):
                key,raw=read_probe(cap,f'anchor-{pair}-{seed}-{index}',dict(spec,parameters=[*pair,index]))
                ratio,residual=affine_scale(pcm(raw)[:,0],baseline,reference)
                entries.append(dict(index=index,key=key,relative_gain=ratio,affine_relative_residual=residual))
            rows.append(dict(pair=pair,seed=seed,baseline_key=base_key,reference_key=ref_key,entries=entries))
            if seed%8==0:log('anchor group',pair=pair,seed=seed,observations=cap.status()['observations'])
    return dict(kind='high-gain-refinement' if high else 'gain-anchor-calibration',estimates=rows,status=cap.status())


def lsf_probe(cap):
    cal=calibration(cap)
    indices=(0,17,73,127,256,401,511)
    rows=[]
    for i in indices:
        for j in indices:
            spec=dict(source='comb',seed=19,gain=128,parameters=[i,j,63])
            key,raw=read_probe(cap,f'lsf-probe-{i}-{j}',spec)
            carrier=np.array(source('comb',19))*128
            try:
                fit=fit_envelope(spectrum(raw,cal['scale']),carrier)
                rows.append(dict(pair=[i,j],key=key,**fit))
            except ArithmeticError as error:
                rows.append(dict(pair=[i,j],key=key,error=str(error)))
        log('LSF observability row',index=i,observations=cap.status()['observations'])
    return dict(kind='lsf-factor-observability',calibration=cal,estimates=rows,status=cap.status())


def analysis_input(cap,prefix):
    candidates=sorted((cap.out/'analyses').glob(prefix+'-*.json'),key=lambda p:p.stat().st_mtime_ns)
    if not candidates:raise RuntimeError('missing analysis: '+prefix)
    path=candidates[-1]
    raw=path.read_bytes()
    if path.stem.rsplit('-',1)[1]!=digest(raw)[:16]:raise RuntimeError('analysis digest mismatch')
    value=json.loads(raw)
    if value['status']['native_identity']!=cap.identity:raise RuntimeError('analysis native identity differs')
    return value,dict(file=path.name,sha256=digest(raw))


def freeze(cap):
    gain,gd=analysis_input(cap,'gains')
    anchor,ad=analysis_input(cap,'anchors')
    high,hd=analysis_input(cap,'refine-gains')
    return dict(freeze_gains(gain,anchor,high,dict(gains=gd,anchors=ad,refinement=hd)),status=cap.status())


def validate_gains(cap):
    candidate,dependency=analysis_input(cap,'freeze')
    if not candidate['all_determined']:raise RuntimeError('gain candidate remains incomplete')
    words=[r['word'] for r in candidate['rows']]
    gains=np.array(words,dtype='<u4').view('<f4').astype(float)
    training,_=analysis_input(cap,'gains')
    # Fixed before held-out acquisition. Small gains have less observable
    # energy; use the empirical per-index training floor, with safety factor 8.
    tolerances=np.maximum(8*np.max([[x['affine_relative_residual'] for x in row['rows']]
        for row in training['estimates']],axis=0),8*np.finfo(np.float32).eps)
    cal=calibration(cap)
    controls=[];checks=[]
    for seed in (1001,1002,1003,1004):
        for gain in (128,129):
            cutoff=256 if seed%2 else 384
            spec=dict(source='comb',seed=seed,gain=gain,cutoff=cutoff)
            replicate='gain-validation-'+dependency['sha256']
            base_key,raw=read_probe(cap,f'validate-base-{seed}-{gain}',spec,replicate)
            baseline=pcm(raw)[:,0]
            ref_key,raw=read_probe(cap,f'validate-ref-{seed}-{gain}',dict(spec,parameters=[401,41,63]),replicate)
            reference=pcm(raw)[:,0]
            carrier=np.array(source('comb',seed,cutoff=cutoff))*2**((gain-100)/4)
            fit=fit_envelope(spectrum(raw,cal['scale']),carrier,cutoff)
            absolute_relative_error=float(abs(fit['gain']/gains[63]-1))
            controls.append(dict(seed=seed,carrier_gain=gain,cutoff=cutoff,baseline_key=base_key,reference_key=ref_key,
                estimate=fit['gain'],absolute_relative_error=absolute_relative_error,passed=absolute_relative_error<=2e-6))
            for i in range(64):
                key,raw=read_probe(cap,f'validate-{seed}-{gain}-{i}',dict(spec,parameters=[401,41,i]),replicate)
                value=pcm(raw)[:,0]
                expected=gains[i]/gains[63]
                residual=value-baseline-expected*(reference-baseline)
                error=float(np.linalg.norm(residual)/max(np.linalg.norm(value-baseline),1e-300))
                ratio,_=affine_scale(value,baseline,reference)
                checks.append(dict(index=i,key=key,seed=seed,carrier_gain=gain,cutoff=cutoff,
                    observed_relative_gain=ratio,expected_relative_gain=float(expected),relative_waveform_error=error,
                    tolerance=float(tolerances[i]),passed=bool(error<=tolerances[i])))
            log('gain held-out group',seed=seed,carrier_gain=gain,observations=cap.status()['observations'])
    return dict(schema_version=1,kind='bwe2-gain-heldout-validation',candidate=dependency,
        checks=checks,controls=controls,all_passed=all(r['passed'] for r in checks+controls),
        independent_cases=len(checks),status=cap.status())


def main():
    install_discovery_guard()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('pilot','gains','anchors','refine-gains','freeze','validate','lsf-probe','lsf-result','audit','status'))
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--mount',type=Path)
    parser.add_argument('--seeds',type=int,default=4)
    parser.add_argument('--reanalyze',action='store_true',help='create a new analysis version from existing raw observations')
    args=parser.parse_args()
    if not 1<=args.seeds<=64:
        parser.error('seeds must be 1..64')
    if args.stage=='status':
        log('status',**read_status(args.out));return
    if args.binary is None:parser.error('capture and analysis require --binary')
    cap=Capture(args.out,args.binary,args.evidence,args.mount)
    journal=cap.out/'analysis-attempts.jsonl'
    started=time.time()
    def record(state,**extra):
        with journal.open('ab') as stream:
            stream.write(canonical(dict(started=started,time=time.time(),stage=args.stage,state=state,**extra))+b'\n')
            stream.flush();os.fsync(stream.fileno())
    try:
        tool=producer()
        record('started',producer=tool)
        if not args.reanalyze and args.stage!='audit':
            existing=sorted((cap.out/'analyses').glob(args.stage+'-*.json'),key=lambda p:p.stat().st_mtime_ns)
            if existing:
                path=existing[-1];raw=path.read_bytes();value=json.loads(raw)
                if digest(raw)[:16]!=path.stem.rsplit('-',1)[1]:raise RuntimeError('frozen analysis is corrupt')
                counts={'gains':args.seeds,'anchors':2*args.seeds,'refine-gains':args.seeds}
                compatible=args.stage not in counts or len(value.get('estimates',[]))==counts[args.stage]
                if args.stage=='validate':
                    _,candidate=analysis_input(cap,'freeze')
                    compatible=compatible and value['candidate']==candidate
                if compatible:
                    audit=cap.audit()
                    record('reused',result=path.name,sha256=digest(raw),audit=audit)
                    log('frozen analysis reused',path=str(path),status=cap.status());return
        snapshot=cap.out/'producers'/tool['sha256']
        snapshot.mkdir(parents=True,exist_ok=True)
        for name in tool['files']:
            path=snapshot/name
            if not path.exists():atomic(path,(ROOT/'scripts'/name).read_bytes())
        if args.stage=='pilot':value=pilot(cap)
        elif args.stage=='gains':value=gain_sweep(cap,args.seeds)
        elif args.stage=='anchors':value=anchors(cap,args.seeds)
        elif args.stage=='refine-gains':value=anchors(cap,args.seeds,True)
        elif args.stage=='freeze':value=freeze(cap)
        elif args.stage=='validate':value=validate_gains(cap)
        elif args.stage=='lsf-probe':value=lsf_probe(cap)
        elif args.stage=='audit':value=cap.audit()
        else:
            inputs,dep=analysis_input(cap,'lsf-probe')
            value=lsf_identifiability(inputs,dep)
            value['capture_status']=cap.status()
        if producer()!=tool:
            raise RuntimeError('producer changed during analysis')
        cap.check(force=True)
        value.update(producer=tool,policy='bwe2-pcm-observability-v1',created=time.time())
        directory=cap.out/'analyses'
        directory.mkdir(exist_ok=True)
        path=directory/(args.stage+'-'+digest(canonical(value))[:16]+'.json')
        atomic(path,canonical(value))
        record('complete',result=path.name,sha256=digest(path.read_bytes()),status=cap.status())
        log('analysis saved',path=str(path),status=cap.status())
    except BaseException:
        record('failed',traceback=traceback.format_exc(),status=cap.status())
        raise
    finally:
        cap.close()


if __name__=='__main__':
    main()
