#!/usr/bin/env python3
"""Bounded synthetic APAC encoding through documented ExtAudioFile interfaces."""
import argparse
import ctypes as C
import json
from pathlib import Path

import bwe2_blackbox
import numpy as np
from bwe2_blackbox_wire import canonical,digest
from bwe2_blackbox_capture import atomic
from bwe2_encoder_settings import ASBD,fourcc


class Layout(C.Structure):
    _fields_=[('tag',C.c_uint32),('bitmap',C.c_uint32),('count',C.c_uint32)]


class Buffer(C.Structure):
    _fields_=[('channels',C.c_uint32),('bytes',C.c_uint32),('data',C.c_void_p)]


class BufferList(C.Structure):
    _fields_=[('count',C.c_uint32),('buffer',Buffer)]


def synthesize(spec):
    rate=spec.get('rate',48000);seconds=spec.get('seconds',2.);channels=spec.get('channels',2)
    if rate not in (24000,32000,44100,48000,96000) or not .25<=seconds<=2. or channels!=2:
        raise ValueError('encoder pilot is bounded to two channels and at most two seconds')
    frames=round(rate*seconds);t=np.arange(frames)/rate;rng=np.random.default_rng(spec.get('seed',20261007))
    signal=spec.get('signal','vowel')
    if signal=='vowel':
        fundamental=130.;harmonics=np.arange(1,int(rate/2/fundamental)+1);frequencies=fundamental*harmonics
        weights=1/harmonics.astype(float)
        for formant,width in ((700,110),(1200,140),(2600,220)):
            weights*=1/np.sqrt(1+((frequencies-formant)/width)**2)
        value=sum(w*np.sin(2*np.pi*f*t+.02*np.sin(2*np.pi*5*t)) for f,w in zip(frequencies,weights))
        value*=.55+.45*np.sin(2*np.pi*2.2*t)**2
    elif signal in ('formant-noise','bwe-shaped-noise','noise'):
        value=rng.standard_normal(frames)
        frequencies=np.fft.rfftfreq(frames,1/rate);shape=np.ones(len(frequencies))
        if signal=='formant-noise':
            shape=.02+sum(1/(1+((frequencies-f)/width)**2) for f,width in ((700,110),(1500,170),(2900,300),(6500,700),(10500,1100)))
        elif signal=='bwe-shaped-noise':
            # Independently chosen stable envelope on the extension region.
            from bwe2_blackbox_math import lsf_to_lpc
            lsf=np.arange(1,17)*12000/17+np.sin(np.arange(16))*150
            a=lsf_to_lpc(lsf);phase=np.pi*np.clip((frequencies-6000)/12000,0,1)
            envelope=abs(np.exp(-1j*phase[:,None]*np.arange(17)[None,:])@a)
            shape=np.where(frequencies<6000,1.,np.where(frequencies<18000,1/envelope,.01))
        value=np.fft.irfft(np.fft.rfft(value)*shape,n=frames)
    else:raise ValueError('unknown bounded synthetic source')
    value=.7*value/max(np.max(abs(value)),1e-100)
    # Repeated channels are intentional: low-rate CPE controls have no hidden
    # channel-layout or independently generated audio differences.
    return np.ascontiguousarray(np.repeat(value[:,None],2,axis=1),dtype='<f4')


def encode(spec,out):
    out=Path(out);out.mkdir()
    samples=synthesize(spec);rate=spec.get('rate',48000);atomic(out/'source.f32le',samples.tobytes())
    audio=C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox')
    cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFURLCreateFromFileSystemRepresentation.argtypes=[C.c_void_p,C.c_char_p,C.c_long,C.c_ubyte];cf.CFURLCreateFromFileSystemRepresentation.restype=C.c_void_p
    cf.CFRelease.argtypes=[C.c_void_p];cf.CFRelease.restype=None
    audio.ExtAudioFileCreateWithURL.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(ASBD),C.POINTER(Layout),C.c_uint32,C.POINTER(C.c_void_p)];audio.ExtAudioFileCreateWithURL.restype=C.c_int32
    audio.ExtAudioFileSetProperty.argtypes=[C.c_void_p,C.c_uint32,C.c_uint32,C.c_void_p];audio.ExtAudioFileSetProperty.restype=C.c_int32
    audio.ExtAudioFileGetProperty.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p];audio.ExtAudioFileGetProperty.restype=C.c_int32
    audio.ExtAudioFileWrite.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(BufferList)];audio.ExtAudioFileWrite.restype=C.c_int32
    audio.ExtAudioFileDispose.argtypes=[C.c_void_p];audio.ExtAudioFileDispose.restype=C.c_int32
    audio.AudioConverterSetProperty.argtypes=audio.ExtAudioFileSetProperty.argtypes;audio.AudioConverterSetProperty.restype=C.c_int32
    audio.AudioConverterGetProperty.argtypes=audio.ExtAudioFileGetProperty.argtypes;audio.AudioConverterGetProperty.restype=C.c_int32
    filename=str((out/'encoded.caf').resolve()).encode();url=cf.CFURLCreateFromFileSystemRepresentation(None,filename,len(filename),False)
    file=C.c_void_p();result=dict(spec=spec,source_sha256=digest(samples.tobytes()),source_frames=len(samples),operations=[],properties={},complete=False)
    def require(name,status):
        result['operations'].append(dict(operation=name,status=status))
        if status:raise RuntimeError(name+' returned '+str(status))
    try:
        fmt=ASBD(rate,fourcc('apac'),0,0,1024,0,2,0,0);layout=Layout((101<<16)|2,0,0)
        require('ExtAudioFileCreateWithURL',audio.ExtAudioFileCreateWithURL(url,fourcc('caff'),C.byref(fmt),C.byref(layout),1,C.byref(file)))
        pcm=ASBD(rate,fourcc('lpcm'),9,8,1,8,2,32,0)
        require('client-format',audio.ExtAudioFileSetProperty(file,fourcc('cfmt'),C.sizeof(pcm),C.byref(pcm)))
        converter=C.c_void_p();size=C.c_uint32(C.sizeof(converter))
        require('public-converter',audio.ExtAudioFileGetProperty(file,fourcc('acnv'),C.byref(size),C.byref(converter)))
        requested={'cdrc':0}
        for option,prop in (('bitrate_mode','acbf'),('bitrate','brat'),('quality','cdqu'),('vbr_quality','vbrq'),('content_source','csrc')):
            if option in spec:requested[prop]=spec[option]
        for prop,value in requested.items():
            integer=C.c_int32(value);status=audio.AudioConverterSetProperty(converter,fourcc(prop),4,C.byref(integer))
            actual=C.c_int32();size=C.c_uint32(4);read=audio.AudioConverterGetProperty(converter,fourcc(prop),C.byref(size),C.byref(actual))
            result['properties'][prop]=dict(requested=value,set_status=status,get_status=read,actual=actual.value if not read else None)
        config=C.c_void_p()
        require('synchronize-converter',audio.ExtAudioFileSetProperty(file,fourcc('accf'),C.sizeof(config),C.byref(config)))
        for start in range(0,len(samples),4096):
            block=samples[start:start+4096];buffers=BufferList(1,Buffer(2,block.nbytes,block.ctypes.data))
            require('ExtAudioFileWrite',audio.ExtAudioFileWrite(file,len(block),C.byref(buffers)))
        for prop in requested:
            actual=C.c_int32();size=C.c_uint32(4)
            status=audio.AudioConverterGetProperty(converter,fourcc(prop),C.byref(size),C.byref(actual))
            result['properties'][prop].update(final_get_status=status,final_actual=actual.value if not status else None)
        status=audio.ExtAudioFileDispose(file);file=C.c_void_p();require('ExtAudioFileDispose',status)
        result.update(complete=True,encoded_sha256=digest((out/'encoded.caf').read_bytes()),encoded_bytes=(out/'encoded.caf').stat().st_size)
    except Exception as error:result['error']=repr(error)
    finally:
        if file.value:result['cleanup_status']=audio.ExtAudioFileDispose(file)
        if url:cf.CFRelease(url)
        result['producer_sha256']=digest(Path(__file__).read_bytes());atomic(out/'result.json',canonical(result))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spec',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--tool-sha',required=True)
    args=p.parse_args()
    if args.tool_sha!=digest(Path(__file__).read_bytes()):raise RuntimeError('encoder producer changed')
    result=encode(json.loads(args.spec.read_bytes()),args.out);print(json.dumps(result),flush=True)
    raise SystemExit(0 if result['complete'] else 1)
