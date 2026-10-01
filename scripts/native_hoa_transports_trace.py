"""Read-only HOA transport composition boundaries; reuses existing spatial evidence readers."""
import hashlib
import json
import os
import shlex
import struct
import subprocess
from pathlib import Path

import native_frame_trace as base
import native_channels_trace as channels
import native_hoa_trace as hoa
import native_hoa_salient_subbands_trace as spatial

TYPES = []
ORIGINAL = channels.hit


def hit(frame, location, data):
    key = location.GetBreakpoint().GetID()
    try:
        if key not in channels.RETURNS and channels.KINDS.get(key) == 'core':
            reader = base.reg(frame, 'x1')
            position = channels.reader(frame, reader)
            channels.ROLE = position['role']
            event = channels.event('core', start=position)
            instance = base.reg(frame, 'x0')
            begin, end = struct.unpack('<QQ', base.memory(frame, instance + 0xb0, 16))
            if end - begin != len(TYPES) * 16:
                raise RuntimeError('HOA element vector differs from declared types')
            channels.ELEMENTS = {}
            first = 0
            for index, address in enumerate(range(begin, end, 16)):
                pointer = channels.ptr(frame, address + 8)
                kind = struct.unpack('<I', base.memory(frame, pointer + 8, 4))[0]
                if kind != TYPES[index]:
                    raise RuntimeError('HOA element order/type differs')
                width = {0: 1, 1: 2, 3: 1, 6: 0}[kind]
                channels.ELEMENTS[pointer] = dict(pointer=pointer, element_index=index,
                    element_type=kind, channels=list(range(first, first + width)))
                first += width
            event['elements'] = [{k:v for k,v in e.items() if k != 'pointer'}
                                 for e in channels.ELEMENTS.values()]
            event['transport_channels'] = first
            channels.back(frame, 'core', event, reader=reader)
            return False
        if channels.KINDS.get(key) == 'synthesis' and base.reg(frame, 'w1') == 0:
            if spatial.SPATIAL is None:
                raise RuntimeError('synthesis before spatial capture')
            hoa.EVENTS.append(dict(kind='hoa_history', sequence=channels.SEQUENCE,
                                   role=channels.ROLE, after=spatial.spatial(frame, spatial.SPATIAL)))
        return ORIGINAL(frame, location, data)
    except Exception as error:
        channels.ERRORS.append(str(error))
        return True


def __lldb_init_module(debugger, state):
    global TYPES
    TYPES = json.loads(os.environ['APAC_HOA_TCE_TYPES'])
    count = int(os.environ['APAC_CHANNEL_COUNT'])
    if not TYPES or any(t not in (0, 1, 3, 6) for t in TYPES):
        raise RuntimeError('unverified HOA transport types')
    spatial.COUNT = count
    debugger.HandleCommand('script import native_hoa_trace')
    hoa.spatial = spatial.spatial
    channels.LAYOUT_TYPES[count] = TYPES
    channels.hit = hit
    hoa.__lldb_init_module(debugger, state)
    target = debugger.GetSelectedTarget()
    for key, kind in list(channels.KINDS.items()):
        if kind not in ('capacity', 'packet', 'core', 'element', 'reset', 'synthesis', 'ancillary', 'bwe2'):
            target.BreakpointDelete(key)
            del channels.KINDS[key]
    bp = target.BreakpointCreateByRegex(r'^APACExtElement::Deserialize\(')
    channels.KINDS[bp.GetID()] = 'element'
    bp.SetScriptCallbackFunction(__name__ + '.hit')


def finish(debugger):
    import lldb
    process = debugger.GetSelectedTarget().GetProcess()
    modules = [Path(m.__file__) for m in (base, channels, hoa, spatial)]
    result = dict(component_sha256=base.COMPONENT_SHA256,
        trace_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        trace_dependencies={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in modules},
        architecture='arm64', channels=channels.CHANNELS, tce_types=TYPES,
        packets=channels.PACKETS, events=channels.EVENTS, hoa_events=hoa.EVENTS,
        errors=channels.ERRORS + hoa.ERRORS,
        pending_returns=len(channels.RETURNS) + len(hoa.RETURNS),
        process_exit_code=process.GetExitStatus() if process.GetState() == lldb.eStateExited else None)
    raw = (json.dumps(result) + '\n').encode()
    if len(raw) > 128 * 1024 * 1024:
        raise RuntimeError('native transport trace exceeds output budget')
    with Path(os.environ['APAC_HOA_TRACE_OUTPUT']).open('xb') as f:
        f.write(raw)


def trace_bundle(binary, bundle, root, tce_types, frames):
    count = json.loads((bundle / 'manifest.json').read_text())['file']['format']['channels']
    output = root / 'native-hoa.json'
    env = dict(os.environ, APAC_CHANNEL_COUNT=str(count), APAC_HOA_TRACE_OUTPUT=str(output.resolve()),
               APAC_HOA_TCE_TYPES=json.dumps(tce_types))
    if any(k.startswith('DYLD_') and v for k, v in env.items()):
        raise RuntimeError('injected native reference')
    args = ['replay', str(bundle.resolve()), '--out', str((root / 'native-pcm').resolve()),
            '--frames', str(frames), '--processing-policy', 'drc-off']
    proc = subprocess.run(['xcrun', 'lldb', '--batch', '-o',
        'command script import ' + shlex.quote(str(Path(__file__).resolve())),
        '-o', 'run ' + shlex.join(args), '-o',
        'script native_hoa_transports_trace.finish(lldb.debugger)', '--', str(binary.resolve())],
        env=env, capture_output=True, text=True, timeout=180)
    (root / 'debugger.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode or not output.is_file():
        raise RuntimeError('HOA transport trace failed: ' + (proc.stdout + proc.stderr)[-1200:])
    result = json.loads(output.read_text())
    if result['errors'] or result['pending_returns'] or result['process_exit_code'] != 0:
        raise RuntimeError(str({k:result[k] for k in ('errors', 'pending_returns', 'process_exit_code')}))
    return result
