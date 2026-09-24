#!/usr/bin/env python3
"""Strict PCM comparison of restricted Rust SQ synthesis and Apple reference replay.

Every failure, including high-amplitude float differences, remains a failed case.
No alignment search, gain adjustment, tolerance changes or implicit zero frames.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from spectrum_vectors import bundle, frame, matrix_cases
from validate import require, write_json
from validate_replay import command, sha256_file


def sequences():
    for case in matrix_cases():
        yield case['kind'], [{}, case, {}, {}]
    for rate_block in [1, 2, 3]:
        for band in range(14 if rate_block == 2 else 49):
            for side in ['left', 'right']:
                cb = 1 + band % 11
                values = [1,-1,0,1] if cb <= 4 else [16,-17] if cb == 11 else [1,-1]
                active = dict(block=rate_block, grouping=[0,0x55,0x7f][band%3], **{side:{band:(cb,values,160)}})
                sequence = [{},dict(block=1),dict(block=2),dict(block=3),{},{}]
                sequence[rate_block] = active
                yield 'window_transition', sequence
    yield 'overlap', [{},dict(left={0:(1,[1,0,0,0],160)}),dict(left={48:(11,[16,-17],160)},right={1:(5,[1,-1],160)}),{},{}]
    high=dict(gain=255,left={0:(1,[-1,1,-1,1],255)},right={1:(2,[1,-1,1,-1],255)})
    yield 'overlap_pressure',[{},high,high,{},{}]
    yield 'short_pressure',[{},dict(block=1),dict(high,block=2),dict(block=3),{},{}]
    yield 'mixed_windows',[{},dict(high,block=1,right_block=0),dict(block=2,right_block=0),dict(block=3,right_block=0),{},{}]



def batch_cases(items, count=48):
    batch=[]
    for item in items:
        batch.append(item)
        if len(batch)==count:
            yield batch
            batch=[]
    if batch:
        yield batch


def run(binary, rate, batch, first_index, report, reference_fft=None):
    with tempfile.TemporaryDirectory(prefix='apac-synthesis-validation-') as tmp:
        root=Path(tmp)
        payloads=[frame(case)[0] for _,sequence in batch for case in sequence]
        bundle(root/'packets',payloads,rate)
        records={}
        for name in ['replay','decode-sq']:
            args=[name,root/'packets','--out',root/name]
            if name=='replay': args+=['--frames',len(payloads)*1024]
            if name=='replay' and reference_fft:
                env=dict(os.environ,DYLD_INSERT_LIBRARIES=str(reference_fft),APAC_REFERENCE_FFT_AUDIT=str(root/'fft-audit.json'))
                result=subprocess.run([str(binary),*map(str,args)],env=env,capture_output=True,text=True,timeout=120)
                require(result.returncode==0,result.stderr)
                records[name]=json.loads(result.stdout)
                audit=json.loads((root/'fft-audit.json').read_text())
                require(audit['executions']>0 and audit['method']=='f64_dif_round_f32','reference DFT was not controlled')
                report['reference_fft_executions']+=audit['executions']
            else:
                records[name]=command(binary,*args)
        streams=[]
        for name in ['replay','decode-sq']:
            meta=json.loads((root/name/'pcm.json').read_text())
            require(meta['frames']==len(payloads)*1024 and meta['channels']==2 and meta['sample_rate']==rate and meta['all_finite'],'PCM shape mismatch')
            data=(root/name/'pcm.f32le').read_bytes()
            require(hashlib.sha256(data).hexdigest()==meta['sha256'],'PCM hash mismatch')
            values=struct.unpack('<'+str(len(data)//4)+'f',data)
            require(all(math.isfinite(v) for v in values),'nonfinite PCM samples')
            streams.append(values)
        cursor=0
        for i,(kind,sequence) in enumerate(batch):
            count=len(sequence)*2048
            reference,candidate=(s[cursor:cursor+count] for s in streams)
            nonzero=any(any(values) for case in sequence for side in ['left','right'] for _,values,_ in case.get(side,{}).values())
            require(not nonzero or (any(reference) and any(candidate)), 'nonzero spectrum became silence')
            differences=[abs(a-b) for a,b in zip(reference,candidate)]
            bad=[j for j,(a,d) in enumerate(zip(reference,differences)) if d>1e-6+1e-5*abs(a)]
            record=dict(index=first_index+i,rate=rate,kind=kind,frames=count//2,passed=not bad,
                max_absolute_error=max(differences),failed_samples=len(bad),reference_peak=max(map(abs,reference)))
            if bad:
                j=bad[0];record['first_failure']=dict(sample=j,reference=reference[j],candidate=candidate[j])
            report['cases'].append(record)
            cursor+=count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=Path('target/debug/apac-tool'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference-fft',type=Path,help='Explicit diagnostic public-DFT replacement library; not unmodified Apple output.')
    parser.add_argument('--rates',type=int,nargs='+',default=[48000,44100])
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing to overwrite report')
    if sys.platform!='darwin':parser.error('Apple PCM reference validation requires macOS')
    binary=args.binary.resolve()
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    component_sha=sha256_file(component)
    reference_fft=args.reference_fft.resolve() if args.reference_fft else None
    report=dict(schema_version=1,component_sha256=component_sha,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        tool_sha256=sha256_file(binary),started_utc=datetime.now(timezone.utc).isoformat(),atol=1e-6,rtol=1e-5,
        reference='AudioToolbox with diagnostic Float64 DIF DFT' if reference_fft else 'unmodified AudioToolbox replay',
        reference_fft_sha256=sha256_file(reference_fft) if reference_fft else None, reference_fft_executions=0,
        candidate='rust_sq_f32_modulation_f64_fft_v1',cases=[],errors=[])
    for rate in args.rates:
        first=0
        for batch in batch_cases(sequences()):
            try:run(binary,rate,batch,first,report,reference_fft)
            except Exception as error:report['errors'].append(dict(rate=rate,first_case=first,error=str(error)))
            first+=len(batch)
            if first%480==0:print('PCM cases',rate,first,file=sys.stderr,flush=True)
    report.update(passed=not report['errors'] and all(c['passed'] for c in report['cases']),
        passed_cases=sum(c['passed'] for c in report['cases']),failed_cases=sum(not c['passed'] for c in report['cases']),
        max_absolute_error=max((c['max_absolute_error'] for c in report['cases']),default=0),finished_utc=datetime.now(timezone.utc).isoformat())
    write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ['passed','passed_cases','failed_cases','max_absolute_error','errors']}))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
