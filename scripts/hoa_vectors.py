"""Independent restricted ambient-HOA writer; expected positions come from emitted bits."""
import copy,hashlib,json
from spectrum_vectors import bits,pack,bundle as base_bundle
from channel_vectors import single,scene_bits
from drc_vectors import header as drc_header,payload as drc_payload
from tns_vectors import filter_spec
from bwe2_vectors import source_case
PROFILE='apac-hoa-ambient-math-v1'

def cookie(scene=True,drc=False,rich=False,*,order=3,rate=48000):
    n=(order+1)**2
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)
    wire+='1100110'+bits(1,2)+bits(0,2)+bits(0,2)+bits(order,4)+bits(0,4)+bits(n-1,(n-1).bit_length())+'00'+bits(n,5)+'000'*n
    wire+='0'+bits(190,16)+bits(n,16)+'0' # tagged HOA, no remapping
    wire+='0'+bits(0,3)+bits(0,2)+'0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc:wire+=drc_header(rate,rich=rich,channels=n)
    raw=pack(wire+'000');return len(raw).to_bytes(4,'big')+raw[4:]

def config(index):return dict(element_index=index,kind='sce',tce_type=0,output_channels=[index])
def packet(case,scene=True,drc=False,rich=False,*,order=3,rate=48000):
    n=(order+1)**2
    frame_type=case.get('frame_type',1);wire=bits(frame_type,2);inner=None;inner_range=None
    if frame_type==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],scene,drc,rich,order=order,rate=rate);wire+=bits(len(raw),16);wire+='0'*(-len(wire)%8);start=len(wire);wire+=''.join(bits(v,8) for v in raw);inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire);window=case.get('block',0);wire+=bits(window,2);elements=[]
    specs=case.get('elements',[{} for _ in range(n)])
    if len(specs)!=n:raise ValueError('requires the declared SCE count')
    for i,spec in enumerate(specs):
        start=len(wire)
        if spec is None:
            wire+='0';truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire));present=False
        else:
            spec=dict(spec,block=window);encoded,truth=single(spec,0,rate,start-2);wire+=encoded[:2]+encoded[4:];truth['end_bit_offset']=truth.pop('tns_end_bit_offset');present=True
        truth.update(configuration=config(i),present=present,start_bit_offset=start);elements.append(truth)
    spatial_start=len(wire);mode=case.get('mode');wire+=bits(int(mode is not None),1)
    if mode is not None:wire+=bits(mode,3)
    spatial=dict(start_bit_offset=spatial_start,end_bit_offset=len(wire),single_coding_mode=mode is not None,coding_mode=mode,ambient_indices=list(range(n)))
    payload_end=len(wire);wire+='0'*(-len(wire)%8);core_end=len(wire)
    if scene:wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{}));spec.setdefault('rich',rich);data,drc_truth=drc_payload(spec,rate,len(wire),channels=n);wire+=data
    wire+='0';end=len(wire)
    if drc:wire+='0'
    raw=pack(wire)
    return raw,dict(frame_type=frame_type,common_window=window,core_start_bit_offset=core_start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
        elements=elements,inner=inner,inner_range=inner_range,drc=drc_truth,spatial=spatial,
        tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))

def bundle(root,payloads,scene=True,drc=False,rich=False,priming=0,remainder=0,*,order=3,rate=48000):
    n=(order+1)**2
    base_bundle(root,payloads,rate);cfg=cookie(scene,drc,rich,order=order,rate=rate);(root/'cookie.bin').write_bytes(cfg)
    path=root/'manifest.json';m=json.loads(path.read_text());f=m['file'];f['format']['channels']=n
    f['layout']['value']=dict(tag=(190<<16)|n,bitmap=0,descriptions=[],name='HOA ACN/SN3D',ambisonic_order=order,ambisonic_channel_order='ACN',ambisonic_normalization='SN3D')
    f['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest());f['packet_table']['value']=dict(valid_frames=len(payloads)*1024-priming-remainder,priming_frames=priming,remainder_frames=remainder)
    path.write_text(json.dumps(m),encoding='utf-8')

def excitation(index,block=0,grouping=0,gain=160,q=1,*,order=3):
    elems=[None]*((order+1)**2);elems[index]=dict(grouping=grouping,gain=gain,bands={0:(11,[q,0],gain)} if abs(q)>1 else {0:(1,[q,0,0,0],gain)})
    return dict(block=block,elements=elems)

def sequences():
    plain=dict(scene=True,drc=False,rich=False);rich=dict(scene=True,drc=True,rich=True);absent=dict(elements=[None]*16)
    for i in range(16):yield 'basis_'+str(i),plain,[excitation(i,q=(-1 if i%2 else 1)),{},absent]
    for mask in (0,0x55,0x7f):yield 'short_'+str(mask),plain,[excitation(0,1),excitation(15,2,mask),excitation(0,3),absent]
    for block in (0,1,2,3):yield 'window_'+str(block),plain,[excitation(7,block,0x55),absent,{}]
    for direction in (False,True):
        a=excitation(15);a['elements'][15].update(max_sfb=49,tns=filter_spec([1,-1],direction=direction));yield 'tns_'+str(direction),plain,[a,absent,{}]
    for block,group in ((0,0),(2,0x55)):
        src=source_case(block,group,upper=True,flat=True,gain=160);count=4 if block==2 else 1
        elems=[None]*16;elems[0]=dict(grouping=group,bands=src['left'],gain=160,bwe2=dict(lsf=[163,24],gains=[32]*count))
        yield 'bwe_'+str(block),plain,[dict(block=block,elements=elems),absent,{}]
        zeros=[None]*16;zeros[15]=dict(grouping=group,bwe2=dict(lsf=[511,511],gains=[]))
        yield 'zero_sfb_bwe_'+str(block),plain,[dict(block=block,elements=zeros),absent,{}]
    yield 'escape',plain,[excitation(0,gain=255,q=8191),excitation(15,gain=0,q=-8191),absent,{}]
    for mode in range(6):yield 'mode_'+str(mode),plain,[dict(excitation(0),mode=mode),absent,{}]
    a=dict(excitation(0),mode=5,drc=dict(header=True,gains=[-3]));b=dict(excitation(15),drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))
    yield 'drc_updates',rich,[a,b,absent,{}]
    yield 'embedded',rich,[a,dict(excitation(7,3),frame_type=2,preroll=dict(excitation(15,2,0x55),mode=3)),absent,{}]
    yield 'empty_no_scene',dict(scene=False,drc=False,rich=False),[absent,{},absent]

def canonical(x):
    if isinstance(x,list):return [canonical(v) for v in x]
    if isinstance(x,dict):return {k:canonical(v) for k,v in x.items() if k!='scaled'}
    return x

def manifest():
    rows=[]
    for i,(kind,options,cases) in enumerate(sequences()):
        cfg=cookie(**options);parts=[packet(case,**options) for case in cases];h=hashlib.sha256(cfg)
        for raw,_ in parts:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=i,kind=kind,packets=len(parts),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in parts]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def state_fixture():
    options=dict(scene=True,drc=True,rich=True);a=dict(excitation(0),mode=3,drc=dict(header=True,gains=[-3]));b=dict(excitation(15),mode=1);first,_=packet(a,**options);good,t=packet(b,**options)
    bad=bytearray(good);p=t['elements'][-1]['start_bit_offset']+1;bad[p//8]|=1<<(7-p%8)
    late=bytearray(good);p=t['spatial']['start_bit_offset']+1
    for i in range(3):late[(p+i)//8]|=1<<(7-(p+i)%8)
    outer,truth=packet(dict(b,frame_type=2,preroll=dict(a,mode=5,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))),**options)
    failures={}
    for name,pos in [('embedded_error',truth['inner_range']['start_bit_offset']+truth['inner']['spatial']['start_bit_offset']+1),('outer_after_embedded_error',truth['spatial']['start_bit_offset']+1)]:
        value=bytearray(outer)
        for i in range(3):value[(pos+i)//8]|=1<<(7-(pos+i)%8)
        failures[name]=value.hex()
    return dict(cookie=cookie(**options).hex(),first=first.hex(),next=good.hex(),last_element_error=bytes(bad).hex(),late_spatial_error=bytes(late).hex(),**failures)
