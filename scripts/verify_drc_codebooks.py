#!/usr/bin/env python3
"""Hash-gated read-only DRC encoder/decoder and optional public-source check."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
from drc_vectors import GAIN_CODES
from native_frame_trace import COMPONENT_SHA256


def extract(path):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=COMPONENT_SHA256:raise ValueError('unverified AudioCodecs component')
    magic,count=struct.unpack_from('>II',raw)
    if magic!=0xcafebabe:raise ValueError('unexpected Mach-O')
    for i in range(count):
        cpu,_,offset,size,_=struct.unpack_from('>IIIII',raw,8+20*i)
        if cpu==0x100000c:break
    else:raise ValueError('verified arm64e slice absent')
    data=memoryview(raw)[offset:offset+size];position=32;segments=[]
    for _ in range(struct.unpack_from('<I',data,16)[0]):
        command,length=struct.unpack_from('<II',data,position)
        if command==0x19:
            _,va,_,fo,fs=struct.unpack_from('<16sQQQQ',data,position+8);segments.append((va,fo,fs))
        position+=length
    def read(address,count):
        for va,fo,fs in segments:
            if va<=address and address+count<=va+fs:return bytes(data[fo+address-va:fo+address-va+count])
        raise ValueError('table outside verified file segment')
    def unpack(word):return dict(value_eighth_db=(word&255)-16,bits=(word>>8)&255,code=word>>16)
    encoder=[unpack(v) for v in struct.unpack('<25I',read(0x6527b4,100))]
    decoder=[]
    for address,count in [(0x6529a0,2),(0x6529a8,13),(0x6529dc,1),(0x6529e0,8),(0x652a00,1)]:
        decoder.extend(unpack(v) for v in struct.unpack('<'+str(count)+'I',read(address,4*count)))
    own=[dict(value_eighth_db=v,bits=len(c),code=int(c,2)) for c,v in GAIN_CODES]
    key=lambda row:row['value_eighth_db']
    if sorted(encoder,key=key)!=sorted(decoder,key=key) or sorted(encoder,key=key)!=sorted(own,key=key):
        raise AssertionError('gain codeword disagreement between encoder, decoder and portable writer')
    return sorted(own,key=key)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--component',type=Path,default=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs'))
    p.add_argument('--public-rom',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();entries=extract(args.component)
    public=None
    if args.public_rom:
        raw=args.public_rom.read_bytes();public=hashlib.sha256(raw).hexdigest()
        if public!='b722fe150284274c415f8b13c55207b514ad34852a398e34c231a6bdf47378e5':raise ValueError('unverified public source revision')
        table=raw.decode().split('ia_drc_gain_tbls_prof_0_1[',1)[1].split('};',1)[0]
        rows=[dict(bits=int(n),code=int(c,16),value_eighth_db=int(float(v)*8)) for n,c,v in re.findall(r'\{(\d+),\s*(0x[0-9A-Fa-f]+),\s*([-\d.]+)f\}',table)]
        if sorted(rows,key=lambda r:r['value_eighth_db'])!=entries:raise AssertionError('public UniDRC gain table differs')
    result=dict(component_sha256=COMPONENT_SHA256,architecture='arm64e',gain_entries=entries,
                public_revision='libxaac 6c771f2ccdef83ebc6dbf7694b1a0ce9c0585d46',public_rom_sha256=public,passed=True)
    with args.output.open('x',encoding='utf-8') as out:json.dump(result,out,indent=2);out.write('\n')
    print(json.dumps(dict(passed=True,gain_entries=len(entries),sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))

if __name__=='__main__':main()
