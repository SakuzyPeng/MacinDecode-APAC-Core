"""Hash-bound per-component spatial-subband HOA evidence; reads memory without changing native state."""
import hashlib, json, os, shlex, struct, subprocess
from pathlib import Path
import native_frame_trace as base
import native_channels_trace as channels
import native_hoa_trace as hoa
from native_hoa_salient_trace import vector, nested

SPATIAL=None
COUNT=0
original_hit=channels.hit


def spatial(frame,pointer):
    global SPATIAL
    info={name:hoa.u32(frame,pointer+offset) for name,offset in
          [('frame_samples',0x28),('output_channels',0x2c),('coefficients',0x30),
           ('salient',0x34),('ambient',0x38),('transform_count',0x50),('transform_index',0x54),('coding_mode',0x118)]}
    n=info['coefficients']; s=info['salient']
    if n not in (4,9,16) or info['output_channels']!=COUNT or not 1<=s<=n or s+info['ambient']>COUNT or info['ambient'] not in (0,4) or info['transform_count']>4:
        raise RuntimeError('unqualified mixed HOA configuration')
    SPATIAL=pointer; info['flags']=list(base.memory(frame,pointer+0x10,0x12))
    info['ambient_indices']=vector(frame,pointer+0x638,'I',info['ambient'])
    info['quantization_bits']=hoa.u32(frame,pointer+0x3c)
    info['omitted_ambient_count']=hoa.u32(frame,pointer+0x618)
    info['subbands']=vector(frame,pointer+0x58,'I',s)
    info['coefficient_counts']=vector(frame,pointer+0x70,'I',s)
    if len(info['subbands'])!=s or any(not 1<=v<=16 for v in info['subbands']): raise RuntimeError('unqualified spatial subband counts')
    maximum=max(info['subbands']);info['maximum_subbands']=hoa.u32(frame,pointer+0x44)
    if maximum!=info['maximum_subbands']: raise RuntimeError('maximum subband count differs')
    for name,offset,kind in [('quantized',0xb8,'i'),('history',0xe8,'f'),('signs',0x100,'B')]:
        info[name]=vector(frame,pointer+offset,kind,s*16*16)
    begin,end=struct.unpack('<QQ',base.memory(frame,pointer+0x88,16))
    if end-begin!=maximum*24: raise RuntimeError('unverified spatial boundary cache')
    info['subband_tables']=[vector(frame,begin+(i-1)*24,'I',i) for i in range(1,maximum+1)]
    for name,offset in [('modes',0x120),('clusters',0x138),('azimuth',0x150),('elevation',0x168),('omitted_counts',0x620)]:
        info[name]=nested(frame,pointer+offset,'I',s,16)
    info['dynamic_subbands']=hoa.u32(frame,pointer+0x48)
    info['dynamic_ends']=vector(frame,pointer+0xa0,'I',info['dynamic_subbands']) if info['flags'][12] else []
    info['dynamic_maps']=[list(struct.unpack('<9I',base.memory(frame,pointer+0x198+b*0x90,36))) for b in range(8)] if info['flags'][12] else []
    return info


def channel_hit(frame,location,data):
    try:
        if channels.KINDS.get(location.GetBreakpoint().GetID())=='synthesis' and base.reg(frame,'w1')==0:
            if SPATIAL is None: raise RuntimeError('synthesis before spatial capture')
            hoa.EVENTS.append(dict(kind='hoa_history',sequence=channels.SEQUENCE,role=channels.ROLE,after=spatial(frame,SPATIAL)))
    except Exception as error: hoa.ERRORS.append(str(error)); return True
    return original_hit(frame,location,data)


def __lldb_init_module(debugger,state):
    global COUNT
    COUNT=int(os.environ['APAC_CHANNEL_COUNT'])
    if COUNT not in (4,9,16): raise RuntimeError('unverified coefficient count')
    debugger.HandleCommand('script import native_hoa_trace')
    channels.LAYOUT_TYPES[COUNT]=[0]*COUNT; hoa.spatial=spatial; channels.hit=channel_hit
    hoa.__lldb_init_module(debugger,state)
    target=debugger.GetSelectedTarget()
    for key,kind in list(channels.KINDS.items()):
        if kind not in ('capacity','packet','core','element','reset','synthesis','ancillary'):
            target.BreakpointDelete(key); del channels.KINDS[key]


def finish(debugger):
    import lldb
    process=debugger.GetSelectedTarget().GetProcess()
    result=dict(component_sha256=base.COMPONENT_SHA256,trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                architecture='arm64',channels=COUNT,packets=channels.PACKETS,events=channels.EVENTS,hoa_events=hoa.EVENTS,
                errors=channels.ERRORS+hoa.ERRORS,pending_returns=len(channels.RETURNS)+len(hoa.RETURNS),
                process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    raw=(json.dumps(result)+'\n').encode()
    if len(raw)>128*1024*1024: raise RuntimeError('trace exceeds output limit')
    with Path(os.environ['APAC_HOA_TRACE_OUTPUT']).open('xb') as f: f.write(raw)


def trace_bundle(binary,bundle,root,frames=1024):
    count=json.loads((bundle/'manifest.json').read_text())['file']['format']['channels']; output=root/'native-hoa.json'
    env=dict(os.environ,APAC_CHANNEL_COUNT=str(count),APAC_HOA_TRACE_OUTPUT=str(output.resolve()))
    if any(k.startswith('DYLD_') and v for k,v in env.items()): raise RuntimeError('injected native reference')
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames),'--processing-policy','drc-off']
    proc=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),
                         '-o','run '+shlex.join(args),'-o','script native_hoa_salient_subbands_trace.finish(lldb.debugger)',
                         '--',str(binary.resolve())],env=env,capture_output=True,text=True,timeout=180)
    (root/'debugger.log').write_text(proc.stdout+proc.stderr,encoding='utf-8')
    if proc.returncode or not output.is_file(): raise RuntimeError('mixed trace failed: '+(proc.stdout+proc.stderr)[-1600:])
    result=json.loads(output.read_text())
    if result['errors'] or result['pending_returns'] or result['process_exit_code']!=0:
        raise RuntimeError(str({k:result[k] for k in ('errors','pending_returns','process_exit_code')}))
    return result
