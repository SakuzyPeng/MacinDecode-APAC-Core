"""Hash-bound, read-only HOA boundary snapshots for small representative bundles."""
import hashlib,json,os,shlex,struct,subprocess
from pathlib import Path
import native_channels_trace as channels
import native_frame_trace as base

KINDS={};RETURNS={};EVENTS=[];ERRORS=[];DECODER=None

def u32(frame,p):return struct.unpack('<I',base.memory(frame,p,4))[0]
def spatial(frame,p):
    info={name:u32(frame,p+off) for name,off in [('frame_samples',0x28),('output_channels',0x2c),('coefficients',0x30),('salient',0x34),('ambient',0x38),('transform_count',0x50),('transform_index',0x54),('coding_mode',0x118)]}
    if info['coefficients']!=16 or info['ambient']>16 or info['salient']>16:raise RuntimeError('unverified HOA spatial object layout')
    info['flags']=list(base.memory(frame,p+0x10,0x12));begin,end=struct.unpack('<QQ',base.memory(frame,p+0x638,16))
    if end-begin!=info['ambient']*4:raise RuntimeError('unverified ambient map vector')
    info['ambient_indices']=list(struct.unpack('<'+str(info['ambient'])+'I',base.memory(frame,begin,end-begin)))
    return info

def back(frame,e,**saved):
    target=frame.GetThread().GetProcess().GetTarget();bp=target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC());bp.SetThreadID(frame.GetThread().GetThreadID())
    RETURNS[bp.GetID()]=dict(event=e,**saved);bp.SetScriptCallbackFunction(__name__+'.hit')

def hit(frame,location,_dict):
    global DECODER
    try:
        key=location.GetBreakpoint().GetID()
        if key in RETURNS:
            saved=RETURNS.pop(key);frame.GetThread().GetProcess().GetTarget().BreakpointDelete(key);e=saved['event'];e['status']=base.reg(frame,'w0')
            if 'reader' in saved:e['end']=channels.reader(frame,saved['reader'])
            if e['kind']=='hoa_ics':e['ics']=base.ics_info(frame,saved['pointer']);e['limits']=list(base.memory(frame,saved['pointer']+8,2))
            elif e['kind']=='hoa_spatial':e['after']=spatial(frame,saved['pointer'])
            elif e['kind']=='hoa_deserialize':
                e['window']=u32(frame,saved['pointer']+0x130)
                e['transport']=[c for element in channels.ELEMENTS.values() for c in channels.snapshot(frame,element)]
            return False
        kind=KINDS[key];p=base.reg(frame,'x0');reader=base.reg(frame,'x1')
        if kind=='hoa_decode':
            DECODER=p
            e=dict(kind=kind,sequence=channels.SEQUENCE,role=channels.ROLE,frame_type=base.reg(frame,'w2'));EVENTS.append(e);return False
        position=channels.reader(frame,reader);e=dict(kind=kind,sequence=channels.SEQUENCE,role=position['role'],start=position);EVENTS.append(e)
        if kind=='hoa_ics':e['coding_type']=base.reg(frame,'w2')
        if kind=='hoa_spatial':e['before']=spatial(frame,p)
        back(frame,e,pointer=p,reader=reader);return False
    except Exception as error:ERRORS.append(str(error));return True

def __lldb_init_module(debugger,_dict):
    debugger.HandleCommand('script import native_channels_trace')
    channels.LAYOUT_TYPES[16]=[0]*16
    channels.__lldb_init_module(debugger,_dict)
    target=debugger.GetSelectedTarget()
    for kind,pattern in [('hoa_deserialize',r'^APACHOADecoder::Deserialize\('),('hoa_decode',r'^APACHOADecoder::DecodeAPACFrame\('),('hoa_ics',r'^APACHOAICSInfo::Deserialize\('),('hoa_spatial',r'^HOASpatialDecoder::Deserialize\(')]:
        bp=target.BreakpointCreateByRegex(pattern);KINDS[bp.GetID()]=kind;bp.SetScriptCallbackFunction(__name__+'.hit')

def finish(debugger):
    import lldb
    process=debugger.GetSelectedTarget().GetProcess()
    result=dict(component_sha256=base.COMPONENT_SHA256,trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),architecture='arm64',channels=16,
                packets=channels.PACKETS,events=channels.EVENTS,hoa_events=EVENTS,errors=channels.ERRORS+ERRORS,pending_returns=len(channels.RETURNS)+len(RETURNS),process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    raw=(json.dumps(result)+'\n').encode();assert len(raw)<=128*1024*1024
    with Path(os.environ['APAC_HOA_TRACE_OUTPUT']).open('xb') as f:f.write(raw)

def trace_bundle(binary,bundle,root,frames=2048):
    output=root/'native-hoa.json';env=dict(os.environ,APAC_CHANNEL_COUNT='16',APAC_HOA_TRACE_OUTPUT=str(output.resolve()))
    if any(k.startswith('DYLD_') and v for k,v in env.items()):raise RuntimeError('injected native reference')
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames),'--processing-policy','drc-off']
    proc=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),'-o','run '+shlex.join(args),'-o','script native_hoa_trace.finish(lldb.debugger)','--',str(binary.resolve())],env=env,capture_output=True,text=True,timeout=180)
    (root/'debugger.log').write_text(proc.stdout+proc.stderr,encoding='utf-8')
    if proc.returncode or not output.is_file():raise RuntimeError('HOA trace failed: '+(proc.stdout+proc.stderr)[-1600:])
    result=json.loads(output.read_text())
    if result['errors'] or result['pending_returns'] or result['process_exit_code']!=0:raise RuntimeError('incomplete HOA trace: '+str({k:result[k] for k in ('errors','pending_returns','process_exit_code')}))
    return result
