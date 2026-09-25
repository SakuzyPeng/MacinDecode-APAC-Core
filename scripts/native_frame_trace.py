"""Research-only LLDB oracle for the verified arm64 AudioCodecs build.

This launches our own replay process, reads bit-reader positions at codec entry
points, and resumes it. No private function is called and no codec state is
modified. These memory layouts are not part of the portable parser or C bridge.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess

COMPONENT_SHA256 = "826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd"
KINDS = {}
EVENTS = []
ERRORS = []
CURRENT = None
PENDING = None
CORE_READER = None
SEQUENCE = -1
SPECTRA = []
RETURNS = {}
CHANNEL_COUNT = 0
TRACE_SPECTRA = False
TRACE_WINDOWS = False
WINDOWS = {}
TRACE_CAC = False
CACS = []
FRAME_READER = None
TRACE_TNS = False
TNS = []
TNS_APPLY = []
BWE_ENTRIES = []
CPE = None
TRACE_BWE2 = False
BWE2_READS = []
BWE2_APPLY = []


def trace_bundle(binary, bundle, root, spectra=False, windows=False, cac=False, tns=False, allow_replay_failure=False, bwe2=False):
    output = root / "native-boundaries.json"
    if output.exists():
        raise RuntimeError("refusing to overwrite native boundary report")
    manifest = json.loads((bundle / "manifest.json").read_text())
    frames = manifest["file"]["packet_table"]["value"]["valid_frames"]
    env = dict(os.environ, APAC_FRAME_TRACE_OUTPUT=str(output))
    if spectra or cac or tns or bwe2:
        env["APAC_SPECTRUM_TRACE"] = "1"
    else:
        env.pop("APAC_SPECTRUM_TRACE", None)
    if windows:
        env["APAC_WINDOW_TRACE"] = "1"
    else:
        env.pop("APAC_WINDOW_TRACE", None)
    if cac:
        env['APAC_CAC_TRACE'] = '1'
    else:
        env.pop('APAC_CAC_TRACE', None)
    if tns:
        env['APAC_TNS_TRACE'] = '1'
    else:
        env.pop('APAC_TNS_TRACE', None)
    if bwe2:
        env['APAC_BWE2_TRACE']='1'
        env['APAC_TNS_TRACE']='1'
    else:
        env.pop('APAC_BWE2_TRACE',None)
    module = Path(__file__).resolve()
    replay = ["replay", str(bundle), "--out", str(root / "native-trace-pcm"), "--frames", str(frames)]
    process = subprocess.run(["xcrun", "lldb", "--batch", "-o", "command script import " + shlex.quote(str(module)),
                              "-o", "run " + shlex.join(replay), "-o", "script native_frame_trace.finish(lldb.debugger)",
                              "--", str(binary)], env=env, capture_output=True, text=True, timeout=120)
    if process.returncode or not output.exists():
        raise RuntimeError("native boundary trace failed: " + (process.stdout + process.stderr)[-2000:])
    result = json.loads(output.read_text())
    if result["errors"] or (result["process_exit_code"] != 0 and not allow_replay_failure):
        raise RuntimeError("native boundary trace incomplete: " + json.dumps({k: result[k] for k in ["errors", "process_exit_code", "packet_calls"]}))
    return result


def reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def memory(frame, address, size):
    import lldb
    error = lldb.SBError()
    data = frame.GetThread().GetProcess().ReadMemory(address, size, error)
    if not error.Success() or len(data) != size:
        raise RuntimeError("cannot read native oracle memory: " + str(error))
    return data


def reader(frame, pointer):
    current, end, total, _cache, cached_bits = struct.unpack("<QQIIi", memory(frame, pointer, 28))
    position = total * 8 - ((end - current) * 8 + cached_bits)
    if CURRENT is None or end != CURRENT["end"] or total != CURRENT["packet_bytes"]:
        return None  # Embedded preroll uses a separate reader and backing buffer.
    if not 0 <= position <= total * 8:
        raise RuntimeError("unexpected bit-reader layout or out-of-bounds native cursor")
    return position


def ics_info(frame, pointer):
    data = memory(frame, pointer + 8, 12)
    packed = struct.unpack_from("<I", data, 8)[0]
    count = data[4]
    if count > 8:
        raise RuntimeError("unverified native ICS layout")
    return {"block_type": data[2], "max_sfb": data[3], "active_group_count": count,
            "active_window_groups": [(packed >> (4 * i)) & 15 for i in range(count)]}


def capture(frame, pointer, boundary):
    global PENDING, CORE_READER
    position = reader(frame, pointer)
    if position is None:
        return
    entry = {key: CURRENT[key] for key in ["sequence", "packet_sha256", "packet_bytes"]}
    entry.update({"native_bit_offset": position, "boundary": boundary,
                  "ics": [ics_info(frame, p) for p in PENDING["ics"]] if PENDING else []})
    EVENTS.append(entry)
    if len(EVENTS) > 8192:
        raise RuntimeError("native trace exceeded packet limit")
    PENDING = None
    CORE_READER = None


def tns_data(frame, pointer, info):
    data = memory(frame, pointer, 190)
    short = info['block_type'] == 2
    windows = []
    for w in range(8 if short else 1):
        count = (data[1] >> (7-w)) & 1 if short else data[1]
        if count > (1 if short else 3):
            raise RuntimeError('unverified native TNS filter count')
        filters = []
        for i in range(count):
            offset = 2+23*(w if short else i)
            direction, length, order = data[offset:offset+3]
            if order > (7 if short else 12):
                raise RuntimeError('unverified native TNS order')
            codes = list(data[offset+3:offset+3+order])
            filters.append(dict(direction=bool(direction) if order else None, length=length, order=order,
                                quantized=[(v & 15)-8 for v in codes],
                                resolution=(3+(codes[0] >> 4)) if codes else None))
        windows.append(dict(window_index=w, filters=filters))
    return dict(present=bool(data[0]), windows=windows, limits=list(data[186:190]))


def return_breakpoint(frame, saved):
    target = frame.GetThread().GetProcess().GetTarget()
    bp = target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
    bp.SetThreadID(frame.GetThread().GetThreadID())
    RETURNS[bp.GetID()] = saved
    bp.SetScriptCallbackFunction(__name__ + '.on_breakpoint')


def bwe2_data(frame, pointer):
    active_ptr=struct.unpack('<Q',memory(frame,pointer+8,8))[0]
    active=struct.unpack('<Q',memory(frame,active_ptr,8))[0]
    indices=struct.unpack('<Q',memory(frame,pointer+0x30,8))[0]
    gains=struct.unpack('<Q',memory(frame,pointer+0x20,8))[0]
    return dict(active=[bool(active & (1<<i)) for i in range(2)],
                lsf_indices=[list(struct.unpack('<2i',memory(frame,indices+8*i,8))) for i in range(2)],
                gain_indices=[list(struct.unpack('<8i',memory(frame,gains+32*i,32))) for i in range(2)])


def on_breakpoint(frame, location, _dict):
    global CURRENT, PENDING, CORE_READER, SEQUENCE, CHANNEL_COUNT, FRAME_READER, CPE
    try:
        bp_id = location.GetBreakpoint().GetID()
        if bp_id in RETURNS:
            saved = RETURNS.pop(bp_id)
            frame.GetThread().GetProcess().GetTarget().BreakpointDelete(bp_id)
            if saved.get('kind') == 'bwe2_read':
                if reg(frame,'w0')!=0:raise RuntimeError('native BWE2 parser rejected input')
                event=saved['event'];event['end_bit_offset']=reader(frame,saved['reader'])
                event['data']=bwe2_data(frame,saved['pointer']);BWE2_READS.append(event)
                return False
            if saved.get('kind') == 'bwe2_apply':
                if reg(frame,'w0')!=0:raise RuntimeError('native BWE2 transform rejected input')
                event=saved['event'];event['after']=list(struct.unpack('<1024f',memory(frame,saved['buffer'],4096)))
                lpc=saved['lpc']
                for name,offset in [('source_lpc',0x18),('target_lpc',0x30),('conditioned_lsf',0xc0)]:
                    pointer=struct.unpack('<Q',memory(frame,lpc+offset,8))[0]
                    count=16 if name=='conditioned_lsf' else 17
                    event[name]=list(struct.unpack('<'+str(count)+'f',memory(frame,pointer,4*count)))
                BWE2_APPLY.append(event)
                return False
            if saved.get('kind') == 'tns_apply':  # Apply returns void, not a status.
                event = saved['event']
                event['after'] = list(struct.unpack('<1024f', memory(frame, saved['buffer'], 4096)))
                TNS_APPLY.append(event)
                return False
            if reg(frame, "w0") != 0:
                raise RuntimeError("native channel stream rejected input")
            if saved.get('kind') == 'tns_read':
                event = saved['event']
                event['end_bit_offset'] = reader(frame, saved['reader'])
                event['data'] = tns_data(frame, saved['pointer'], event['ics'])
                TNS.append(event)
                return False
            if saved.get('kind') == 'bwe':
                BWE_ENTRIES.append(dict(sequence=CURRENT['sequence'], packet_sha256=CURRENT['packet_sha256'],
                                        bit_offset=reader(frame, saved['reader'])))
                return False
            if saved.get('kind') == 'cac':
                event = saved['event']
                position = reader(frame, saved['reader'])
                if position != event['end_bit_offset']:
                    raise RuntimeError('CAC transform consumed unexpected bits')
                event['after'] = [list(struct.unpack('<1024f', memory(frame, p, 4096))) for p in saved['buffers']]
                event['tns_start_bit_offset'] = position
                CACS.append(event)
                if len(CACS) > 8192:
                    raise RuntimeError('CAC trace exceeded limit')
                return False
            position = reader(frame, saved["reader"])
            if position is None:
                raise RuntimeError("native stream reader changed identity")
            first, end = struct.unpack("<QQ", memory(frame, saved["stream"] + 0x2f0, 16))
            if end - first != 4096:
                raise RuntimeError("unverified native spectrum size")
            values = list(struct.unpack("<1024f", memory(frame, first, 4096)))
            SPECTRA.append(dict(sequence=CURRENT["sequence"], packet_sha256=CURRENT["packet_sha256"],
                channel_index=saved["channel_index"], stream_bit_offset=saved["start"], end_bit_offset=position, scaled=values))
            if len(SPECTRA) > 8192:
                raise RuntimeError("native spectrum trace exceeded limit")
            return False
        kind = KINDS[bp_id]
        if kind == "window":
            if not WINDOWS:
                instance = reg(frame, "x0")
                for name, count, size_offset, pointer_offset in [("long", 1024, 0x28, 0x80), ("short", 128, 0x50, 0x78)]:
                    actual = struct.unpack("<I", memory(frame, instance + size_offset, 4))[0]
                    if actual != count:
                        raise RuntimeError("unverified sine window size")
                    pointer = struct.unpack("<Q", memory(frame, instance + pointer_offset, 8))[0]
                    WINDOWS[name] = list(struct.unpack("<" + str(count) + "f", memory(frame, pointer, count * 4)))
            return False
        if kind == "packet":
            base, size = reg(frame, "x1"), reg(frame, "x2")
            if not 0 < size <= 16 * 1024 * 1024:
                raise RuntimeError("unexpected native packet size")
            SEQUENCE += 1
            CHANNEL_COUNT = 0
            CURRENT = {"end": base + size, "packet_bytes": size, "sequence": SEQUENCE,
                       "packet_sha256": hashlib.sha256(memory(frame, base, size)).hexdigest()}
            PENDING = CORE_READER = FRAME_READER = CPE = None
        elif kind == "core":
            pointer = reg(frame, "x1")
            if reader(frame, pointer) is not None:
                CORE_READER = pointer
        elif kind == "sq":
            pointer = reg(frame, "x1")
            if reader(frame, pointer) is not None:
                PENDING = {"reader": pointer, "ics": []}
                FRAME_READER = pointer
                CPE = reg(frame, 'x0')
                if TRACE_TNS:
                    return_breakpoint(frame, dict(kind='bwe', reader=pointer))
        elif kind in ('tns_read', 'tns_apply') and FRAME_READER is not None:
            pointer = reg(frame, 'x0')
            if pointer not in (CPE+0x80, CPE+0x13e):
                return False
            channel = int(pointer == CPE+0x13e)
            if kind == 'tns_read':
                source = reg(frame, 'x1')
                position = reader(frame, source)
                if position is None:
                    return False
                event = dict(sequence=CURRENT['sequence'], packet_sha256=CURRENT['packet_sha256'],
                             channel_index=channel, start_bit_offset=position, ics=ics_info(frame, reg(frame,'x2')))
                return_breakpoint(frame, dict(kind=kind, event=event, reader=source, pointer=pointer))
            else:
                info = ics_info(frame, reg(frame, 'x1'))
                buffer = reg(frame, 'x4')
                event = dict(sequence=CURRENT['sequence'], packet_sha256=CURRENT['packet_sha256'],
                             channel_index=channel, bit_offset=reader(frame, FRAME_READER), ics=info,
                             full_band_count=reg(frame,'w2'), data=tns_data(frame,pointer,info),
                             before=list(struct.unpack('<1024f', memory(frame,buffer,4096))))
                return_breakpoint(frame, dict(kind=kind, event=event, buffer=buffer))
        elif kind == 'bwe2_read' and FRAME_READER is not None:
            pointer=reg(frame,'x1');position=reader(frame,pointer)
            if position is None:return False
            if reg(frame,'x3')!=CPE or reg(frame,'w2')!=0:raise RuntimeError('unverified BWE2 element/channel')
            event=dict(sequence=CURRENT['sequence'],packet_sha256=CURRENT['packet_sha256'],start_bit_offset=position)
            return_breakpoint(frame,dict(kind=kind,event=event,reader=pointer,pointer=reg(frame,'x0')))
        elif kind == 'bwe2_apply' and FRAME_READER is not None:
            channel=reg(frame,'w3')
            if channel not in (0,1):raise RuntimeError('unverified BWE2 channel')
            own=[e for e in BWE2_READS if e['sequence']==CURRENT['sequence']]
            if len(own)!=1:return False
            first,end=struct.unpack('<QQ',memory(frame,reg(frame,'x2'),16))
            if end-first!=4096:raise RuntimeError('unverified BWE2 spectrum vector')
            lpcs=struct.unpack('<Q',memory(frame,reg(frame,'x0')+0x48,8))[0]
            lpc=struct.unpack('<Q',memory(frame,lpcs+8*channel,8))[0]
            event=dict(sequence=CURRENT['sequence'],packet_sha256=CURRENT['packet_sha256'],channel_index=channel,
                       ics=ics_info(frame,reg(frame,'x1')),bit_offset=own[0]['end_bit_offset'],
                       before=list(struct.unpack('<1024f',memory(frame,first,4096))))
            return_breakpoint(frame,dict(kind=kind,event=event,buffer=first,lpc=lpc))
        elif kind == 'cac' and FRAME_READER is not None:
            position = reader(frame, FRAME_READER)
            if position is None:
                return False
            own_streams = [s for s in SPECTRA if s['sequence'] == CURRENT['sequence']]
            if len(own_streams) != 2:
                raise RuntimeError('CAC entered without two current-frame spectra')
            info = ics_info(frame, reg(frame, 'x1'))
            instance = reg(frame, 'x0')
            runs = []
            if info['max_sfb']:
                arrays = []
                for offset in (0x20, 0x38):
                    first, end = struct.unpack('<QQ', memory(frame, instance+offset, 16))
                    if end-first != 120:
                        raise RuntimeError('unverified CAC run-vector layout')
                    arrays.append(memory(frame, first, 120))
                for value, repeat in zip(*arrays):
                    runs.append(dict(gain_index=value, repeat_code=repeat))
                    if repeat == 43:
                        break
                else:
                    raise RuntimeError('native CAC runs have no terminal code')
            buffers = [reg(frame, 'x2'), reg(frame, 'x3')]
            event = dict(sequence=CURRENT['sequence'], packet_sha256=CURRENT['packet_sha256'],
                         ics=info, runs=runs, start_bit_offset=own_streams[-1]['end_bit_offset'],
                         end_bit_offset=position,
                         before=[list(struct.unpack('<1024f', memory(frame, p, 4096))) for p in buffers])
            target = frame.GetThread().GetProcess().GetTarget()
            bp = target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
            bp.SetThreadID(frame.GetThread().GetThreadID())
            RETURNS[bp.GetID()] = dict(kind='cac', reader=FRAME_READER, event=event, buffers=buffers)
            bp.SetScriptCallbackFunction(__name__ + '.on_breakpoint')
        elif kind == "ics" and PENDING and reg(frame, "x1") == PENDING["reader"]:
            PENDING["ics"].append(reg(frame, "x0"))
        elif kind == "sq_payload":
            pointer = reg(frame, "x1")
            position = reader(frame, pointer)
            if TRACE_SPECTRA and position is not None:
                target = frame.GetThread().GetProcess().GetTarget()
                address = frame.GetThread().GetFrameAtIndex(1).GetPC()
                bp = target.BreakpointCreateByAddress(address)
                bp.SetThreadID(frame.GetThread().GetThreadID())
                RETURNS[bp.GetID()] = dict(reader=pointer, stream=reg(frame, "x0"), start=position, channel_index=CHANNEL_COUNT)
                CHANNEL_COUNT += 1
                bp.SetScriptCallbackFunction(__name__ + ".on_breakpoint")
            if PENDING:
                capture(frame, pointer, "sq_left_channel_stream")
        elif kind == "cpe_reset" and CORE_READER is not None and PENDING is None:
            capture(frame, CORE_READER, "cpe_absent")
        return False
    except Exception as error:
        ERRORS.append(str(error))
        return True


def __lldb_init_module(debugger, _dict):
    global TRACE_SPECTRA, TRACE_WINDOWS, TRACE_CAC, TRACE_TNS, TRACE_BWE2
    TRACE_WINDOWS = os.environ.get("APAC_WINDOW_TRACE") == "1"
    TRACE_SPECTRA = os.environ.get("APAC_SPECTRUM_TRACE") == "1"
    TRACE_CAC = os.environ.get('APAC_CAC_TRACE') == '1'
    TRACE_TNS = os.environ.get('APAC_TNS_TRACE') == '1'
    TRACE_BWE2 = os.environ.get('APAC_BWE2_TRACE') == '1'
    target = debugger.GetSelectedTarget()
    if not target.GetTriple().startswith("arm64"):
        raise RuntimeError("native boundary oracle is verified only for arm64 macOS")
    component = Path("/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs")
    digest = hashlib.sha256()
    with component.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != COMPONENT_SHA256:
        raise RuntimeError("reverify native oracle memory layouts for this AudioCodecs component")
    points = {
        "packet": r"^APACASPDecoder::DecodeFrame\(",
        "core": r"^APACCore(LBR)?Decoder::Deserialize\(TBitstreamReader",
        "sq": r"^APACChannelPairElement::DeserializeSQFrame\(",
        "ics": r"^APACICSInfo::Deserialize\(",
        "sq_payload": r"^APACIndividualChannelStream::Deserialize\(",
        "cpe_reset": r"^APACChannelPairElement::Reset\(",
    }
    if TRACE_WINDOWS:
        points["window"] = r"^APACSynthesisFilterBank::FrequencyToTimeInPlace\("
    if TRACE_CAC:
        points['cac'] = r'^APACCACDecoder::ProcessCac\('
    if TRACE_TNS:
        points['tns_read'] = r'^APACTNSData::ParseTNSData\('
        points['tns_apply'] = r'^APACTNSData::Apply\('
    if TRACE_BWE2:
        points['bwe2_read']=r'^APACBWE2Decoder::Deserialize\('
        points['bwe2_apply']=r'^APACBWE2Decoder::DecodeFrame\('
    for kind, pattern in points.items():
        bp = target.BreakpointCreateByRegex(pattern)
        KINDS[bp.GetID()] = kind
        bp.SetScriptCallbackFunction(__name__ + ".on_breakpoint")


def finish(debugger):
    import lldb
    process = debugger.GetSelectedTarget().GetProcess()
    exit_code = process.GetExitStatus() if process.GetState() == lldb.eStateExited else None
    report = {"schema_version": 1, "component_sha256": COMPONENT_SHA256, "architecture": "arm64",
              "method": "LLDB read-only bit-reader and ICS snapshots at native function entries",
              "process_exit_code": exit_code,
              "packet_calls": SEQUENCE + 1, "events": EVENTS, "errors": ERRORS}
    if TRACE_SPECTRA:
        report["method"] = "LLDB read-only prefix entries and channel-return bit-reader / Float32 spectrum snapshots"
        if RETURNS:
            report["errors"].append("unreturned native channel streams")
        report["spectra"] = SPECTRA
    if TRACE_WINDOWS:
        report["method"] += "; read-only sine-window Float32 snapshots"
        report["windows"] = WINDOWS
    if TRACE_CAC:
        report['method'] += '; read-only CAC runs and pre/post spectra before TNS'
        report['cac'] = CACS
    if TRACE_TNS:
        report['method'] += '; read-only TNS parse/apply returns and BWE2 entry'
        report.update(tns=TNS, tns_apply=TNS_APPLY, bwe_entries=BWE_ENTRIES)
    if TRACE_BWE2:
        report['method'] += '; read-only BWE2 parameters, LPC and pre/post spectra'
        report.update(bwe2_reads=BWE2_READS,bwe2_apply=BWE2_APPLY)
    with Path(os.environ["APAC_FRAME_TRACE_OUTPUT"]).open("x") as output:
        json.dump(report, output, indent=2)
