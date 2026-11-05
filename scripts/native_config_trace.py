"""Read-only config call tracing in an explicitly launched arm64e process.

This is a diagnostic call trace, not qualification of additional object layouts.
Only the small ContentOrigin/std::string records inspected for 26B123 are read.
"""
import json
import os
from pathlib import Path
import struct

import native_frame_trace as base
from native_component import CURRENT_SHA256


KINDS = {}
RETURNS = {}
EVENTS = []
ERRORS = []
BRANCHES = {}


def string(frame, pointer):
    raw = base.memory(frame, pointer, 24)
    if raw[23] & 128:
        address, length = struct.unpack_from('<QQ', raw)
    else:
        address, length = pointer, raw[23]
    if length > 128:
        raise ValueError('unexpected version string length')
    if length == 0:
        return ''
    return base.memory(frame, address, length).decode('utf-8')


def hit(frame, location, _dict):
    try:
        base.observe(frame, location)
        target = frame.GetThread().GetProcess().GetTarget()
        key = location.GetBreakpoint().GetID()
        if key in BRANCHES:
            EVENTS.append(dict(index=len(EVENTS), kind='hoa_branch', **BRANCHES[key],
                               registers={f'w{i}': base.reg(frame, f'w{i}') for i in range(29)}))
            return False
        if key in RETURNS:
            saved = RETURNS.pop(key)
            target.BreakpointDelete(key)
            event = saved['event']
            event['return_w0'] = base.reg(frame, 'w0')
            event['return_sequence'] = len(EVENTS)
            EVENTS.append(dict(kind='return', entry=event['index'], return_w0=event['return_w0']))
            if event['kind'] == 'version_string':
                event['value'] = string(frame, saved['x2'])
            if event['kind'] == 'origin_init':
                event['object_32_hex'] = base.memory(frame, saved['x0'], 32).hex()
            return False
        kind = KINDS[key]
        event = dict(index=len(EVENTS), kind=kind, symbol=frame.GetFunctionName(),
                     stack=[frame.GetThread().GetFrameAtIndex(i).GetFunctionName()
                            for i in range(min(8, frame.GetThread().GetNumFrames()))])
        EVENTS.append(event)
        if kind == 'hoa_init' and os.environ.get('APAC_CONFIG_BRANCH_TRACE') == '1' and not BRANCHES:
            for instruction in frame.GetSymbol().GetInstructions(target):
                mnemonic = instruction.GetMnemonic(target)
                offset = instruction.GetAddress().GetFileAddress() - frame.GetSymbol().GetStartAddress().GetFileAddress()
                # Inspected 26B123 dynamic-selection guard and its error branch.
                if offset in (6064, 6076, 6164):
                    address = instruction.GetAddress()
                    bp = target.BreakpointCreateByAddress(address.GetLoadAddress(target))
                    BRANCHES[bp.GetID()] = dict(file_address=address.GetFileAddress(),
                                                instruction=mnemonic + ' ' + instruction.GetOperands(target))
                    bp.SetScriptCallbackFunction(__name__ + '.hit')
        if kind == 'origin_serialize':
            event['object_32_hex'] = base.memory(frame, base.reg(frame, 'x0'), 32).hex()
        bp = target.BreakpointCreateByAddress(frame.GetThread().GetFrameAtIndex(1).GetPC())
        bp.SetThreadID(frame.GetThread().GetThreadID())
        RETURNS[bp.GetID()] = dict(event=event, x0=base.reg(frame, 'x0'), x2=base.reg(frame, 'x2'))
        bp.SetScriptCallbackFunction(__name__ + '.hit')
        if len(EVENTS) > 2048:
            raise ValueError('config event budget exceeded')
        return False
    except Exception as error:
        ERRORS.append(str(error))
        return True


def __lldb_init_module(debugger, _dict):
    target = debugger.GetSelectedTarget()
    base.begin_probe(target, 'hoa')
    if base.component_sha256() != CURRENT_SHA256:
        raise ValueError('this diagnostic is bound to the inspected 26B123 component')
    patterns = {
        'global_deserialize': r'^apac::GlobalConfig::Deserialize\(',
        'hoa_config_deserialize': r'^apac::hoa::CodecConfig::Deserialize\(',
        'profile_inference': r'^apac::ProfileLevelConfig::InferProfileFromCodecConfigs\(',
        'profile_level': r'^apac::ProfileLevelConfig::GetLevelFromCodecConfigs\(',
        'decoder_init': r'^APACDecoder::Initialize\(',
        'hoa_init': r'^APACHOADecoder::Initialize\(',
        'origin_init': r'^apac::ContentOrigin::Initialize\(',
        'origin_serialize': r'^apac::ContentOrigin::Serialize\(',
        'origin_deserialize': r'^apac::ContentOrigin::Deserialize\(',
        'version_string': r'^apac::CStringValueFromCFDictKey\(',
    }
    for kind, pattern in patterns.items():
        bp = base.breakpoint(target, pattern)
        KINDS[bp.GetID()] = kind
        bp.SetScriptCallbackFunction(__name__ + '.hit')


def finish(debugger):
    import lldb
    process = debugger.GetSelectedTarget().GetProcess()
    report = dict(events=EVENTS, errors=ERRORS, pending_returns=len(RETURNS),
                  qualification='diagnostic_config_calls',
                  process_exit_code=process.GetExitStatus() if process.GetState() == lldb.eStateExited else None)
    base.finish_capture(report)
    with Path(os.environ['APAC_CONFIG_TRACE_OUTPUT']).open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
