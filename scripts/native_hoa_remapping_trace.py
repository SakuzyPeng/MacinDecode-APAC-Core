"""Read-only carrier-vector snapshots around native remapping."""
import json,os,shlex,struct,subprocess,sys,hashlib
from pathlib import Path
import native_hoa_source_layouts_trace as source
import native_channels_trace as channels
import native_frame_trace as base
RETURNS={};EVENTS=[];ERRORS=[]

def vector(frame,p):
    begin,end=struct.unpack('<QQ',base.memory(frame,p,16))
    if not 0<end-begin<=121*8 or (end-begin)%8:raise RuntimeError('invalid carrier vector')
    pointers=struct.unpack('<'+str((end-begin)//8)+'Q',base.memory(frame,begin,end-begin))
    return [struct.unpack('<f',base.memory(frame,x,4))[0] for x in pointers]

def hit(frame,location,data):
    try:
        base.observe(frame, location)
        key=location.GetBreakpoint().GetID();target=frame.GetThread().GetProcess().GetTarget()
        if key in RETURNS:
            event,p=RETURNS.pop(key);target.BreakpointDelete(key);event['status']=base.reg(frame,'w0');event['after']=vector(frame,p);return False
        instance=base.reg(frame,'x0');p=base.reg(frame,'x1');begin,end=struct.unpack('<QQ',base.memory(frame,instance+8,16))
        if not 1<=end-begin<=121:raise RuntimeError('invalid remapping vector')
        event=dict(sequence=channels.SEQUENCE,role=channels.ROLE,wire_indices=list(base.memory(frame,begin,end-begin)),before=vector(frame,p));EVENTS.append(event)
        bp=target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC());bp.SetThreadID(frame.GetThread().GetThreadID());RETURNS[bp.GetID()]=(event,p);bp.SetScriptCallbackFunction(__name__+'.hit');return False
    except Exception as error:ERRORS.append(str(error));return True

def __lldb_init_module(debugger,state):
    base.begin_probe(debugger.GetSelectedTarget(), 'hoa_remapping')
    debugger.HandleCommand('script import native_hoa_transports_trace')
    source.__lldb_init_module(debugger,state)
    bp=base.breakpoint(debugger.GetSelectedTarget(), r'^APACChannelRemapper::Process\(');bp.SetScriptCallbackFunction(__name__+'.hit')

def finish(debugger):
    source.finish(debugger);p=Path(os.environ['APAC_HOA_TRACE_OUTPUT']);r=json.loads(p.read_text());r['remapping']=EVENTS;r['errors']+=ERRORS;r['pending_returns']+=len(RETURNS);r['remapping_probe_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();p.write_text(json.dumps(r)+'\n')

def trace_bundle(binary,bundle,root,n,types=None,frames=1024):
    output=root/'native-hoa.json';env=dict(os.environ,APAC_CHANNEL_COUNT=str(n),APAC_HOA_TCE_TYPES=json.dumps([0]*n if types is None else types),APAC_HOA_TRACE_OUTPUT=str(output.resolve()))
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames),'--processing-policy','drc-off']
    proc=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),'-o','run '+shlex.join(args),'-o','script native_hoa_remapping_trace.finish(lldb.debugger)','--',str(binary.resolve())],env=env,capture_output=True,text=True,timeout=60)
    (root/'debugger.log').write_text(proc.stdout+proc.stderr)
    return json.loads(output.read_text()) if output.exists() else dict(errors=['missing trace'],process_exit_code=proc.returncode)
