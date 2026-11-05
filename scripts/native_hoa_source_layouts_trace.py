"""Read-only native source-layout evidence, with existing bounded HOA readers."""
import hashlib,json,os,shlex,struct,subprocess,sys
from pathlib import Path
import native_hoa_transports_trace as transport
import native_hoa_salient_subbands_trace as spatial
import native_hoa_trace as hoa
import native_frame_trace as base

original_spatial=spatial.spatial
MATRICES={}
def snapshot(frame,p):
    info=original_spatial(frame,p)
    info['source_conversion_enabled']=bool(base.memory(frame,p+32,1)[0])
    info['lfe_indices']=list(struct.unpack('<2b',base.memory(frame,p+1624,2)))
    if os.environ.get('APAC_HOA_CAPTURE_MATRICES')=='1' and not MATRICES:
        import math
        target=frame.GetThread().GetProcess().GetTarget()
        for row in json.loads(Path(os.environ['APAC_HOA_MATRIX_CATALOG']).read_text()):
            name=row['matrix']
            if name in MATRICES:continue
            rows=sum(label not in (4,37,62) for label in row['labels']);columns=(math.isqrt(rows-1)+1)**2
            symbols=target.FindSymbols('HOASpatialDecoder::'+name)
            if symbols.GetSize()!=1:raise RuntimeError('matrix symbol not unique '+name)
            address=symbols.GetContextAtIndex(0).GetSymbol().GetStartAddress().GetLoadAddress(target)
            MATRICES[name]=list(struct.unpack('<'+str(rows*columns)+'I',base.memory(frame,address,rows*columns*4)))
    return info

def __lldb_init_module(debugger,state):
    base.begin_probe(debugger.GetSelectedTarget(), 'hoa_source_layouts')
    spatial.spatial=snapshot
    transport.__lldb_init_module(debugger,state)

def finish(debugger):
    transport.finish(debugger)
    if MATRICES:
        p=Path(os.environ['APAC_HOA_TRACE_OUTPUT']);r=json.loads(p.read_text());r['matrix_constants']=MATRICES;p.write_text(json.dumps(r)+'\n')

def trace_bundle(binary,bundle,root,types,frames):
    n=json.loads((bundle/'manifest.json').read_text())['file']['format']['channels']
    output=root/'native-hoa.json'
    env=dict(os.environ,APAC_CHANNEL_COUNT=str(n),APAC_HOA_TCE_TYPES=json.dumps(types),APAC_HOA_TRACE_OUTPUT=str(output.resolve()))
    if any(k.startswith('DYLD_') and v for k,v in env.items()):raise RuntimeError('injected reference')
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),'--frames',str(frames),'--processing-policy','drc-off']
    proc=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),'-o','run '+shlex.join(args),'-o','script native_hoa_source_layouts_trace.finish(lldb.debugger)','--',str(binary.resolve())],env=env,capture_output=True,text=True,timeout=60)
    (root/'debugger.log').write_text(proc.stdout+proc.stderr)
    if not output.exists():return dict(errors=['trace missing'],process_exit_code=proc.returncode)
    result=json.loads(output.read_text());result['source_probe_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.write_text(json.dumps(result)+'\n')
    return result
