"""Read-only, hash-bound salient descriptors/history and coefficient snapshots.

Reuse existing element and synthesis boundaries; do not retrace the unchanged
SQ, TNS, CAC and BWE2 internals. No private function or state is modified.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import native_hoa_trace as hoa
import native_channels_trace as channels
import native_frame_trace as base

SPATIAL = None
original_spatial = hoa.spatial
original_hit = channels.hit


def vector(frame, address, kind, maximum):
    begin, end = struct.unpack('<QQ', base.memory(frame, address, 16))
    size = struct.calcsize(kind)
    if end < begin or (end-begin) % size or end-begin > maximum*size:
        raise RuntimeError('unverified salient vector shape')
    count = (end-begin)//size
    if count == 0:
        return []
    return list(struct.unpack('<'+kind*count, base.memory(frame, begin, end-begin)))


def nested(frame, address, kind, outer, inner):
    begin, end = struct.unpack('<QQ', base.memory(frame, address, 16))
    if end-begin != outer*24:
        raise RuntimeError('unverified salient nested vector shape')
    return [vector(frame, p, kind, inner) for p in range(begin, end, 24)]


def spatial(frame, pointer):
    global SPATIAL
    info = {name:hoa.u32(frame,pointer+offset) for name,offset in
            [('frame_samples',0x28),('output_channels',0x2c),('coefficients',0x30),
             ('salient',0x34),('ambient',0x38),('transform_count',0x50),
             ('transform_index',0x54),('coding_mode',0x118)]}
    info['flags'] = list(base.memory(frame,pointer+0x10,0x12))
    info['ambient_indices'] = vector(frame,pointer+0x638,'I',16)
    if (info['salient'], info['ambient'], info['coefficients']) != (5, 0, 16):
        raise RuntimeError('requires the qualified salient5/ambient0 control')
    SPATIAL = pointer
    info['quantization_bits'] = hoa.u32(frame, pointer+0x3c)
    for name, offset, kind, limit in [('subbands',0x58,'I',5),('coefficient_counts',0x70,'I',5),
                                    ('quantized',0xb8,'i',320),('history',0xe8,'f',320),
                                    ('signs',0x100,'B',320)]:
        info[name] = vector(frame, pointer+offset, kind, limit)
    # Native boundaries are indexed by the number of spatial subbands.
    begin, end = struct.unpack('<QQ', base.memory(frame, pointer+0x88, 16))
    if end-begin < 4*24 or end-begin > 16*24:
        raise RuntimeError('unverified spatial subband table')
    info['four_subband_ends'] = vector(frame, begin+3*24, 'I', 4)
    for name, offset in [('modes',0x120),('clusters',0x138),('azimuth',0x150),('elevation',0x168)]:
        info[name] = nested(frame, pointer+offset, 'I', 5, 4)
    return info


def channel_hit(frame, location, data):
    try:
        if channels.KINDS.get(location.GetBreakpoint().GetID()) == 'synthesis' and base.reg(frame, 'w1') == 0:
            if SPATIAL is None:
                raise RuntimeError('synthesis before spatial capture')
            hoa.EVENTS.append(dict(kind='hoa_history', sequence=channels.SEQUENCE,
                                   role=channels.ROLE, after=spatial(frame, SPATIAL)))
    except Exception as error:
        hoa.ERRORS.append(str(error)); return True
    return original_hit(frame, location, data)


def __lldb_init_module(debugger, state):
    debugger.HandleCommand('script import native_hoa_trace')
    hoa.spatial = spatial
    channels.hit = channel_hit
    hoa.__lldb_init_module(debugger, state)
    target = debugger.GetSelectedTarget()
    for key, kind in list(channels.KINDS.items()):
        if kind not in ('capacity','packet','core','element','reset','synthesis','ancillary'):
            target.BreakpointDelete(key)
            del channels.KINDS[key]


def finish(debugger):
    import lldb
    process = debugger.GetSelectedTarget().GetProcess()
    result = dict(component_sha256=base.COMPONENT_SHA256,
                  trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  architecture='arm64', channels=16, packets=channels.PACKETS,
                  events=channels.EVENTS, hoa_events=hoa.EVENTS,
                  errors=channels.ERRORS+hoa.ERRORS,
                  pending_returns=len(channels.RETURNS)+len(hoa.RETURNS),
                  process_exit_code=process.GetExitStatus() if process.GetState()==lldb.eStateExited else None)
    raw=(json.dumps(result)+'\n').encode()
    if len(raw)>128*1024*1024:
        raise RuntimeError('trace exceeds output budget')
    with Path(os.environ['APAC_HOA_TRACE_OUTPUT']).open('xb') as f:
        f.write(raw)


def trace_bundle(binary, bundle, root, frames=1024):
    output=root/'native-hoa.json'
    env=dict(os.environ, APAC_CHANNEL_COUNT='16', APAC_HOA_TRACE_OUTPUT=str(output.resolve()))
    if any(k.startswith('DYLD_') and v for k,v in env.items()):
        raise RuntimeError('injected native reference')
    args=['replay',str(bundle.resolve()),'--out',str((root/'native-pcm').resolve()),
          '--frames',str(frames),'--processing-policy','drc-off']
    proc=subprocess.run(['xcrun','lldb','--batch','-o','command script import '+shlex.quote(str(Path(__file__).resolve())),
                         '-o','run '+shlex.join(args),'-o','script native_hoa_salient_trace.finish(lldb.debugger)',
                         '--',str(binary.resolve())],env=env,capture_output=True,text=True,timeout=180)
    (root/'debugger.log').write_text(proc.stdout+proc.stderr,encoding='utf-8')
    if proc.returncode or not output.is_file():
        raise RuntimeError('salient trace failed: '+(proc.stdout+proc.stderr)[-1600:])
    result=json.loads(output.read_text())
    if result['errors'] or result['pending_returns'] or result['process_exit_code']!=0:
        raise RuntimeError('incomplete salient trace: '+str({k:result[k] for k in ('errors','pending_returns','process_exit_code')}))
    return result
