#!/usr/bin/env python3
"""Bounded joint coefficient search on an explicit dyadic LPC hypothesis."""
import argparse
import heapq
import itertools
import json
import os
from pathlib import Path
import time
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import atomic
from bwe2_blackbox_math import fit_envelope,fit_transfer
from bwe2_blackbox_wire import ROOT,canonical,digest,source
from bwe2_float_profile import PublicDft,predict


def reduce_basis(basis):
    basis=np.asarray(basis,float)
    if basis.ndim!=2 or basis.shape[0]<basis.shape[1] or not basis.shape[1] or not np.all(np.isfinite(basis)):
        raise ValueError('finite tall lattice basis required')
    if np.linalg.matrix_rank(basis)!=basis.shape[1]:raise ValueError('singular lattice basis')
    b=basis.copy();n=b.shape[1];transform=np.eye(n,dtype=np.int64)
    def gram_schmidt():
        star=b.copy();mu=np.zeros((n,n));norm=np.zeros(n)
        for i in range(n):
            for j in range(i):mu[i,j]=np.dot(b[:,i],star[:,j])/norm[j];star[:,i]-=mu[i,j]*star[:,j]
            norm[i]=star[:,i]@star[:,i]
        return mu,norm
    k=1;iterations=0
    while k<n:
        iterations+=1
        if iterations>10000:raise RuntimeError('basis reduction iteration bound')
        mu,norm=gram_schmidt()
        for j in range(k-1,-1,-1):
            q=int(np.rint(mu[k,j]))
            if q:
                b[:,k]-=q*b[:,j];transform[:,k]-=q*transform[:,j];mu,norm=gram_schmidt()
        if norm[k]>=(.75-mu[k,k-1]**2)*norm[k-1]:k+=1
        else:
            b[:,[k,k-1]]=b[:,[k-1,k]];transform[:,[k,k-1]]=transform[:,[k-1,k]];k=max(k-1,1)
        if np.max(abs(transform))>100000000:raise RuntimeError('unimodular transform grew beyond bound')
    if np.max(abs(b-basis@transform))>1e-7:raise RuntimeError('basis reduction residual')
    return b,transform


def nearest_lattice(basis,target,count=512,node_limit=2000000):
    target=np.asarray(target,float)
    if target.shape!=(len(basis),) or not np.all(np.isfinite(target)) or count<1 or node_limit<1:
        raise ValueError('invalid bounded lattice search')
    reduced,transform=reduce_basis(basis);q,r=np.linalg.qr(reduced);center=np.linalg.solve(r,q.T@target)
    n=len(center);z=np.zeros(n,np.int64)
    for k in range(n-1,-1,-1):z[k]=round(center[k]-np.dot(r[k,k+1:],z[k+1:]-center[k+1:])/r[k,k])
    radius=float(np.linalg.norm(r@(z-center))**2)+512.
    heap=[];visited=0
    def visit(k,cost):
        nonlocal visited,radius
        visited+=1
        if visited>node_limit:raise RuntimeError('lattice enumeration node bound')
        if k<0:
            vector=tuple(map(int,transform@z))
            heapq.heappush(heap,(-cost,vector))
            if len(heap)>count:heapq.heappop(heap)
            if len(heap)==count:radius=min(radius,-heap[0][0])
            return
        middle=center[k]-np.dot(r[k,k+1:],z[k+1:]-center[k+1:])/r[k,k]
        extent=np.sqrt(max(0,radius-cost))/abs(r[k,k])
        lo=int(np.ceil(middle-extent));hi=int(np.floor(middle+extent))
        for integer in sorted(range(lo,hi+1),key=lambda value:abs(value-middle)):
            extra=(r[k,k]*(integer-middle))**2
            if cost+extra<=radius+1e-10:z[k]=integer;visit(k-1,cost+extra)
    visit(n-1,0.)
    return sorted([dict(cost=-cost,offset=list(vector)) for cost,vector in heap],key=lambda row:row['cost']),visited


def arithmetic_steps(a):
    p=np.zeros(17);q=np.zeros(17);p[0]=q[0]=1
    for i in range(1,9):p[i]=a[i]+a[17-i]-p[i-1];q[i]=a[i]-a[17-i]+q[i-1]
    p[9:]=p[7::-1];q[9:]=q[7::-1]
    steps=[]
    for i in range(1,17):
        terms=[]
        # Constant edge terms have no rounding uncertainty of their own.
        if i!=16:terms.append(abs(float(np.spacing(np.float32(p[i]+q[i])))))
        if i!=1:terms.append(abs(float(np.spacing(np.float32(p[i-1]-q[i-1])))))
        steps.append(max(min(terms)/2,abs(float(np.spacing(np.float32(a[i]))))))
    return np.array(steps)


def forward_lattice(initial,observed_words,bins,gain,quantum,real=True,offset=0,public_absolute=False):
    a=np.float32(initial);observed=observed_words.view(np.float32).astype(float)
    ulps=np.spacing(observed_words.view(np.float32)).astype(float)
    fft=PublicDft(real,offset);history=[]
    if public_absolute:
        from bwe2_public_math import PublicMath
        math=PublicMath()
    def output(coefficients):
        if public_absolute:return math.predict(coefficients,gain,fft.execute,'zvabs','svdiv')[bins]
        return predict(coefficients,gain,fft.execute,'separate','divide_then_gain')[bins]
    def score(pred):
        diff=pred.view(np.uint32).astype(np.int64)-observed_words.astype(np.int64)
        return float(np.mean(diff**2)),int(np.count_nonzero(diff==0)),int(np.max(abs(diff)))
    try:
        for iteration in range(6):
            base=output(a);best=score(base);chosen=a.copy()
            jac=np.empty((len(bins),16))
            for i in range(16):
                plus=a.copy();minus=a.copy();plus[i+1]=np.float32(plus[i+1]+8*quantum[i]);minus[i+1]=np.float32(minus[i+1]-8*quantum[i])
                jac[:,i]=(output(plus).astype(float)-output(minus).astype(float))/(16*ulps)
            candidates,nodes=nearest_lattice(jac,(observed-base.astype(float))/ulps,count=512)
            for row in candidates:
                trial=a.copy();trial[1:]=np.float32(a[1:].astype(float)+np.array(row['offset'])*quantum)
                current=score(output(trial))
                if current[0]<best[0] or (current[0]==best[0] and current[1]>best[1]):chosen=trial;best=current
            history.append(dict(iteration=iteration,mse_ulp=best[0],exact_bins=best[1],max_ulp=best[2],nodes=nodes))
            if np.array_equal(chosen.view(np.uint32),a.view(np.uint32)):break
            a=chosen
        return dict(lpc_f32=a.view(np.uint32).tolist(),mse_ulp=best[0],exact_bins=best[1],max_ulp=best[2],history=history,real=real,offset=offset,public_absolute=public_absolute)
    finally:fft.close()


def corner_search(continuous,observed_words,bins,gain,quantum):
    """Exhaust the adjacent 2^16 grid corners, independent of a local fit."""
    low=np.floor(continuous[1:]/quantum)*quantum
    transforms=[PublicDft(real,offset) for real,offset in ((True,0),(True,4),(False,0),(False,4))]
    best=[None]*len(transforms);counts=[0]*len(transforms);matches=[[] for _ in transforms]
    try:
        for number,corner in enumerate(itertools.product((0,1),repeat=16)):
            a=np.float32(np.r_[1.,low+quantum*corner])
            for i,fft in enumerate(transforms):
                prediction=predict(a,gain,fft.execute,'separate','divide_then_gain')[bins].view(np.uint32).astype(np.int64)
                diff=prediction-observed_words.astype(np.int64);mse=float(np.mean(diff**2))
                row=dict(lpc_f32=a.view(np.uint32).tolist(),mse_ulp=mse,exact_bins=int(np.count_nonzero(diff==0)),max_ulp=int(max(abs(diff))))
                counts[i]+=1
                if best[i] is None or (mse,-row['exact_bins'])<(best[i]['mse_ulp'],-best[i]['exact_bins']):best[i]=row
                if mse==0:matches[i].append(row['lpc_f32'])
            if number%16384==16383:print(json.dumps(dict(corners=number+1,best=[r['mse_ulp'] for r in best])),flush=True)
        return [dict(real=fft.real,offset=offset,**row,exact_matches=found,evaluations=n)
                for fft,offset,row,found,n in zip(transforms,(0,4,0,4),best,matches,counts)]
    finally:
        for fft in transforms:fft.close()


def run(document,grid_exponent=23,forward=False,corners=False,public_absolute=False):
    bins=np.array(sorted(map(int,document['words'])))
    observed_words=np.array([document['words'][str(k)] for k in bins],np.uint32)
    observed=observed_words.view(np.float32).astype(float);ulps=np.spacing(observed_words.view(np.float32)).astype(float)
    if len(bins)<32:raise ValueError('at least 32 response bins required')
    gain_raw=(ROOT/'data/bwe2-gains-measured-v1.json').read_bytes()
    if digest(gain_raw)!='f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865':raise RuntimeError('gain source differs')
    gain=float(np.array(json.loads(gain_raw)['excitation_gains_f32'],np.uint32).view(np.float32)[63]);amplitude=128*gain
    a=np.array(fit_transfer(bins,observed/128)['lpc'])
    e=np.exp(-1j*np.pi/512*bins[:,None]*np.arange(17)[None,:])
    for _ in range(12):
        z=e@a;pred=amplitude/abs(z)
        jac=-pred[:,None]*(e[:,1:]*np.conj(z)[:,None]).real/(abs(z)**2)[:,None]/ulps[:,None]
        delta=np.linalg.lstsq(jac,(observed-pred)/ulps,rcond=None)[0];a[1:]+=delta
        if np.max(abs(delta))<1e-13:break
    quantum=arithmetic_steps(a) if grid_exponent==0 else np.full(16,2.**(-grid_exponent))
    base=np.rint(a[1:]/quantum).astype(np.int64);fraction=a[1:]/quantum-base
    if corners:
        fits=corner_search(a,observed_words,bins,gain,quantum)
        return dict(kind='adjacent-corner-lpc-grid-hypothesis',grid_exponent=grid_exponent,coefficient_steps=quantum.tolist(),
                    continuous_lpc=a.tolist(),fits=fits,eligible_lsf=False)
    if forward:
        initial=np.float32(np.r_[1.,base*quantum]);fits=[]
        for real,offset in ((True,0),(True,4),(False,0),(False,4)):
            fitted=forward_lattice(initial,observed_words,bins,gain,quantum,real,offset,public_absolute);fits.append(fitted)
            print(json.dumps({k:v for k,v in fitted.items() if k!='lpc_f32'}),flush=True)
        return dict(kind='forward-float-lattice-hypothesis',grid_exponent=grid_exponent,coefficient_steps=quantum.tolist(),
                    fits=fits,eligible_lsf=False)
    candidates,nodes=nearest_lattice(jac*quantum,(jac*quantum)@fraction)
    fft=PublicDft(True,0);ranked=[]
    try:
        for row in candidates:
            coefficients=np.float32(np.r_[1.,(base+row['offset'])*quantum])
            prediction=predict(coefficients,gain,fft.execute,'separate','divide_then_gain')[bins].view(np.uint32).astype(np.int64)
            error=prediction-observed_words.astype(np.int64)
            ranked.append(dict(lpc_f32=coefficients.view(np.uint32).tolist(),linear_cost=row['cost'],mse_ulp=float(np.mean(error**2)),
                               exact_bins=int(np.count_nonzero(error==0)),max_ulp=int(np.max(abs(error)))))
    finally:fft.close()
    ranked.sort(key=lambda row:(row['mse_ulp'],-row['exact_bins']))
    return dict(kind='joint-lpc-dyadic-grid-hypothesis',grid_exponent=grid_exponent,coefficient_steps=quantum.tolist(),continuous_lpc=a.tolist(),
                enumeration_nodes=nodes,candidates=len(candidates),best=ranked[:20],eligible_lsf=False,
                qualification='the grid and numerical model are hypotheses; fitted LPC does not identify original LSF entries')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spectrum',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--grid-exponent',type=int,default=23)
    p.add_argument('--forward',action='store_true');p.add_argument('--corners',action='store_true');p.add_argument('--public-absolute',action='store_true')
    args=p.parse_args();raw=args.spectrum.read_bytes();result=run(json.loads(raw),args.grid_exponent,args.forward,args.corners,args.public_absolute)
    result.update(spectrum_sha256=digest(raw),producer_sha256=digest(Path(__file__).read_bytes()),created=time.time())
    args.out.mkdir(exist_ok=True,parents=True);path=args.out/(digest(canonical(result))+'.json');atomic(path,canonical(result))
    if args.corners:print(json.dumps(result['fits']),flush=True)
    elif not args.forward:print(json.dumps(dict(nodes=result['enumeration_nodes'],best=result['best'][:2])),flush=True)
    print(path)
