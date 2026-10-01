"""Independent additive HOA writer: full descriptors, explicit coordinates and boundaries."""
import copy, hashlib, json
from spectrum_vectors import bits, pack
from channel_vectors import single, scene_bits
from drc_vectors import header as drc_header, payload as drc_payload
from hoa_vectors import bundle as base_bundle, excitation, canonical
from hoa_salient_vectors import descriptors, format_for
from hoa_mixed_vectors import spatial as descriptor_wire
from hoa_dynamic_vectors import maps, dynamic as mapping_wire

PROFILE = 'apac-hoa-additive-math-v1'


def shape(order, dynamic):
    assert order in (2, 3) and (not dynamic or order == 2)
    return 16 if dynamic else (order + 1)**2


def cookie(scene=True, drc=False, rich=False, *, order=3, rate=48000, dynamic=False, selection=None, transform=0, method=2):
    n=shape(order,dynamic); slots=(order+1)**2
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),
            (0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+'110111'+bits(int(dynamic),1)
    if dynamic: wire+=bits(method,2)+bits(7,4)
    wire+=bits(1,2)+bits(0,2)+bits(0,2)+bits(order,4)+bits(5,4)+bits(4,4)
    wire+=(bits(3,4)+bits(order,2))*5+bits(int(selection is not None),1)
    if selection is not None:
        assert len(selection)==4; limit=slots
        for i in range(3,-1,-1):
            wire+=bits(selection[i],(limit-1).bit_length())
            if selection[i]==i: break
            limit=selection[i]+1
    wire+=bits(int(transform!=0),1)
    if transform: wire+=bits(transform-1,2)
    wire+=bits(n,5)+'000'*n+'0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc: wire+=drc_header(rate,rich=rich,channels=n)
    raw=pack(wire+'000'); return len(raw).to_bytes(4,'big')+raw[4:]


def spatial(case, origin, *, order=3, selection=None, transform=0):
    selected=list(range(4)) if selection is None else list(selection)
    index=case.get('transform_index',3) if transform==4 else transform-1 if transform else 3
    prefix=bits(index,2) if transform==4 else ''
    # Empty omission set, while transport remains mixed and reports retain actual coded indices.
    wire,side=descriptor_wire(case,origin+len(prefix),order=order,selection=[])
    side.update(start_bit_offset=origin,ambient_indices=selected)
    side['salient']['lines_per_window']=[x//8 if case.get('block',0)==2 else x for x in side['salient']['subband_ends']]
    if selection is not None or transform:
        side['ambient']=dict(explicit_selection=selection is not None,selection=selected,
            transform_config=dict(mode='per_frame') if transform==4 else dict(mode='fixed',index=transform-1) if transform else dict(mode='disabled'),
            effective_index=index,index_source='frame' if transform==4 else 'cookie' if transform else 'disabled',
            index_start_bit_offset=origin if transform==4 else None,index_end_bit_offset=origin+len(prefix) if transform==4 else None)
    return prefix+wire,side


def packet(case, scene=True, drc=False, rich=False, *, order=3, rate=48000, dynamic=False, selection=None, transform=0, method=2):
    n=shape(order,dynamic); options=dict(scene=scene,drc=drc,rich=rich,order=order,rate=rate,dynamic=dynamic,selection=selection,transform=transform,method=method)
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
    encoded,side=spatial(case,len(wire),order=order,selection=selection,transform=transform); wire+=encoded
    selected=list(range(4)) if selection is None else list(selection); dyn=None; base_end=len(wire)
    if dynamic:
        encoded,dyn=mapping_wire(case,len(wire),method); wire+=encoded
        dyn.update(internal_spatial_end_bit_offset=base_end,ambient_recovery_slots=selected,ambient_transport_channels=list(range(4)),
                   salient_transport_channels=list(range(4,9)),unused_transport_channels=list(range(9,n)),recovery_numeric_profile=PROFILE)
        if 'ambient' in side: dyn['internal_ambient']=side.pop('ambient')
        side['end_bit_offset']=len(wire)
    payload_end=len(wire); wire+='0'*(-len(wire)%8); core_end=len(wire)
    if scene: wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{})); spec.setdefault('rich',rich); encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n); wire+=encoded
    wire+='0'; end=len(wire)
    if drc: wire+='0'
    raw=pack(wire)
    additive=dict(combination='add',numeric_profile=PROFILE,coordinate_space='internal_slots' if dynamic else 'acn',selection=selected,
                  salient_transport_channels=list(range(4,9)),ambient_transport_channels=list(range(4)),
                  effective_transform_index=case.get('transform_index',3) if transform==4 else transform-1 if transform else 3,
                  spectral_stage='hoa_recovery_slots_before_dynamic_selection' if dynamic else 'hoa_coefficients_before_synthesis')
    return raw,dict(frame_type=typ,common_window=block,elements=elements,spatial=side,additive=additive,dynamic_selection=dyn,inner=inner,inner_range=inner_range,drc=drc_truth,
                    internal_spatial_end_bit_offset=base_end,core_start_bit_offset=core_start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))


def bundle(root,payloads,scene=True,drc=False,rich=False,priming=0,remainder=0,**options):
    base_bundle(root,payloads,scene,drc,rich,priming,remainder,order=3 if options.get('dynamic') else options.get('order',3),rate=options.get('rate',48000))
    cfg=cookie(scene,drc,rich,**options); (root/'cookie.bin').write_bytes(cfg)
    path=root/'manifest.json'; m=json.loads(path.read_text()); m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest()); path.write_text(json.dumps(m))


def basis(coefficient=0,component=0,mode=1,*,order=3,dynamic=False,ambient=(1,-1,1,-1),block=0,grouping=0,q=1,gain=160,cluster=0,angles=(37,121)):
    n=shape(order,dynamic); source_order=3 if n==16 else 2
    c=excitation(4+component,block,grouping,gain,q,order=source_order)
    for slot,value in enumerate(ambient):
        if value: c['elements'][slot]=excitation(slot,block,grouping,gain,value,order=source_order)['elements'][slot]
    c['descriptors']=descriptors(mode,coefficient,component,order=order,cluster=cluster,angles=angles)
    return c


def native_controls():
    for name,order,dynamic,rate,drc,selection in (
            ('fixed2',2,False,44100,True,[0,1,3,8]),('fixed3',3,False,48000,False,[1,5,10,15]),
            ('dynamic',2,True,48000,False,[0,2,4,8])):
        opts=dict(order=order,dynamic=dynamic,rate=rate,scene=True,drc=drc,rich=drc,selection=selection,transform=4,method=1)
        cases=[]
        for i,mode in enumerate((0,1,2,4,3,5,3)):
            c=basis(selection[i%4],i%5,mode,order=order,dynamic=dynamic,cluster=2)
            c.update(transform_index=i%4,mappings=maps(i,bool(i%2)),list_mode=bool(i%2)); cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3])
        child=dict(basis(selection[0],2,1,order=order,dynamic=dynamic,block=2,grouping=0x55),transform_index=2)
        cases.append(dict(basis(selection[3],4,3,order=order,dynamic=dynamic,block=3),transform_index=1,frame_type=2,preroll=child,
                          drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
        cases.append(dict(transform_index=3))
        yield name,opts,cases


def sequences():
    yield from native_controls()
    for order,dynamic,rate in ((2,False,48000),(3,False,44100),(2,True,44100)):
        opts=dict(order=order,dynamic=dynamic,rate=rate,scene=False,drc=False,selection=None,transform=0,method=2)
        a=basis(0,0,1,order=order,dynamic=dynamic,ambient=(1,0,0,0)); a['global_mode']=1
        a.update(list_mode=True,mappings=maps(7,True))
        yield 'implicit_identity_'+str(order)+'_'+str(dynamic),opts,[a,{}]
    opts=dict(order=3,dynamic=False,rate=48000,scene=True,drc=False,selection=[1,5,10,15],transform=1)
    cases=[]
    for sc in range(5):
        c=basis(0,sc,0,ambient=(0,0,0,0)); c['global_mode']=0
        for sb in range(4): c['descriptors'][sc][sb]['quantized']=[16+(k%3)*16 for k in range(16)]
        cases.append(c)
    for slot in range(4): cases.append(excitation(slot,order=3))
    yield 'core_isolation',opts,cases+[{}]
    opts=dict(order=2,dynamic=True,rate=44100,scene=True,drc=True,rich=True,selection=[0,2,4,8],transform=4,method=0)
    cases=[basis(8,4,2,order=2,dynamic=True),dict(elements=[None]*16),basis(4,0,1,order=2,dynamic=True,ambient=(0,0,0,0)),excitation(2,order=3)]
    for i,c in enumerate(cases): c.update(transform_index=i,mappings=maps(i,True),list_mode=True)
    cases[0]['drc']=dict(header=True,gains=[-3]); cases[1]['drc']=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])
    cases+=[dict(elements=[{}]*16,transform_index=2),dict(transform_index=3)]
    yield 'absence_zero_bands_drc',opts,cases
    opts=dict(order=2,dynamic=True,rate=48000,scene=True,drc=False,selection=[0,2,4,8],transform=4,method=2)
    cases=[]
    for index in range(4):
        c=basis(0,0,0,order=2,dynamic=True,ambient=(0,0,0,0),q=4096,gain=252)
        for sc in range(5):
            for sb in range(4): c['descriptors'][sc][sb]['quantized']=[32]*9
        # Exactly +2^54 salient, +1 salient, then -2^54 ambient: residual 1.
        for sb in range(4): c['descriptors'][0][sb]['quantized'][0]=0; c['descriptors'][1][sb]['quantized'][0]=0
        c['elements'][4]=excitation(4,q=-4096,gain=252,order=3)['elements'][4]
        c['elements'][5]=excitation(5,q=-1,gain=100,order=3)['elements'][5]
        from generate_hoa_static_ambient_tables import SIGNS
        # Matrix row zero, first input is +/- 1/2. Twice the source gives -2^54.
        sign=1 if index==3 else SIGNS[index][0][0]
        c['elements'][0]=excitation(0,q=-sign*4096,gain=252,order=3)['elements'][0]
        if index!=3:
            # Two ambient terms each -2^53; no unsupported sf=256.
            c['elements'][1]=excitation(1,q=-SIGNS[index][1][0]*4096,gain=252,order=3)['elements'][1]
        c.update(transform_index=index,mappings=maps(7,True),list_mode=True); cases.append(c)
    yield 'cross_contribution_residual',opts,cases+[{}]
    from tns_vectors import filter_spec
    from bwe2_vectors import source_case
    c=basis(15,4,1,ambient=(1,-1,1,-1)); source=source_case(0,0,upper=True,flat=True,gain=160)
    c['elements'][8]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=True),bwe2=dict(lsf=[163,24],gains=[32]))
    c['elements'][15]=copy.deepcopy(c['elements'][8])
    a=basis(0,0,0,q=8191,gain=255,ambient=(0,0,0,0)); a['elements'][5]=excitation(5,q=-8191,gain=255,order=3)['elements'][5]; a['descriptors'][1]=copy.deepcopy(a['descriptors'][0])
    yield 'joint_tools_escape_unused',dict(order=3,rate=48000,selection=[0,3,8,15],transform=2),[c,a,{}]
    cases=[basis(10,2,1,block=1),basis(10,2,3,block=2,grouping=0x7f),basis(10,2,3,block=3),{}]
    for slot,group in ((0,0),(1,0x55),(2,0x7f),(4,0),(6,0x55)):
        cases[1]['elements'][slot]=excitation(slot,2,group,order=3)['elements'][slot]
    yield 'mixed_groups_windows',dict(order=3,rate=44100,selection=[0,1,2,3],transform=3),cases
    for method in (0,2):
        c=basis(8,4,1,order=2,dynamic=True,block=2,grouping=0x7f); c.update(mappings=maps(4,True),list_mode=True,transform_index=0)
        yield 'dynamic_bands_'+str(method),dict(order=2,dynamic=True,rate=44100,selection=[0,2,4,8],transform=4,method=method),[c,dict(mappings=maps(2),transform_index=3)]


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
    for order,dynamic in ((2,False),(3,False),(2,True)):
        opts=dict(order=order,dynamic=dynamic,rate=44100,selection=[0,1,3,8],transform=4,scene=True,drc=True,rich=True)
        a=dict(basis(8,0,4,order=order,dynamic=dynamic,cluster=2),transform_index=0,drc=dict(header=True,gains=[-3]))
        b=dict(basis(8,0,3,order=order,dynamic=dynamic),transform_index=2,list_mode=True,mappings=maps(3,True)); b['elements'][-1]={}
        first,_=packet(a,**opts); good,t=packet(b,**opts)
        def replace(raw,pos,encoded):
            wire=''.join(format(v,'08b') for v in raw); return pack(wire[:pos]+encoded+wire[pos+len(encoded):]).hex()
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])); outer,ot=packet(dict(b,frame_type=2,preroll=child),**opts)
        bad=dict(last_element_error=replace(good,t['elements'][-1]['start_bit_offset']+1,'1'),
                 late_spatial_error=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                 late_tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                 embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                 outer_after_embedded_error=replace(outer,ot['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'))
        if dynamic:
            band=t['dynamic_selection']['mappings'][0]; bad['dynamic_error']=replace(good,band['start_bit_offset']+4,bits(band['target_acn_indices'][0],4))
        rows.append(dict(options=opts,cookie=cookie(**opts).hex(),first=first.hex(),next=good.hex(),embedded_good=outer.hex(),errors=bad,
                         internal_end_bit=t['internal_spatial_end_bit_offset']))
        if not dynamic:
            for mode in range(6):
                c=basis(8,0,mode,order=order,cluster=1); wire,truth=spatial(c,0,order=order,selection=opts['selection'],transform=4)
                spatial_cases.append(dict(cookie=cookie(**opts).hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=rows,spatial_cases=spatial_cases)
