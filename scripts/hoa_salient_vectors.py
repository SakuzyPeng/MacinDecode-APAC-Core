"""Independent fixed salient5/ambient0 wire writer and explicit spatial truth."""
import copy
import hashlib
import json
from spectrum_vectors import bits, pack
from channel_vectors import single, scene_bits
from hoa_vectors import config, bundle as ambient_bundle, excitation
from drc_vectors import header as drc_header, payload as drc_payload
from hoa_salient_format import format_for

PROFILE='apac-hoa-salient-math-v1'
FORMAT=format_for(3)
ORDER2_FORMAT=format_for(2)
ORDER1_FORMAT=format_for(1)
ENDS=[32,80,216,1024]


def cookie(scene=True,drc=False,rich=False,*,order=3,rate=48000):
    n=(order+1)**2
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+'1100110'+bits(1,2)+bits(0,2)+bits(0,2)+bits(order,4)+bits(5,4)+bits(0,(n-1).bit_length())
    wire+=(bits(3,4)+bits(order,order.bit_length()))*5+'0'+bits(n,5)+'000'*n
    wire+='0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc:wire+=drc_header(rate,rich=rich,channels=n)
    raw=pack(wire+'000');return len(raw).to_bytes(4,'big')+raw[4:]


def descriptors(mode=0,coefficient=None,component=0,cluster=0,angles=(0,90),*,order=3):
    n=(order+1)**2
    out=[]
    for sc in range(5):
        row=[]
        for sb in range(4):
            q=[0 if mode==3 else 32]*n
            if coefficient is not None and sc==component:q[coefficient]=16 if mode==3 else 48
            row.append(dict(mode=mode,quantized=q,signs_positive=[True]*n,cluster=cluster,angles=angles))
        out.append(row)
    return out


def spatial(case,origin,*,order=3):
    n=(order+1)**2;fmt=format_for(order)
    global_mode=case.get('global_mode');wire=bits(int(global_mode is not None),1)
    if global_mode is not None:wire+=bits(global_mode,3)
    result=[]
    for sc,row in enumerate(case.get('descriptors',descriptors(order=order))):
        assert len(row)==4
        for sb,spec in enumerate(row):
            start=origin+len(wire);mode=spec.get('mode',0)
            if global_mode is None:wire+=bits(mode,3)
            else:assert mode==global_mode
            q=list(spec.get('quantized',[0 if mode==3 else 32]*n));signs=list(spec.get('signs_positive',[True]*n));cluster=None;angles=(None,None)
            def huff(book,index):
                length,code=book[index];return bits(code,length)
            if mode==0:wire+=''.join(bits(v,6) for v in q)
            elif mode==5:
                angles=spec.get('angles',(0,90));wire+=bits(angles[0],9)+bits(angles[1],8);q=q[:4]
                wire+=''.join(huff(fmt['modes'][1]['codebooks'][0],v) for v in q)
            else:
                table=fmt['modes'][mode]
                if mode==4:cluster=spec.get('cluster',0);wire+=bits(cluster,2)
                for b,group in enumerate(table['groups']):
                    if cluster is not None and cluster!=b:continue
                    for i in group:
                        wire+=huff(table['codebooks'][b],q[i])
                        if table['signs']:wire+=bits(int(signs[i]),1)
            result.append(dict(component_index=sc,subband_index=sb,mode=mode,start_bit_offset=start,end_bit_offset=origin+len(wire),
                               quantized=q,signs_positive=signs if mode==3 else [],cluster=cluster,azimuth_degrees=angles[0],elevation_offset_degrees=angles[1]))
    assert len(result)==20
    return wire,dict(start_bit_offset=origin,end_bit_offset=origin+len(wire),single_coding_mode=global_mode is not None,
                     coding_mode=global_mode,ambient_indices=[],salient=dict(subband_ends=ENDS,descriptors=result))


def packet(case,scene=True,drc=False,rich=False,*,order=3,rate=48000):
    n=(order+1)**2
    typ=case.get('frame_type',1);wire=bits(typ,2);inner=None;inner_range=None
    if typ==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],scene,drc,rich,order=order,rate=rate);wire+=bits(len(raw),16);wire+='0'*(-len(wire)%8);start=len(wire);wire+=''.join(bits(v,8) for v in raw);inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire);block=case.get('block',0);wire+=bits(block,2);elements=[]
    specs=case.get('elements',[None]*n);assert len(specs)==n
    for i,spec in enumerate(specs):
        start=len(wire)
        if spec is None:
            wire+='0';truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire));present=False
        else:
            encoded,truth=single(dict(spec,block=block),0,rate,start-2);wire+=encoded[:2]+encoded[4:];truth['end_bit_offset']=truth.pop('tns_end_bit_offset');present=True
        truth.update(configuration=dict(config(i),output_channels=[],transport_channels=[i]),present=present,start_bit_offset=start);elements.append(truth)
    encoded,side=spatial(case,len(wire),order=order);wire+=encoded;payload_end=len(wire);wire+='0'*(-len(wire)%8);core_end=len(wire)
    side['salient']['lines_per_window']=[n//8 if block==2 else n for n in ENDS]
    if scene:wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{}));spec.setdefault('rich',rich);encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n);wire+=encoded
    wire+='0';end=len(wire)
    if drc:wire+='0'
    raw=pack(wire)
    return raw,dict(frame_type=typ,common_window=block,elements=elements,spatial=side,inner=inner,inner_range=inner_range,drc=drc_truth,
                    core_start_bit_offset=core_start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))


def bundle(root,payloads,scene=True,drc=False,rich=False,priming=0,remainder=0,*,order=3,rate=48000):
    ambient_bundle(root,payloads,scene,drc,rich,priming,remainder,order=order,rate=rate);cfg=cookie(scene,drc,rich,order=order,rate=rate);(root/'cookie.bin').write_bytes(cfg)
    p=root/'manifest.json';m=json.loads(p.read_text());m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest());p.write_text(json.dumps(m))


def basis(coefficient=0,component=0,mode=0,*,order=3,**options):
    case=excitation(component,options.pop('block',0),options.pop('grouping',0),options.pop('gain',160),options.pop('q',1),order=order)
    case['descriptors']=descriptors(mode,coefficient,component,order=order,**options)
    return case


def sequences():
    plain=dict(scene=True,drc=False,rich=False);rich=dict(scene=True,drc=True,rich=True)
    for k in range(16):
        yield 'coefficient_'+str(k),plain,[basis(k,k%5,1),{},{}]
    for mode in range(6):
        a=basis(7,0,mode,cluster=2,angles=(37,121));a['global_mode']=mode
        b=basis(7,0,3);b['descriptors'][0][0]['signs_positive'][7]=False
        yield 'mode_'+str(mode),plain,[a,b,{}]
    yield 'transform_clusters',plain,[basis(15,4,4,cluster=c) for c in range(4)]+[{}]
    yield 'direction_boundaries',plain,[basis(8,2,5,angles=a) for a in ((0,0),(0,90),(90,90),(180,90),(359,180),(360,90),(511,255),(37,121))]+[{}]
    from spectrum_vectors import TABLES
    for block,group in ((0,0),(1,0),(2,0),(2,0x55),(2,0x7f),(3,0)):
        a=basis(0,0,2,block=block,grouping=group);offsets=TABLES['short_offsets' if block==2 else 'long_offsets'];ends=[n//8 if block==2 else n for n in ENDS]
        bands={}
        for point in [0]+[v for end in ends[:-1] for v in (end-1,end)]+[ends[-1]-1]:
            sfb=next(i for i in range(len(offsets)-1) if offsets[i]<=point<offsets[i+1]);q=[0,0];q[(point-offsets[sfb])%2]=1;bands[sfb]=(11,q,160)
        a['elements'][0]['bands']=bands
        for sc in range(5):
            for sb in range(4):a['descriptors'][sc][sb]['quantized']=[48 if sc==0 and k==sb*4 else 32 for k in range(16)]
        yield 'subband_window_'+str(block)+'_'+str(group),plain,[a,{},{}]
    mixed=basis(0,0,0,block=2,grouping=0x55)
    for sc in range(5):
        mixed['elements'][sc]=copy.deepcopy(excitation(sc,2,(0,0x55,0x7f)[sc%3])['elements'][sc])
        for sb in range(4):mixed['descriptors'][sc][sb]=descriptors((sc+sb)%6,sc+sb,sc,cluster=sc%4,angles=(37+sc,90+sb))[sc][sb]
    yield 'mixed_modes_and_groups',plain,[mixed,{},{}]
    unused=basis(15,15,1);unused['descriptors']=descriptors(1,15,0)
    yield 'unused_transport_validated',plain,[unused,basis(15,4,1),{}]
    strong=basis(12,0,4,cluster=3,gain=255,q=8191)
    strong['elements'][1]=copy.deepcopy(excitation(1,0,gain=255,q=-8191)['elements'][1]);strong['descriptors'][1]=copy.deepcopy(strong['descriptors'][0])
    yield 'escape_high_gain_cancellation',plain,[strong,basis(12,4,3,gain=0,q=-8191),{}]
    from tns_vectors import filter_spec
    from bwe2_vectors import source_case
    tools=basis(15,4,1);source=source_case(0,0,upper=True,flat=True,gain=160)
    tools['elements'][4]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=True),bwe2=dict(lsf=[163,24],gains=[32]))
    yield 'tns_bwe2_joint',plain,[tools,{},{}]
    a=dict(basis(0,0,1),drc=dict(header=True,gains=[-3]));b=dict(basis(15,4,3),drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))
    yield 'drc_updates',rich,[a,b,{},{}]
    yield 'embedded_history',rich,[a,dict(b,frame_type=2,preroll=basis(9,1,4,block=2,grouping=0x55,cluster=1)),{},{}]


def manifest():
    from hoa_vectors import canonical
    rows=[]
    for index,(kind,options,cases) in enumerate(sequences()):
        cfg=cookie(**options);parts=[packet(case,**options) for case in cases];h=hashlib.sha256(cfg)
        for raw,_ in parts:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,packets=len(parts),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in parts]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixture():
    options=dict(scene=True,drc=True,rich=True)
    a=dict(basis(15,4,1),drc=dict(header=True,gains=[-3]));b=basis(9,0,3);b['elements'][15]={}
    first,_=packet(a,**options);good,t=packet(b,**options);bad=bytearray(good);p=t['elements'][-1]['start_bit_offset']+1;bad[p//8]|=1<<(7-p%8)
    def mode_error(raw,position):
        out=bytearray(raw)
        for i in range(3):out[(position+i)//8]|=1<<(7-(position+i)%8)
        return out.hex()
    late=mode_error(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'])
    tail=bytearray(good);p=t['tail']['ancillary_end_bit_offset']-1;tail[p//8]|=1<<(7-p%8)
    outer,truth=packet(dict(b,frame_type=2,preroll=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))),**options)
    return dict(cookie=cookie(**options).hex(),first=first.hex(),next=good.hex(),last_element_error=bad.hex(),late_spatial_error=late,late_tail_error=tail.hex(),
                embedded_error=mode_error(outer,truth['inner_range']['start_bit_offset']+truth['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset']),
                outer_after_embedded_error=mode_error(outer,truth['spatial']['salient']['descriptors'][-1]['start_bit_offset']))
