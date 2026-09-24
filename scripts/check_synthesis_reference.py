#!/usr/bin/env python3
"""Check whether two original vDSP output-alignment paths admit one PCM oracle.

Only output storage alignment is controlled; the original vDSP executes both
DFTs. Disjoint atol/rtol intervals mean no one candidate sample can pass both.
This diagnostic never changes a decoder result or treats a mismatch as a pass.
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

from spectrum_vectors import frame,bundle
from validate import require,write_json
from validate_replay import sha256_file


def disjoint_intervals(a,b,atol=1e-6,rtol=1e-5):
    require(len(a)==len(b),'reference lengths differ')
    require(atol>=0 and rtol>=0 and math.isfinite(atol) and math.isfinite(rtol),'invalid tolerances')
    failures=[]
    for i,(x,y) in enumerate(zip(a,b)):
        require(math.isfinite(x) and math.isfinite(y),'nonfinite reference')
        dx,dy=atol+rtol*abs(x),atol+rtol*abs(y)
        if abs(x-y)>dx+dy:
            failures.append(dict(sample=i,aligned64=x,offset16=y,interval64=[x-dx,x+dx],interval16=[y-dy,y+dy]))
    return failures


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=Path('target/debug/apac-tool'))
    p.add_argument('--reference-fft',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if sys.platform!='darwin':p.error('native reference checks require macOS')
    if args.output.exists():p.error('refusing to overwrite report')
    report=dict(schema_version=1,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        component_sha256=sha256_file(Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')),
        reference_library_sha256=sha256_file(args.reference_fft),tool_sha256=sha256_file(args.binary),atol=1e-6,rtol=1e-5,runs=[])
    with tempfile.TemporaryDirectory(prefix='apac-reference-consistency-') as tmp:
        root=Path(tmp)
        cases=[{},dict(gain=255,left={0:(1,[-1,1,-1,1],255)}),{},{}]
        bundle(root/'packets',[frame(c)[0] for c in cases])
        manifest=json.loads((root/'packets/manifest.json').read_text())
        report['packet_data_sha256']=manifest['packet_data_sha256']
        report['cookie_sha256']=manifest['file']['cookie']['value']['sha256']
        outputs=[];settings=[]
        for mode in ['aligned64','offset16']:
            audit=root/(mode+'.json');destination=root/mode
            env=dict(os.environ,DYLD_INSERT_LIBRARIES=str(args.reference_fft.resolve()),APAC_REFERENCE_FFT_MODE=mode,APAC_REFERENCE_FFT_AUDIT=str(audit))
            r=subprocess.run([str(args.binary.resolve()),'replay',str(root/'packets'),'--out',str(destination),'--frames','4096'],capture_output=True,text=True,env=env,timeout=120)
            require(r.returncode==0,r.stderr)
            metadata=json.loads((destination/'pcm.json').read_text());raw=(destination/'pcm.f32le').read_bytes()
            evidence=json.loads(audit.read_text())
            require(metadata['frames']==4096 and metadata['channels']==2 and metadata['sample_rate']==48000,'reference shape differs')
            require(evidence['method']=='vdsp_'+mode and evidence['executions']>0,'alignment mode not established')
            require(hashlib.sha256(raw).hexdigest()==metadata['sha256'],'reference hash mismatch')
            outputs.append(struct.unpack('<8192f',raw));settings.append(metadata['decoder_settings'])
            report['runs'].append(dict(mode=mode,pcm_sha256=metadata['sha256'],audit=evidence))
        require(settings[0]==settings[1],'reference processing settings changed')
        report['decoder_settings']=settings[0]
        failures=disjoint_intervals(*outputs)
        report.update(passed=not failures,reference_consistent=not failures,disjoint_samples=len(failures),examples=failures[:8],
            conclusion='No single fixed PCM can meet both reference paths at the stated tolerance.' if failures else 'Reference intervals overlap.')
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    write_json(args.output,report);print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
