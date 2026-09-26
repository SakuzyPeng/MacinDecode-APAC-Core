"""Read-only packet/state snapshots for the hash-verified arm64 component.

No private function is called and no codec state is changed. Native Float32
values are diagnostic observations, never the portable mathematical oracle.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

import native_frame_trace as base

KINDS = {}
RETURNS = {}
PACKETS = []
EVENTS = []
ERRORS = []
CURRENT = None
ROLE = None
SEQUENCE = -1
RAW = None


def ptr(frame, address):
    return struct.unpack('<Q', base.memory(frame, address, 8))[0]


def floats(frame, address, count=1024):
    return list(struct.unpack('<' + str(count) + 'f', base.memory(frame, address, count * 4)))


def reader(frame, pointer):
    current, end, total, cache, cached = struct.unpack('<QQIIi', base.memory(frame, pointer, 28))
    position = total * 8 - ((end-current)*8 + cached)
    if not 0 <= position <= total*8:
        raise RuntimeError('reader cursor outside its declared buffer')
    data = base.memory(frame, end-total, total)
    digest = hashlib.sha256(data).hexdigest()
    if data == RAW:
        role, offset = 'current', 0
    else:
        # Identify the one declared ASP child by its wire span, even when its
        # bytes also appear elsewhere in the packet. Reader addresses differ.
        wire=''.join(format(v,'08b') for v in RAW)
        if wire[:5]!='10001':raise RuntimeError('unexpected non-current reader')
        size=int(wire[5:21],2)
        if not 0<size<=4096:raise RuntimeError('unverified native preroll size')
        offset=24
        if RAW[offset//8:offset//8+size]!=data:raise RuntimeError('preroll reader disagrees with its declared span')
        role='preroll'
    return dict(role=role, relative_bit_offset=position, outer_bit_offset=offset+position,
                buffer_start_bit=offset, buffer_bytes=total, buffer_sha256=digest)


def back(frame, kind, event, **saved):
    target = frame.GetThread().GetProcess().GetTarget()
    bp = target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
    bp.SetThreadID(frame.GetThread().GetThreadID())
    RETURNS[bp.GetID()] = dict(kind=kind, event=event, **saved)
    bp.SetScriptCallbackFunction(__name__ + '.hit')


def event(kind):
    value = dict(kind=kind, sequence=SEQUENCE, role=ROLE)
    EVENTS.append(value)
    if len(EVENTS) > 10000:
        raise RuntimeError('state trace event limit')
    return value


def element(frame, pointer):
    result=[]
    for ics_offset, stream_offset in [(0x10,0x18),(0x200,0x208)]:
        ics, stream = ptr(frame,pointer+ics_offset), ptr(frame,pointer+stream_offset)
        begin, end = struct.unpack('<QQ', base.memory(frame, stream+0x2f0,16))
        if end-begin != 4096:
            raise RuntimeError('unverified CPE stream shape')
        result.append(dict(ics=base.ics_info(frame,ics), scaled=floats(frame,begin)))
    return result


def hit(frame, location, _dict):
    global CURRENT, ROLE, SEQUENCE, RAW
    try:
        bp_id=location.GetBreakpoint().GetID()
        if bp_id in RETURNS:
            saved=RETURNS.pop(bp_id)
            frame.GetThread().GetProcess().GetTarget().BreakpointDelete(bp_id)
            kind, e=saved['kind'], saved['event']
            if kind == 'synthesis':
                e['status']=base.reg(frame,'w0')
                e['output']=floats(frame,saved['buffer'])
                e['overlap_after']=floats(frame,saved['overlap'])
                e['shape_after']=base.memory(frame,saved['shape'],1)[0]
            elif kind == 'cpe_reset':
                e['after']=element(frame,saved['pointer'])
            elif kind == 'scene':
                e['status']=base.reg(frame,'w0')
                e['after']=[floats(frame,p,saved['count']) for p in saved['buffers']]
            elif kind == 'packet':
                e['status']=base.reg(frame,'w0')
            else:
                e['status']=base.reg(frame,'w0')
                e['end']=reader(frame,saved['reader'])
            return False
        kind=KINDS[bp_id]
        if kind=='packet':
            SEQUENCE+=1
            address,size=base.reg(frame,'x1'),base.reg(frame,'w2')
            if not 0<size<=16*1024*1024:
                raise RuntimeError('packet size')
            RAW=base.memory(frame,address,size);ROLE=None
            CURRENT=dict(sequence=SEQUENCE,packet_sha256=hashlib.sha256(RAW).hexdigest(),
                         packet_bytes=size,frame_type=RAW[0]>>6)
            PACKETS.append(CURRENT)
            back(frame,kind,CURRENT)
        elif CURRENT is None:
            return False
        elif kind in ('core','deserialize','ancillary','bwe2','scene_read','core_tools'):
            source=base.reg(frame,'x2' if kind=='core_tools' else 'x1')
            info=reader(frame,source)
            if kind=='core':ROLE=info['role']
            e=event(kind);e['start']=info
            if kind in ('core','ancillary'):e['frame_type']=base.reg(frame,'w2')
            back(frame,kind,e,reader=source)
        elif kind=='cpe_reset':
            pointer=base.reg(frame,'x0');e=event(kind)
            e['before']=element(frame,pointer)
            back(frame,kind,e,pointer=pointer)
        elif kind=='synthesis':
            instance=base.reg(frame,'x0');channel=base.reg(frame,'w1');buffer=base.reg(frame,'x2')
            if channel not in (0,1):raise RuntimeError('requires stereo probe')
            count=struct.unpack('<I',base.memory(frame,instance+0x28,4))[0]
            if count!=1024:raise RuntimeError('unverified synthesis size')
            overlap=ptr(frame,instance+8)+channel*4096;shape=ptr(frame,instance+0x18)+channel
            e=event(kind);e.update(channel=channel,block=base.reg(frame,'w4'),shape=base.reg(frame,'w3'),
                                shape_before=base.memory(frame,shape,1)[0],input=floats(frame,buffer),
                                overlap_before=floats(frame,overlap))
            back(frame,kind,e,buffer=buffer,overlap=overlap,shape=shape)
        elif kind=='scene':
            # The trailing unsigned parameter is unused in this component; frame size is configured.
            count=1024
            inputs=[ptr(frame,base.reg(frame,'x1')+i*8) for i in range(2)]
            outputs=[ptr(frame,base.reg(frame,'x2')+i*8) for i in range(2)]
            e=event(kind);e.update(frames=count,unused_argument=base.reg(frame,'w3'),before=[floats(frame,p,count) for p in inputs])
            back(frame,kind,e,buffers=outputs,count=count)
        return False
    except Exception as error:
        ERRORS.append(str(error));return True


def __lldb_init_module(debugger,_dict):
    target=debugger.GetSelectedTarget()
    if not target.GetTriple().startswith('arm64'):raise RuntimeError('requires arm64')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    if hashlib.sha256(component.read_bytes()).hexdigest()!=base.COMPONENT_SHA256:raise RuntimeError('component hash changed')
    points={'packet':r'^APACASPDecoder::DecodeFrame\(', 'core':r'^APACCoreDecoder::DecodeAPACFrame\(',
            'core_tools':r'^APACCoreDecoder::DeserializeExtTools\(',
            'deserialize':r'^APACCoreLBRDecoder::Deserialize\(TBitstreamReader',
            'ancillary':r'^apac::APACAncillaryFrameDecoder::DecodeFrame\(',
            'bwe2':r'^APACBWE2Decoder::Deserialize\(', 'cpe_reset':r'^APACChannelPairElement::Reset\(',
            'synthesis':r'^APACSynthesisFilterBank::FrequencyToTimeInPlace\(',
            'scene':r'^SC_SceneController::ProcessAudio\(', 'scene_read':r'^SC_AudioScenes::Deserialize\('}
    for kind,pattern in points.items():
        bp=target.BreakpointCreateByRegex(pattern);KINDS[bp.GetID()]=kind
        bp.SetScriptCallbackFunction(__name__+'.hit')


def finish(debugger):
    import lldb
    process=debugger.GetSelectedTarget().GetProcess()
    result=dict(trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),component_sha256=base.COMPONENT_SHA256,architecture='arm64',packets=PACKETS,events=EVENTS,
                errors=ERRORS,pending_returns=len(RETURNS),
                process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    path=Path(os.environ['APAC_STATE_TRACE_OUTPUT'])
    raw=(json.dumps(result,indent=2)+'\n').encode()
    if len(raw)>128*1024*1024:raise RuntimeError('native snapshot exceeds 128 MiB')
    with path.open('xb') as out:out.write(raw)


def trace_bundle(binary,bundle,root,frames=None):
    import shlex
    import subprocess
    output=root/'native-state.json'
    if output.exists():raise RuntimeError('refusing to overwrite native state snapshot')
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if frames is None:frames=max(1,manifest['file']['packet_table']['value']['valid_frames'])
    env=dict(os.environ,APAC_STATE_TRACE_OUTPUT=str(output.resolve()))
    command=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames)]
    args=['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),
          '-o','run '+shlex.join(command),'-o','script native_packet_trace.finish(lldb.debugger)','--',str(binary.resolve())]
    process=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
    if process.returncode or not output.exists():raise RuntimeError('state trace failed: '+(process.stdout+process.stderr)[-2000:])
    result=json.loads(output.read_text(encoding='utf-8'))
    if result['errors'] or result['process_exit_code']!=0 or result['pending_returns']:
        raise RuntimeError('native state trace incomplete: '+json.dumps({k:result[k] for k in ('errors','process_exit_code','pending_returns')}))
    return result
