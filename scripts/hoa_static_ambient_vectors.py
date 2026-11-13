"""Independent static-selection/FOA wire writer; legacy defaults stay frozen."""
import copy, hashlib, json
from spectrum_vectors import bits, pack
from channel_vectors import single, scene_bits
from drc_vectors import header as drc_header, payload as drc_payload
from hoa_vectors import bundle as ambient_bundle, excitation, canonical
from hoa_mixed_vectors import spatial as mixed_spatial, basis as mixed_basis, mapping
from generate_hoa_static_ambient_tables import PROFILE


def indices(order, mixed, selection):
    return list(range(4 if mixed else (order+1)**2)) if selection is None else list(selection)


def cookie(scene=True,drc=False,rich=False,*,order=3,rate=48000,mixed=True,selection=None,transform=0):
    n=(order+1)**2; ambient=4 if mixed else n; salient=5 if mixed else 0
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),
            (3 if rate==48000 else 4,6),(0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+'1100110'+bits(1,2)+bits(0,2)+bits(0,2)+bits(order,4)+bits(salient,4)
    wire+=bits(ambient if salient else ambient-1,(n-1).bit_length())
    if mixed: wire+=(bits(3,4)+bits(order,2))*5
    wire+=bits(int(selection is not None),1)
    if selection is not None:
        assert len(selection)==ambient
        limit=n
        for i in range(ambient-1,-1,-1):
            wire+=bits(selection[i],(limit-1).bit_length())
            if selection[i]==i: break
            limit=selection[i]+1
    wire+=bits(int(transform!=0),1)
    if transform: wire+=bits(transform-1,2)
    wire+=bits(n,5)+'000'*n+'0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc: wire+=drc_header(rate,rich=rich,channels=n)
    raw=pack(wire+'000'); return len(raw).to_bytes(4,'big')+raw[4:]


def spatial(case,origin,*,order,mixed,selection,transform):
    selected=indices(order,mixed,selection); wire=''
    index=case.get('transform_index',3) if transform==4 else transform-1 if transform else 3
    if transform==4: wire=bits(index,2)
    transform_end=origin+len(wire)
    if mixed:
        tail,side=mixed_spatial(case,transform_end,order=order,selection=selected); wire+=tail
        side['salient']['lines_per_window']=[x//8 if case.get('block',0)==2 else x for x in side['salient']['subband_ends']]
    else:
        mode=case.get('global_mode'); wire+=bits(int(mode is not None),1)
        if mode is not None: wire+=bits(mode,3)
        side=dict(single_coding_mode=mode is not None,coding_mode=mode,ambient_indices=selected)
    side.update(start_bit_offset=origin,end_bit_offset=origin+len(wire))
    config=dict(mode='disabled') if not transform else dict(mode='per_frame') if transform==4 else dict(mode='fixed',index=transform-1)
    side['ambient']=dict(explicit_selection=selection is not None,selection=selected,transform_config=config,
                         effective_index=index,index_source='frame' if transform==4 else 'cookie' if transform else 'disabled',
                         index_start_bit_offset=origin if transform==4 else None,index_end_bit_offset=transform_end if transform==4 else None)
    return wire,side


def packet(case,scene=True,drc=False,rich=False,*,order=3,rate=48000,mixed=True,selection=None,transform=0):
    n=(order+1)**2; options=dict(scene=scene,drc=drc,rich=rich,order=order,rate=rate,mixed=mixed,selection=selection,transform=transform)
    typ=case.get('frame_type',1); wire=bits(typ,2); inner=None; inner_range=None
    if typ==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],**options); wire+=bits(len(raw),16); wire+='0'*(-len(wire)%8); start=len(wire)
            wire+=''.join(bits(v,8) for v in raw); inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire); block=case.get('block',0); wire+=bits(block,2); elements=[]
    specs=case.get('elements',[None]*n); assert len(specs)==n
    for i,spec in enumerate(specs):
        start=len(wire)
        if spec is None:
            wire+='0'; truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire)); present=False
        else:
            encoded,truth=single(dict(spec,block=block),0,rate,start-2); wire+=encoded[:2]+encoded[4:]
            truth['end_bit_offset']=truth.pop('tns_end_bit_offset'); present=True
        truth.update(configuration=dict(element_index=i,kind='sce',tce_type=0,output_channels=[],transport_channels=[i]),present=present,start_bit_offset=start); elements.append(truth)
    encoded,side=spatial(case,len(wire),order=order,mixed=mixed,selection=selection,transform=transform); wire+=encoded
    payload_end=len(wire); wire+='0'*(-len(wire)%8); core_end=len(wire)
    if scene: wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{})); spec.setdefault('rich',rich); encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n); wire+=encoded
    wire+='0'; end=len(wire)
    if drc: wire+='0'
    raw=pack(wire); mixed_map=mapping(order) if mixed else None
    if mixed_map: mixed_map['ambient_output_coefficients']=indices(order,mixed,selection)
    return raw,dict(frame_type=typ,common_window=block,elements=elements,spatial=side,mixed=mixed_map,inner=inner,inner_range=inner_range,drc=drc_truth,native_numeric_stress=bool(case.get('native_numeric_stress')),
                    core_start_bit_offset=core_start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))


def bundle(root,payloads,scene=True,drc=False,rich=False,priming=0,remainder=0,**options):
    ambient_bundle(root,payloads,scene,drc,rich,priming,remainder,order=options.get('order',3),rate=options.get('rate',48000))
    cfg=cookie(scene,drc,rich,**options); (root/'cookie.bin').write_bytes(cfg)
    path=root/'manifest.json'; m=json.loads(path.read_text()); m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest()); path.write_text(json.dumps(m))


def ambient_basis(slot, index, order=1, block=0, grouping=0, q=1, gain=160):
    return dict(excitation(slot,block,grouping,gain,q,order=order),transform_index=index)


def native_controls():
    options=dict(order=1,rate=44100,mixed=False,selection=[0,1,2,3],transform=4,scene=True,drc=True,rich=True)
    cases=[ambient_basis(slot,index) for index in range(4) for slot in range(4)]
    cases[0]['drc']=dict(header=True,gains=[-3])
    cases+=[ambient_basis(0,0,block=1),ambient_basis(2,1,block=2,grouping=0x55),
            dict(ambient_basis(1,2,block=3),frame_type=2,preroll=ambient_basis(3,3,block=2,grouping=0x7f)),dict(transform_index=0)]
    yield 'ambient1-switch',options,cases
    options=dict(order=3,rate=48000,mixed=True,selection=[1,5,10,15],transform=4,scene=True,drc=False,rich=False)
    cases=[]
    for i,mode in enumerate((0,1,2,4,3,5,3)):
        c=mixed_basis(8,4,mode,ambient=[1,-1,1,-1],cluster=2,angles=(37,121)); c['transform_index']=i%4; cases.append(c)
    cases+=[dict(mixed_basis(8,0,1,block=2,grouping=0x55,ambient=[1,1,-1,-1]),transform_index=2),dict(transform_index=3)]
    yield 'mixed3-selection',options,cases
    yield 'mixed2-fixed',dict(order=2,rate=44100,mixed=True,selection=[0,1,3,8],transform=2,scene=True,drc=False,rich=False),[mixed_basis(4,4,3,order=2,ambient=[1,-1,1,-1])]
    case=ambient_basis(0,3,order=3); case['elements'][15]=excitation(15,order=3)['elements'][15]
    yield 'ambient3-fixed',dict(order=3,rate=48000,mixed=False,selection=None,transform=3,scene=True,drc=False,rich=False),[case]
    cases=[]
    for index in range(3):
        case=ambient_basis(0,index,q=4096,gain=252)
        case['elements'][1]=excitation(1,gain=100,order=1)['elements'][1]
        case['elements'][2]=excitation(2,q=-4096,gain=252,order=1)['elements'][2]
        case['native_numeric_stress']=True; cases.append(case)
    yield 'ambient1-cancellation',dict(order=1,rate=48000,mixed=False,selection=None,transform=4,scene=True,drc=False,rich=False),cases


def sequences():
    yield from native_controls()
    for order,rate,mixed,transform in ((1,48000,False,1),(3,44100,False,2),(2,48000,True,3)):
        selected=[0,2,4,8] if mixed else None
        case=mixed_basis(1,0,1,order=order,ambient=[1,-1,1,-1]) if mixed else ambient_basis(2,3,order=order)
        yield 'fixed_'+str(order),dict(order=order,rate=rate,mixed=mixed,selection=selected,transform=transform,scene=True,drc=False,rich=False),[case,{}]
    for order,rate,selected in ((2,48000,[0,2,4,8]),(3,44100,[4,8,12,15])):
        cases=[]
        for mode in (0,1,3,4,3,5,3):
            c=mixed_basis(1 if order==2 else 0,4,mode,order=order,ambient=[1,-1,1,-1],cluster=1,angles=(37,121))
            if mode==3:
                for row in c['descriptors'][4]: row['signs_positive']=[False]*((order+1)**2)
            cases.append(c)
        yield 'selection_only_'+str(order),dict(order=order,rate=rate,mixed=True,selection=selected,transform=0,scene=True,drc=False,rich=False),cases+[{}]
    for order,mixed in ((3,False),(2,True)):
        selected=list(range(4 if mixed else 16)); case=mixed_basis(8,4,1,order=2) if mixed else excitation(15,order=3)
        yield 'explicit_identity_'+str(order),dict(order=order,rate=48000,mixed=mixed,selection=selected,transform=0,scene=True,drc=False,rich=False),[case,{}]
    for order,rate,mixed in ((3,44100,False),(3,44100,True)):
        cases=[]; n=(order+1)**2; selected=[1,5,10,15] if mixed else None
        for block,group in ((1,0),(2,0),(2,0x55),(2,0x7f),(3,0)):
            c=mixed_basis(8,4,3,order=order,block=block,grouping=group,ambient=[1,-1,1,-1]) if mixed else ambient_basis(0,0,order=order,block=block,grouping=group)
            c['transform_index']=len(cases)%4
            for slot in range(4): c['elements'][slot]=excitation(slot,block,(0,0x55,0x7f,group)[slot] if block==2 else 0,order=order)['elements'][slot]
            cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3])
        child=dict(copy.deepcopy(cases[-2]),transform_index=2,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))
        cases[-1].update(frame_type=2,preroll=child,transform_index=1)
        cases+=[dict(transform_index=0),dict(transform_index=3)]
        yield 'windows_drc_embedded_'+str(mixed),dict(order=order,rate=rate,mixed=mixed,selection=selected,transform=4,scene=True,drc=True,rich=True),cases
    from bwe2_vectors import source_case
    from tns_vectors import filter_spec
    source=source_case(0,0,upper=True,flat=True,gain=160)
    c=mixed_basis(7,4,1,order=2,ambient=[1,-1,1,-1]); c['transform_index']=0
    for slot in (0,8): c['elements'][slot]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=bool(slot)),bwe2=dict(lsf=[163,24],gains=[32]))
    yield 'joint_tools',dict(order=2,rate=48000,mixed=True,selection=[0,1,3,8],transform=4,scene=True,drc=False,rich=False),[c,dict(transform_index=1),dict(transform_index=3)]
    c=dict(block=2,elements=[None]*16,transform_index=2); c['elements'][15]=dict(grouping=0x55,bwe2=dict(lsf=[511,511],gains=[]))
    yield 'zero_sfb_unused',dict(order=3,rate=48000,mixed=True,selection=[1,5,10,15],transform=4,scene=False,drc=False,rich=False),[c,dict(transform_index=0),dict(transform_index=3)]


def manifest():
    rows=[]
    for index,(kind,options,cases) in enumerate(sequences()):
        generated=[packet(c,**options) for c in cases]; h=hashlib.sha256(cookie(**options))
        for raw,_ in generated: h.update(len(raw).to_bytes(8,'little')); h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=options,packets=len(cases),input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    rows=[]; spatial_cases=[]
    for order,mixed,selected in ((1,False,[0,1,2,3]),(3,True,[1,5,10,15])):
        options=dict(order=order,rate=44100,mixed=mixed,selection=selected,transform=4,scene=True,drc=True,rich=True)
        a=mixed_basis(8,4,4,ambient=[1,-1,1,-1],cluster=2) if mixed else ambient_basis(0,0)
        a.update(transform_index=0,global_mode=4,drc=dict(header=True,gains=[-3]))
        b=mixed_basis(8,0,3) if mixed else ambient_basis(1,3); b.update(transform_index=3,global_mode=3); b['elements'][-1]={}
        first,_=packet(a,**options); good,t=packet(b,**options)
        bad=bytearray(good); p=t['elements'][-1]['start_bit_offset']+1; bad[p//8]|=1<<(7-p%8)
        def broken(raw,p,width=3):
            value=bytearray(raw)
            for i in range(width): value[(p+i)//8]|=1<<(7-(p+i)%8)
            return value.hex()
        def mode_pos(t): return t['spatial']['start_bit_offset']+3
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])); outer,ot=packet(dict(b,frame_type=2,preroll=child),**options)
        tail=broken(good,t['tail']['ancillary_end_bit_offset']-1,1)
        rows.append(dict(options=options,cookie=cookie(**options).hex(),first=first.hex(),next=good.hex(),embedded_good=outer.hex(),
                         last_element_error=bad.hex(),late_spatial_error=broken(good,mode_pos(t)),late_tail_error=tail,
                         embedded_error=broken(outer,ot['inner_range']['start_bit_offset']+mode_pos(ot['inner'])),outer_after_embedded_error=broken(outer,mode_pos(ot))))
        for transform in (0,1,2,3,4):
            c=dict(b,transform_index=2); wire,truth=spatial(c,0,order=order,mixed=mixed,selection=selected,transform=transform)
            opts=dict(options,transform=transform)
            spatial_cases.append(dict(cookie=cookie(**opts).hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=rows,spatial_cases=spatial_cases)
