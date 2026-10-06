#!/usr/bin/env python3
"""Hash-gated read-only BWE2 wire dictionaries; never used by a Rust build.

The encoder quantizer and decoder share the two LSF stage dictionaries and the
64-entry excitation-gain dictionary. Only IEEE values and dimensions are kept,
not native pointers, object layouts, or executable code.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

COMPONENT_SHA256 = '826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd'
DESTINATION = Path(__file__).resolve().parents[1]/'data/bwe2-format-v1.json'


def extract(path):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=COMPONENT_SHA256:
        raise ValueError('unverified AudioCodecs component; reverify BWE2 format constants')
    magic,count=struct.unpack_from('>II',raw)
    if magic!=0xcafebabe:raise ValueError('unexpected verified Mach-O container')
    for i in range(count):
        cpu,_,offset,size,_=struct.unpack_from('>IIIII',raw,8+20*i)
        if cpu==0x1000007:break
    else:raise ValueError('verified x86_64 slice absent')
    data=memoryview(raw)[offset:offset+size]
    position,segments=32,[]
    for _ in range(struct.unpack_from('<I',data,16)[0]):
        command,length=struct.unpack_from('<II',data,position)
        if command==0x19:
            _,va,_,file_offset,file_size=struct.unpack_from('<16sQQQQ',data,position+8)
            segments.append((va,file_offset,file_size))
        position+=length
    def read(address,size):
        for va,file_offset,file_size in segments:
            if va<=address and address+size<=va+file_size:
                return bytes(data[file_offset+address-va:file_offset+address-va+size])
        raise ValueError('BWE2 data outside verified file segments')
    books=[]
    for stage,address in enumerate((0x8a29a4,0x8aa9a4)):
        descriptor=read(0xaad6e0+16*stage,16)
        entries,dimension=struct.unpack_from('<HH',descriptor)
        pointer=struct.unpack_from('<Q',descriptor,8)[0]&0xffffffffff
        if (entries,dimension,pointer)!=(512,16,address):raise ValueError('unverified LSF stage descriptor')
        values=list(struct.unpack('<8192I',read(address,32768)))
        books.append([values[i*16:(i+1)*16] for i in range(512)])
    values=dict(lsf_codebooks_f32=books,excitation_gains_f32=list(struct.unpack('<64I',read(0xa2f430,256))))
    digest=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,format_profile='apac-bwe2-format-v1',
                source=dict(component='AudioCodecs 7.0',system='macOS 27.0 / 26A428',component_sha256=COMPONENT_SHA256,
                            architecture='x86_64',method='shared encoder/decoder LSF and excitation-gain wire dictionaries'),
                tables_sha256=digest,**values)


def check_wire_values(stored, observed):
    """Compare wire values without relabelling measured gain provenance."""
    keys=('schema_version','format_profile','tables_sha256','lsf_codebooks_f32','excitation_gains_f32')
    if any(stored.get(key)!=observed.get(key) for key in keys):
        raise AssertionError('BWE2 wire dictionaries differ')
    if 'gain_replacement' in stored.get('source',{}):
        from generate_bwe2_gains_measured import load_measurement,regenerate
        if regenerate(stored,load_measurement())!=stored:
            raise AssertionError('BWE2 measured gain provenance differs')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',type=Path,default=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs'))
    parser.add_argument('--table',type=Path,default=DESTINATION)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args();result=extract(args.component)
    data=(json.dumps(result,indent=2)+'\n').encode()
    if args.write:
        with args.table.open('xb') as output:output.write(data)
    else:check_wire_values(json.loads(args.table.read_bytes()),result)
    print(json.dumps(dict(tables_sha256=result['tables_sha256'],lsf_stages=2,lsf_entries=512,lsf_dimension=16,excitation_gains=64)))


if __name__=='__main__':main()
