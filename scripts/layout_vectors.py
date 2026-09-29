"""Independent fixed 12/24-channel packets; old vector enumeration is unchanged."""
import copy,hashlib,itertools,json,struct
from functools import lru_cache
from channel_vectors import EXTENDED_LAYOUTS as LAYOUTS,layout,configuration,cookie,packet,bundle,excitation,WINDOWS,state_fixtures
from channel_vectors import sequences as channel_sequences
from access_vectors import generated,expected_access
from caf_vectors import digest,sha
PROFILE='apac-channel-layout-v2'

def masks(n):
    count=len(layout(n)[2]);all_present=(1<<count)-1
    values={0,all_present}
    for a in range(count):
        values.update((1<<a,all_present^(1<<a)))
        for b in range(a):values.update(((1<<a)|(1<<b),all_present^((1<<a)|(1<<b))))
    return sorted(values)

def sequences(n):
    for kind,options,seq in channel_sequences(n,masks(n)):
        if kind=='scalar_band':
            active=next(e for e in seq[0]['elements'] if e is not None)
            band=next(iter(active['bands']));last=13 if active['block']==2 else 48
            if band not in (0,1,last):continue
        yield kind,options,seq
    absent=dict(elements=[None]*len(layout(n)[2]))
    for cfg in configuration(n):
        if cfg['kind']!='cpe':continue
        for gain,side in itertools.product((9,26),('left','right')):
            elems=[None]*len(layout(n)[2]);elems[cfg['element_index']]=dict(cac_gain=gain,**{side:{0:(1,[1,-1,0,0],160)}})
            yield 'local_cac',dict(scene=False,drc=False,rich=False),[dict(elements=elems),absent,{}]
    lfes=[c for c in configuration(n) if c['kind']=='lfe']
    if len(lfes)==2:
        for window in WINDOWS:
            elems=[None]*len(layout(n)[2])
            for i,c in enumerate(lfes):elems[c['element_index']]=dict(block=window[0],grouping=window[1],gain=255,bands={0:(11,[8191 if i==0 else -8191,16],255)})
            a=dict(elements=elems);b=copy.deepcopy(a);b['elements'][lfes[0]['element_index']]=None
            yield 'dual_lfe',dict(scene=True,drc=True,rich=True),[a,b,dict(a,frame_type=2,preroll=b),absent,{}]

def canonical(value):
    if isinstance(value,list):return [canonical(x) for x in value]
    if isinstance(value,dict):return {k:canonical(x) for k,x in value.items() if k!='scaled'}
    return value

@lru_cache(maxsize=1)
def math_manifest():
    rows=[]
    for n,rate in itertools.product(LAYOUTS,(48000,44100)):
        for index,(kind,options,seq) in enumerate(sequences(n)):
            h=hashlib.sha256(cookie(n,rate,**options));truths=[]
            for c in seq:
                raw,t=packet(c,n,rate,**options);h.update(len(raw).to_bytes(8,'little'));h.update(raw);truths.append(t)
            rows.append(dict(channels=n,rate=rate,index=index,kind=kind,packets=len(seq),input_sha256=h.hexdigest(),truth_sha256=digest(canonical(truths))))
    return dict(schema_version=1,profile=PROFILE,sequences=rows,sha256=digest(rows))

def access_cases(n):
    kinds={}
    for case in sequences(n):kinds.setdefault(case[0],[]).append(case)
    for kind,rows in kinds.items():
        if kind=='scalar_band':continue
        if kind=='channel_window':
            wanted={0,3,7,8,n-1}|({9,15,16} if n==24 else set())
            indices=[ch*len(WINDOWS)+w for ch in sorted(wanted) for w in (0,3)]
        elif kind in ('mixed_element_windows','zero_band_extensions','dual_lfe'):indices=range(len(rows))
        elif kind=='joint_tools':indices=range(0,len(rows),4)
        elif kind=='preroll_windows':indices=(0,5,10,15)
        else:indices=sorted({0,1,len(rows)-2,len(rows)-1})
        for i in indices:
            if 0<=i<len(rows):yield rows[i]

@lru_cache(maxsize=1)
def access_manifest():
    rows=[]
    for n,rate in itertools.product(LAYOUTS,(48000,44100)):
        for index,case in enumerate(access_cases(n)):
            d=generated(n,rate,index,case)
            truth=[dict(name=name,start=start,requested=count,access=expected_access(d,start,count)) for name,start,count in d['ranges']]
            rows.append(dict(channels=n,rate=rate,index=index,kind=case[0],packets=len(d['payloads']),caf_sha256=sha(d['caf']),mp4_sha256=sha(d['mp4']),expectations_sha256=digest(truth)))
    return dict(schema_version=1,profile=PROFILE,cases=rows,sha256=digest(rows))

def presence_templates(n,rate):
    raw,t=packet({},n,rate,scene=False);wire=''.join(format(v,'08b') for v in raw)
    rows=[]
    for e in t['elements']:
        start=e['start_bit_offset'];end=e['bwe2']['end_bit_offset'] if e['bwe2'] else e['end_bit_offset']
        rows.append(dict(wire=wire[start:end],body_bits=e['end_bit_offset']-start,configuration=e['configuration']))
    return rows

def presence_record(n,rate,mask,templates):
    wire='01';ranges=[]
    for i,t in enumerate(templates):
        start=len(wire);present=not (mask>>i&1);wire+=t['wire'] if present else '0'
        ranges.append((start,start+t['body_bits'] if present else start+1,len(wire)))
    wire+='0'*(-len(wire)%8);core=len(wire);wire+='0';wire+='0'*(-len(wire)%8)
    raw=int(wire,2).to_bytes(len(wire)//8,'big')
    identity=struct.pack('<6Q',n,rate,mask,len(raw),core,len(wire))+hashlib.sha256(raw).digest()
    identity+=b''.join(struct.pack('<3Q',*r) for r in ranges)
    return identity

@lru_cache(maxsize=1)
def presence_manifest():
    rows=[];total=0
    for n,rate in itertools.product(LAYOUTS,(48000,44100)):
        templates=presence_templates(n,rate);h=hashlib.sha256();count=1<<len(templates)
        for mask in range(count):h.update(presence_record(n,rate,mask,templates))
        rows.append(dict(channels=n,rate=rate,cookie=cookie(n,rate,scene=False).hex(),templates=templates,cases=count,stage_sha256=h.hexdigest()));total+=count
    return dict(schema_version=1,profile=PROFILE,cases=total,layouts=rows,sha256=digest(rows))
