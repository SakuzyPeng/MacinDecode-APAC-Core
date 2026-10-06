#!/usr/bin/env python3
"""Fit and freeze observable LPC vectors using only public numerical models.

The two real-DFT alignment variants are fixed before validation. Fitted LPCs
are not identified LSF codebook entries and never receive eligible_lsf=true.
"""
import argparse
import json
from pathlib import Path
import time
import platform
import subprocess
import itertools

import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import atomic
from bwe2_blackbox_math import fit_transfer,lpc_to_lsf
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_float_profile import PublicDft,refine_continuous,optimize_words
from bwe2_lpc_lattice import forward_lattice,nearest_lattice
from bwe2_public_math import PublicMath

POLICY=dict(transform='public-vDSP-DFT-zrop-1024',fft_offsets=[0,4],magnitude='public-vDSP-zvabs',
            math_offset=0,division='public-vDSP-svdiv',multiply='Float32-gain-after-division',
            source_amplitude=128,gain_index=63)


def gain_value():
    raw=(ROOT/'data/bwe2-gains-measured-v1.json').read_bytes()
    if digest(raw)!='f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865':raise RuntimeError('qualified gain source changed')
    return float(np.array(json.loads(raw)['excitation_gains_f32'],np.uint32).view(np.float32)[63])


def variants(words,source_gain=128):
    if source_gain not in (128,132):raise ValueError('unvalidated source amplitude')
    a=np.array(words,np.uint32).view(np.float32)
    if a.shape!=(17,) or a[0]!=1 or not np.all(np.isfinite(a)):raise ValueError('invalid monic fitted LPC')
    math=PublicMath();result={}
    for offset in POLICY['fft_offsets']:
        fft=PublicDft(True,offset)
        try:result[str(offset)]=np.float32(math.predict(a,gain_value(),fft.execute)*(2 if source_gain==132 else 1))
        finally:fft.close()
    return result


def polish_pairs(initial,observed,bins,gain,offset):
    """Bounded two-coordinate word moves to escape single-coordinate ties."""
    words=np.array(initial,np.uint32);math=PublicMath();fft=PublicDft(True,offset);evaluations=0
    def score(trial):
        nonlocal evaluations
        evaluations+=1
        prediction=math.predict(trial.view(np.float32),gain,fft.execute)[bins].view(np.uint32).astype(np.int64)
        delta=prediction-observed.astype(np.int64)
        return (float(np.mean(delta**2)),-int(np.count_nonzero(delta==0)),int(max(abs(delta))))
    best=score(words);history=[]
    try:
        for iteration in range(4):
            if best[0]==0:break
            selected=words.copy();previous=best
            for i,j in itertools.combinations(range(1,17),2):
                for di,dj in itertools.product((-8,-4,-2,-1,1,2,4,8),repeat=2):
                    if not 0<=int(words[i])+di<2**32 or not 0<=int(words[j])+dj<2**32:continue
                    if any((int(words[k])+delta)&0x7f800000==0x7f800000 for k,delta in ((i,di),(j,dj))):continue
                    trial=words.copy();trial[i]=int(words[i])+di;trial[j]=int(words[j])+dj
                    current=score(trial)
                    if current<best:best=current;selected=trial
                    if best[0]==0:break
                if best[0]==0:break
            words=selected;history.append(dict(iteration=iteration,mse_ulp=best[0],exact_bins=-best[1]))
            if best==previous:break
        return dict(lpc_f32=words.tolist(),mse_ulp=best[0],exact_bins=-best[1],max_ulp=best[2],
                    model_evaluations=evaluations,history=history,offset=offset,method='bounded-pair-polish')
    finally:fft.close()


def polish_small(initial,observed,bins,gain,offset):
    """Jointly search the smaller coefficients on their actual Float32 grid."""
    a=np.array(initial,np.uint32).view(np.float32);indices=np.where(abs(a[1:])<.0625)[0]+1
    quantum=abs(np.spacing(a[indices])).astype(float);math=PublicMath();fft=PublicDft(True,offset)
    y=observed.view(np.float32).astype(float);ulps=np.spacing(observed.view(np.float32)).astype(float);history=[]
    def output(trial):return math.predict(trial,gain,fft.execute)[bins]
    def score(values):
        delta=values.view(np.uint32).astype(np.int64)-observed.astype(np.int64)
        return float(np.mean(delta**2)),-int(np.count_nonzero(delta==0)),int(max(abs(delta)))
    try:
        for iteration in range(5):
            base=output(a);best=score(base);selected=a.copy();jac=np.empty((len(bins),len(indices)))
            if best[0]==0:break
            for j,index in enumerate(indices):
                plus=a.copy();minus=a.copy();plus[index]=np.float32(plus[index]+64*quantum[j]);minus[index]=np.float32(minus[index]-64*quantum[j])
                jac[:,j]=(output(plus).astype(float)-output(minus).astype(float))/(128*ulps)
            candidates,nodes=nearest_lattice(jac,(y-base.astype(float))/ulps,count=1024)
            for row in candidates:
                trial=a.copy();trial[indices]=np.float32(a[indices].astype(float)+quantum*row['offset'])
                current=score(output(trial))
                if current<best:best=current;selected=trial
                if best[0]==0:break
            history.append(dict(iteration=iteration,mse_ulp=best[0],exact_bins=-best[1],nodes=nodes))
            if np.array_equal(a.view(np.uint32),selected.view(np.uint32)):break
            a=selected
        return dict(lpc_f32=a.view(np.uint32).tolist(),mse_ulp=best[0],exact_bins=-best[1],max_ulp=best[2],history=history,
                    offset=offset,indices=indices.tolist(),method='bounded-small-coefficient-lattice')
    finally:fft.close()


def fit(document):
    bins=np.array(sorted(map(int,document['words'])))
    if len(bins)!=256 or not document.get('complete'):raise ValueError('complete single-carrier spectrum required')
    words=np.array([document['words'][str(k)] for k in bins],np.uint32);observed=words.view(np.float32).astype(float)
    gain=gain_value();initial=fit_transfer(bins,observed/128)['lpc'];continuous=refine_continuous(initial,observed,gain,bins)
    fits=[];math=PublicMath()
    for exponent in (24,23):
        quantum=np.full(16,2.**(-exponent));seed=np.float32(np.r_[1.,np.rint(continuous[1:]/quantum)*quantum])
        for offset in POLICY['fft_offsets']:
            lattice=forward_lattice(seed,words,bins,gain,quantum,True,offset,True)
            fft=PublicDft(True,offset)
            try:
                fitted=optimize_words(np.array(lattice['lpc_f32'],np.uint32).view(np.float32),words.astype(np.int64),
                                      lambda a:math.predict(a,gain,fft.execute),bins)
            finally:fft.close()
            fits.append(dict(grid_exponent=exponent,offset=offset,lattice=lattice,**fitted))
            print(json.dumps(dict(grid=exponent,offset=offset,exact=fitted['exact_bins'],max_ulp=fitted['max_ulp'])),flush=True)
        if any(f['exact_bins']==256 for f in fits):break
    if not any(f['exact_bins']==256 for f in fits):
        best=min(fits,key=lambda f:f['mse_ulp'])
        polished=polish_pairs(best['lpc_f32'],words,bins,gain,best['offset']);fits.append(polished)
        print(json.dumps(dict(polish=True,exact=polished['exact_bins'],max_ulp=polished['max_ulp'])),flush=True)
    matches={tuple(f['lpc_f32']) for f in fits if f['exact_bins']==256}
    chosen=list(next(iter(matches))) if len(matches)==1 else None
    return dict(kind='frozen-observable-lpc-model',pair=document['pair'],policy=POLICY,
                unique_among_searched_fits=len(matches)==1,fit_complete=len(matches)==1,eligible_lsf=False,
                lpc_f32=chosen,inverse_polynomial_lsf_estimate=lpc_to_lsf(np.array(chosen,np.uint32).view(np.float32).astype(float)).tolist() if chosen else None,
                fits=fits,qualification='observable monic LPC candidate under a fixed public numerical model; not proof of unique internal LPC or original LSF entries')


def main():
    bwe2_blackbox.install_discovery_guard()
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spectrum',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();raw=args.spectrum.read_bytes();result=fit(json.loads(raw))
    producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
              ('bwe2_public_lpc.py','bwe2_public_math.py','bwe2_float_profile.py','bwe2_lpc_lattice.py','bwe2_blackbox_math.py','bwe2_blackbox.py')}
    result.update(spectrum_sha256=digest(raw),producer=producer,created=time.time(),
        native_identity=json.loads(raw).get('native_identity'),
        code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        analysis_environment=dict(python=platform.python_version(),numpy=np.__version__,architecture=platform.machine(),
                                  system_version=subprocess.check_output(['sw_vers'],text=True).strip()))
    args.out.mkdir(exist_ok=True,parents=True);snapshot=args.out/'producers'/digest(canonical(producer));snapshot.mkdir(parents=True,exist_ok=True)
    for name in producer:
        if not (snapshot/name).exists():atomic(snapshot/name,(ROOT/'scripts'/name).read_bytes())
    path=args.out/(digest(canonical(result))+'.json');atomic(path,canonical(result));print(path,flush=True)


if __name__=='__main__':main()
