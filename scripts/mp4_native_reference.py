"""Read-only public AudioFile packet evidence and independent BMFF table walker.

Only native acceptance imports/opens Apple's public frameworks. No production
code, codec implementation, or artificial truth uses these functions.
"""
import ctypes as C
import hashlib,os,struct
from pathlib import Path
from validate import require

def wire(path):
    """Small reference tables for native test inputs; never candidate reports."""
    f=Path(path).open('rb');nodes={};length=Path(path).stat().st_size
    def walk(lo,hi):
        while lo<hi:
            f.seek(lo);header=f.read(8);require(len(header)==8,'short reference box');size,tag=struct.unpack('>I4s',header);head=8
            if size==1:size=struct.unpack('>Q',f.read(8))[0];head=16
            if size==0:size=hi-lo
            require(head<=size<=hi-lo,'reference box bounds')
            if tag in (b'moov',b'trak',b'edts',b'mdia',b'minf',b'stbl',b'dinf'):walk(lo+head,lo+size)
            elif tag in (b'mvhd',b'mdhd',b'elst',b'stsd',b'stco',b'co64',b'stss',b'stsc',b'stsz',b'stts'):
                require(tag not in nodes,'duplicate reference box');nodes[tag]=(lo+head,size-head)
            lo+=size
    try:
        walk(0,length)
        def data(tag):
            off,n=nodes[tag];require(n<=8*1024*1024,'reference table too large');f.seek(off);return f.read(n)
        def timing(b):
            at=12 if b[0]==0 else 20;return struct.unpack_from('>I',b,at)[0],struct.unpack_from('>I' if b[0]==0 else '>Q',b,at+4)[0]
        movie_rate,movie_duration=timing(data(b'mvhd'));rate,total=timing(data(b'mdhd'))
        b=data(b'elst');require(struct.unpack_from('>I',b,4)[0]==1,'reference needs single edit');duration,prime=struct.unpack_from('>Ii' if b[0]==0 else '>Qq',b,8)
        require(duration*rate%movie_rate==0,'reference fractional edit');valid=duration*rate//movie_rate
        b=data(b'stsd');require(struct.unpack_from('>I',b,4)[0]==1 and b[12:16]==b'apac','reference sample entry');config=None;at=44
        while at<len(b):
            n,tag=struct.unpack_from('>I4s',b,at);require(n>=8 and at+n<=len(b),'reference extension bounds')
            if tag==b'dapa':require(config is None,'duplicate reference cookie');config=b[at:at+n]
            at+=n
        require(config is not None,'reference cookie missing')
        b=data(b'stsz');fixed,count=struct.unpack_from('>II',b,4);require(count<=2_000_000,'reference count bound')
        sizes=[fixed]*count if fixed else list(struct.unpack('>'+str(count)+'I',b[12:]));require(total==count*1024,'reference total duration')
        tag=b'co64' if b'co64' in nodes else b'stco';b=data(tag);offsets=[r[0] for r in struct.iter_unpack('>Q' if tag==b'co64' else '>I',b[8:])]
        runs=list(struct.iter_unpack('>III',data(b'stsc')[8:]));records=[];k=0;run=0
        for index,offset in enumerate(offsets,1):
            while run+1<len(runs) and runs[run+1][0]<=index:run+=1
            require(runs[run][2]==1,'reference sample description')
            for _ in range(runs[run][1]):
                require(k<count,'reference extra samples');records.append((offset,sizes[k]));offset+=sizes[k];k+=1
        require(k==count,'reference missing samples')
        require(sum(n for n,d in struct.iter_unpack('>II',data(b'stts')[8:]))==count,'reference time count')
        require(all(d==1024 for n,d in struct.iter_unpack('>II',data(b'stts')[8:])),'reference packet duration')
        return dict(rate=rate,count=count,cookie=config,records=records,table=dict(valid_frames=valid,priming_frames=prime,remainder_frames=total-prime-valid))
    finally:f.close()

class Description(C.Structure):_fields_=[('offset',C.c_int64),('frames',C.c_uint32),('bytes',C.c_uint32)]
class Frame(C.Structure):_fields_=[('frame',C.c_int64),('packet',C.c_int64),('offset',C.c_uint32)]
class Format(C.Structure):_fields_=[('rate',C.c_double)]+[(x,C.c_uint32) for x in ('format','flags','packet_bytes','packet_frames','frame_bytes','channels','bits','reserved')]
class PacketTable(C.Structure):_fields_=[('valid',C.c_int64),('priming',C.c_int32),('remainder',C.c_int32)]

def fourcc(s):return int.from_bytes(s.encode(),'big')
class AudioFile:
    def __init__(self,path):
        self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation');self.af=C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox');self.handle=C.c_void_p()
        self.cf.CFURLCreateFromFileSystemRepresentation.argtypes=[C.c_void_p,C.c_char_p,C.c_long,C.c_uint8];self.cf.CFURLCreateFromFileSystemRepresentation.restype=C.c_void_p
        self.cf.CFRelease.argtypes=[C.c_void_p]
        self.af.AudioFileOpenURL.argtypes=[C.c_void_p,C.c_int8,C.c_uint32,C.POINTER(C.c_void_p)];self.af.AudioFileOpenURL.restype=C.c_int32
        self.af.AudioFileClose.argtypes=[C.c_void_p];self.af.AudioFileClose.restype=C.c_int32
        self.af.AudioFileGetPropertyInfo.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p];self.af.AudioFileGetPropertyInfo.restype=C.c_int32
        self.af.AudioFileGetProperty.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p];self.af.AudioFileGetProperty.restype=C.c_int32
        self.af.AudioFileReadPacketData.argtypes=[C.c_void_p,C.c_uint8,C.POINTER(C.c_uint32),C.POINTER(Description),C.c_int64,C.POINTER(C.c_uint32),C.c_void_p];self.af.AudioFileReadPacketData.restype=C.c_int32
        raw=os.fsencode(Path(path).resolve());url=self.cf.CFURLCreateFromFileSystemRepresentation(None,raw,len(raw),0)
        require(bool(url),'CFURL creation failed')
        try:require(self.af.AudioFileOpenURL(url,1,0,C.byref(self.handle))==0,'AudioFileOpenURL failed')
        finally:self.cf.CFRelease(url)
    def close(self):
        if self.handle:require(self.af.AudioFileClose(self.handle)==0,'AudioFileClose failed');self.handle=C.c_void_p()
    def get(self,tag,value):
        size=C.c_uint32(C.sizeof(value));require(self.af.AudioFileGetProperty(self.handle,fourcc(tag),C.byref(size),C.byref(value))==0,'native property failed: '+tag)
        require(size.value==C.sizeof(value),'native property size: '+tag);return value
    def bytes(self,tag,limit=8*1024*1024):
        size=C.c_uint32();require(self.af.AudioFileGetPropertyInfo(self.handle,fourcc(tag),C.byref(size),None)==0,'native property info: '+tag);require(0<size.value<=limit,'native property bounds')
        raw=C.create_string_buffer(size.value);require(self.af.AudioFileGetProperty(self.handle,fourcc(tag),C.byref(size),raw)==0,'native property data: '+tag);return raw.raw
    def packets(self,count,maximum):
        require(0<maximum<=16*1024*1024,'native maximum packet bound');batch=min(64,max(1,(16*1024*1024)//maximum));capacity=batch*maximum
        raw=C.create_string_buffer(capacity);descriptions=(Description*batch)();start=0
        while start<count:
            n=C.c_uint32(min(batch,count-start));size=C.c_uint32(capacity)
            status=self.af.AudioFileReadPacketData(self.handle,0,C.byref(size),descriptions,start,C.byref(n),raw)
            require(status in (0,-39) and 0<n.value<=batch and size.value<=capacity,'native packet read failed')
            for j in range(n.value):
                d=descriptions[j];require(0<=d.offset and 0<d.bytes<=maximum and d.offset+d.bytes<=size.value,'native packet bounds')
                frame=self.get('pkfr',Frame(0,start+j,0));require(frame.frame==(start+j)*1024,'native packet frame mapping differs')
                require(d.frames in (0,1024),'native variable frame count differs')
                yield start+j,C.string_at(C.addressof(raw)+d.offset,d.bytes)
            start+=n.value
        n=C.c_uint32(1);size=C.c_uint32(capacity);status=self.af.AudioFileReadPacketData(self.handle,0,C.byref(size),descriptions,count,C.byref(n),raw)
        require(status in (0,-39) and n.value==0,'native extra packets')

def verify(path):
    reference=wire(path);af=AudioFile(path)
    try:
        count=af.get('pcnt',C.c_uint64()).value;fmt=af.get('dfmt',Format());table=af.get('pnfo',PacketTable());config=af.bytes('mgic');layout=af.bytes('cmap');tag,bitmap,descriptions=struct.unpack_from('=III',layout)
        require(count==reference['count'] and config==reference['cookie'],'native cookie/count differs')
        require(fmt.format==fourcc('apac') and fmt.rate==reference['rate'] and fmt.packet_frames==1024,'native format differs')
        require(dict(valid_frames=table.valid,priming_frames=table.priming,remainder_frames=table.remainder)==reference['table'],'native edit/priming/remainder differs')
        require(fmt.channels in (1,2,6,8) and tag==({1:100,2:101,6:121,8:128}[fmt.channels]<<16)|fmt.channels and bitmap==descriptions==0,'native channel map differs')
        audio=hashlib.sha256();boundaries=hashlib.sha256();seen=0
        with Path(path).open('rb') as f:
            for i,raw in af.packets(count,af.get('psze',C.c_uint32()).value):
                offset,size=reference['records'][i];f.seek(offset);require(f.read(size)==raw,'native packet bytes differ from independent table')
                audio.update(raw);boundaries.update(struct.pack('<QQQQ',i,offset,size,1024));boundaries.update(hashlib.sha256(raw).digest());seen+=1
        require(seen==count,'native missing packets')
        return dict(passed=True,packets=count,channels=fmt.channels,rate=fmt.rate,layout_tag=tag,cookie_sha256=hashlib.sha256(config).hexdigest(),table=reference['table'],audio_sha256=audio.hexdigest(),packets_sha256=boundaries.hexdigest())
    finally:af.close()
