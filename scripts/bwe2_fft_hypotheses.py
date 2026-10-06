#!/usr/bin/env python3
"""Compare public FFT families and two-carrier rounding hypotheses offline."""
import argparse
import itertools
import json
from pathlib import Path
import time

import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import atomic
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_float_profile import PublicDft,PublicRadix2,predict


def score(values,observed):
    delta=values.view(np.uint32).astype(np.int64)-observed.astype(np.int64)
    return dict(mse_ulp=float(np.mean(delta**2)),exact_bins=int(np.count_nonzero(delta==0)),
                max_ulp=int(np.max(abs(delta))))


def run(a,observed,gain,all_offsets=False):
    bins=np.arange(1,512,2);transforms={};failures=[]
    offsets=list(itertools.product((0,4,8,12),repeat=4)) if all_offsets else [(x,)*4 for x in (0,1,2,4,8,12)]
    for family,kind in (('dft',PublicDft),('radix2',PublicRadix2)):
        for real,in_place,alignment in itertools.product((True,False),(True,False),offsets):
            if in_place and alignment[2:]!=alignment[:2]:continue
            spec=dict(family=family,real=real,in_place=in_place,alignment=alignment)
            obj=kind(real=real,in_place=in_place,offset=alignment)
            try:
                re,im=obj.execute(a)
                # A documented FFT is a numerical model, not evidence about
                # which function is used inside the native codec.
                error=np.max(abs(re+1j*im-np.fft.fft(a,1024)[:512]))
                if error>1e-4:raise ArithmeticError('public transform normalization differs')
                key=digest(re.tobytes()+im.tobytes())
                if key not in transforms:transforms[key]=dict(specs=[],re=re,im=im)
                transforms[key]['specs'].append(spec)
            except Exception as error:failures.append(dict(spec=spec,error=repr(error)))
            finally:obj.close()
    models={}
    for key,transform in transforms.items():
        for sqrt,division in itertools.product(('separate','fused','hypot'),('divide_then_gain','gain_over_envelope','multiply_then_divide','double')):
            values=predict(a,gain,lambda _: (transform['re'],transform['im']),sqrt,division)[bins]
            code=digest(values.tobytes())
            if code not in models:models[code]=dict(values=values,specs=[],**score(values,observed))
            models[code]['specs'].append(dict(transform=key,sqrt=sqrt,division=division))
    ranked=sorted(models,key=lambda k:models[k]['mse_ulp']);mixtures=[]
    for i,left in enumerate(ranked):
        for right in ranked[i:]:
            average=np.float32(models[left]['values']*.5+models[right]['values']*.5)
            result=score(average,observed)
            mixtures.append(dict(left=left,right=right,**result))
    mixtures.sort(key=lambda r:(r['mse_ulp'],-r['exact_bins']))
    return dict(kind='public-fft-model-family-and-carrier-mixtures',eligible_lsf=False,
        qualification='same fitted LPC tested across public numerical models; no native intermediate state read',
        lpc_f32=np.float32(a).view(np.uint32).tolist(),transforms={k:v['specs'] for k,v in transforms.items()},
        models={k:dict(specs=v['specs'],values_f32=v['values'].view(np.uint32).tolist(),
                       **{f:v[f] for f in ('mse_ulp','exact_bins','max_ulp')}) for k,v in models.items()},
        best_models=ranked[:10],best_mixtures=mixtures[:20],failures=failures)


def main():
    bwe2_blackbox.install_discovery_guard()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,required=True);p.add_argument('--spectrum',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--all-offsets',action='store_true')
    args=p.parse_args();profile_raw=args.profile.read_bytes();spectrum_raw=args.spectrum.read_bytes()
    profile=json.loads(profile_raw);document=json.loads(spectrum_raw)
    a=np.array(min(profile['fitted'],key=lambda r:r['mse_ulp'])['lpc_f32'],np.uint32).view(np.float32)
    observed=np.array([document['words'][str(k)] for k in range(1,512,2)],np.uint32)
    gains=(ROOT/'data/bwe2-gains-measured-v1.json').read_bytes()
    if digest(gains)!='f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865':raise RuntimeError('gain source changed')
    gain=np.array(json.loads(gains)['excitation_gains_f32'],np.uint32).view(np.float32)[63]
    result=run(a,observed,gain,args.all_offsets)
    producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
              ('bwe2_fft_hypotheses.py','bwe2_float_profile.py','bwe2_blackbox.py')}
    result.update(profile_sha256=digest(profile_raw),spectrum_sha256=digest(spectrum_raw),gain_sha256=digest(gains),
                  producer=producer,created=time.time())
    args.out.mkdir(exist_ok=True,parents=True)
    snapshot=args.out/'producers'/digest(canonical(producer));snapshot.mkdir(parents=True,exist_ok=True)
    for name in producer:
        if not (snapshot/name).exists():atomic(snapshot/name,(ROOT/'scripts'/name).read_bytes())
    path=args.out/(digest(canonical(result))+'.json');atomic(path,canonical(result))
    print(json.dumps(dict(transforms=len(result['transforms']),models=len(result['models']),
        best=[{k:v for k,v in result['models'][key].items() if k!='values_f32'} for key in result['best_models'][:2]],
        mixtures=result['best_mixtures'][:3],failures=result['failures'])),flush=True)
    print(path)


if __name__=='__main__':main()
