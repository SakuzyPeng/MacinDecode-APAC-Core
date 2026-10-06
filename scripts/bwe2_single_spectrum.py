#!/usr/bin/env python3
"""Single-carrier BWE2 spectra with public linear HOA bands and sparse power."""
import argparse
import itertools
import json
from pathlib import Path
import time

import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import Capture,atomic
from bwe2_blackbox_math import envelope
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_spectral_null_wire import SingleCarrierWriter,WideSingleCarrierWriter
from bwe2_spectral_residual import calibrate,infer


def grid(centers,channels=16):
    if channels==25:
        if len(centers) not in (4,8):raise ValueError('four or eight centers required')
        result=[list(centers)]
        for j in range(len(centers)):
            for step in (-1,1):
                row=list(centers);word=row[j]
                if not 0<word<0x7f800000:raise ValueError('positive finite centers required')
                row[j]=max((word>>23)<<23,min(word+step,(((word>>23)+1)<<23)-1));result.append(row)
        while len(result)<channels:result.append(list(centers))
        return result
    if len(centers)!=4 or channels!=16:raise ValueError('four centers and 16 channels required')
    choices=[]
    for word in centers:
        if not 0<word<0x7f800000:raise ValueError('positive finite centers required')
        lo=(word>>23)<<23;hi=lo+(1<<23)-1
        first=max(lo,min(word,hi-1));choices.append((first,first+1))
    return [list(row) for row in itertools.product(*choices)]


class Experiment:
    def __init__(self,capture):
        self.capture=capture
        self.producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
            ('bwe2_single_spectrum.py','bwe2_spectral_null_wire.py','bwe2_spectral_residual.py',
             'bwe2_blackbox_capture.py','bwe2_blackbox_wire.py','bwe2_blackbox_math.py','bwe2_blackbox.py',
             'bwe2_public_lpc.py','bwe2_public_math.py','bwe2_float_profile.py','bwe2_lpc_lattice.py')}
        folder=capture.out/'producers'/digest(canonical(self.producer));folder.mkdir(parents=True,exist_ok=True)
        for name in self.producer:
            if not (folder/name).exists():atomic(folder/name,(ROOT/'scripts'/name).read_bytes())

    def probe(self,label,sequence,replicate=''):
        for name,sha in self.producer.items():
            if digest((ROOT/'scripts'/name).read_bytes())!=sha:raise RuntimeError('producer changed during capture')
        key,raw=self.capture.probe(label,dict(sequence=sequence),replicate)
        return dict(key=key,pcm_sha256=digest(raw)),np.frombuffer(raw,'<f4').reshape(len(sequence),2048,self.capture.signature['channels']).astype(float)

    def save(self,kind,result):
        result=dict(kind=kind,producer=self.producer,native_identity=self.capture.identity,wire_signature=self.capture.signature,
                    code_commit=__import__('subprocess').check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
                    created=time.time(),eligible_lsf=False,**result)
        path=self.capture.out/(kind+'-'+digest(canonical(result))[:16]+'.json');atomic(path,canonical(result));return path

    def controls(self):
        sequence=[];targets=[]
        wide=self.capture.signature['channels']==25
        for phase in ((1,3,5,7) if wide else (8,)):
            for band in range(4,12):
                lines=self.capture.writer.targets(dict(band=band,phase=phase))[1]
                for coordinate,line in enumerate(lines):
                    if wide and coordinate not in (0,7):continue
                    words=[0]*len(lines);words[coordinate]=0x3f800000
                    sequence.append(dict(band=band,phase=phase,bwe=False,words=words));targets.append(line)
        receipt,samples=self.probe('single-carrier-unit-calibration',sequence)
        calibration=calibrate(samples,targets)
        # Known 24-bit values straddle mantissa carries; residuals from -64 to
        # +64 ULP verify the lattice bound without any unknown dictionary.
        sequence=[];specs=[]
        for band,coordinate,word in ((4,0,0x4737bf01),(7,2,0x4712ffff),(11,3,0x46a10000)):
            if wide:word&=0xffff0000
            for delta in (-64,-1,0,1,64):
                candidates=[[0]*4 for _ in range(self.capture.signature['channels'])]
                for channel in range(len(candidates)):candidates[channel][coordinate]=word+delta+channel-8
                spec=dict(band=band,coordinate=coordinate,synthetic_word=word,candidates=candidates)
                sequence.append(spec);specs.append(spec)
        known_receipt,known_samples=self.probe('single-carrier-known-word-controls',sequence)
        checks=[]
        for spec,raw in zip(specs,known_samples):
            coordinate=spec['coordinate'];line=self.capture.writer.targets(spec)[1][coordinate]
            candidates=[[row[coordinate]] for row in spec['candidates']]
            result=infer(raw,candidates,[line],calibration['scale'])
            result.update(band=spec['band'],coordinate=coordinate,expected_word=spec['synthetic_word'])
            result['passed']=result['qualified'] and result['words']==[spec['synthetic_word']];checks.append(result)
        path=self.save('single-controls',dict(calibration=calibration,unit_receipt=receipt,
            known_receipt=known_receipt,checks=checks,passed=all(row['passed'] for row in checks)))
        if not all(row['passed'] for row in checks):raise RuntimeError('known-word control failed: '+str(path))
        return path

    def measure(self,controls,seed,pair,replicate,seed_sha256=None):
        control_raw=controls.read_bytes();control=json.loads(control_raw)
        if not control['passed'] or control['native_identity']!=self.capture.identity:raise RuntimeError('invalid calibration identity')
        if control.get('wire_signature',SingleCarrierWriter().signature())!=self.capture.signature:raise RuntimeError('calibration wire identity differs')
        # Seed amplitudes are only search centers; every measured word must be
        # independently inferred from new PCM and survive within-frame checks.
        sequence=[]
        wide=self.capture.signature['channels']==25
        for phase in ((1,3,5,7) if wide else (8,)):
            for band in range(4,12):
                _,targets=self.capture.writer.targets(dict(band=band,phase=phase))
                centers=[np.float32(seed[line-256]).view(np.uint32).item() for line in targets]
                sequence.append(dict(band=band,phase=phase,parameters=[*pair,63],candidates=grid(centers,self.capture.signature['channels'])))
        receipt,samples=self.probe('single-carrier-spectrum-'+str(pair),sequence,replicate)
        records=[];words={}
        for spec,raw in zip(sequence,samples):
            band,targets=self.capture.writer.targets(spec)
            result=infer(raw,spec['candidates'],targets,control['calibration']['scale'])
            result.update(band=band,phase=spec['phase'],targets=targets,candidates=spec['candidates']);records.append(result)
            if result['qualified']:words.update({str(line-256):word for line,word in zip(targets,result['words'])})
        path=self.save('single-spectrum',dict(pair=pair,controls_sha256=digest(control_raw),receipt=receipt,
            records=records,words=words,complete=len(words)==(256 if wide else 32),replicate=replicate,
            source_gain=128,gain_index=63,seed_sha256=seed_sha256,
            qualification='one BWE carrier; conditional residual lattice with separately recorded exact zeros'))
        return path

    def validate_model(self,model_path,replicate,source_gain=128,seed=None):
        from bwe2_public_lpc import POLICY,variants
        raw=model_path.read_bytes();model=json.loads(raw)
        if not model['fit_complete'] or model['policy']!=POLICY:raise ValueError('unfrozen or unsupported LPC model')
        predictions=variants(model['lpc_f32'],source_gain);pair=model['pair'];sequence=[];expected=[]
        for phase in (1,3,5,7):
            for band in range(4,12):
                _,targets=self.capture.writer.targets(dict(band=band,phase=phase));bins=np.array(targets)-256
                options={offset:value[bins].view(np.uint32).tolist() for offset,value in predictions.items()}
                candidates=list(options.values())
                # The alternative global alignment is presented in the same
                # native frame. Neighbor controls make a broad zero response
                # distinguishable from a unique cancellation value.
                for coordinate in range(len(targets)):
                    for delta in (-1,1):
                        row=list(candidates[0]);row[coordinate]+=delta;candidates.append(row)
                while len(candidates)<25:candidates.append(list(candidates[0]))
                sequence.append(dict(band=band,phase=phase,parameters=[*pair,63],source_gain=source_gain,
                                     seed=seed,candidates=candidates));expected.append(options)
        receipt,samples=self.probe('frozen-lpc-model-validation',sequence,digest(raw)+'-'+replicate)
        started=self.capture.db.execute("SELECT started FROM attempts WHERE key=? AND status='success'",(receipt['key'],)).fetchone()[0]
        if started<model['created']:raise RuntimeError('validation observation predates the frozen model')
        records=[];global_offsets=set(predictions)
        for i,(spec,options,y) in enumerate(zip(sequence,expected,samples)):
            zeros=np.where(np.all(y==0,axis=0))[0].tolist()
            zero_values={tuple(spec['candidates'][j]) for j in zeros}
            matched={offset for offset,word in options.items() if tuple(word) in zero_values}
            global_offsets &= matched
            passed=len(zero_values)==1 and bool(matched)
            records.append(dict(frame=i,band=spec['band'],phase=spec['phase'],zero_channels=zeros,
                                unique_zero_values=[list(v) for v in zero_values],matching_offsets=sorted(matched),passed=passed))
        path=self.save('lpc-model-validation',dict(candidate_sha256=digest(raw),pair=pair,receipt=receipt,
            source_gain=source_gain,seed=seed,records=records,global_matching_offsets=sorted(global_offsets),
            passed=bool(global_offsets) and all(row['passed'] for row in records),
            qualification='exact PCM cancellation validates this observable LPC model, not the original two-stage LSF dictionary'))
        return path


def seed_from_profile(path,gain):
    document=json.loads(path.read_bytes())
    a=np.array(document['continuous_lpc'],float)
    z=np.exp(-1j*np.pi/512*np.arange(512)[:,None]*np.arange(17)[None,:])@a
    return 128*gain/abs(z)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('controls','measure','validate-model'));p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--evidence',type=Path);p.add_argument('--mount',type=Path)
    p.add_argument('--controls',type=Path)
    seed_group=p.add_mutually_exclusive_group();seed_group.add_argument('--profile',type=Path);seed_group.add_argument('--seed-spectrum',type=Path)
    p.add_argument('--pair',type=int,nargs=2,default=(0,0))
    p.add_argument('--replicate',default='single-carrier-v1')
    p.add_argument('--wide',action='store_true',help='one carrier and eight tones per linear HOA band, 25 output channels')
    p.add_argument('--model',type=Path);p.add_argument('--source-gain',type=int,choices=(128,132),default=128);p.add_argument('--seed',type=int)
    args=p.parse_args()
    if any(not 0<=i<512 for i in args.pair):p.error('pair outside bounded dictionary syntax')
    capture=Capture(args.out,args.binary,args.evidence,args.mount,writer=WideSingleCarrierWriter() if args.wide else SingleCarrierWriter())
    try:
        experiment=Experiment(capture)
        if args.stage=='controls':path=experiment.controls()
        elif args.stage=='validate-model':
            if not args.wide or args.model is None:p.error('model validation requires --wide and --model')
            path=experiment.validate_model(args.model,args.replicate,args.source_gain,args.seed)
        else:
            if args.controls is None or (args.profile is None and args.seed_spectrum is None):p.error('measure requires controls and a profile or seed spectrum')
            gains=(ROOT/'data/bwe2-gains-measured-v1.json').read_bytes()
            if digest(gains)!='f2ee23ee8d2080482dc6317caac83047d0da9de6b73d775941578ede7983e865':raise RuntimeError('gain source changed')
            gain=float(np.array(json.loads(gains)['excitation_gains_f32'],np.uint32).view(np.float32)[63])
            if args.seed_spectrum is not None:
                seed_raw=args.seed_spectrum.read_bytes();document=json.loads(seed_raw)
                if document['pair']!=args.pair or not document['complete']:raise RuntimeError('incomplete or unrelated seed spectrum')
                seed=np.full(512,np.nan)
                for k,word in document['words'].items():seed[int(k)]=np.array([word],np.uint32).view(np.float32)[0]
            else:seed_raw=args.profile.read_bytes();seed=seed_from_profile(args.profile,gain)
            path=experiment.measure(args.controls,seed,args.pair,args.replicate,digest(seed_raw))
        print(path,flush=True);print(json.dumps(capture.status()),flush=True)
    finally:capture.close()


if __name__=='__main__':main()
