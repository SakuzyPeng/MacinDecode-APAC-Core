#!/usr/bin/env python3
"""Query documented public AudioConverter settings; no codec memory inspection."""
import argparse
import ctypes as C
import json
from pathlib import Path
import plistlib


class ASBD(C.Structure):
    _fields_=[('rate',C.c_double)]+[(name,C.c_uint32) for name in
        ('format','flags','bytes_per_packet','frames_per_packet','bytes_per_frame','channels','bits','reserved')]


class ComponentDescription(C.Structure):
    _fields_=[(name,C.c_uint32) for name in ('type','subtype','manufacturer','flags','mask')]


def fourcc(text):return int.from_bytes(text.encode('ascii'),'big')


def query(codec=False):
    audio=C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox')
    cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    audio.AudioConverterNew.argtypes=[C.POINTER(ASBD),C.POINTER(ASBD),C.POINTER(C.c_void_p)]
    audio.AudioConverterNew.restype=C.c_int32
    audio.AudioConverterDispose.argtypes=[C.c_void_p];audio.AudioConverterDispose.restype=C.c_int32
    audio.AudioConverterGetPropertyInfo.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.POINTER(C.c_ubyte)]
    audio.AudioConverterGetPropertyInfo.restype=C.c_int32
    audio.AudioConverterGetProperty.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p]
    audio.AudioConverterGetProperty.restype=C.c_int32
    cf.CFPropertyListCreateData.argtypes=[C.c_void_p,C.c_void_p,C.c_long,C.c_ulong,C.POINTER(C.c_void_p)]
    cf.CFPropertyListCreateData.restype=C.c_void_p
    cf.CFDataGetLength.argtypes=[C.c_void_p];cf.CFDataGetLength.restype=C.c_long
    cf.CFDataGetBytePtr.argtypes=[C.c_void_p];cf.CFDataGetBytePtr.restype=C.POINTER(C.c_ubyte)
    cf.CFRelease.argtypes=[C.c_void_p];cf.CFRelease.restype=None
    input_format=ASBD(48000,fourcc('lpcm'),9,8,1,8,2,32,0)
    output_format=ASBD(48000,fourcc('apac'),0,0,1024,0,2,0,0)
    converter=C.c_void_p()
    if codec:
        audio.AudioComponentFindNext.argtypes=[C.c_void_p,C.POINTER(ComponentDescription)];audio.AudioComponentFindNext.restype=C.c_void_p
        audio.AudioComponentInstanceNew.argtypes=[C.c_void_p,C.POINTER(C.c_void_p)];audio.AudioComponentInstanceNew.restype=C.c_int32
        audio.AudioComponentInstanceDispose.argtypes=[C.c_void_p];audio.AudioComponentInstanceDispose.restype=C.c_int32
        audio.AudioCodecInitialize.argtypes=[C.c_void_p,C.POINTER(ASBD),C.POINTER(ASBD),C.c_void_p,C.c_uint32];audio.AudioCodecInitialize.restype=C.c_int32
        audio.AudioCodecUninitialize.argtypes=[C.c_void_p];audio.AudioCodecUninitialize.restype=C.c_int32
        audio.AudioCodecGetPropertyInfo.argtypes=audio.AudioConverterGetPropertyInfo.argtypes;audio.AudioCodecGetPropertyInfo.restype=C.c_int32
        audio.AudioCodecGetProperty.argtypes=audio.AudioConverterGetProperty.argtypes;audio.AudioCodecGetProperty.restype=C.c_int32
        description=ComponentDescription(fourcc('aenc'),fourcc('apac'),fourcc('appl'),0,0)
        component=audio.AudioComponentFindNext(None,C.byref(description))
        if not component:return dict(api='AudioCodec public settings',component_found=False)
        status=audio.AudioComponentInstanceNew(component,C.byref(converter))
        result=dict(create_status=status,api='AudioCodec public settings',properties={})
        if status:return result
        result['initialize_status']=audio.AudioCodecInitialize(converter,C.byref(input_format),C.byref(output_format),None,0)
        get_info,get_property=audio.AudioCodecGetPropertyInfo,audio.AudioCodecGetProperty
        names=('acs ','cdqu','brat','acbf','vbrq')
    else:
        status=audio.AudioConverterNew(C.byref(input_format),C.byref(output_format),C.byref(converter))
        result=dict(create_status=status,api='AudioConverter public settings',properties={})
        get_info,get_property=audio.AudioConverterGetPropertyInfo,audio.AudioConverterGetProperty
        names=('acps','cdqu','brat','aebr','vebr','aesr','vesr')
    if status:return result
    try:
        for name in names:
            size=C.c_uint32();writable=C.c_ubyte()
            code=get_info(converter,fourcc(name),C.byref(size),C.byref(writable))
            row=dict(info_status=code,bytes=size.value,writable=bool(writable.value))
            result['properties'][name]=row
            if code:continue
            if size.value>65536:raise RuntimeError('public property unexpectedly large')
            data=(C.c_ubyte*max(size.value,1))()
            code=get_property(converter,fourcc(name),C.byref(size),data)
            row['get_status']=code
            row['returned_bytes']=size.value
            if code:continue
            if name in ('acps','acs '):
                if size.value!=C.sizeof(C.c_void_p):raise RuntimeError('unexpected settings representation')
                pointer=C.cast(data,C.POINTER(C.c_void_p))[0]
                error=C.c_void_p()
                serialized=cf.CFPropertyListCreateData(None,pointer,100,0,C.byref(error))
                if not serialized:raise RuntimeError('public settings are not a property list')
                try:
                    count=cf.CFDataGetLength(serialized)
                    if count>1024*1024:raise RuntimeError('settings plist exceeds bound')
                    row['value']=plistlib.loads(C.string_at(cf.CFDataGetBytePtr(serialized),count))
                finally:cf.CFRelease(serialized)
            elif name in ('cdqu','brat','acbf','vbrq'):
                if size.value!=4:raise RuntimeError('unexpected integer property size')
                row['value']=C.cast(data,C.POINTER(C.c_uint32))[0]
            else:
                if size.value%16:raise RuntimeError('unexpected range property size')
                values=C.cast(data,C.POINTER(C.c_double))
                row['value']=[[values[i],values[i+1]] for i in range(0,size.value//8,2)]
    finally:
        if codec:
            if result['initialize_status']==0:result['uninitialize_status']=audio.AudioCodecUninitialize(converter)
            result['dispose_status']=audio.AudioComponentInstanceDispose(converter)
        else:result['dispose_status']=audio.AudioConverterDispose(converter)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--codec',action='store_true')
    args=parser.parse_args();value=query(args.codec)
    with args.out.open('x') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps(value))
