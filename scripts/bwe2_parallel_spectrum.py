#!/usr/bin/env python3
"""Observe BWE2 rounding profiles with simultaneous in-frame null candidates."""
import argparse
import gzip
import itertools
import json
import os
from pathlib import Path
import sqlite3
import struct
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import Capture,atomic
from bwe2_blackbox_math import spectrum
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_spectral_null_wire import ParallelNullWriter


def float_word(x):return struct.unpack('<I',struct.pack('<f',float(x)))[0]
def float_value(w):return struct.unpack('<f',struct.pack('<I',w))[0]


def candidate_grid(centers):
    count=25 if len(centers)==1 else 5
    choices=[]
    for center in centers:
        exponent=center>>23
        if not 1<=exponent<=254:raise ValueError('candidate center outside normal positive Float32 range')
        lo,hi=exponent<<23,((exponent+1)<<23)-1
        first=max(lo,min(center-count//2,hi-count+1))
        choices.append(range(first,first+count))
    return [list(row) for row in itertools.product(*choices)]


class Observer:
    def __init__(self,capture,old_root):
        self.capture=capture;self.old_root=Path(old_root)
        old=json.loads((self.old_root/'manifest.json').read_bytes())
        if old['native_identity']!=capture.identity or old['volume']!=capture.config['volume']:
            raise RuntimeError('seed observations have a different native identity')
        self.pool=Path(old['evidence'])/'objects'
        self.db=sqlite3.connect(f'file:{self.old_root/"state.sqlite3"}?mode=ro',uri=True)
        raw=(self.old_root/'analyses/lsf-probe-1a8a04be0459864f.json').read_bytes()
        if digest(raw)!='1a8a04be0459864f41728e90ea84a71ba451a35b16707b9ea36fcf39ebd869fa':
            raise RuntimeError('original observation index changed')
        self.pilot=json.loads(raw);self.pilot_sha=digest(raw)
        self.producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
            ('bwe2_parallel_spectrum.py','bwe2_spectral_null_wire.py','bwe2_blackbox_capture.py','bwe2_blackbox_wire.py','bwe2_blackbox_math.py','bwe2_blackbox.py')}
        snapshot=capture.out/'producers'/digest(canonical(self.producer));snapshot.mkdir(parents=True,exist_ok=True)
        for name in self.producer:
            if not (snapshot/name).exists():atomic(snapshot/name,(ROOT/'scripts'/name).read_bytes())

    def seed(self,pair):
        entry=next(r for r in self.pilot['estimates'] if r['pair']==list(pair))
        receipt=json.loads(self.db.execute('SELECT receipt FROM observations WHERE key=?',(entry['key'],)).fetchone()[0])
        sha=receipt['artifacts']['native/pcm.f32le'];raw=gzip.decompress((self.pool/(sha+'.gz')).read_bytes())
        if digest(raw)!=sha:raise RuntimeError('seed PCM corrupted')
        return abs(spectrum(raw,self.pilot['calibration']['scale']))

    def probe(self,label,spec,replicate=''):
        for name,sha in self.producer.items():
            if digest((ROOT/'scripts'/name).read_bytes())!=sha:raise RuntimeError('observer producer changed')
        key,raw=self.capture.probe(label,spec,replicate)
        return key,np.frombuffer(raw,dtype='<f4').reshape(-1,25).astype(float),digest(raw)

    def observe(self,pair,source_line,target_line,initial,replicate):
        band,targets=self.capture.writer.targets(dict(source_line=source_line,target_line=target_line))
        spec=dict(parameters=[*pair,63],source_gain=128,source_line=source_line,target_line=target_line)
        centers=[float_word(initial[line]) for line in targets]
        controls=[];attempts=[]
        for iteration in range(5):
            grid=candidate_grid(centers)
            key,samples,sha=self.probe('grid-'+str(pair)+'-'+str(source_line)+'-'+str(band),dict(spec,candidates=grid),replicate+f'-{iteration}')
            zeros=np.where(np.all(samples==0,axis=0))[0].tolist()
            attempts.append(dict(key=key,pcm_sha256=sha,grid=grid,zero_channels=zeros))
            if len(zeros)>1:raise RuntimeError('more than one candidate channel is exactly zero')
            if len(zeros)==1:
                chosen=zeros[0]
                result=dict(pair=pair,source_line=source_line,targets=targets,words=grid[chosen],zero_channel=chosen,
                    simultaneous_unique_zero=True,attempts=attempts,controls=controls,
                    qualification='per-invocation differential spectral response; not a claim of invariant LSF or LPC words')
                directory=self.capture.out/'null-records';directory.mkdir(exist_ok=True)
                path=directory/(digest(canonical(result))+'.json')
                if not path.exists():atomic(path,canonical(result))
                return result
            channel=12;units=[]
            for coordinate in range(len(targets)):
                words=[0]*len(targets);words[coordinate]=float_word(1.)
                k,unit,unit_sha=self.probe('unit-'+str(targets)+'-'+str(coordinate),dict(spec,bwe=False,words=words))
                controls.append(dict(key=k,pcm_sha256=unit_sha));units.append(unit[:,channel])
            matrix=np.array(units).T
            if np.linalg.cond(matrix)>2:raise RuntimeError('null residual calibration is ill conditioned')
            delta=np.linalg.lstsq(matrix,samples[:,channel],rcond=None)[0]
            candidate=grid[channel]
            centers=[float_word(float_value(w)-d) for w,d in zip(candidate,delta)]
        raise RuntimeError('no exact simultaneous null within the bounded search')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--observations',type=Path,required=True);parser.add_argument('--evidence',type=Path);parser.add_argument('--mount',type=Path)
    parser.add_argument('--pair',type=int,nargs=2,default=(0,0));parser.add_argument('--sources',type=int,default=64)
    parser.add_argument('--replicate',default='parallel-spectrum-v1')
    args=parser.parse_args()
    if not 1<=args.sources<=64 or any(not 0<=i<512 for i in args.pair):parser.error('invalid bounded scope')
    capture=Capture(args.out,args.binary,args.evidence,args.mount,writer=ParallelNullWriter())
    try:
        observer=Observer(capture,args.observations);initial=observer.seed(args.pair);entries=[];words={}
        for source_line in range(129,129+2*args.sources,2):
            bands=set()
            for target in range(source_line+128,source_line+513,128):
                band,targets=capture.writer.targets(dict(source_line=source_line,target_line=target))
                if band in bands:continue
                bands.add(band)
                result=observer.observe(args.pair,source_line,target,initial,args.replicate)
                entries.append(result)
                words.update({str(line-256):w for line,w in zip(result['targets'],result['words'])})
            print(json.dumps(dict(source_line=source_line,words=len(words),calls=sum(capture.status()['attempts'].values()))),flush=True)
        capture.audit()
        value=dict(kind='simultaneous-null-spectral-observations',pair=args.pair,source_gain=128,gain_index=63,
            source_index_sha256=observer.pilot_sha,words=words,entries=entries,producer=observer.producer,
            replicate=args.replicate,status=capture.status(),created=time.time(),eligible_lsf=False)
        directory=capture.out/'spectra';directory.mkdir(exist_ok=True)
        path=directory/(digest(canonical(value))+'.json');atomic(path,canonical(value));print(path,flush=True)
    finally:capture.close()


if __name__=='__main__':main()
