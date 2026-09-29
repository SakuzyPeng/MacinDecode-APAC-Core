#!/usr/bin/env python3
"""Native DRC-off kernel, selection and timing proof; outer floats are diagnostic.

A zero property readback alone never passes. Independent mathematical PCM and
cross-platform exactness are validated separately. Apple's switching arithmetic
is measured without making its rounding part of the portable decoder model.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from native_drc_trace import trace_bundle
from native_frame_trace import COMPONENT_SHA256
from validate import require,write_json
from validate_portable import source_digest
from validate_replay import sha256_file,check_accounting
from validate_spectra import ulp

GATE_PROFILE = "apac-drc-off-native-v2"


def pcm_bytes(channels):
    return [struct.pack('<'+str(len(c))+'f',*c) for c in channels]


def identity(event,channels=2):
    before,after=event['before'],event['after']
    require(len(before)==len(after)==channels,'wrong DRC channel count')
    require(all(len(c)==event['frames']==1024 for c in before+after),'wrong DRC frame length')
    require(all(math.isfinite(x) for c in before+after for x in c),'nonfinite DRC snapshot')
    a,b=pcm_bytes(before),pcm_bytes(after)
    require(event['before_sha256']==[hashlib.sha256(v).hexdigest() for v in a] and
            event['after_sha256']==[hashlib.sha256(v).hexdigest() for v in b], 'snapshot hash mismatch')
    changed=[];maximum=0.;distance=0
    for ch,(left,right) in enumerate(zip(before,after)):
        for i,(x,y) in enumerate(zip(left,right)):
            if struct.pack('<f',x)!=struct.pack('<f',y):
                if not changed:changed=[dict(channel=ch,frame=i,before=x,after=y)]
                maximum=max(maximum,abs(x-y));distance=max(distance,ulp(x,y))
    mismatches=sum(x!=y for aa,bb in zip(a,b) for x,y in zip(struct.iter_unpack('<I',aa),struct.iter_unpack('<I',bb)))
    require(event['identity']==(mismatches==0),'trace identity flag disagrees with values')
    return dict(sequence=event['sequence'],role=event['role'],frames=event['frames'],identity=mismatches==0,
                changed_samples=mismatches,max_absolute_error_internal=maximum,max_absolute_error_normalized=maximum/32768,
                max_ulp=distance,first_failure=changed[0] if changed else None)


def inspect(trace,replay,pcm,raw,policy,require_nonzero=True):
    require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and
            not trace['pending_returns'] and trace['process_exit_code']==0,'incomplete qualified native trace')
    check_accounting(replay,pcm)
    channels=pcm.get('channels',2)
    require(channels in (1,2,6,8,12,24),'unsupported DRC proof layout')
    settings=pcm['decoder_settings'];audit=settings.get('processing_policy',{}).get('value')
    if policy=='drc-off':
        require(audit is not None and audit['initial']['verified'],'explicit off request not verified')
        require(audit['initial'].get('initial_reset')==dict(operation='AudioConverterReset',os_status=0,before_input=True),'initial public reset not verified')
        requests=audit['initial']['requests']
        require([r['property'] for r in requests]==['mdrc','^pro','^tlc'] and
                all(r['requested']==r['os_status']==0 for r in requests),'off properties not explicitly set')
        for group in (audit['initial']['readback'],audit['final_readback']):
            require(all(group[k]['value']==0 and group[k]['error'] is None for k in ('mdrc','^pro','ptlc')),'off readback changed')
    selected=[e for e in trace['events'] if e['kind']=='selection']
    require(selected and all(e['status']==0 for e in selected),'no successful selected-set evidence')
    empty=all(e['selected']['count']==0 and not e['selected']['set_ids'] and
              e['selected']['loudness_normalization_gain_db']==0 for e in selected)
    wrappers=[e for e in trace['events'] if e['kind']=='wrapper_process']
    kernels=[e for e in trace['events'] if e['kind']=='processor_process']
    gains=[e for e in trace['events'] if e['kind']=='gain_read']
    resets=[e for e in trace['events'] if e['kind']=='gain_reset']
    require(wrappers and kernels and gains,'missing DRC payload or processing snapshots')
    require(all(e['status']==0 for e in wrappers+kernels),'native DRC processing failed')
    ordinary=[e for e in wrappers if e['role']=='current']
    require(len(ordinary)==len(trace['packets'])==replay['consumed_packets'],'outer packet/frame count mismatch')
    require(len(gains)==len(wrappers),'missing DRC payload for processed frame')
    key=lambda e:(e['sequence'],e['role'])
    require(len({key(e) for e in wrappers})==len(wrappers),'duplicate wrapper snapshot')
    require({key(e) for e in gains}=={key(e) for e in kernels}=={key(e) for e in wrappers},'unassociated native processing evidence')
    nonzero=any(v!=0 for e in wrappers for c in e['before'] for v in c)
    if require_nonzero:require(nonzero,'off proof requires nonzero PCM excitation')
    inputs={key(e):e['before_sha256'] for e in wrappers}
    require(all(e['before_sha256']==inputs[key(e)] for e in kernels),'DRC kernel input differs from wrapper input')
    failed_gain=[e for e in gains if e['status']!=0]
    metrics=[identity(e,channels) for e in wrappers];kernel_metrics=[identity(e,channels) for e in kernels]
    no_delay=all(e['state_before']['delay_samples']==e['state_after']['delay_samples']==0 for e in wrappers) and all(e['delay_samples']==0 for e in kernels)
    values=[x/32768 for e in ordinary for pair in zip(*e['after']) for x in pair]
    first=replay['discarded_before_frames']*channels;count=pcm['frames']*channels
    expected=struct.pack('<'+str(count)+'f',*values[first:first+count])
    require(expected==raw,'DRC wrapper output does not match returned PCM at the reported timeline')
    require(sha256_file(Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs'))==COMPONENT_SHA256,'component changed during proof')
    return dict(policy=policy,gate_profile=GATE_PROFILE,outer_float_role='diagnostic_only',nonzero_input_observed=nonzero,passed=empty and no_delay and not failed_gain and not resets and all(m['identity'] for m in kernel_metrics),
                empty_selected_sets=empty,zero_added_delay=no_delay,returned_pcm_matches_timeline=True,
                frame_count=len(wrappers),gain_payloads=len(gains),gain_parser_failures=len(failed_gain),gain_resets=len(resets),
                whole_path_identity=all(m['identity'] for m in metrics),kernel_identity=all(m['identity'] for m in kernel_metrics),
                changed_samples=sum(m['changed_samples'] for m in metrics),
                max_ulp=max(m['max_ulp'] for m in metrics),frames=metrics,kernels=kernel_metrics,
                pcm_sha256=pcm['sha256'],frames_saved=pcm['frames'],selected_sets=[e['selected'] for e in selected],
                native_processing_policy=audit)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--artifacts',type=Path,required=True)
    p.add_argument('--frames',type=int,default=4096)
    args=p.parse_args()
    require(not args.report.exists() and not args.artifacts.exists(),'refusing to overwrite DRC proof artifacts')
    require(args.binary.is_file() and args.frames>0,'binary and positive frames required')
    original=sha256_file(args.binary)
    report=dict(schema_version=1,created_at=datetime.now(timezone.utc).isoformat(),passed=False,
                source_sha256=source_digest(),binary_sha256=original,component_sha256=COMPONENT_SHA256,
                gate=GATE_PROFILE,cases=[],errors=[])
    import subprocess
    report['code_commit']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    report['toolchain']=subprocess.check_output(['rustc','+1.98.0','--version'],text=True).strip()
    for policy in ('default','drc-off'):
        try:
            root=args.artifacts/policy;trace=trace_bundle(args.binary,args.bundle,root,args.frames,policy)
            replay=json.loads((root/'native-pcm/replay.json').read_text(encoding='utf-8'))
            pcm=json.loads((root/'native-pcm/pcm.json').read_text(encoding='utf-8'))
            result=inspect(trace,replay,pcm,(root/'native-pcm/pcm.f32le').read_bytes(),policy)
            report['cases'].append(result)
        except Exception as error:report['errors'].append(dict(policy=policy,error=str(error)))
        print('DRC proof '+policy+' recorded',file=sys.stderr,flush=True)
    if sha256_file(args.binary)!=original:report['errors'].append(dict(error='binary changed during proof'))
    explicit=[r for r in report['cases'] if r['policy']=='drc-off']
    report['passed']=not report['errors'] and len(explicit)==1 and explicit[0]['passed']
    write_json(args.report,report)
    print(json.dumps(dict(passed=report['passed'],report=str(args.report),errors=report['errors'])))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
