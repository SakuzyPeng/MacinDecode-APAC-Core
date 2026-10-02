"""Independent shared-configuration and multiple-ASC wire construction."""
import copy, hashlib, json
from pathlib import Path
from spectrum_vectors import bits, pack, bundle as base_bundle
from hoa_salient_subbands_vectors import cookie as hoa_cookie, esc
from hoa_transport_vectors import packet as hoa_packet
from channel_vectors import cookie as channel_cookie, packet as channel_packet
from packet_vectors import neutral_scene, shifted
from drc_vectors import header as drc_header, payload as drc_payload
from shared_config_tables import RATES

ROOT=Path(__file__).resolve().parents[1]
PROFILE='apac-hoa-shared-configuration-v1'


def component_options(component,rate):
    return dict(component.get('options',{}),rate=rate,scene=False,drc=False)


def parts(component,rate):
    result={};opts=component_options(component,rate)
    if component.get('type',2)==2:hoa_cookie(**opts,_parts=result)
    else:channel_cookie(**opts,_parts=result)
    return result


def scene_wire(count,drc=False,definition=None):
    if definition is not None:
        from hoa_scene_vectors import encode
        return encode(count,drc,definition)
    # The first neutral nonlanguage item names all declared source components.
    original=neutral_scene();start=40
    assert original[start:start+12]==bits(1,6)+bits(0,6)
    result=original[:start]+bits(count,6)+''.join(bits(i,6) for i in range(count))+original[start+12:]
    return ('1'+result[1:]) if drc else result


def effective_components(components,rate):
    result=[];starts=set();cursor=0
    for c in components:
        start=c.get('start',cursor);cursor+=parts(c,rate)['channels']
        if start not in starts:result.append(c);starts.add(start)
    return result


def cookie(components,rate=48000,profile=5,level=0,scene=False,drc=False,rich=False,shared=None,additional=None,total_channels=None,custom=None,extensions=(),drc_sets=None,drc_configuration=None,scene_graph=None,renderer_metadata=None,scene_definition=None,_parts=None):
    shared=shared or {};rows=[parts(c,rate) for c in components];active=effective_components(components,rate);n=total_channels or sum(parts(c,rate)['channels'] for c in active)
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(profile,6),(level,4),(shared.get('flag_a',0),1),(RATES.index(rate),6),(0,6),(n,8),(shared.get('parameter_b',2),8),(shared.get('flag_c',0),1)]
    wire=''.join(bits(v,w) for v,w in fields)+esc(len(components),(3,6,12));cursor=0
    for c,row in zip(components,rows):
        wire+=bits(c.get('start',cursor),8)+row['codec'];cursor+=row['channels']
    wire+=bits(additional is not None,1)
    if additional is not None:
        wire+=esc(len(additional),(3,6,12))+''.join(bits(c['start'],8)+bits(c['type'],3) for c in additional)
    parameters=additional if additional is not None else components
    for c in parameters:wire+=esc(c.get('parameter_0',0),(3,6,9))+esc(c.get('parameter_1',0),(2,8,32))
    wire+=bits(scene_graph is not None,1)
    if scene_graph is not None:
        from hoa_passive_vectors import position
        wire+=bits(len(scene_graph),6)+''.join(position(p)[0] for p in scene_graph)
    wire+=bits(scene,1)+(scene_wire(len(additional) if additional is not None else len(components),drc,scene_definition) if scene else '')+bits(drc,1)
    if drc:
        if drc_sets is not None or drc_configuration is not None:
            from hoa_shared_drc_vectors import header
            wire+=header(drc_sets,rate,n,drc_configuration)
        else:wire+=drc_header(rate,rich=rich,channels=n)
    wire+=bits(renderer_metadata is not None,1)
    if renderer_metadata is not None:
        from hoa_passive_vectors import metadata
        wire+=metadata(renderer_metadata)
    wire+=bits(custom is not None,1)
    if custom is not None:
        body=bits(custom.get('parameter_0',1),16)+esc(custom.get('parameter_1',0),(4,8))+bits(custom.get('variable',False),1)
        body+='0'*(-len(body)%8);body+=''.join(bits(v,8) for v in custom.get('payload',[]));wire+=esc(len(body)//8-1,(4,8,16))+body
    if _parts is not None:_parts['ancillary_end_bit_offset']=len(wire)
    for extension in extensions:
        payload=extension['payload'];wire+='1'+esc(extension['type'],(4,8,16))+esc(len(payload)-1,(4,8,16))+''.join(bits(v,8) for v in payload)
    wire+='0';raw=pack(wire)
    return len(raw).to_bytes(4,'big')+raw[4:]


def packet(case,components,rate=48000,scene=False,drc=False,rich=False,custom=None,drc_sets=None,additional=None,drc_configuration=None,scene_graph=None,scene_definition=None,**unused):
    typ=case.get('frame_type',1);wire=bits(typ,2);inner=None;inner_range=None
    if typ==2:
        wire+='0'+bits('preroll' in case,2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],components,rate,scene,drc,rich,custom,drc_sets,additional,drc_configuration,scene_graph,scene_definition)
            wire+=esc(len(raw),(16,16));wire+='0'*(-len(wire)%8);at=len(wire)
            wire+=''.join(bits(v,8) for v in raw);inner_range=dict(start_bit_offset=at,end_bit_offset=len(wire))
    cores=[];active=components
    specs=case.get('components',[{} for _ in active]);assert len(specs)==len(active)
    for component,spec in zip(active,specs):
        opts=component_options(component,rate);writer=hoa_packet if component.get('type',2)==2 else channel_packet
        raw,t=writer(spec,**opts);start=t['core_start_bit_offset'];end=t['core_payload_end_bit_offset'];at=len(wire)
        wire+=''.join(bits(v,8) for v in raw)[start:end];wire+='0'*(-len(wire)%8)
        truth=shifted(t,at-start);truth['core_end_bit_offset']=len(wire);truth['tail']=None
        cores.append(truth)
    core_end=len(wire)
    graph_truth=None
    if scene_graph is not None:
        from hoa_passive_vectors import position
        start=len(wire);present='graph_updates' in case;wire+=bits(present,1)
        if present:
            updates=case['graph_updates'];previous=case.get('graph_previous',scene_graph)
            for i,initial in enumerate(previous):
                update=updates.get(i,updates.get(str(i)))
                wire+=bits(update is not None,1)
                if update is not None:wire+=position(update,False,position(initial)[1])[0]
        graph_truth=dict(start_bit_offset=start,end_bit_offset=len(wire),update_present=present,position_count=len(scene_graph))
    if scene:wire+=('1'+scene_wire(len(additional) if additional is not None else len(active),drc,case.get('scene_definition',scene_definition)) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{}));spec.setdefault('rich',rich);n=sum(parts(c,rate)['channels'] for c in effective_components(components,rate))
        if drc_sets is not None or drc_configuration is not None:
            from hoa_shared_drc_vectors import payload
            encoded,drc_truth=payload(spec,drc_sets,rate,len(wire),n,drc_configuration)
        else:encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n)
        wire+=encoded
    trimming_at=len(wire);trim=case.get('trimming');wire+=bits(trim is not None,1)
    if trim is not None:wire+=esc(trim[0],(11,16,20))+esc(trim[1],(11,16,20))
    if custom is not None:
        spec=case.get('custom');wire+=bits(spec is not None,1)
        if spec is not None:
            body=esc(spec.get('parameter',0),(10,13,16)) if custom.get('variable',False) else ''
            body+='0'*(-len(body)%8);body+=''.join(bits(v,8) for v in spec.get('payload',[0]))
            wire+=esc(len(body)//8-1,(4,8,16))+body
    ancillary_end=len(wire)
    if drc or custom is not None:wire+='0'
    raw=pack(wire)
    truth=dict(frame_type=typ,components=cores,inner=inner,inner_range=inner_range,drc=drc_truth,
                    core_end_bit_offset=core_end,trimming_bit_offset=trimming_at,ancillary_end_bit_offset=ancillary_end,packet_end_bit_offset=len(raw)*8)
    if graph_truth is not None:truth['scene_graph']=graph_truth
    return raw,truth


def bundle(root,payloads,priming=0,remainder=0,**opts):
    base_bundle(root,payloads,opts.get('rate',48000));cfg=cookie(**opts);(root/'cookie.bin').write_bytes(cfg)
    n=opts.get('total_channels') or sum(parts(c,opts.get('rate',48000))['channels'] for c in effective_components(opts['components'],opts.get('rate',48000)))
    p=root/'manifest.json';manifest=json.loads(p.read_text());f=manifest['file'];f['format']['channels']=n
    f['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest());f['layout']=dict(value=None,error=None)
    f['packet_table']['value']=dict(valid_frames=1024*len(payloads)-priming-remainder,priming_frames=priming,remainder_frames=remainder)
    p.write_text(json.dumps(manifest))


def ambient(n=4,**opts):
    order=0
    while (order+1)**2<n:order+=1
    base=dict(order=order,counts=[],ambient_count=n,path='replace')
    if (order+1)**2!=n:base['coefficient_count']=n
    return dict(type=2,options=dict(base,**opts))


def marked(component,gain=192,block=0):
    opts=component.get('options',{});k=component.get('type',2)
    if k==0:
        from channel_vectors import excitation
        return excitation(opts['channels'],opts['channels']-1,(block,0x55 if block==2 else 0),gain)
    n=parts(component,48000)['channels'];types=opts.get('tce_types',[0]*n);elements=[];slot=0
    for kind in types:
        band=lambda i:{0:(1,[1,0,0,0],gain+i*4)}
        if kind==6:elements.append(dict(parameter=3,payload=[123]));continue
        if kind==1:elements.append(dict(independent=False,gain=gain,grouping=0x55,left=band(slot),right=band(slot+1),cac_gain=9));slot+=2
        else:elements.append(dict(gain=gain+slot*4,grouping=0x55,bands=band(slot)));slot+=1
    return dict(block=block,elements=elements)


def rate_case(rate,block,tool):
    from spectrum_vectors import TABLES
    from shared_config_tables import offsets
    from tns_vectors import filter_spec
    off=offsets(rate,block==2,TABLES);full=len(off)-1
    signal=lambda band,gain:{band:(1,[1,0,0,0],gain)}
    if tool=='last':
        pair=dict(independent=True,gain=168,left=signal(full-1,168),right=signal(0,172),max_sfb=full,grouping=0x55)
        single=dict(gain=172,bands=signal(full-1,172),max_sfb=full,grouping=0x55)
    elif tool=='tns':
        q=[0]*(7 if block==2 else 12);q[-1]=1;spec=filter_spec(q,full,window=7 if block==2 else 0)
        pair=dict(gain=168,left=signal(0,168),right=signal(full-1,172),max_sfb=full,cac_gain=9,left_tns=spec,right_tns=spec,grouping=0x55)
        single=dict(gain=172,bands=signal(0,172),max_sfb=full,tns=spec,grouping=0x55)
    else:
        line=16 if block==2 else 128;band=next(i for i in range(full) if off[i]<=line<off[i+1]);maximum=band+1
        pair=dict(gain=168,left=signal(band,168),right=signal(band,172),max_sfb=maximum,cac_gain=9,left_bwe2=dict(lsf=[17,29]),bwe2_flags=[True,False],grouping=0x55)
        single=dict(gain=172,bands=signal(band,172),max_sfb=maximum,bwe2=dict(lsf=[17,29]),grouping=0x55)
    return dict(block=block,elements=[pair,single,dict(gain=176,bands=signal(0,176),grouping=0x55)])


def sequences():
    from hoa_salient_subbands_vectors import descriptors
    from hoa_source_layout_vectors import options as source_options
    for rate in RATES:
        cs=[ambient(4,tce_types=[1,0,3])];opts=dict(components=cs,rate=rate,drc=True,rich=True)
        cases=[dict(components=[rate_case(rate,b,t)]) for t,b in [('last',0),('tns',1),('bwe',2),('last',2),('tns',2),('bwe',3)]]
        cases+=[dict(frame_type=2,preroll=cases[2],components=[rate_case(rate,0,'tns')]),{}]
        yield 'rate-tools-'+str(rate),opts,cases
        source=source_options(4,labels=[196608+i for i in range(9)],parameter=2,dynamic=True,counts=[2],ambient_count=2,path='add',scene=False,tce_types=[1,0],remapping=[1,2,0],remapping_tail=[15]*6,subbands=3)
        cs=[dict(type=2,options={k:v for k,v in source.items() if k not in ('rate','scene','drc')})];opts=dict(components=cs,rate=rate)
        cases=[]
        for block,mode in [(0,0),(1,1),(2,3),(3,2)]:
            case=marked(cs[0],168,block);case.update(descriptors=descriptors([2],mode,3,order=1),list_mode=block%2==0,mappings=[[(j+block)%9 for j in range(4)] for _ in range(8)]);cases.append(dict(components=[case]))
        yield 'rate-spatial-'+str(rate),opts,cases+[{}]
    pairs=[('pair',[ambient(1),ambient(4)]),('hoa-channel',[ambient(4),dict(type=0,options=dict(channels=6))]),('channel-hoa',[dict(type=0,options=dict(channels=2)),ambient(5)]),('reverse-starts',[dict(ambient(1),start=4),dict(ambient(4),start=0)])]
    for name,cs in pairs:
        opts=dict(components=cs,scene=True,drc=True,rich=True,shared=dict(flag_a=True,parameter_b=1))
        cases=[dict(components=[marked(c,168+4*i,b) for i,c in enumerate(cs)],scene_update=b==2) for b in (0,1,2,3)]
        cases.append(dict(frame_type=2,preroll=cases[2],components=[marked(c,180,0) for c in cs]));cases.append({})
        yield 'stream-'+name,opts,cases
    for name,starts in [('alias',[0,0]),('alias-triple',[0,0,0]),('alias-followed',[0,0,1])]:
        cs=[dict(ambient(1),start=i) for i in starts];opts=dict(components=cs)
        cases=[dict(components=[marked(c,168+i*4,b) for i,c in enumerate(cs)]) for b in (0,2,3)]+[{}]
        yield 'stream-'+name,opts,cases
    for kind in range(6):
        cs=[ambient(4)];opts=dict(components=cs,additional=[dict(start=1,type=kind,parameter_0=70,parameter_1=258)])
        yield 'additional-'+str(kind),opts,[dict(components=[marked(cs[0],172,b)]) for b in (0,2,3)]+[{}]
    cs=[dict(ambient(4),parameter_0=581,parameter_1=4294967553)];opts=dict(components=cs,shared=dict(flag_a=True,parameter_b=0))
    yield 'shared-fields',opts,[dict(components=[marked(cs[0],172,b)]) for b in (0,2,3)]+[{}]

    for variable in (False,True):
        cs=[ambient(1),dict(type=0,options=dict(channels=2))];opts=dict(components=cs,custom=dict(parameter_1=270,variable=variable,payload=[0,165,255]),drc=True,rich=True,scene=True)
        cases=[dict(components=[marked(c,168+i*4) for i,c in enumerate(cs)],custom=dict(parameter=index,payload=[index%256,0,255])) for index in (0,1023,9214,74749)]
        cases+=[dict(frame_type=2,preroll=cases[1],components=[marked(c,176,2) for c in cs]),{}]
        yield 'auxiliary-variable-'+str(int(variable)),opts,cases
    extensions=[dict(type=k,payload=[165,0,255]) for k in (0,1,2,4,15,270,65805)]+[dict(type=3,payload=[0]*5)]
    cs=[ambient(4)];opts=dict(components=cs,extensions=extensions)
    yield 'opaque-extensions',opts,[dict(components=[marked(cs[0],168,b)]) for b in (0,2,3)]+[{}]

    drc_configs=[('profiles',[dict(profile=0),dict(profile=1),dict(profile=2),dict(profile=3)]),('bands',[dict(bands=3)]),('spline',[dict(profile=2,interpolation=False)]),('full',[dict(full=True,delta=32,aligned=True)]),('default',[dict(delta=None)]),('step3',[dict(delta=3,aligned=True)])]
    for name,sets in drc_configs:
        from hoa_shared_drc_vectors import sequences as gain_sequences
        cs=[ambient(4),dict(type=0,options=dict(channels=2))];opts=dict(components=cs,drc=True,drc_sets=sets)
        cases=[]
        for k in range(3):
            specs=[]
            for _,s in gain_sequences(sets,48000).values():
                initial=0 if s.get('profile',0)==0 else -8
                specs.append(dict(mode=1,gains=[initial,initial+1,initial-1],slopes=[k,7,14-k]))
            cases.append(dict(components=[marked(c,168+i*4,2 if k==1 else 0) for i,c in enumerate(cs)],drc=dict(sequences=specs,header=k==1)))
        cases+=[dict(frame_type=2,preroll=cases[1],components=[marked(c,172) for c in cs]),{}]
        yield 'shared-drc-'+name,opts,cases

    for name,cs,additional in [
        ('alias-scene',[dict(ambient(1),start=i) for i in (1,0,0)],None),
        ('alias-scene-large',[dict(ambient(4),start=1),dict(ambient(4),start=1),dict(ambient(1),start=0)],None),
        ('split-source',[ambient(1),ambient(4)],[dict(start=2,type=2),dict(start=0,type=2)]),
        ('additional-whole',[dict(ambient(1),start=4),dict(ambient(4),start=0)],[dict(start=0,type=0)])]:
        opts=dict(components=cs,scene=True,additional=additional)
        yield 'stream-'+name,opts,[dict(components=[marked(c,168+4*i,b) for i,c in enumerate(cs)],scene_update=b==2) for b in (0,2,3)]+[{}]
    for n in (122,255):
        cs=[ambient(121),ambient(1)] if n==122 else [ambient(121),ambient(121),ambient(9),ambient(4)]
        opts=dict(components=cs,profile=0);parts_list=[]
        for c in cs:
            count=parts(c,48000)['channels'];elements=[None]*count;elements[-1]=dict(gain=172,bands={0:(1,[1,0,0,0],172)});parts_list.append(dict(elements=elements))
        yield 'stream-output-'+str(n),opts,[dict(components=parts_list),dict(components=[dict(c,block=2) for c in parts_list]),dict(components=[dict(c,block=3) for c in parts_list]),{}]

    for rate in (96000,48000,7350):
        from tns_vectors import filter_spec
        cs=[ambient(1)];opts=dict(components=cs,rate=rate);cases=[]
        for encoded in (12,13,31):
            spec=filter_spec([1]+[0]*11,49);spec[0]['filters'][0]['encoded_order']=encoded
            cases.append(dict(components=[dict(elements=[dict(gain=172,bands={0:(1,[1,0,0,0],172)},tns=spec)])]))
        yield 'tns-order-alias-'+str(rate),opts,cases+[{}]

    configs=[('no-coefficients',dict(coefficients=[])),('no-gains',dict(coefficients=[dict(sets=[])])),('other-location',dict(coefficients=[dict(location=2,sets=[{}])])),('later-location',dict(coefficients=[dict(location=2,sets=[{}]),dict(location=1,sets=[dict(profile=1)])])),('duplicate-location',dict(coefficients=[dict(sets=[dict(profile=1)]),dict(sets=[dict(profile=2)])])),('empty-cookie',dict(header_present=False)),('metadata-cookie',dict(metadata_only=True))]
    for name,config in configs:
        cs=[ambient(1)];opts=dict(components=cs,drc=True,drc_configuration=config)
        cases=[dict(components=[marked(cs[0],172,b)]) for b in (0,2,3)]+[{}]
        if name=='empty-cookie':
            fresh=dict(coefficients=[dict(sets=[dict(profile=1)])]);cases[1]['drc']=dict(header=True,configuration=fresh,configuration_changed=True)
            for case in cases[2:]:case['drc']=dict(configuration=fresh)
        yield 'shared-drc-'+name,opts,cases
    for frames,step in ((1,64),(128,64),(2048,2048),(32768,64)):
        config=dict(coefficients=[dict(frame_samples=frames,sets=[dict(delta=step)])]);cs=[ambient(1)];opts=dict(components=cs,drc=True,drc_configuration=config)
        yield 'shared-drc-frame-'+str(frames),opts,[dict(components=[marked(cs[0],172)]),{}]
    cs=[ambient(1)];base=dict(coefficients=[dict(sets=[dict(delta=32)])]);changed=dict(coefficients=[dict(sets=[dict(delta=3,aligned=True),dict(profile=2)])])
    opts=dict(components=cs,drc=True,drc_configuration=base)
    first=dict(components=[marked(cs[0],172)]);change=dict(components=[marked(cs[0],176,2)],drc=dict(header=True,configuration=changed,configuration_changed=True))
    final=dict(components=[marked(cs[0],176)],drc=dict(configuration=changed,extensions=[dict(type=1,bits='101'),dict(type=15,bits='0'*2048)]))
    yield 'shared-drc-changing-configuration',opts,[first,change,final,dict(drc=dict(configuration=changed))]
    opts=dict(components=cs,drc=True,drc_sets=[dict(profile=1)]*63)
    yield 'shared-drc-63-sequences',opts,[dict(components=[marked(cs[0],172)]),{}]
    opts=dict(components=cs,drc=True,drc_sets=[dict(bands=15)])
    yield 'shared-drc-15-bands',opts,[dict(components=[marked(cs[0],172)]),{}]
    opts=dict(components=cs,drc=True,drc_sets=[dict(delta=1)])
    case=dict(components=[marked(cs[0],172)],drc=dict(sequences=[dict(mode=1,frame_end=False,gains=list(range(256)),times=[1]*256)]))
    yield 'shared-drc-256-nodes',opts,[case,{}]

    cs=[ambient(1)];opts=dict(components=cs)
    base=dict(components=[marked(cs[0],172)])
    yield 'trimming-declarations',opts,[dict(base,trimming=(1,2)),base,dict(base,trimming=(1024,0)),dict(base,trimming=(0,1024)),dict(base,trimming=(0,0)),{}]
    opts=dict(components=cs,drc=True)
    yield 'legacy-future-gain-nodes',opts,[dict(base,drc=dict(mode=1,frame_end=False,gains=[0,0],times=[32,32])),dict(base,drc=dict(mode=1,frame_end=False,gains=[0],times=[45])),{}]

    cs=[ambient(1)]
    for name,extra in [('layout-unsignalled',dict(layout={})),('layout-cicp',dict(layout=dict(defined=1))),('layout-explicit',dict(layout=dict(defined=0,positions=[1]))),('downmix',dict(downmix=[dict(id=1,channels=1,layout=1,coefficients=[15])]))]:
        cfg=dict(coefficients=[dict(sets=[{}])],**extra);opts=dict(components=cs,drc=True,drc_configuration=cfg)
        yield 'shared-drc-'+name,opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176)],drc=dict(header=True)),{}]

    from hoa_shared_drc_vectors import metadata_controls
    cs=[ambient(4)]
    for name,extra in metadata_controls():
        cfg=dict(coefficients=[dict(sets=[{}])],**extra);opts=dict(components=cs,drc=True,drc_configuration=cfg)
        yield 'shared-drc-metadata-'+name,opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176,2)],drc=dict(header=True)),dict(components=[marked(cs[0],174,3)]),{}]

    from hoa_passive_vectors import controls as passive_controls
    cs=[ambient(4)]
    for name,metadata in passive_controls():
        opts=dict(components=cs,renderer_metadata=metadata)
        yield 'passive-'+name,opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176,2)],trimming=(1,2)),{}]
    opts=dict(components=cs,scene_graph=[])
    yield 'scene-graph-empty',opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176)],graph_updates={},trimming=(1,2)),{}]
    graph=[dict(parent_dynamic=True,range_dynamic=True,position=[1,2,3],rotation=[255,127,127,127])]
    opts=dict(components=cs,scene_graph=graph)
    polar=dict(graph[0],polar=True)
    yield 'scene-graph-position-history',opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176,2)],graph_updates={0:dict(parent=0,range=3,position=[1,2,3],polar=True)}),dict(components=[marked(cs[0],174,3)],graph_previous=[polar],graph_updates={0:dict(position=[0,0,0],position_delta=True,rotation=[0,0,0,0],rotation_delta=True)}),dict(components=[marked(cs[0],175)],graph_updates={0:dict(position=[1,2,3])}),{}]
    for precision in (0,4,12,15):
        opts=dict(components=cs,scene_graph=[dict(position_precision=precision,rotation_precision=precision,position=[1,2,3])])
        yield 'scene-graph-precision-'+str(precision),opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176)],graph_updates={0:dict(position=[1,2,3])}),{}]
    graph=[dict(parent_dynamic=True,range_dynamic=True,position=[1,2,3]),dict(parent_dynamic=True,parent=1,position=[0,1,2])]
    opts=dict(components=cs,scene_graph=graph)
    yield 'scene-graph-parent-history',opts,[dict(components=[marked(cs[0],172)]),dict(components=[marked(cs[0],176)],graph_updates={1:dict(parent=0,position=[1,2,3])}),dict(components=[marked(cs[0],174)],graph_updates={1:dict(parent=1,position=[1,2,3])}),{}]
    cs=[ambient(1),dict(type=0,options=dict(channels=2))]
    opts=dict(components=cs,scene_graph=[dict(parent_dynamic=True,position=[1,2,3])],renderer_metadata={},scene=True,drc=True,custom=dict(variable=True))
    core=[marked(cs[0],172),dict(elements=[dict(left={0:(1,[1,0,0,0],172)},right={0:(1,[0,1,0,0],174)})])]
    base=dict(components=core,graph_updates={0:dict(position=[2,3,4])},scene_update=True,drc=dict(gains=[-8]),trimming=(1,2),custom=dict(parameter=1024,payload=[165,60]))
    yield 'scene-graph-combined',opts,[base,dict(base,frame_type=2,preroll=base),base,{}]

    from hoa_scene_vectors import variants as scene_variants
    cs=[ambient(1),ambient(4)]
    for name,definition in scene_variants(2):
        if name in ('no-parameters','split-items','category-split','different-languages') or (name.startswith('controls-') and name.endswith('-0')):continue
        if name.startswith('parameter-1-') and name!='parameter-1-256':continue
        opts=dict(components=cs,scene=True,scene_definition=definition)
        yield 'neutral-scene-'+name,opts,[dict(components=[marked(c,172+8*i) for i,c in enumerate(cs)]),dict(components=[marked(c,176+8*i) for i,c in enumerate(cs)],scene_update=True),{}]


def manifest():
    rows=[]
    for index,(name,opts,cases) in enumerate(sequences()):
        config=cookie(**opts);h=hashlib.sha256(config);truth=[]
        for case in cases:
            raw,t=packet(case,**opts);h.update(len(raw).to_bytes(8,'little'));h.update(raw);truth.append(t)
        rows.append(dict(index=index,name=name,options=json.loads(json.dumps(opts)),packets=len(cases),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(truth,sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    result=[]
    for name,opts,cases in sequences():
        cfg=cookie(**opts);first,first_truth=packet(cases[0],**opts);good_case=copy.deepcopy(cases[-2])
        if good_case.get('drc',{}).get('configuration') is not None:good_case['drc'].update(header=True,configuration_changed=True)
        if 'graph_previous' in good_case:
            good_case.pop('graph_previous')
            good_case['graph_updates']={i:dict(parent=p.get('parent',0),range=p.get('range',0),position=[0,0,0],polar=p.get('polar',False)) for i,p in enumerate(opts['scene_graph'])}
        good,t=packet(good_case,**opts);at=t['trimming_bit_offset'];wire=''.join(bits(v,8) for v in good)
        bad=pack(wire[:at]+'1'+esc(1024,(11,16,20))+esc(1,(11,16,20)));n=opts.get('total_channels') or sum(parts(c,opts.get('rate',48000))['channels'] for c in effective_components(opts['components'],opts.get('rate',48000)))
        first_wire=''.join(bits(v,8) for v in first);at=first_truth['components'][-1]['elements'][0]['start_bit_offset']+1
        late=pack(first_wire[:at]+'1'+first_wire[at+1:])
        record=dict(name=name,cookie=cfg.hex(),first=first.hex(),required_bytes=(first_truth['ancillary_end_bit_offset']+7)//8,good=good.hex(),bad=bad.hex(),late=late.hex(),channels=n,rate=opts.get('rate',48000),declared_components=len(opts['components']))
        graph=opts.get('scene_graph')
        if graph and graph[0].get('parent_dynamic'):
            invalid=copy.deepcopy(cases[0]);invalid['graph_updates']={0:dict(parent=len(graph)+1)}
            record['bad_graph']=packet(invalid,**opts)[0].hex()
        result.append(record)
    return dict(fixtures=result)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    for name,data in [('hoa-shared-vectors-v1.json',manifest()),('hoa-shared-state-v1.json',state_fixtures())]:
        path=ROOT/'data'/name
        if a.check:assert json.loads(path.read_text())==data
        else:path.write_text(json.dumps(data,indent=2)+'\n')
