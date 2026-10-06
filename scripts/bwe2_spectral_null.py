#!/usr/bin/env python3
"""Recover observable BWE2 spectral Float32 words by exact HOA cancellation."""
import argparse
import json
import os
from pathlib import Path
import struct
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import Capture,atomic
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_spectral_null_wire import DualNullWriter,split_float


def value(word):return struct.unpack('<f',struct.pack('<I',word))[0]
def word(value):return struct.unpack('<I',struct.pack('<f',float(value)))[0]


class NullOracle:
    def __init__(self,capture):
        self.capture=capture
        self.producer={p.name:digest(p.read_bytes()) for p in [ROOT/'scripts'/name for name in
            ('bwe2_spectral_null.py','bwe2_spectral_null_wire.py','bwe2_blackbox_capture.py','bwe2_blackbox_wire.py','bwe2_blackbox.py')]}
        directory=capture.out/'producers'/digest(canonical(self.producer));directory.mkdir(parents=True,exist_ok=True)
        for name in self.producer:
            if not (directory/name).exists():atomic(directory/name,(ROOT/'scripts'/name).read_bytes())

    def check(self):
        for name,sha in self.producer.items():
            if digest((ROOT/'scripts'/name).read_bytes())!=sha:raise RuntimeError('nulling producer changed')

    def probe(self,label,spec,replicate=''):
        self.check();key,raw=self.capture.probe(label,spec,replicate)
        samples=np.frombuffer(raw,dtype='<f4').reshape(-1,9).astype(float)
        if np.any(samples[:,1:]!=0):raise RuntimeError('unexpected other HOA output')
        return key,samples[:,0],digest(raw)

    def recover(self,pair,source_line,target_line,gain=63):
        band,targets=self.capture.writer.targets(dict(source_line=source_line,target_line=target_line))
        identity=dict(pair=list(pair),source_line=source_line,targets=targets,gain=gain,source_gain=128)
        token=digest(canonical(identity))
        directory=self.capture.out/'certificates';directory.mkdir(exist_ok=True)
        path=directory/(token+'.json')
        if path.exists():
            result=json.loads(path.read_bytes())
            if result['identity']!=identity:raise RuntimeError('certificate identity differs')
            for reference in result['references']:
                cached=self.capture.db.execute('SELECT receipt FROM observations WHERE key=?',(reference['key'],)).fetchone()
                if cached is None:raise RuntimeError('certificate observation missing')
                receipt=json.loads(cached[0]);raw=self.capture.read_blob(receipt['artifacts']['native/pcm.f32le'])
                if digest(raw)!=reference['pcm_sha256']:raise RuntimeError('certificate PCM changed')
            return result
        spec=dict(source_line=source_line,target_line=target_line,parameters=[*pair,gain],source_gain=128)
        references=[]
        def probe(label,args,replicate=''):
            key,samples,sha=self.probe(token+'-'+label,args,replicate)
            references.append(dict(label=label,key=key,pcm_sha256=sha))
            return samples,key
        control,_=probe('power-invariance',dict(spec,flip=False,words=[0]*len(targets)))
        if np.any(control!=0):raise RuntimeError('identical-power carriers do not cancel')
        units=[]
        for j in range(len(targets)):
            words=[0]*len(targets);words[j]=word(1.)
            unit,_=probe('unit-'+str(j),dict(spec,bwe=False,words=words))
            if not np.any(unit):raise RuntimeError('unobservable cancellation basis')
            units.append(unit)
        matrix=np.array(units).T;gram=matrix.T@matrix
        if np.linalg.cond(matrix)>2:raise RuntimeError('ill-conditioned cancellation control')
        project=lambda y:np.linalg.solve(gram,matrix.T@y)
        baseline,_=probe('uncancelled',dict(spec,words=[0]*len(targets)))
        estimates=-project(baseline)
        if np.any(estimates<=0):raise RuntimeError('nonpositive spectral value')
        words=[word(v) for v in estimates]
        discovered=None;attempts=[]
        for iteration in range(8):
            for w in words:split_float(w)
            residual,key=probe('candidate-'+str(iteration),dict(spec,words=words))
            amplitudes=project(residual)
            attempts.append(dict(words=list(words),key=key,residual_amplitudes=amplitudes.tolist(),exact_zero=bool(np.all(residual==0))))
            if np.all(residual==0):discovered=list(words);break
            next_words=[word(value(w)-delta) for w,delta in zip(words,amplitudes)]
            if next_words==words:raise RuntimeError('residual is not removable on the Float32 grid')
            words=next_words
        if discovered is None:raise RuntimeError('spectral cancellation failed to converge')
        frozen=dict(identity=identity,words=discovered,discovery=attempts,references=list(references),producer=self.producer,
                    qualification='observable spectral outputs, not original LSF entries')
        frozen_dir=self.capture.out/'frozen';frozen_dir.mkdir(exist_ok=True)
        frozen_path=frozen_dir/(token+'.json')
        if frozen_path.exists():
            old=json.loads(frozen_path.read_bytes())
            if old['identity']!=identity or old['words']!=discovered:raise RuntimeError('frozen spectral candidate differs')
        else:atomic(frozen_path,canonical(dict(created=time.time(),**frozen)))
        frozen_sha=digest(frozen_path.read_bytes());checks=[]
        for j in range(len(targets)):
            for delta in (-1,1):
                candidate=discovered.copy();candidate[j]+=delta
                residual,key=probe(f'neighbor-{j}-{delta}',dict(spec,words=candidate),frozen_sha)
                amplitudes=project(residual)
                passed=bool(np.any(residual) and amplitudes[j]*delta>0)
                checks.append(dict(kind='neighbor',coordinate=j,delta=delta,key=key,passed=passed,
                    residual_amplitudes=amplitudes.tolist()))
        for label,extra,candidate in (
            ('independent-signs',dict(seed=20261006),discovered),
            ('double-amplitude',dict(source_gain=132,seed=20261007),[word(2*value(w)) for w in discovered])):
            residual,key=probe(label,dict(spec,words=candidate,**extra),frozen_sha)
            checks.append(dict(kind=label,key=key,passed=bool(np.all(residual==0))))
        if not all(r['passed'] for r in checks):
            failure=dict(identity=identity,words=discovered,frozen_sha256=frozen_sha,checks=checks,
                references=references,created=time.time(),producer=self.producer,eligible_lsf=False,
                status='independent-validation-failed')
            failures=self.capture.out/'validation-failures';failures.mkdir(exist_ok=True)
            failure_path=failures/(digest(canonical(failure))+'.json');atomic(failure_path,canonical(failure))
            raise RuntimeError('spectral null certificate failed independent validation: '+str(failure_path))
        result=dict(identity=identity,words=discovered,values=[value(w) for w in discovered],frozen_sha256=frozen_sha,
            checks=checks,references=references,created=time.time(),producer=self.producer,
            exact_spectral_words=True,eligible_lsf=False)
        atomic(path,canonical(result));return result


def main():
    bwe2_blackbox.install_discovery_guard()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence',type=Path);parser.add_argument('--mount',type=Path)
    parser.add_argument('--pair',type=int,nargs=2,default=(0,0));parser.add_argument('--sources',type=int,default=64)
    args=parser.parse_args()
    if not 1<=args.sources<=64 or any(not 0<=v<512 for v in args.pair):parser.error('invalid bounded sweep')
    capture=Capture(args.out,args.binary,args.evidence,args.mount,writer=DualNullWriter())
    try:
        oracle=NullOracle(capture);results=[];words={}
        for source_line in range(129,129+2*args.sources,2):
            visited=set()
            for target in range(source_line+128,source_line+512+1,128):
                band,targets=capture.writer.targets(dict(source_line=source_line,target_line=target))
                if band in visited:continue
                visited.add(band)
                result=oracle.recover(args.pair,source_line,target);results.append(result['frozen_sha256'])
                words.update({str(line-256):w for line,w in zip(result['identity']['targets'],result['words'])})
            print(json.dumps(dict(source_line=source_line,words=len(words),calls=sum(capture.status()['attempts'].values()))),flush=True)
        capture.audit();oracle.check()
        value_=dict(kind='exact-observable-bwe2-spectrum',pair=args.pair,source_gain=128,gain_index=63,
            words=words,certificates=results,producer=oracle.producer,status=capture.status(),eligible_lsf=False,created=time.time())
        directory=capture.out/'spectra';directory.mkdir(exist_ok=True)
        path=directory/(digest(canonical(value_))+'.json');atomic(path,canonical(value_));print(path,flush=True)
    finally:capture.close()


if __name__=='__main__':main()
