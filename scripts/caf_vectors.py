"""Independent CAF v1 writer and frozen restricted-decoder input vectors."""
import copy
import hashlib
import itertools
import json
import struct
from packet_vectors import WINDOWS,basis,packet,cookie,bundle,sequences as packet_sequences
from drc_pcm_vectors import sequences as drc_sequences,packet as drc_packet,cookie as drc_cookie,bundle as drc_bundle,oracle_truth

PROFILE='apac-caf-input-v1'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
def varint(value):
    assert value>=0
    out=[value&127];value>>=7
    while value:out.append(128|(value&127));value>>=7
    return bytes(reversed(out))
def chunk(tag,payload,size=None):return struct.pack('>4sq',tag,len(payload) if size is None else size)+payload

VARIANTS=[dict(order=order,chan=True,terminal=False) for order in itertools.permutations(('kuki','pakt','data','chan'))]
VARIANTS += [dict(order=order,chan=False,terminal=False) for order in itertools.permutations(('kuki','pakt','data'))]
VARIANTS += [dict(order=order+('data',),chan=True,terminal=True) for order in itertools.permutations(('kuki','pakt','chan'))]

def encode(config,packets,rate=48000,priming=0,remainder=0,variant=0,edit=0,channels=2):
    v=VARIANTS[variant%len(VARIANTS)];audio=b''.join(packets);frames=len(packets)*1024
    payloads=dict(desc=struct.pack('>d4sIIIII',rate,b'apac',0,0,1024,channels,0),kuki=config,
                  chan=struct.pack('>III',({1:100,2:101,6:121,8:128,12:192,24:204}[channels]<<16)|channels,0,0),
                  pakt=struct.pack('>qqii',len(packets),frames-priming-remainder,priming,remainder)+b''.join(varint(len(p)) for p in packets),
                  data=struct.pack('>I',edit)+audio)
    header=b'caff\0\x01\0\0';out=bytearray(header);metadata=hashlib.sha256(header);chunks={}
    for name in ('desc','unknown')+v['order']:
        raw=payloads[name] if name!='unknown' else b'ignored-metadata\x00\xff'
        tag=name.encode() if name!='unknown' else b'free'
        packed=chunk(tag,raw,-1 if name=='data' and v['terminal'] else None)
        metadata.update(packed[:12])
        if name!='unknown':
            chunks[name]=dict(offset=len(out)+12,bytes=len(raw))
            metadata.update(raw[:4] if name=='data' else raw)
        out.extend(packed)
    packet_hash=hashlib.sha256();offset=0
    for i,p in enumerate(packets):
        packet_hash.update(struct.pack('<QQQQ',i,offset,len(p),1024));packet_hash.update(hashlib.sha256(p).digest());offset+=len(p)
    truth=dict(kind='caf',profile=PROFILE if channels==2 else 'apac-caf-input-v2',packet_count=len(packets),file_bytes=len(out),layout_source='chan' if v['chan'] else 'cookie',edit_count=edit,
               packet_table=dict(valid_frames=frames-priming-remainder,priming_frames=priming,remainder_frames=remainder),chunks=chunks,skipped_chunks=1,
               metadata_sha256=metadata.hexdigest(),cookie_sha256=sha(config),audio_sha256=sha(audio),packets_sha256=packet_hash.hexdigest(),
               access='sequential_from_packet_zero',verification='two_pass_read_consistency_no_stored_checksums',consistency_verified=True)
    return bytes(out),truth

def cases():
    for window,independent,drc in itertools.product(WINDOWS,(False,True),(False,True)):
        a=basis(window,'left',independent=independent);b=basis(window,'right',independent=not independent)
        seq=[a,dict(absent=True),dict(b,frame_type=2,preroll=a),{},b,{}]
        yield 'state_windows',drc,dict(scene=True,rich=True,scene_drc_flag=True) if drc else dict(scene=True),seq
    for kind,options,seq in packet_sequences():
        if kind in ('joint_tools','content_origin'):yield kind,False,options,seq
    for kind,options,seq in drc_sequences():
        if kind in ('joint_tools','escape_pressure','metadata_updates'):yield 'drc_'+kind,True,options,seq

def generate(case,rate,index):
    kind,drc,options,seq=case
    parts=[drc_packet(c,rate,options) if drc else packet(c,rate,options.get('scene',False)) for c in seq]
    config=drc_cookie(rate,**options) if drc else cookie(rate,**options)
    packets=[p for p,_ in parts];priming=(0,1,1024,2048)[index%4];remainder=(0,1,127,1023)[index//4%4]
    raw,truth=encode(config,packets,rate,priming,remainder,index,edit=index%3)
    return config,packets,raw,truth,[oracle_truth(t) if drc else t for _,t in parts]

def make_bundle(path,case,rate,packets,truth):
    _,drc,options,_=case
    (drc_bundle if drc else bundle)(path,packets,rate,**options)
    m=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    m['file']['packet_table']['value']=truth['packet_table']
    (path/'manifest.json').write_text(json.dumps(m),encoding='utf-8')

def manifest():
    records=[]
    for rate in (48000,44100):
        for index,case in enumerate(cases()):
            _,packets,raw,truth,_=generate(case,rate,index)
            records.append(dict(rate=rate,index=index,kind=case[0],drc=case[1],packets=len(packets),caf_sha256=sha(raw),truth_sha256=digest(truth)))
    return dict(schema_version=1,profile=PROFILE,cases=records,sha256=digest(records))
