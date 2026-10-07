"""Read-only arm64 channel-element and packet snapshots, bound to a component hash.

No private function is invoked and no decoder state is modified. Raw native
Float32 samples remain diagnostic observations, never mathematical ground truth.
"""
import hashlib,json,os,shlex,struct,subprocess
from pathlib import Path
import native_frame_trace as base

KINDS={};RETURNS={};EVENTS=[];PACKETS=[];ERRORS=[];ELEMENTS={}
CURRENT=None;RAW=None;SEQUENCE=-1;ROLE=None;CURRENT_ELEMENT=None;CHANNELS=0
LAYOUT_TYPES={1:[0],2:[1],6:[1,0,3,1],8:[1,0,3,1,1],12:[1,0,3,1,1,1,1],16:[1,0,3,1,1,1,1,1,1],24:[1,0,3,1,1,0,3,1,1,0,0,1,1,0,0,1]}

def ptr(frame,address):return struct.unpack('<Q',base.memory(frame,address,8))[0]
def floats(frame,address,count=1024):return list(struct.unpack('<'+str(count)+'f',base.memory(frame,address,count*4)))
def values(frame,address):return floats(frame,address)
def reader(frame,pointer):
    current,end,total,cache,cached=struct.unpack('<QQIIi',base.memory(frame,pointer,28))
    position=total*8-((end-current)*8+cached)
    if not 0<=position<=total*8:raise RuntimeError('reader cursor outside buffer')
    data=base.memory(frame,end-total,total);start=0;role='current'
    if data!=RAW:
        wire=''.join(format(v,'08b') for v in RAW)
        if wire[:5]!='10001':raise RuntimeError('unknown child reader')
        count=int(wire[5:21],2);start=24
        if count==65535:count+=int(wire[21:37],2);start=40
        if RAW[start//8:start//8+count]!=data:raise RuntimeError('child range differs')
        role='preroll'
    return dict(role=role,relative_bit_offset=position,outer_bit_offset=start+position,
                buffer_start_bit=start,buffer_bytes=total,buffer_sha256=hashlib.sha256(data).hexdigest())
def snapshot(frame,element):
    out=[]
    for i,(ics_offset,stream_offset) in enumerate([(0x10,0x18),(0x200,0x208)][:len(element['channels'])]):
        ics=ptr(frame,element['pointer']+ics_offset);stream=ptr(frame,element['pointer']+stream_offset)
        begin,end=struct.unpack('<QQ',base.memory(frame,stream+0x2f0,16))
        if end-begin!=4096:raise RuntimeError('unexpected element spectrum size')
        out.append(dict(channel_index=element['channels'][i],ics=base.ics_info(frame,ics),scaled=values(frame,begin)))
    return out

def event(kind,**fields):
    e=dict(kind=kind,sequence=SEQUENCE,role=ROLE,**fields);EVENTS.append(e)
    if len(EVENTS)>20000:raise RuntimeError('event budget exceeded')
    return e

def back(frame,kind,e,**saved):
    target=frame.GetThread().GetProcess().GetTarget()
    bp=target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC());bp.SetThreadID(frame.GetThread().GetThreadID())
    RETURNS[bp.GetID()]=dict(kind=kind,event=e,**saved);bp.SetScriptCallbackFunction(__name__+'.hit')

def hit(frame,location,_dict):
    global RAW,SEQUENCE,ROLE,CURRENT_ELEMENT,ELEMENTS,CURRENT
    try:
        key=location.GetBreakpoint().GetID()
        if key in RETURNS:
            saved=RETURNS.pop(key);frame.GetThread().GetProcess().GetTarget().BreakpointDelete(key)
            kind=saved['kind'];e=saved['event']
            if kind not in ('reset','tns_apply'):e['status']=base.reg(frame,'w0')
            if 'reader' in saved:e['end']=reader(frame,saved['reader'])
            if kind=='capacity':
                begin,end,capacity=struct.unpack('<QQQ',base.memory(frame,saved['pointer']+0x2e8,24))
                if not begin<=end<=capacity:raise RuntimeError('unverified ASP buffer layout')
                e.update(preroll_bytes=end-begin,capacity_bytes=capacity-begin)
            elif kind in ('element','reset'):
                e['after']=snapshot(frame,saved['element']);CURRENT_ELEMENT=None
            elif kind=='stream':
                begin,end=struct.unpack('<QQ',base.memory(frame,saved['stream']+0x2f0,16))
                if end-begin!=4096:raise RuntimeError('unverified stream size')
                e['scaled']=values(frame,begin)
            elif kind=='tns':e['parameters']=base.tns_data(frame,saved['pointer'],e['ics'])
            elif kind=='tns_apply':e['after']=values(frame,saved['buffer'])
            elif kind=='cac':e['after']=[values(frame,p) for p in saved['buffers']]
            elif kind=='bwe2':
                p=saved['pointer'];active=ptr(frame,ptr(frame,p+8));indices=ptr(frame,p+0x30);gains=ptr(frame,p+0x20)
                e['parameters']=[]
                for local,i in enumerate(e['channels']):
                    enabled=bool(active&(1<<i));info=base.ics_info(frame,ptr(frame,saved['element']['pointer']+(0x10 if local==0 else 0x200)))
                    groups=info['active_group_count']
                    e['parameters'].append(dict(channel_index=i,active=enabled,lsf_indices=list(struct.unpack('<2i',base.memory(frame,indices+i*8,8))) if enabled else None,
                                               gain_indices=list(struct.unpack('<'+str(groups)+'i',base.memory(frame,gains+i*32,groups*4))) if enabled and groups else []))
            elif kind=='synthesis':e['output']=values(frame,saved['buffer'])
            elif kind=='scene':e['after']=[values(frame,p) for p in saved['buffers']]
            return False
        kind=KINDS[key]
        if kind=='capacity':
            e=event(kind);back(frame,kind,e,pointer=base.reg(frame,'x0'));return False
        if kind=='packet':
            SEQUENCE+=1;ROLE=None;CURRENT_ELEMENT=None
            size=base.reg(frame,'w2')
            if not 0<size<=16*1024*1024:raise RuntimeError('packet size')
            RAW=base.memory(frame,base.reg(frame,'x1'),size)
            e=event(kind,packet_sha256=hashlib.sha256(RAW).hexdigest(),packet_bytes=size);CURRENT=e;PACKETS.append(e);back(frame,kind,e);return False
        if RAW is None:return False
        if kind=='core':
            source=base.reg(frame,'x1');info=reader(frame,source);ROLE=info['role'];e=event(kind,start=info)
            instance=base.reg(frame,'x0');begin,end=struct.unpack('<QQ',base.memory(frame,instance+0xb0,16))
            if end-begin!=len(LAYOUT_TYPES[CHANNELS])*16:raise RuntimeError('unverified core element vector')
            ELEMENTS={};channel=0
            for i,address in enumerate(range(begin,end,16)):
                pointer=ptr(frame,address+8);typ=struct.unpack('<I',base.memory(frame,pointer+8,4))[0]
                if typ!=LAYOUT_TYPES[CHANNELS][i]:raise RuntimeError('unverified element type/order')
                width=2 if typ==1 else 1
                ELEMENTS[pointer]=dict(pointer=pointer,element_index=i,element_type=typ,channels=list(range(channel,channel+width)));channel+=width
            if channel!=CHANNELS:raise RuntimeError('element channel total differs')
            e['elements']=[{k:v for k,v in x.items() if k!='pointer'} for x in ELEMENTS.values()]
            back(frame,kind,e,reader=source)
        elif kind in ('element','reset'):
            pointer=base.reg(frame,'x0')
            if pointer not in ELEMENTS:return False
            element=ELEMENTS[pointer];CURRENT_ELEMENT=dict(element,reader=base.reg(frame,'x1') if kind=='element' else None)
            e=event(kind,**{k:v for k,v in element.items() if k!='pointer'})
            if kind=='element':
                source=base.reg(frame,'x1');e['start']=reader(frame,source);back(frame,kind,e,reader=source,element=element)
            else:e['before']=snapshot(frame,element);back(frame,kind,e,element=element)
        elif kind in ('stream','tns','tns_apply') and CURRENT_ELEMENT is not None:
            element=CURRENT_ELEMENT;p=base.reg(frame,'x0');channel=None
            if kind=='stream':
                for i,offset in enumerate((0x18,0x208)[:len(element['channels'])]):
                    if p==ptr(frame,element['pointer']+offset):channel=element['channels'][i]
                if channel is None:raise RuntimeError('stream not associated with element')
                source=base.reg(frame,'x1');e=event(kind,element_index=element['element_index'],channel_index=channel,start=reader(frame,source),ics=base.ics_info(frame,base.reg(frame,'x2')))
                back(frame,kind,e,reader=source,stream=p)
            else:
                for i,offset in enumerate((0x80,0x13e) if element['element_type']==1 else (0x28,)):
                    if p==element['pointer']+offset:channel=element['channels'][i]
                if channel is None:raise RuntimeError('TNS not associated with element')
                ics=base.ics_info(frame,ptr(frame,element['pointer']+(0x10 if channel==element['channels'][0] else 0x200)))
                e=event(kind,element_index=element['element_index'],channel_index=channel,ics=ics)
                if kind=='tns':
                    source=base.reg(frame,'x1');e['start']=reader(frame,source);back(frame,kind,e,reader=source,pointer=p)
                else:
                    buffer=base.reg(frame,'x4');e['before']=values(frame,buffer);back(frame,kind,e,buffer=buffer)
        elif kind=='cac' and CURRENT_ELEMENT is not None:
            element=CURRENT_ELEMENT;source=element['reader'];instance=base.reg(frame,'x0');info=base.ics_info(frame,base.reg(frame,'x1'));runs=[]
            if element['element_type']!=1:raise RuntimeError('CAC outside CPE')
            if info['max_sfb']:
                arrays=[]
                for offset in (0x20,0x38):
                    first,last=struct.unpack('<QQ',base.memory(frame,instance+offset,16))
                    if last-first!=120:raise RuntimeError('CAC run-vector shape changed')
                    arrays.append(base.memory(frame,first,120))
                for gain,repeat in zip(*arrays):
                    runs.append(dict(gain_index=gain,repeat_code=repeat))
                    if repeat==43:break
                else:raise RuntimeError('CAC missing terminal')
            buffers=[base.reg(frame,'x2'),base.reg(frame,'x3')]
            e=event(kind,element_index=element['element_index'],position=reader(frame,source),runs=runs,before=[values(frame,p) for p in buffers])
            back(frame,kind,e,reader=source,buffers=buffers)
        elif kind=='bwe2':
            element=ELEMENTS.get(base.reg(frame,'x3'))
            if element is None:raise RuntimeError('BWE2 not associated with element')
            source=base.reg(frame,'x1');e=event(kind,element_index=element['element_index'],channels=element['channels'],channel_start=base.reg(frame,'w2'),start=reader(frame,source))
            back(frame,kind,e,reader=source,pointer=base.reg(frame,'x0'),element=element)
        elif kind=='synthesis':
            channel=base.reg(frame,'w1');buffer=base.reg(frame,'x2')
            if channel>=CHANNELS:raise RuntimeError('synthesis channel outside layout')
            e=event(kind,channel_index=channel,block=base.reg(frame,'w4'),input=values(frame,buffer));back(frame,kind,e,buffer=buffer)
        elif kind in ('core_tools','ancillary'):
            source=base.reg(frame,'x2' if kind=='core_tools' else 'x1');e=event(kind,start=reader(frame,source));back(frame,kind,e,reader=source)
        elif kind=='scene':
            inputs=[ptr(frame,base.reg(frame,'x1')+i*8) for i in range(CHANNELS)];outputs=[ptr(frame,base.reg(frame,'x2')+i*8) for i in range(CHANNELS)]
            e=event(kind,before=[values(frame,p) for p in inputs]);back(frame,kind,e,buffers=outputs)
        return False
    except Exception as error:ERRORS.append(str(error));return True

def __lldb_init_module(debugger,_dict):
    global CHANNELS
    target=debugger.GetSelectedTarget();CHANNELS=int(os.environ['APAC_CHANNEL_COUNT'])
    if not target.GetTriple().startswith('arm64') or CHANNELS not in LAYOUT_TYPES:raise RuntimeError('unverified target/layout')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    if hashlib.sha256(component.read_bytes()).hexdigest()!=base.COMPONENT_SHA256:raise RuntimeError('component hash changed')
    points=dict(capacity=r'^APACASPDecoder::Initialize\(',packet=r'^APACASPDecoder::DecodeFrame\(',core=r'^APACCore(LBR)?Decoder::Deserialize\(TBitstreamReader',
                element=r'^APAC(SingleChannel|ChannelPair|LFE)Element::Deserialize\(',reset=r'^APAC(SingleChannel|ChannelPair|LFE)Element::Reset\(',
                stream=r'^APACIndividualChannelStream::Deserialize\(',tns=r'^APACTNSData::ParseTNSData\(',tns_apply=r'^APACTNSData::Apply\(',
                cac=r'^APACCACDecoder::ProcessCac\(',bwe2=r'^APACBWE2Decoder::Deserialize\(',synthesis=r'^APACSynthesisFilterBank::FrequencyToTimeInPlace\(',
                core_tools=r'^APACCoreDecoder::DeserializeExtTools\(',ancillary=r'^apac::APACAncillaryFrameDecoder::DecodeFrame\(',scene=r'^SC_SceneController::ProcessAudio\(')
    for kind,pattern in points.items():
        bp=target.BreakpointCreateByRegex(pattern);KINDS[bp.GetID()]=kind;bp.SetScriptCallbackFunction(__name__+'.hit')

def finish(debugger):
    import lldb
    process=debugger.GetSelectedTarget().GetProcess()
    result=dict(component_sha256=base.COMPONENT_SHA256,trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),architecture='arm64',channels=CHANNELS,
                packets=PACKETS,events=EVENTS,errors=ERRORS,pending_returns=len(RETURNS),process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    raw=(json.dumps(result,indent=2)+'\n').encode()
    if len(raw)>128*1024*1024:raise RuntimeError('trace exceeds 128 MiB')
    with Path(os.environ['APAC_CHANNEL_TRACE_OUTPUT']).open('xb') as out:out.write(raw)

def trace_bundle(binary,bundle,root,frames=4096):
    output=root/'native-channels.json';manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'));channels=manifest['file']['format']['channels']
    env=dict(os.environ,APAC_CHANNEL_COUNT=str(channels),APAC_CHANNEL_TRACE_OUTPUT=str(output.resolve()))
    if any(k.startswith('DYLD_') and v for k,v in env.items()):raise RuntimeError('injected libraries are not a native reference')
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames),'--processing-policy','drc-off']
    process=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),
                            '-o','run '+shlex.join(args),'-o','script native_channels_trace.finish(lldb.debugger)','--',str(binary.resolve())],
                            env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
    if process.returncode or not output.exists():raise RuntimeError('native channel trace failed: '+(process.stdout+process.stderr)[-2000:])
    result=json.loads(output.read_text(encoding='utf-8'))
    if result['errors'] or result['pending_returns'] or result['process_exit_code']!=0:raise RuntimeError('native trace incomplete: '+json.dumps({k:result[k] for k in ('errors','pending_returns','process_exit_code')}))
    return result
