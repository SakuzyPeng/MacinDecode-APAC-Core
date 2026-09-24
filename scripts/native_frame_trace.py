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


def trace_bundle(binary, bundle, root, spectra=False, windows=False):
    output = root / "native-boundaries.json"
    if output.exists():
        raise RuntimeError("refusing to overwrite native boundary report")
    manifest = json.loads((bundle / "manifest.json").read_text())
    frames = manifest["file"]["packet_table"]["value"]["valid_frames"]
    env = dict(os.environ, APAC_FRAME_TRACE_OUTPUT=str(output))
    if spectra:
        env["APAC_SPECTRUM_TRACE"] = "1"
    else:
        env.pop("APAC_SPECTRUM_TRACE", None)
    if windows:
        env["APAC_WINDOW_TRACE"] = "1"
    else:
        env.pop("APAC_WINDOW_TRACE", None)
    module = Path(__file__).resolve()
    replay = ["replay", str(bundle), "--out", str(root / "native-trace-pcm"), "--frames", str(frames)]
    process = subprocess.run(["xcrun", "lldb", "--batch", "-o", "command script import " + shlex.quote(str(module)),
                              "-o", "run " + shlex.join(replay), "-o", "script native_frame_trace.finish(lldb.debugger)",
                              "--", str(binary)], env=env, capture_output=True, text=True, timeout=120)
    if process.returncode or not output.exists():
        raise RuntimeError("native boundary trace failed: " + (process.stdout + process.stderr)[-2000:])
    result = json.loads(output.read_text())
    if result["errors"] or result["process_exit_code"] != 0:
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


def on_breakpoint(frame, location, _dict):
    global CURRENT, PENDING, CORE_READER, SEQUENCE, CHANNEL_COUNT
    try:
        bp_id = location.GetBreakpoint().GetID()
        if bp_id in RETURNS:
            saved = RETURNS.pop(bp_id)
            frame.GetThread().GetProcess().GetTarget().BreakpointDelete(bp_id)
            if reg(frame, "w0") != 0:
                raise RuntimeError("native channel stream rejected input")
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
            PENDING = CORE_READER = None
        elif kind == "core":
            pointer = reg(frame, "x1")
            if reader(frame, pointer) is not None:
                CORE_READER = pointer
        elif kind == "sq":
            pointer = reg(frame, "x1")
            if reader(frame, pointer) is not None:
                PENDING = {"reader": pointer, "ics": []}
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
    global TRACE_SPECTRA, TRACE_WINDOWS
    TRACE_WINDOWS = os.environ.get("APAC_WINDOW_TRACE") == "1"
    TRACE_SPECTRA = os.environ.get("APAC_SPECTRUM_TRACE") == "1"
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
    with Path(os.environ["APAC_FRAME_TRACE_OUTPUT"]).open("x") as output:
        json.dump(report, output, indent=2)
