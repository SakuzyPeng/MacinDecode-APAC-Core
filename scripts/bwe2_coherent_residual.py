#!/usr/bin/env python3
"""Reanalyze an existing coherent dual-carrier capture; never call a decoder."""
import argparse
import json
from pathlib import Path
import time

import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import Capture,atomic,validate
from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_spectral_null_wire import ParallelNullWriter
from bwe2_spectral_residual import calibrate,infer


def read_observation(capture,key):
    row=capture.db.execute('SELECT receipt FROM observations WHERE key=?',(key,)).fetchone()
    if row is None:raise RuntimeError('original successful observation is missing')
    receipt=json.loads(row[0]);artifacts={k:capture.read_blob(v) for k,v in receipt['artifacts'].items()}
    request=json.loads(artifacts['request.json'])
    if digest(canonical(request))!=key:raise RuntimeError('original request key differs')
    validate(artifacts,request['frames'],capture.signature)
    return np.frombuffer(artifacts['native/pcm.f32le'],'<f4').reshape(-1,2048,25).astype(float),artifacts


def run(capture,source):
    raw=source.read_bytes();document=json.loads(raw);samples,artifacts=read_observation(capture,document['key'])
    if digest(artifacts['native/pcm.f32le'])!=document['pcm_sha256'] or len(samples)!=len(document['records']):
        raise RuntimeError('coherent capture index differs')
    units=[];targets=[];references=[];seen=set()
    for key,spec_raw in capture.db.execute('SELECT key,spec FROM uses ORDER BY key'):
        spec=json.loads(spec_raw);words=spec.get('words',[])
        if spec.get('bwe') is not False or words.count(0x3f800000)!=1 or any(w not in (0,0x3f800000) for w in words):continue
        _,lines=capture.writer.targets(spec);line=lines[words.index(0x3f800000)]
        if line in seen:continue
        seen.add(line);unit,_=read_observation(capture,key)
        if len(unit)!=1:raise RuntimeError('unit control has more than one query frame')
        units.append(unit[0]);targets.append(line);references.append(dict(key=key,line=line))
    if len(units)<16:raise RuntimeError('insufficient independent original unit-tone controls')
    calibration=calibrate(np.array(units),targets);records=[];words={};consistent=True
    for i,(entry,pcm) in enumerate(zip(document['records'],samples)):
        result=infer(pcm,entry['grid'],entry['targets'],calibration['scale'])
        result.update(frame=i,kind=entry['kind'],targets=entry['targets']);records.append(result)
        if result['qualified']:
            for line,word in zip(entry['targets'],result['words']):
                key=str(line-256)
                if key in words and words[key]!=word:consistent=False
                words[key]=word
    return dict(kind='reproducible-coherent-residual-spectrum',pair=document['pair'],source_sha256=digest(raw),
        receipt_key=document['key'],calibration=calibration,calibration_references=references,
        words=words,records=records,consistent_repeated_anchors=consistent,
        complete=len(words)==256 and consistent and all(r['qualified'] for r in records),eligible_lsf=False)


def main():
    bwe2_blackbox.install_discovery_guard()
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    args=p.parse_args();capture=Capture(args.out,args.binary,writer=ParallelNullWriter())
    try:
        before=capture.status()['attempts'];result=run(capture,args.source)
        if before!=capture.status()['attempts']:raise RuntimeError('read-only reanalysis changed native attempt count')
        producer={name:digest((ROOT/'scripts'/name).read_bytes()) for name in
                  ('bwe2_coherent_residual.py','bwe2_spectral_residual.py','bwe2_spectral_null_wire.py','bwe2_blackbox_capture.py','bwe2_blackbox_math.py','bwe2_blackbox.py')}
        snapshot=capture.out/'producers'/digest(canonical(producer));snapshot.mkdir(parents=True,exist_ok=True)
        for name in producer:
            if not (snapshot/name).exists():atomic(snapshot/name,(ROOT/'scripts'/name).read_bytes())
        result.update(producer=producer,created=time.time());path=capture.out/('coherent-analysis-'+digest(canonical(result))[:16]+'.json')
        atomic(path,canonical(result));print(path,flush=True)
    finally:capture.close()


if __name__=='__main__':main()
