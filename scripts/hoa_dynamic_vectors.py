"""Independent 9-slot to 16-ACN dynamic writer with explicit wire and mapping truth."""
import copy,hashlib,json
from spectrum_vectors import bits,pack
from channel_vectors import single,scene_bits
from drc_vectors import header as drc_header,payload as drc_payload
from hoa_vectors import bundle as base_bundle,excitation,canonical
from hoa_salient_vectors import spatial as salient_spatial,basis as salient_basis
from hoa_mixed_vectors import spatial as mixed_spatial,basis as mixed_basis
from hoa_static_ambient_vectors import spatial as static_spatial
from generate_hoa_dynamic_format import PROFILE,generate


def cookie(scene=True,drc=False,rich=False,*,rate=48000,mixed=False,selection=None,transform=0,method=2):
    assert mixed or (selection is None and transform==0)
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),
            (0,6),(16,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+'1100111'+bits(method,2)+bits(7,4)
    wire+=bits(1,2)+bits(0,2)+bits(0,2)+bits(2,4)+bits(5,4)+bits(4 if mixed else 0,4)
    wire+=(bits(3,4)+bits(2,2))*5+bits(int(selection is not None),1)
    if selection is not None:
        assert len(selection)==4; limit=9
        for i in range(3,-1,-1):
            wire+=bits(selection[i],(limit-1).bit_length())
            if selection[i]==i: break
            limit=selection[i]+1
    if mixed:
        wire+=bits(int(transform!=0),1)
        if transform: wire+=bits(transform-1,2)
    wire+=bits(16,5)+'000'*16+'0'+bits(190,16)+bits(16,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc: wire+=drc_header(rate,rich=rich,channels=16)
    raw=pack(wire+'000'); return len(raw).to_bytes(4,'big')+raw[4:]


def maps(rotation=0,reverse=False):
    rows=[]
    for b in range(8):
        row=sorted((rotation+2*b+i)%16 for i in range(9))
        rows.append(list(reversed(row)) if reverse else row)
    return rows


def dynamic(case,origin,method):
    listed=case.get('list_mode',False); rows=case.get('mappings',maps()); assert len(rows)==8
    wire=bits(int(listed),1); entries=[]
    for b,row in enumerate(rows):
        assert len(row)==9; start=origin+len(wire)
        if listed: wire+=''.join(bits(i,4) for i in row); targets=list(row)
        else:
            assert len(set(row))==9; wire+=''.join(bits(int(i in row),1) for i in range(16)); targets=sorted(row)
        entries.append(dict(subband_index=b,target_acn_indices=targets,start_bit_offset=start,end_bit_offset=origin+len(wire)))
    tables=generate(); block=case.get('block',0)
    return wire,dict(encoding='index_list' if listed else 'bitmap',method=method,subband_ends=tables['long_ends'][method],
                     lines_per_window=tables['short_ends' if block==2 else 'long_ends'][method],start_bit_offset=origin,end_bit_offset=origin+len(wire),mappings=entries)


def packet(case,scene=True,drc=False,rich=False,*,rate=48000,mixed=False,selection=None,transform=0,method=2):
    options=dict(scene=scene,drc=drc,rich=rich,rate=rate,mixed=mixed,selection=selection,transform=transform,method=method)
    typ=case.get('frame_type',1); wire=bits(typ,2); inner=None; inner_range=None
    if typ==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],**options); wire+=bits(len(raw),16); wire+='0'*(-len(wire)%8); start=len(wire)
            wire+=''.join(bits(v,8) for v in raw); inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire); block=case.get('block',0); wire+=bits(block,2); elements=[]
    specs=case.get('elements',[None]*16); assert len(specs)==16
    for i,spec in enumerate(specs):
        start=len(wire)
        if spec is None:
            wire+='0'; truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire)); present=False
        else:
            encoded,truth=single(dict(spec,block=block),0,rate,start-2); wire+=encoded[:2]+encoded[4:]
            truth['end_bit_offset']=truth.pop('tns_end_bit_offset'); present=True
        truth.update(configuration=dict(element_index=i,kind='sce',tce_type=0,output_channels=[],transport_channels=[i]),present=present,start_bit_offset=start); elements.append(truth)
    internal_ambient=None
    if mixed and (selection is not None or transform):
        encoded,side=static_spatial(case,len(wire),order=2,mixed=True,selection=selection,transform=transform)
        internal_ambient=side.pop('ambient')
    else:
        encoded,side=(mixed_spatial if mixed else salient_spatial)(case,len(wire),order=2)
    side['salient']['lines_per_window']=[x//8 if block==2 else x for x in side['salient']['subband_ends']]
    wire+=encoded; base_end=len(wire); encoded,dyn=dynamic(case,len(wire),method); wire+=encoded; payload_end=len(wire); side['end_bit_offset']=len(wire)
    selected=(list(range(4)) if selection is None else list(selection)) if mixed else []
    dyn.update(internal_spatial_end_bit_offset=base_end,ambient_recovery_slots=selected,ambient_transport_channels=list(range(4)) if mixed else [],
               salient_transport_channels=list(range(4,9)) if mixed else list(range(5)),unused_transport_channels=list(range(9 if mixed else 5,16)))
    if internal_ambient is not None: dyn['internal_ambient']=internal_ambient
    wire+='0'*(-len(wire)%8); core_end=len(wire)
    if scene: wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{})); spec.setdefault('rich',rich); encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=16); wire+=encoded
    wire+='0'; end=len(wire)
    if drc: wire+='0'
    raw=pack(wire)
    return raw,dict(frame_type=typ,common_window=block,elements=elements,spatial=side,dynamic_selection=dyn,inner=inner,inner_range=inner_range,drc=drc_truth,
                    core_start_bit_offset=core_start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))


def bundle(root,payloads,scene=True,drc=False,rich=False,priming=0,remainder=0,**options):
    base_bundle(root,payloads,scene,drc,rich,priming,remainder,order=3,rate=options.get('rate',48000))
    cfg=cookie(scene,drc,rich,**options); (root/'cookie.bin').write_bytes(cfg)
    path=root/'manifest.json'; m=json.loads(path.read_text()); m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest()); path.write_text(json.dumps(m))


def basis(slot=0,component=0,mode=1,*,mixed=False,**options):
    c=(mixed_basis if mixed else salient_basis)(slot,component,mode,order=2,**options)
    c['elements'] += [None]*7
    return c


def native_controls():
    for mixed,rate,drc in ((False,44100,True),(True,48000,False)):
        options=dict(rate=rate,mixed=mixed,selection=[0,2,4,8] if mixed else None,transform=4 if mixed else 0,method=2,scene=True,drc=drc,rich=drc)
        cases=[]
        for i,mode in enumerate((0,1,4,3,5,3)):
            c=basis(7,4,mode,mixed=mixed,cluster=2,angles=(37,121)); c.update(mappings=maps(i,reverse=bool(i%2)),list_mode=bool(i%2),transform_index=i%4); cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3])
        child=dict(basis(6,2,4,mixed=mixed,block=2,grouping=0x55,cluster=1),mappings=maps(7,True),list_mode=True,transform_index=2)
        cases.append(dict(basis(5,1,3,mixed=mixed,block=3),mappings=maps(3),transform_index=1,frame_type=2,preroll=child,
                          drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
        cases.append(dict(mappings=maps(6),transform_index=3))
        yield 'mixed-main' if mixed else 'salient-main',options,cases
    for method in (0,1):
        for rate in (44100,48000):
            c=dict(basis(8,4,1,mixed=True,block=2,grouping=0x7f,ambient=[1,-1,1,-1]),mappings=maps(5,True),list_mode=True)
            yield 'bands'+str(method)+'-'+str(rate),dict(rate=rate,mixed=True,selection=[0,1,3,8],transform=2,method=method,scene=True,drc=False,rich=False),[c]


def sequences():
    yield from native_controls()
    from spectrum_vectors import TABLES
    from bwe2_vectors import source_case
    from tns_vectors import filter_spec
    for mixed in (False,True):
        selected=[0,2,4,8] if mixed else []; cases=[]
        for slot in range(9):
            if slot in selected:
                c=excitation(selected.index(slot),order=3)
            else: c=basis(slot,slot%5,1,mixed=mixed)
            c.update(list_mode=True,mappings=maps(slot,True)); cases.append(c)
        yield 'slot_basis_'+str(mixed),dict(rate=48000,mixed=mixed,selection=selected if mixed else None,transform=0,method=2,scene=True,drc=False,rich=False),cases+[{}]
    for method in range(3):
        cases=[]
        for block in (0,2):
            c=basis(8,4,1,block=block,grouping=0x55); offsets=TABLES['short_offsets' if block==2 else 'long_offsets']; ends=generate()['short_ends' if block==2 else 'long_ends'][method]; bands={}
            for point in [0]+[p for end in ends[:-1] for p in (end-1,end)]+[ends[-1]-1]:
                sfb=next(i for i in range(len(offsets)-1) if offsets[i]<=point<offsets[i+1]); q=[0,0]; q[(point-offsets[sfb])%2]=1; bands[sfb]=(11,q,160)
            c['elements'][4]['bands']=bands; c.update(mappings=maps(3,True),list_mode=True); cases.append(c)
        yield 'band_edges_'+str(method),dict(rate=44100 if method==1 else 48000,mixed=False,selection=None,transform=0,method=method,scene=True,drc=False,rich=False),cases+[{}]
    for mixed in (False,True):
        options=dict(rate=44100,mixed=mixed,selection=[0,1,3,8] if mixed else None,transform=4 if mixed else 0,method=0 if mixed else 1,scene=True,drc=True,rich=True)
        cases=[]
        for j,(block,group) in enumerate(((1,0),(2,0),(2,0x55),(2,0x7f),(3,0))):
            c=basis(7,0,(4,3,5,3,1)[j],mixed=mixed,block=block,grouping=group,cluster=1,angles=(37,121))
            c.update(mappings=maps(j,reverse=bool(j%2)),list_mode=bool(j%2),transform_index=j%4)
            if mixed:
                for slot in range(4): c['elements'][slot]=excitation(slot,block,(0,0x55,0x7f,group)[slot] if block==2 else 0,order=3)['elements'][slot]
            cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3]); child=copy.deepcopy(cases[-2]); child.update(drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]),mappings=maps(7,True),list_mode=True)
        cases[-1].update(frame_type=2,preroll=child); cases+=[dict(mappings=maps(4),transform_index=3),{}]
        yield 'windows_drc_embedded_'+str(mixed),options,cases
    for mixed in (False,True):
        c=dict(elements=[None]*16,mappings=maps(4),list_mode=False); start=9 if mixed else 5
        for slot in range(start,16): c['elements'][slot]=excitation(slot,q=(-1 if slot%2 else 1),order=3)['elements'][slot]
        source=source_case(0,0,upper=True,flat=True,gain=160)
        c['elements'][15]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=True),bwe2=dict(lsf=[163,24],gains=[32]))
        yield 'unused_carriers_'+str(mixed),dict(rate=48000,mixed=mixed,selection=None,transform=0,method=1,scene=True,drc=False,rich=False),[c,{},{}]
    c=dict(elements=[None]*16,list_mode=True,mappings=maps(7,True),transform_index=0)
    for slot,q,gain in ((0,4096,252),(1,1,100),(2,-4096,252)): c['elements'][slot]=excitation(slot,q=q,gain=gain,order=3)['elements'][slot]
    yield 'ambient_cancellation',dict(rate=48000,mixed=True,selection=[0,2,4,8],transform=4,method=0,scene=True,drc=False,rich=False),[c,dict(transform_index=3)]
    for listed in (False,True):
        c=basis(6,4,1); c.update(mappings=maps(7),list_mode=listed)
        yield 'equivalent_'+str(listed),dict(rate=44100,mixed=False,selection=None,transform=0,method=0,scene=False,drc=False,rich=False),[c,dict(mappings=maps(2),list_mode=listed),{}]


def manifest():
    rows=[]
    for index,(kind,options,cases) in enumerate(sequences()):
        generated=[packet(c,**options) for c in cases]; h=hashlib.sha256(cookie(**options))
        for raw,_ in generated: h.update(len(raw).to_bytes(8,'little')); h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=options,packets=len(cases),input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    rows=[]; mapping_cases=[]
    for mixed in (False,True):
        options=dict(rate=44100,mixed=mixed,selection=[0,2,4,8] if mixed else None,transform=4 if mixed else 0,method=2,scene=True,drc=True,rich=True)
        a=dict(basis(7,0,4,mixed=mixed,cluster=2),mappings=maps(7,True),list_mode=True,transform_index=0,drc=dict(header=True,gains=[-3]))
        b=dict(basis(7,0,3,mixed=mixed),mappings=maps(2,True),list_mode=True,transform_index=2); b['elements'][15]={}
        first,_=packet(a,**options); good,t=packet(b,**options)
        def replace(raw,pos,encoded):
            wire=''.join(format(v,'08b') for v in raw); return pack(wire[:pos]+encoded+wire[pos+len(encoded):]).hex()
        pos=t['elements'][-1]['start_bit_offset']+1; bad_last=replace(good,pos,'1')
        bad_spatial=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111')
        band=t['dynamic_selection']['mappings'][0]; duplicate=replace(good,band['start_bit_offset']+4,bits(band['target_acn_indices'][0],4))
        bitmap,bt=packet(dict(b,list_mode=False,mappings=maps()),**options); at=bt['dynamic_selection']['mappings'][0]['start_bit_offset']
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])); outer,ot=packet(dict(b,frame_type=2,preroll=child),**options)
        ib=ot['inner']['dynamic_selection']['mappings'][0]; ob=ot['dynamic_selection']['mappings'][0]
        rows.append(dict(options=options,cookie=cookie(**options).hex(),first=first.hex(),next=good.hex(),embedded_good=outer.hex(),last_element_error=bad_last,late_spatial_error=bad_spatial,
                         dynamic_error=duplicate,dynamic_error_bit=band['start_bit_offset']+4,internal_end_bit=t['dynamic_selection']['start_bit_offset'],
                         bitmap_too_few=replace(bitmap,at,'0'),bitmap_too_many=replace(bitmap,at+15,'1'),
                         late_tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                         embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ib['start_bit_offset']+4,bits(ib['target_acn_indices'][0],4)),
                         outer_after_embedded_error=replace(outer,ob['start_bit_offset']+4,bits(ob['target_acn_indices'][0],4))))
    for method in range(3):
        for listed in (False,True):
            c=dict(mappings=maps(3,listed),list_mode=listed,block=2); wire,truth=dynamic(c,0,method)
            options=dict(rate=48000,mixed=False,method=method)
            mapping_cases.append(dict(cookie=cookie(**options).hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=rows,mapping_cases=mapping_cases)
