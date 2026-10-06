#!/usr/bin/env python3
"""Fit public spectral observations against explicit floating-point hypotheses."""
import argparse
import ctypes as C
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


class PublicDft:
    """A numerical hypothesis using documented Accelerate entry points only."""
    def __init__(self,real=False,offset=0,in_place=False):
        self.lib=C.CDLL('/System/Library/Frameworks/Accelerate.framework/Accelerate')
        create=getattr(self.lib,'vDSP_DFT_zrop_CreateSetup' if real else 'vDSP_DFT_zop_CreateSetup')
        create.argtypes=[C.c_void_p,C.c_ulong,C.c_int];create.restype=C.c_void_p
        self.lib.vDSP_DFT_Execute.argtypes=[C.c_void_p]+[C.POINTER(C.c_float)]*4
        self.lib.vDSP_DFT_Execute.restype=None
        self.lib.vDSP_DFT_DestroySetup.argtypes=[C.c_void_p];self.lib.vDSP_DFT_DestroySetup.restype=None
        self.setup=create(None,1024,1)
        if not self.setup:raise RuntimeError('public DFT setup failed')
        self.real=real;self.in_place=in_place;size=512 if real else 1024;self.buffers=[]
        offsets=[offset]*4 if isinstance(offset,int) else list(offset)
        if len(offsets)!=4 or any(not 0<=v<16 for v in offsets):raise ValueError('four bounded buffer offsets required')
        for displacement in offsets:
            backing=np.empty(size+32,dtype=np.float32)
            first=((64-backing.ctypes.data%64)%64)//4+displacement
            self.buffers.append(backing[first:first+size])

    def execute(self,a):
        ir,ii,orr,oi=self.buffers;ir.fill(0);ii.fill(0)
        if self.in_place:orr,oi=ir,ii
        a=np.asarray(a,dtype=np.float32)
        if self.real:ir[:len(a[::2])]=a[::2];ii[:len(a[1::2])]=a[1::2]
        else:ir[:len(a)]=a
        self.lib.vDSP_DFT_Execute(self.setup,*(p.ctypes.data_as(C.POINTER(C.c_float)) for p in (ir,ii,orr,oi)))
        if self.real:
            re=orr.copy()/np.float32(2);im=oi.copy()/np.float32(2);im[0]=0
            return re,im
        return orr[:512].copy(),oi[:512].copy()

    def close(self):
        self.lib.vDSP_DFT_DestroySetup(self.setup)


class SplitComplex(C.Structure):
    _fields_=[('realp',C.POINTER(C.c_float)),('imagp',C.POINTER(C.c_float))]


class PublicRadix2:
    """The documented radix-2 vDSP FFT family, distinct from the DFT API."""
    def __init__(self,real=True,offset=0,in_place=True):
        self.lib=C.CDLL('/System/Library/Frameworks/Accelerate.framework/Accelerate')
        self.lib.vDSP_create_fftsetup.argtypes=[C.c_ulong,C.c_int]
        self.lib.vDSP_create_fftsetup.restype=C.c_void_p
        self.lib.vDSP_destroy_fftsetup.argtypes=[C.c_void_p];self.lib.vDSP_destroy_fftsetup.restype=None
        name='vDSP_fft_'+('zr' if real else 'z')+('ip' if in_place else 'op')
        self.run=getattr(self.lib,name)
        self.run.argtypes=[C.c_void_p,C.POINTER(SplitComplex),C.c_long]
        if not in_place:self.run.argtypes += [C.POINTER(SplitComplex),C.c_long]
        self.run.argtypes += [C.c_ulong,C.c_int];self.run.restype=None
        self.setup=self.lib.vDSP_create_fftsetup(10,0)
        if not self.setup:raise RuntimeError('public radix-2 setup failed')
        self.real,self.in_place=real,in_place;size=512 if real else 1024
        offsets=[offset]*4 if isinstance(offset,int) else list(offset)
        if len(offsets)!=4 or any(not 0<=v<16 for v in offsets):raise ValueError('four bounded buffer offsets required')
        self.buffers=[]
        for displacement in offsets:
            backing=np.empty(size+32,dtype=np.float32)
            first=((64-backing.ctypes.data%64)%64)//4+displacement
            self.buffers.append(backing[first:first+size])

    def execute(self,a):
        ir,ii,orr,oi=self.buffers;ir.fill(0);ii.fill(0)
        a=np.asarray(a,np.float32)
        if self.real:ir[:len(a[::2])]=a[::2];ii[:len(a[1::2])]=a[1::2]
        else:ir[:len(a)]=a
        ptr=lambda p:p.ctypes.data_as(C.POINTER(C.c_float))
        src=SplitComplex(ptr(ir),ptr(ii));args=[self.setup,C.byref(src),1]
        if self.in_place:orr,oi=ir,ii
        else:
            dest=SplitComplex(ptr(orr),ptr(oi));args += [C.byref(dest),1]
        self.run(*args,10,1)
        re,im=orr[:512].copy(),oi[:512].copy()
        if self.real:re/=np.float32(2);im/=np.float32(2);im[0]=0
        return re,im

    def close(self):self.lib.vDSP_destroy_fftsetup(self.setup)


def refine_continuous(a,observed,gain,bins=None):
    bins=np.arange(1,512,2) if bins is None else np.asarray(bins)
    basis=np.exp(-1j*np.pi/512*bins[:,None]*np.arange(17)[None,:])
    target=np.log(128*gain/observed)
    a=np.asarray(a,dtype=float).copy()
    for _ in range(12):
        z=basis@a;residual=np.log(abs(z))-target
        jac=(basis[:,1:]*np.conj(z)[:,None]).real/(abs(z)**2)[:,None]
        step=np.linalg.lstsq(jac,-residual,rcond=None)[0]
        a[1:]+=step
        if max(abs(step))<1e-13:break
    return a


def predict(a,gain,transform,sqrt_mode,division):
    re,im=transform(a);re=np.asarray(re,dtype=np.float32);im=np.asarray(im,dtype=np.float32)
    if sqrt_mode=='separate':energy=np.float32(np.float32(re*re)+np.float32(im*im));env=np.sqrt(energy)
    elif sqrt_mode=='fused':energy=np.float32(re.astype(float)**2+im.astype(float)**2);env=np.sqrt(energy)
    else:env=np.float32(np.sqrt(re.astype(float)**2+im.astype(float)**2))
    if np.any(env<=0) or np.any(~np.isfinite(env)):raise ArithmeticError('nonfinite modeled envelope')
    g=np.float32(gain);x=np.float32(128)
    if division=='divide_then_gain':return np.float32(np.float32(x/env)*g)
    if division=='gain_over_envelope':return np.float32(x*np.float32(g/env))
    if division=='multiply_then_divide':return np.float32(np.float32(x*g)/env)
    return np.float32(float(x)*float(g)/env.astype(float))


def optimize_words(initial,observed_words,predictor,bins=None):
    words=np.asarray(initial,dtype=np.float32).view(np.uint32).copy()
    bins=np.arange(1,512,2) if bins is None else np.asarray(bins);calls=0
    def score(w):
        nonlocal calls
        calls+=1
        predicted=predictor(w.view(np.float32))[bins].view(np.uint32).astype(np.int64)
        delta=predicted-observed_words
        return float(np.mean(delta.astype(float)**2)),int(np.count_nonzero(delta==0)),int(np.max(abs(delta)))
    best=score(words);history=[]
    for iteration in range(10):
        changed=False
        for index in range(1,17):
            current=words[index].item();selected=current;value=best
            for delta in (-32,-16,-8,-4,-2,-1,1,2,4,8,16,32):
                if not 0<=current+delta<2**32:continue
                if (current+delta)&0x7f800000==0x7f800000:continue
                trial=words.copy();trial[index]=current+delta
                result=score(trial)
                if result[0]<value[0] or (result[0]==value[0] and result[1]>value[1]):selected=current+delta;value=result
            if selected!=current:words[index]=selected;best=value;changed=True
        history.append(dict(iteration=iteration,mse_ulp=best[0],exact=best[1],max_ulp=best[2]))
        if not changed:break
    return dict(lpc_f32=words.tolist(),lpc=words.view(np.float32).astype(float).tolist(),mse_ulp=best[0],
                exact_bins=best[1],max_ulp=best[2],model_evaluations=calls,history=history)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spectrum',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();raw=args.spectrum.read_bytes();document=json.loads(raw)
    bins=np.array(sorted(map(int,document['words'])))
    if len(bins)<32:raise ValueError('at least 32 response bins required')
    observed_words=np.array([document['words'][str(k)] for k in bins],dtype=np.uint32)
    observed=observed_words.view(np.float32).astype(float)
    gains_raw=(ROOT/'data/bwe2-gains-measured-v1.json').read_bytes()
    if digest(gains_raw)!='f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865':raise RuntimeError('qualified gain source changed')
    gain=float(np.array(json.loads(gains_raw)['excitation_gains_f32'],dtype=np.uint32).view(np.float32)[63])
    seed=fit_transfer(bins,observed/128)['lpc'];continuous=refine_continuous(seed,observed,gain,bins)
    dfts={};methods={}
    for real in (False,True):
        for offset in (0,4):
            name=('vdsp-real' if real else 'vdsp-complex')+f'-offset{offset}'
            obj=PublicDft(real,offset);dfts[name]=obj;methods[name]=obj.execute
    angle=np.pi/512*np.arange(512)[:,None]*np.arange(17)[None,:]
    methods['double-dft']=lambda a:((np.exp(-1j*angle)@np.asarray(a,dtype=float)).real,(np.exp(-1j*angle)@np.asarray(a,dtype=float)).imag)
    methods['float-direct']=lambda a:(np.sum(np.float32(np.cos(angle))*np.float32(a),axis=1,dtype=np.float32),
                                      -np.sum(np.float32(np.sin(angle))*np.float32(a),axis=1,dtype=np.float32))
    try:
        preliminary=[]
        for name,method in methods.items():
            for rounding in ('separate','fused','hypot'):
                for division in ('divide_then_gain','gain_over_envelope','multiply_then_divide','double'):
                    pred=predict(np.float32(continuous),gain,method,rounding,division)[bins].view(np.uint32).astype(np.int64)
                    delta=pred-observed_words.astype(np.int64)
                    preliminary.append(dict(transform=name,sqrt=rounding,division=division,mse_ulp=float(np.mean(delta.astype(float)**2)),exact_bins=int(np.count_nonzero(delta==0))))
        selected=sorted(preliminary,key=lambda d:d['mse_ulp'])[:6];fitted=[]
        for spec in selected:
            predictor=lambda a:predict(a,gain,methods[spec['transform']],spec['sqrt'],spec['division'])
            result=optimize_words(continuous,observed_words.astype(np.int64),predictor,bins)
            fitted.append(dict(spec=spec,**result));print(json.dumps(dict(spec=spec,mse_ulp=result['mse_ulp'],exact_bins=result['exact_bins'],max_ulp=result['max_ulp'])),flush=True)
        result=dict(kind='public-fft-rounding-hypotheses',spectrum_sha256=digest(raw),gain_source_sha256=digest(gains_raw),
            continuous_lpc=continuous.tolist(),bins=bins.tolist(),preliminary=preliminary,fitted=fitted,created=time.time(),eligible_lsf=False,
            qualification='numerical hypotheses evaluated through public vDSP APIs; no native LPC or LSF values were inspected')
        args.out.mkdir(parents=True,exist_ok=True)
        path=args.out/(digest(canonical(result))+'.json');atomic(path,canonical(result));print(path,flush=True)
    finally:
        for obj in dfts.values():obj.close()


if __name__=='__main__':main()
