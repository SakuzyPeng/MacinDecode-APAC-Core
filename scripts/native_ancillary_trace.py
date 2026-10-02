"""Bounded packet/reader evidence without assuming a discrete TCE layout."""
import hashlib, os, struct
from pathlib import Path
import native_frame_trace as base
import native_channels_trace as channels

KINDS={};RETURNS={};EVENTS=[];PACKETS=[];ERRORS=[]
CURRENT=None;SEQUENCE=-1;ROLE=None;CHANNELS=0
ptr=channels.ptr
floats=channels.floats

def reader(frame,pointer):
    global ROLE
    result=channels.reader(frame,pointer);ROLE=result['role'];return result

def configuration_reader(frame,pointer):
    current,end,total,cache,cached=struct.unpack('<QQIIi',base.memory(frame,pointer,28))
    if not 0<total<=8*1024*1024:raise RuntimeError('configuration trace buffer limit')
    position=total*8-((end-current)*8+cached)
    if not 0<=position<=total*8:raise RuntimeError('configuration trace cursor limit')
    data=base.memory(frame,end-total,total)
    return dict(role='configuration',relative_bit_offset=position,buffer_bytes=total,buffer_sha256=hashlib.sha256(data).hexdigest())

def hit(frame,location,_dict):
    global CURRENT,SEQUENCE,ROLE
    try:
        key=location.GetBreakpoint().GetID();target=frame.GetThread().GetProcess().GetTarget()
        if key in RETURNS:
            saved=RETURNS.pop(key);event=saved['event'];target.BreakpointDelete(key)
            event['status']=base.reg(frame,'w0')
            if saved['kind']=='packet':CURRENT=None
            else:
                read=configuration_reader if saved['configuration'] else reader
                event['end']=read(frame,saved['reader'])
            return False
        kind=KINDS[key]
        if kind!='packet':
            configuration=CURRENT is None;read=configuration_reader if configuration else reader
            source=base.reg(frame,'x1');start=read(frame,source)
            event=dict(kind=kind,sequence=SEQUENCE,role=start['role'],start=start)
            if kind=='position':event['full_configuration']=bool(base.reg(frame,'w2'))
            EVENTS.append(event)
            bp=target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
            bp.SetThreadID(frame.GetThread().GetThreadID())
            RETURNS[bp.GetID()]=dict(kind=kind,event=event,configuration=configuration,reader=source)
            bp.SetScriptCallbackFunction(__name__+'.hit');return False
        size=base.reg(frame,'w2')
        if not 0<size<=16*1024*1024:raise RuntimeError('invalid ASP packet size')
        channels.RAW=base.memory(frame,base.reg(frame,'x1'),size);SEQUENCE+=1;ROLE='current'
        CURRENT=dict(kind='packet',sequence=SEQUENCE,packet_bytes=size,packet_sha256=hashlib.sha256(channels.RAW).hexdigest())
        PACKETS.append(CURRENT);EVENTS.append(CURRENT)
        bp=target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
        bp.SetThreadID(frame.GetThread().GetThreadID());RETURNS[bp.GetID()]=dict(kind='packet',event=CURRENT)
        bp.SetScriptCallbackFunction(__name__+'.hit')
        if len(PACKETS)>10000:raise RuntimeError('packet trace budget exceeded')
        return False
    except Exception as error:ERRORS.append(str(error));return True

def __lldb_init_module(debugger,_dict):
    global CHANNELS
    CHANNELS=int(os.environ['APAC_CHANNEL_COUNT']);target=debugger.GetSelectedTarget()
    if not target.GetTriple().startswith('arm64') or not 1<=CHANNELS<=255:raise RuntimeError('unverified target/channel bound')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    if hashlib.sha256(component.read_bytes()).hexdigest()!=base.COMPONENT_SHA256:raise RuntimeError('component hash changed')
    for kind,pattern in [('packet',r'^APACASPDecoder::DecodeFrame\('),('configuration',r'^apac::AncillaryConfig::Deserialize\('),('ancillary',r'^apac::APACAncillaryFrameDecoder::DecodeFrame\('),('position',r'^sceneposition::Position::Deserialize\(')]:
        bp=target.BreakpointCreateByRegex(pattern);KINDS[bp.GetID()]=kind;bp.SetScriptCallbackFunction(__name__+'.hit')
