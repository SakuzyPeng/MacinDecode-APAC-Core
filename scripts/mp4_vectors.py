"""Independent restricted ISO BMFF writer. Expected offsets come from emission."""
import copy,hashlib,itertools,json,struct
from channel_vectors import LAYOUTS,WINDOWS,cookie,packet,bundle,sequences,excitation
from caf_vectors import digest,sha

PROFILE='apac-mp4-input-v1'
CONTAINERS={b'moov',b'trak',b'edts',b'mdia',b'minf',b'dinf',b'stbl'}
LEAVES={b'ftyp',b'mvhd',b'tkhd',b'elst',b'mdhd',b'hdlr',b'smhd',b'dref',b'stsd',b'stsz',b'stsc',b'stco',b'co64',b'stts',b'ctts',b'stss',b'sgpd',b'sbgp'}
def full(version=0,flags=0):return struct.pack('>I',(version<<24)|flags)
def box(tag,payload,wide=False,terminal=False):return (tag,payload,wide,terminal)
def emit(node,offset=0,records=None,metadata=None):
    tag,payload,wide,terminal=node;head=16 if wide else 8
    if isinstance(payload,list):
        body=b''
        # Child locations are known independently of sample offset values.
        for child in payload:body+=emit(child,offset+head+len(body))
    else:body=payload
    header=struct.pack('>I4sQ',1,tag,len(body)+16) if wide else struct.pack('>I4s',0 if terminal else len(body)+8,tag)
    if records is not None and tag in CONTAINERS|LEAVES:
        records[tag.decode()]=dict(offset=offset,data_offset=offset+head,bytes=len(body)+head)
    if metadata is not None:
        metadata.update(header)
        if isinstance(payload,list):
            at=offset+head
            for child in payload:
                raw=emit(child,at,records,metadata);at+=len(raw)
        elif tag in LEAVES:metadata.update(body)
    return header+body

def timebox(tag,rate,duration,version):
    n={b'mvhd':(100,112),b'mdhd':(24,36)}[tag][version];raw=bytearray(n);raw[:4]=full(version)
    at=12 if version==0 else 20;struct.pack_into('>I',raw,at,rate);struct.pack_into('>I' if version==0 else '>Q',raw,at+4,duration)
    if tag==b'mvhd':
        end=at+8 if version==0 else at+12;struct.pack_into('>IH',raw,end,65536,256)
        matrix=end+16
        for i,v in enumerate((65536,0,0,0,65536,0,0,0,1073741824)):struct.pack_into('>I',raw,matrix+4*i,v)
        struct.pack_into('>I',raw,n-4,2)
    return box(tag,bytes(raw))

def encode(config,packets,rate=48000,channels=2,priming=0,remainder=0,variant=0,**overrides):
    # Orthogonal layout features are also covered explicitly by CLI tests.
    opts=dict(moov_last=bool(variant&1),wide=bool(variant&2),co64=bool(variant&4),version=(variant>>3)&1,
              groups=1+(variant//16)%3,chunk_pattern=((1,),(2,3,1),(4,))[variant//48%3],padding=variant%5,
              terminal=bool(variant&128),ctts=bool(variant&256),fixed=False,movie_timescale=rate)
    opts.update(overrides)
    if opts['terminal']:opts['moov_last']=False;opts['wide']=False
    version=opts['version'];valid=len(packets)*1024-priming-remainder
    assert valid>=0 and valid*opts['movie_timescale']%rate==0
    duration=valid*opts['movie_timescale']//rate
    chunks=[];i=0
    while i<len(packets):
        n=min(opts['chunk_pattern'][len(chunks)%len(opts['chunk_pattern'])],len(packets)-i)
        chunks.append(packets[i:i+n]);i+=n
    group_count=min(opts['groups'],max(1,len(chunks)));groups=[[] for _ in range(group_count)]
    for i,c in enumerate(chunks):groups[min(i*group_count//max(1,len(chunks)),group_count-1)].append(c)
    ftyp=box(b'ftyp',b'mp42'+bytes(4)+b'mp42isom')
    free=box(b'free',b'ignored\0metadata')
    def movie(offsets):
        tkhd=bytearray(84 if version==0 else 96);tkhd[:4]=full(version,7);at=12 if version==0 else 20
        struct.pack_into('>I',tkhd,at,1);struct.pack_into('>I' if version==0 else '>Q',tkhd,at+8,duration)
        struct.pack_into('>H',tkhd,36 if version==0 else 48,256)
        matrix=40 if version==0 else 52
        for i,v in enumerate((65536,0,0,0,65536,0,0,0,1073741824)):struct.pack_into('>I',tkhd,matrix+4*i,v)
        elst=full(version)+struct.pack('>I',1)+struct.pack('>Ii' if version==0 else '>Qq',duration,priming)+struct.pack('>hh',1,0)
        entry=bytes(6)+struct.pack('>H',1)+bytes(8)+struct.pack('>HHHHI',2,16,0,0,opts.get('sample_entry_rate',rate if rate<=65535 else 0)<<16)+config
        stsd=full()+struct.pack('>I',1)+emit(box(b'apac',entry))
        runs=[]
        for i,c in enumerate(chunks,1):
            if not runs or runs[-1][1]!=len(c):runs.append((i,len(c),1))
        if opts['fixed']:
            assert packets and len(set(map(len,packets)))==1
            stsz=full()+struct.pack('>II',len(packets[0]),len(packets))
        else:stsz=full()+struct.pack('>II',0,len(packets))+b''.join(struct.pack('>I',len(p)) for p in packets)
        time_runs=[(len(c),1024) for c in chunks] if variant%2 else ([(len(packets),1024)] if packets else [])
        tables=[box(b'stsd',stsd),box(b'stsz',stsz),box(b'stsc',full()+struct.pack('>I',len(runs))+b''.join(struct.pack('>III',*r) for r in runs)),
                box(b'co64' if opts['co64'] else b'stco',full()+struct.pack('>I',len(offsets))+b''.join(struct.pack('>Q' if opts['co64'] else '>I',p) for p in offsets)),
                box(b'stts',full()+struct.pack('>I',len(time_runs))+b''.join(struct.pack('>II',*r) for r in time_runs)),
                box(b'stss',full()+struct.pack('>I',1 if packets else 0)+(struct.pack('>I',1) if packets else b'')),
                box(b'sgpd',full(1)+b'prol'+struct.pack('>IIH',2,1,1)),
                box(b'sbgp',full()+b'prol'+struct.pack('>I',1 if packets else 0)+(struct.pack('>II',len(packets),1) if packets else b''))]
        if opts['ctts']:tables.append(box(b'ctts',full(version)+struct.pack('>I',1 if packets else 0)+(struct.pack('>II',len(packets),0) if packets else b'')))
        dinf=box(b'dinf',[box(b'dref',full()+struct.pack('>I',1)+emit(box(b'url ',full(0,1))))])
        minf=box(b'minf',[box(b'smhd',bytes(8)),dinf,box(b'stbl',tables,opts['wide'])])
        mdia=box(b'mdia',[timebox(b'mdhd',rate,len(packets)*1024,version),box(b'hdlr',full()+bytes(4)+b'soun'+bytes(12)+b'APAC\0'),minf])
        return box(b'moov',[timebox(b'mvhd',opts['movie_timescale'],duration,version),box(b'trak',[box(b'tkhd',bytes(tkhd)),box(b'edts',[box(b'elst',elst)]),mdia]),box(b'udta',b'unused')],opts['wide'])
    prefix=[ftyp,free];stub=movie([0]*len(chunks));offset=sum(len(emit(n)) for n in prefix)+(0 if opts['moov_last'] else len(emit(stub)))
    offsets=[];packet_offsets=[];data=[]
    for group_index,group in enumerate(groups):
        gap=box(b'free',bytes(group_index+1));data.append(gap);offset+=len(emit(gap))
        payload=bytearray(b'P'*opts['padding']);start=offset+(16 if opts['wide'] else 8)
        for c in group:
            offsets.append(start+len(payload))
            for p in c:packet_offsets.append(start+len(payload));payload.extend(p)
            payload.extend(b'G'*opts['padding'])
        node=box(b'mdat',bytes(payload),opts['wide'],opts['terminal'] and group_index==len(groups)-1)
        data.append(node);offset+=len(emit(node))
    moov=movie(offsets);nodes=prefix+(data+[moov] if opts['moov_last'] else [moov]+data)
    records={};metadata=hashlib.sha256();out=bytearray()
    for n in nodes:out.extend(emit(n,len(out),records,metadata))
    ph=hashlib.sha256()
    for i,(offset,p) in enumerate(zip(packet_offsets,packets)):ph.update(struct.pack('<QQQQ',i,offset,len(p),1024));ph.update(hashlib.sha256(p).digest())
    truth=dict(kind='mp4',profile=PROFILE,brands=dict(major='mp42',minor_version=0,compatible=['mp42','isom']),track_id=1,
               sample_entry=dict(version=0,channelcount=2,samplesize=16,sample_rate=opts.get('sample_entry_rate',rate if rate<=65535 else 0)),layout_source='cookie',packet_count=len(packets),
               packet_table=dict(valid_frames=valid,priming_frames=priming,remainder_frames=remainder),
               timeline=dict(source='single_elst',movie_timescale=opts['movie_timescale'],media_timescale=rate,edit_duration=duration,rounding='exact_integral_frames'),
               file_bytes=len(out),boxes=records,mdat_count=len(groups),skipped_boxes=2+len(groups),metadata_sha256=metadata.hexdigest(),cookie_sha256=sha(config),
               audio_sha256=sha(b''.join(packets)),packets_sha256=ph.hexdigest(),access='sequential_from_packet_zero',consistency_verified=True,verification='two_pass_read_consistency_no_stored_checksums')
    return bytes(out),truth,packet_offsets

def cases(channels):
    for row in sequences(channels):
        if row[0]!='scalar_band':yield row

def generate(channels,rate,index,case):
    kind,options,seq=case;generated=[packet(c,channels,rate,**options) for c in seq];config=cookie(channels,rate,**options)
    packets=[p for p,_ in generated];priming=(0,1,1024,2048)[index%4];remainder=(0,1,127,1023)[index//4%4]
    raw,truth,offsets=encode(config,packets,rate,channels,priming,remainder,index)
    return config,packets,raw,truth,[t for _,t in generated]

def manifest():
    rows=[]
    for channels,rate in itertools.product(LAYOUTS,(48000,44100)):
        for index,case in enumerate(cases(channels)):
            config,packets,raw,truth,_=generate(channels,rate,index,case)
            rows.append(dict(channels=channels,rate=rate,index=index,kind=case[0],packets=len(packets),mp4_sha256=sha(raw),truth_sha256=digest(truth)))
    return dict(schema_version=1,profile=PROFILE,cases=rows,sha256=digest(rows))
