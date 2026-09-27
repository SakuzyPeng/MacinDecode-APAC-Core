"""Read-only DRC evidence for the hash-qualified arm64 AudioCodecs component.

The public replay policy is requested before processing. Breakpoints only read
state; no private functions, return values, or codec buffers are modified.
"""
import hashlib
import json
import os
from pathlib import Path
import struct

import native_frame_trace as base
if os.environ.get('APAC_DRC_CHANNELS')=='1':
    import native_channels_trace as packet
else:
    import native_packet_trace as packet

KINDS = {}
RETURNS = {}
EVENTS = []
ERRORS = []


def u32(frame, address):
    return struct.unpack('<I', base.memory(frame, address, 4))[0]


def gain(frame, pointer):
    begin, end = struct.unpack('<QQ', base.memory(frame, pointer + 8, 16))
    if not 0 <= end-begin <= 32*64 or (end-begin) % 32:
        raise RuntimeError('unverified DRC sequence vector')
    sequences = []
    for p in range(begin, end, 32):
        a, b = struct.unpack('<QQ', base.memory(frame, p+8, 16))
        if not 0 <= b-a <= 12*256 or (b-a) % 12:
            raise RuntimeError('unverified DRC node vector')
        sequences.append(dict(coding_mode=u32(frame,p), frame_end=bool(base.memory(frame,p+4,1)[0]),
                              nodes=[dict(zip(('gain_db','slope','time'),struct.unpack('<ffi',base.memory(frame,n,12)))) for n in range(a,b,12)]))
    return sequences


def selection(frame, pointer):
    # Verified against Process's numSelectedDrcSets and setId stores/log sites.
    count = u32(frame, pointer+0x3c)
    if count > 5:
        raise RuntimeError('unverified selected DRC set count')
    return dict(count=count, set_ids=list(struct.unpack('<5i',base.memory(frame,pointer+0x14,20)))[:count],
                downmix_ids=list(struct.unpack('<5i',base.memory(frame,pointer+0x28,20)))[:count],
                loudness_normalization_gain_db=struct.unpack('<f',base.memory(frame,pointer+0xc,4))[0],
                raw_hex=base.memory(frame,pointer,0x15c).hex())


def back(frame, kind, event, **saved):
    bp=frame.GetThread().GetProcess().GetTarget().BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
    bp.SetThreadID(frame.GetThread().GetThreadID())
    RETURNS[bp.GetID()]=dict(kind=kind,event=event,**saved)
    bp.SetScriptCallbackFunction(__name__+'.hit')


def hit(frame, location, _dict):
    try:
        bp_id=location.GetBreakpoint().GetID()
        if bp_id in RETURNS:
            saved=RETURNS.pop(bp_id)
            frame.GetThread().GetProcess().GetTarget().BreakpointDelete(bp_id)
            kind,e=saved['kind'],saved['event']
            e['status']=base.reg(frame,'w0')
            if kind=='selection':
                e['selected']=selection(frame,saved['output'])
            elif kind in ('wrapper_process','process','processor_process'):
                count=u32(frame,saved['channels'])
                if count!=e['input_channels']:raise RuntimeError('DRC changed channel count')
                e['channels_after']=count
                e['after']=[packet.floats(frame,p,e['frames']) for p in saved['outputs']]
                after=[base.memory(frame,p,e['frames']*4) for p in saved['outputs']]
                e['after_sha256']=[hashlib.sha256(v).hexdigest() for v in after]
                e['identity']=e['before_sha256']==e['after_sha256']
                if 'processor' in saved:
                    p=saved['processor']
                    e['state_after']=dict(delay_samples=u32(frame,p+0x990),transition=u32(frame,p+0x994),active=u32(frame,p+0x998),previous=u32(frame,p+0x99c),crossfade=base.memory(frame,p+0x9a0,1)[0])
            elif kind in ('payload','header','gain_read'):
                e['end']=packet.reader(frame,saved['reader'])
                if kind=='gain_read':
                    e['native_timing_mode_word']=u32(frame,saved['pointer'])
                    e['sequences']=gain(frame,saved['pointer'])
            return False
        kind=KINDS[bp_id]
        e=dict(kind=kind,sequence=packet.SEQUENCE,role=packet.ROLE)
        if kind=='selection':
            e['domain']=base.reg(frame,'w3')
            back(frame,kind,e,output=base.reg(frame,'x4'))
        elif kind in ('wrapper_process','process','processor_process'):
            instance=base.reg(frame,'x0')
            if kind=='processor_process':
                stack=base.reg(frame,'sp')
                output=packet.ptr(frame,stack);channels=packet.ptr(frame,stack+8)
                count=base.reg(frame,'w7');frames=u32(frame,stack+16);source=base.reg(frame,'x6')
                e['transition_flags']=base.reg(frame,'w5')
                e['delay_samples']=u32(frame,instance+0x810)
                extra={}
            else:
                output_reg,channel_reg=('x2','x3') if kind=='wrapper_process' else ('x3','x4')
                count=u32(frame,instance+0x18) if kind=='wrapper_process' else base.reg(frame,'w2')
                processor=packet.ptr(frame,instance+0x28)+8 if kind=='wrapper_process' else instance
                frames=u32(frame,processor+0x2c)
                channels=base.reg(frame,channel_reg);output=base.reg(frame,output_reg);source=base.reg(frame,'x1')
                e['state_before']=dict(delay_samples=u32(frame,processor+0x990),transition=u32(frame,processor+0x994),active=u32(frame,processor+0x998),previous=u32(frame,processor+0x99c),crossfade=base.memory(frame,processor+0x9a0,1)[0])
                extra=dict(processor=processor)
            if count!=getattr(packet,'CHANNELS',2) or frames!=1024:raise RuntimeError(f'wrong declared channels/1024 DRC probe: {kind} frames={frames}, count={count}')
            inputs=[packet.ptr(frame,source+i*8) for i in range(count)]
            outputs=[packet.ptr(frame,output+i*8) for i in range(count)]
            e.update(frames=frames,input_channels=count,channels_before=u32(frame,channels),before=[packet.floats(frame,p,frames) for p in inputs],
                     before_sha256=[hashlib.sha256(base.memory(frame,p,frames*4)).hexdigest() for p in inputs])
            back(frame,kind,e,outputs=outputs,channels=channels,**extra)
        elif kind in ('payload','header','gain_read'):
            if packet.CURRENT is None:return False  # Cookie header uses a different bounded buffer.
            source=base.reg(frame,'x1');e['start']=packet.reader(frame,source)
            if kind!='gain_read':e['payload_type']=base.reg(frame,'w2')
            back(frame,kind,e,reader=source,pointer=base.reg(frame,'x0'))
        elif kind=='gain_reset':
            if packet.CURRENT is None:return False
            e['sequences_before']=gain(frame,base.reg(frame,'x0'))
        EVENTS.append(e)
        if len(EVENTS)>10000:raise RuntimeError('DRC trace event limit')
        return False
    except Exception as error:
        ERRORS.append(str(error));return True


def __lldb_init_module(debugger,_dict):
    import shlex
    debugger.HandleCommand('command script import '+shlex.quote(str(Path(packet.__file__).resolve())))  # Includes component hash / architecture gate.
    target=debugger.GetSelectedTarget()
    points={'payload':r'^mpddrc::UniDrc::Deserialize\(', 'header':r'^mpddrc::UniDrcHeader::Deserialize\(',
            'gain_read':r'^mpddrc::UniDrcGain::Deserialize\(', 'gain_reset':r'^mpddrc::UniDrcGain::Reset\(',
            'selection':r'^mpddrc::UniDrcSelection::Process\(', 'process':r'^mpddrc::UniDrc::Process\(',
            'processor_process':r'^mpddrc::UniDrcProcessor::Process\(',
            'wrapper_process':r'^apac::drc::DynRangeCompressor::ProcessUniDrc\('}
    for kind,pattern in points.items():
        bp=target.BreakpointCreateByRegex(pattern);KINDS[bp.GetID()]=kind
        bp.SetScriptCallbackFunction(__name__+'.hit')


def finish(debugger):
    import lldb
    process=debugger.GetSelectedTarget().GetProcess()
    result=dict(trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                component_sha256=base.COMPONENT_SHA256,packets=packet.PACKETS,events=EVENTS,
                packet_events=packet.EVENTS, errors=ERRORS+packet.ERRORS,
                pending_returns=len(RETURNS)+len(packet.RETURNS),
                process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    raw=(json.dumps(result,indent=2)+'\n').encode()
    if len(raw)>128*1024*1024:raise RuntimeError('DRC trace exceeds output limit')
    with Path(os.environ['APAC_DRC_TRACE_OUTPUT']).open('xb') as out:out.write(raw)


def trace_bundle(binary,bundle,root,frames=4096,policy='drc-off',allow_replay_failure=False,channel_mode=False):
    import shlex
    import subprocess
    if any(os.environ.get(k) for k in ('DYLD_INSERT_LIBRARIES','DYLD_FORCE_FLAT_NAMESPACE')):
        raise RuntimeError('native DRC acceptance requires unmodified framework loading')
    root.mkdir(parents=True,exist_ok=True)
    output=root/'native-drc.json'
    if output.exists():raise RuntimeError('refusing to overwrite DRC trace')
    env=dict(os.environ,APAC_DRC_TRACE_OUTPUT=str(output.resolve()))
    env.pop('APAC_DRC_CHANNELS',None)
    if channel_mode:
        manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
        env.update(APAC_DRC_CHANNELS='1',APAC_CHANNEL_COUNT=str(manifest['file']['format']['channels']))
    command=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),
             '--frames',str(frames),'--processing-policy',policy]
    args=['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),
          '-o','run '+shlex.join(command),'-o','script native_drc_trace.finish(lldb.debugger)','--',str(binary.resolve())]
    process=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
    (root/'lldb.log').write_text(process.stdout+process.stderr,encoding='utf-8')
    if process.returncode or not output.exists():raise RuntimeError('DRC trace failed: '+(process.stdout+process.stderr)[-2000:])
    result=json.loads(output.read_text(encoding='utf-8'))
    if result['errors'] or result['pending_returns'] or (result['process_exit_code']!=0 and not allow_replay_failure):
        raise RuntimeError('DRC trace incomplete: '+json.dumps({k:result[k] for k in ('errors','pending_returns','process_exit_code')}))
    return result
