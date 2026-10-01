"""Independent variable-count spatial writer and actual per-component descriptor truth."""
import copy,hashlib,json
from spectrum_vectors import bits,pack,TABLES
from channel_vectors import single,scene_bits
from drc_vectors import header as drc_header,payload as drc_payload
from hoa_vectors import bundle as base_bundle,excitation,canonical
from hoa_salient_vectors import format_for

from hoa_dynamic_vectors import dynamic as mapping_wire,maps
from generate_hoa_dynamic_subbands_format import boundaries,generate as dynamic_format
from generate_hoa_salient_subbands_format import PROFILE,generate


def control_format_sha256():
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[1]/'data/hoa-spatial-controls-format-v1.json').read_text())['format_sha256']


def control_values(controls,path):
    result=dict(flag_a=True,flag_b=False,flag_c=False,flag_d=path=='add',flag_e=True,flag_f=True,parameter_0=1)
    result.update(controls or {})
    if not result['flag_b']:result['flag_c']=False
    return result

def controls_extended(control,ambient,salient):
    baseline=control_values(None,'salient');current=dict(control);current['flag_d']=False
    return current!=baseline or (control['flag_d'] and not (ambient and salient))

def frame_configuration_wire(case,origin,order,coefficient_count,counts,component_orders,selected,control,explicit):
    present=case.get('configuration_present',True);wire=bits(present,1);n=(order+1)**2 if coefficient_count is None else coefficient_count
    if present:
        wire+=esc(len(counts))+bits(len(selected)-int(not counts),(n-1).bit_length())
        if control['flag_c']:
            for i,count in enumerate(counts):
                wire+=esc(count-1)
                if coefficient_count is None:wire+=bits(order if component_orders is None else component_orders[i],order.bit_length())
        wire+=bits(explicit,1)
        if explicit:
            limit=n
            for i in range(len(selected)-1,-1,-1):
                wire+=bits(selected[i],(limit-1).bit_length())
                if selected[i]==i:break
                limit=selected[i]+1
    components=[dict(order=order if component_orders is None else component_orders[i],subband_count=c,
                     coefficient_count=n if coefficient_count is not None else ((order if component_orders is None else component_orders[i])+1)**2) for i,c in enumerate(counts)]
    return wire,dict(present=bool(present),start_bit_offset=origin,end_bit_offset=origin+len(wire),salient_components=len(counts),ambient_components=len(selected),component_configurations=components,ambient_indices=selected)


def shape(order,dynamic,coefficient_count=None,controls=None):
    assert order in range(11) and (not dynamic or order==2)
    return 16 if dynamic else (order+1)**2 if coefficient_count is None else coefficient_count


def esc(value,widths=(4,6,8)):
    assert value>=0
    out=''
    for width in widths:
        step=(1<<width)-1;out+=bits(min(value,step),width)
        if value<step:return out
        value-=step
    assert value==0
    return out


def cookie(scene=True,drc=False,rich=False,*,order=3,rate=48000,path='salient',dynamic=False,selection=None,transform=0,method=2,subbands=8,counts=(1,3,4,9,16),spatial_method=0,component_orders=None,_count_fields=None,_order_fields=None,_salient_field=None,ambient_count=None,quantization_bits=6,profile=5,level=0,tce_types=None,coefficient_count=None,controls=None):
    assert 0<=len(counts)<=121 and all(1<=n<=16 for n in counts);ambient=(0 if path=='salient' else 4) if ambient_count is None else ambient_count;mixed=ambient!=0;assert mixed or (selection is None and not transform) or (controls or {}).get('flag_b')
    control=control_values(controls,path)
    n=shape(order,dynamic,coefficient_count);m=(order+1)**2 if coefficient_count is None else coefficient_count
    assert component_orders is None or len(component_orders)==len(counts)
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(profile,6),(level,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+bits(int(coefficient_count is None),1)+bits(control['flag_a'],1)+bits(control['flag_b'],1)+(bits(control['flag_c'],1) if control['flag_b'] else '')+''.join(bits(control[k],1) for k in ('flag_d','flag_e','flag_f'))+bits(int(dynamic),1)
    if dynamic:wire+=bits(method,2)+bits(subbands-1,4)
    wire+=bits(control['parameter_0'],2)+bits(spatial_method,2)+bits(quantization_bits-6,2)+(esc(order) if coefficient_count is None else esc(coefficient_count-1,(7,12,17)))
    at=len(wire);encoded=esc(len(counts));wire+=encoded
    if _salient_field is not None:_salient_field.append(dict(name='components[0].hoa.max_salient_components',value=len(counts),bit_offset=at,bit_length=len(encoded)))
    wire+=bits(ambient-int(not counts),(m-1).bit_length())
    for sc,count in enumerate(counts):
        at=len(wire);encoded=esc(count-1);wire+=encoded
        if _count_fields is not None:_count_fields.append(dict(name=f'components[0].hoa.salient[{sc}].subbands_minus_one',value=count-1,bit_offset=at,bit_length=len(encoded)))
        at=len(wire);suborder=order if component_orders is None else component_orders[sc]
        if coefficient_count is None:wire+=bits(suborder,order.bit_length())
        if _order_fields is not None and coefficient_count is None:_order_fields.append(dict(name=f'components[0].hoa.salient[{sc}].order',value=suborder,bit_offset=at,bit_length=order.bit_length()))
    wire+=bits(int(selection is not None),1)
    if selection is not None:
        assert len(selection)==ambient;limit=m
        for i in range(ambient-1,-1,-1):
            wire+=bits(selection[i],(limit-1).bit_length())
            if selection[i]==i:break
            limit=selection[i]+1
    if ambient>3 or control['flag_b']:
        wire+=bits(int(transform!=0),1)
        if transform:wire+=bits(transform-1,2)
    types=[0]*n if tce_types is None else list(tce_types)
    wire+=esc(len(types),(5,10,16))+''.join(bits(t,3) for t in types)+'0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc:wire+=drc_header(rate,rich=rich,channels=n)
    raw=pack(wire+'000');return len(raw).to_bytes(4,'big')+raw[4:]


def descriptors(counts,mode=0,coefficient=None,component=0,*,order=3,cluster=0,angles=(37,121),component_orders=None,quantization_bits=6,coefficient_count=None,controls=None):
    result=[]
    for sc,count in enumerate(counts):
        m=((order if component_orders is None else component_orders[sc])+1)**2 if coefficient_count is None else coefficient_count;row=[]
        for b in range(count):
            q=[0 if mode==3 else (1<<(quantization_bits-1))]*m
            if coefficient is not None and sc==component:q[coefficient]=(b%5)+1 if mode==3 else (40+(b%5)*4)<<(quantization_bits-6)
            row.append(dict(mode=mode,quantized=q,signs_positive=[bool((sc+b)%2)]*m,cluster=cluster,angles=angles))
        result.append(row)
    return result


def spatial(case,origin,*,order=3,path='salient',selection=None,transform=0,counts=(1,3,4,9,16),spatial_method=0,component_orders=None,ambient_count=None,quantization_bits=6,profile=5,level=0,coefficient_count=None,controls=None):
    control=control_values(controls,path)
    m=(order+1)**2 if coefficient_count is None else coefficient_count;ambient=(0 if path=='salient' else 4) if ambient_count is None else ambient_count;mixed=ambient!=0;selected=(list(range(ambient)) if selection is None else list(selection)) if mixed else []
    wire='';frame_configuration=None
    if control['flag_b']:wire,frame_configuration=frame_configuration_wire(case,origin,order,coefficient_count,counts,component_orders,selected,control,selection is not None)
    if ambient<4:transform=0
    transform_begin=origin+len(wire);index=case.get('transform_index',3) if transform==4 else transform-1 if transform else 3
    if transform==4:wire+=bits(index,2)
    at=origin+len(wire);mode=case.get('global_mode');wire+=bits(int(mode is not None),1)
    if mode is not None:wire+=bits(mode,3)
    rows=case.get('descriptors',descriptors(counts,order=order,component_orders=component_orders,quantization_bits=quantization_bits,coefficient_count=coefficient_count,controls=controls));assert len(rows)==len(counts);result=[]
    for sc,row in enumerate(rows):
        suborder=order if component_orders is None else component_orders[sc];m=(suborder+1)**2 if coefficient_count is None else coefficient_count;fmt=format_for(suborder,quantization_bits)
        assert len(row)==counts[sc]
        for band,spec in enumerate(row):
            begin=origin+len(wire);coding=spec.get('mode',0)
            if mode is None:wire+=bits(coding,3)
            else:assert mode==coding
            q=list(spec.get('quantized',[0 if coding==3 else (1<<(quantization_bits-1))]*m));signs=list(spec.get('signs_positive',[True]*m));coded=set();omitted=[k for k in selected if k<m] if path=='replace' and coding<4 else [];cluster=None;angles=(None,None)
            def huff(book,value):length,code=book[value];return bits(code,length)
            if coding==0:
                for i in range(m):
                    if i not in omitted:wire+=bits(q[i],quantization_bits);coded.add(i)
            elif coding==5:
                angles=spec.get('angles',(37,121));wire+=bits(angles[0],9)+bits(angles[1],8)
                for i in range(4 if control['flag_e'] else 0):wire+=huff(fmt['modes'][1]['codebooks'][0],q[i]);coded.add(i)
            else:
                table=fmt['modes'][coding]
                if coding==4:cluster=spec.get('cluster',0);wire+=bits(cluster,2)
                for book,group in enumerate(table['groups']):
                    if cluster is not None and cluster!=book:continue
                    for i in group:
                        if i>=m or i in omitted:continue
                        wire+=huff(table['codebooks'][book],q[i]);coded.add(i)
                        if table['signs']:wire+=bits(int(signs[i]),1)
            indices=sorted(coded)
            d=dict(component_index=sc,subband_index=band,mode=coding,start_bit_offset=begin,end_bit_offset=origin+len(wire),
                   quantized=[q[i] for i in indices] if mixed else q[:4 if control['flag_e'] else 0] if coding==5 else [q[i] if i in coded else 0 for i in range(m)],
                   signs_positive=([signs[i] for i in indices] if mixed else signs) if coding==3 else [],cluster=cluster,azimuth_degrees=angles[0],elevation_offset_degrees=angles[1])
            if mixed:d.update(coded_coefficient_indices=indices,ambient_omitted_coefficients=omitted)
            result.append(d)
    short=case.get('block',0)==2;grids=[dict(component_index=sc,subband_count=count,subband_ends=boundaries(count,spatial_method,control['flag_f']),lines_per_window=[] if short and not control['flag_f'] else [v//8 if short else v for v in boundaries(count,spatial_method,control['flag_f'])]) for sc,count in enumerate(counts)]
    salient=dict(descriptors=result)
    if not control['flag_f']:salient['unrounded_subbands']=True
    if len(set(counts))==1:salient.update(subband_ends=grids[0]['subband_ends'],lines_per_window=grids[0]['lines_per_window'])
    if short and not control['flag_f']:salient.pop('lines_per_window',None)
    if list(counts)!=[4]*5:salient.update(component_subbands=grids,subband_profile=PROFILE,format_sha256=generate()['format_sha256'])
    if spatial_method:
        from generate_hoa_salient_partition_format import PROFILE as partition_profile,generate as partition_format
        salient.update(partition_method=spatial_method,partition_profile=partition_profile,format_sha256=partition_format()['format_sha256'])
    if counts and (coefficient_count is not None or order>3 or quantization_bits!=6 or len(counts)!=5 or (component_orders is not None and list(component_orders)!=[order]*5)):
        from hoa_component_orders_vectors import component_information
        actual_orders=component_orders or [order]*len(counts)
        salient['component_orders']=component_information(actual_orders,quantization_bits,dimensions=[coefficient_count]*len(counts) if coefficient_count is not None else None,partial=coefficient_count is not None)
        if 1 in actual_orders:salient['order1_profile']='apac-hoa-salient-order1-v1'
    if controls_extended(control,ambient,len(counts)) and 'component_orders' in salient:
        for info in salient['component_orders']:info['numeric_profile']='apac-hoa-spatial-controls-math-v1'
    if not control['flag_f']:salient['format_sha256']=control_format_sha256()
    if counts and quantization_bits!=6:salient.update(quantization_bits=quantization_bits,quantization_profile='apac-hoa-salient-quantization-v1')
    if counts and len(counts)!=5:salient.update(component_count=len(counts),count_profile='apac-hoa-salient-counts-v1')
    side=dict(start_bit_offset=origin,end_bit_offset=origin+len(wire),single_coding_mode=mode is not None,coding_mode=mode,ambient_indices=selected)
    if controls_extended(control,ambient,len(counts)):side['controls']=control
    if frame_configuration is not None:side['frame_configuration']=frame_configuration
    if counts:side['salient']=salient
    if control['flag_b'] or mixed and (selection is not None or transform or (not counts and ambient!=m)):side['ambient']=dict(explicit_selection=selection is not None,selection=selected,transform_config=dict(mode='per_frame') if transform==4 else dict(mode='fixed',index=transform-1) if transform else dict(mode='disabled'),effective_index=index,index_source='frame' if transform==4 else 'cookie' if transform else 'disabled',index_start_bit_offset=transform_begin if transform==4 else None,index_end_bit_offset=at if transform==4 else None)
    return wire,side


def packet(case,scene=True,drc=False,rich=False,*,order=3,rate=48000,path='salient',dynamic=False,selection=None,transform=0,method=2,subbands=8,counts=(1,3,4,9,16),spatial_method=0,component_orders=None,ambient_count=None,quantization_bits=6,profile=5,level=0,coefficient_count=None,controls=None):
    if controls is not None:controls=control_values(controls,path)
    n=shape(order,dynamic,coefficient_count);ambient=(0 if path=='salient' else 4) if ambient_count is None else ambient_count;mixed=ambient!=0;opts=dict(scene=scene,drc=drc,rich=rich,order=order,rate=rate,path=path,dynamic=dynamic,selection=selection,transform=transform,method=method,subbands=subbands,counts=counts,spatial_method=spatial_method,component_orders=component_orders,ambient_count=ambient_count,quantization_bits=quantization_bits,profile=profile,level=level,coefficient_count=coefficient_count,controls=controls)
    declared_opts=opts
    if controls and controls.get('flag_b'):
        opts=dict(opts,**case.get('active',{}));counts=opts['counts'];ambient=opts['ambient_count'];ambient_count=ambient;path=opts['path'];selection=opts.get('selection');component_orders=opts.get('component_orders');transform=opts.get('transform',0)
        path='add' if controls['flag_d'] and counts and ambient else 'replace' if ambient else 'salient';opts['path']=path
    typ=case.get('frame_type',1);wire=bits(typ,2);inner=None;inner_range=None
    if typ==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],**declared_opts);wire+=bits(len(raw),16);wire+='0'*(-len(wire)%8);begin=len(wire);wire+=''.join(bits(v,8) for v in raw);inner_range=dict(start_bit_offset=begin,end_bit_offset=len(wire))
    start=len(wire);block=case.get('block',0);wire+=bits(block,2);elements=[];specs=case.get('elements',[None]*n);assert len(specs)==n
    for i,spec in enumerate(specs):
        begin=len(wire)
        if spec is None:wire+='0';truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire));present=False
        else:
            encoded,truth=single(dict(spec,block=block),0,rate,begin-2);wire+=encoded[:2]+encoded[4:];truth['end_bit_offset']=truth.pop('tns_end_bit_offset');present=True
        direct=not counts and ambient==n and selection is None and not transform and not (controls or {}).get('flag_b')
        configuration=dict(element_index=i,kind='sce',tce_type=0,output_channels=[i] if direct else [])
        if not direct:configuration['transport_channels']=[i]
        truth.update(configuration=configuration,present=present,start_bit_offset=begin);elements.append(truth)
    encoded,side=spatial(case,len(wire),order=order,path=path,selection=selection,transform=transform,counts=counts,spatial_method=spatial_method,component_orders=component_orders,ambient_count=ambient_count,quantization_bits=quantization_bits,profile=profile,level=level,coefficient_count=coefficient_count,controls=controls);wire+=encoded;base_end=len(wire);dyn=None;selected=side['ambient_indices']
    if dynamic:
        rounded=control_values(controls,path)['flag_f'];encoded,dyn=mapping_wire(case,len(wire),method);wire+=encoded;dyn.update(subband_ends=boundaries(subbands,method,rounded),lines_per_window=[] if block==2 and not rounded else [v//8 if block==2 else v for v in boundaries(subbands,method,rounded)],internal_spatial_end_bit_offset=base_end,ambient_recovery_slots=selected,ambient_transport_channels=list(range(ambient)),salient_transport_channels=list(range(ambient,ambient+len(counts))),unused_transport_channels=list(range(ambient+len(counts),n)))
        if not rounded:dyn['unrounded_subbands']=True
        if subbands<8:dyn.update(active_subband_count=subbands,subband_profile='apac-hoa-dynamic-subbands-v1',format_sha256=dynamic_format()['format_sha256'])
        if not rounded:dyn['format_sha256']=control_format_sha256()
        if 'ambient' in side:dyn['internal_ambient']=side.pop('ambient')
        side['end_bit_offset']=len(wire)
    payload_end=len(wire);wire+='0'*(-len(wire)%8);core_end=len(wire)
    if scene:wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{}));spec.setdefault('rich',rich);encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n);wire+=encoded
    wire+='0';end=len(wire)
    if drc:wire+='0'
    return pack(wire),dict(**({'frame_options':{k:v for k,v in opts.items() if v is not None}} if controls else {}),frame_type=typ,common_window=block,elements=elements,spatial=side,dynamic_selection=dyn,inner=inner,inner_range=inner_range,drc=drc_truth,internal_spatial_end_bit_offset=base_end,core_start_bit_offset=start,core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                           tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=end,packet_end_bit_offset=len(pack(wire))*8))


def bundle(root,payloads,priming=0,remainder=0,**opts):
    base_bundle(root,payloads,opts.get('scene',True),opts.get('drc',False),opts.get('rich',False),priming,remainder,order=3 if opts.get('dynamic') else opts.get('order',3),rate=opts.get('rate',48000))
    cfg=cookie(**opts);(root/'cookie.bin').write_bytes(cfg);p=root/'manifest.json';m=json.loads(p.read_text());m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest())
    if opts.get('coefficient_count') is not None:
        n=shape(opts['order'],opts.get('dynamic',False),opts['coefficient_count']);m['file']['format']['channels']=n
        layout=m['file']['layout']['value'];layout['tag']=(190<<16)|n;layout.pop('ambisonic_order',None)
        if int(n**.5)**2==n:layout['ambisonic_order']=int(n**.5)-1
    p.write_text(json.dumps(m))


def basis(counts,coefficient=0,component=0,mode=1,*,order=3,path='salient',dynamic=False,block=0,grouping=0,dense=False,q=1,gain=160,component_orders=None):
    n=shape(order,dynamic);offset=0 if path=='salient' else 4;c=excitation(offset+component,block,grouping,gain,q,order=3 if n==16 else order)
    if dense:
        ends=TABLES['short_offsets' if block==2 else 'long_offsets'];c['elements'][offset+component]['bands']={i:(11,[q]*(ends[i+1]-ends[i]),gain) for i in range(len(ends)-1)}
    c['descriptors']=descriptors(counts,mode,coefficient,component,order=order,cluster=2,component_orders=component_orders);return c


def native_controls():
    specs=[('pure2',2,'salient',False,44100,True,[1,3,4,9,16]),('replace3',3,'replace',False,48000,False,[16,9,4,3,1]),('dynamic-add',2,'add',True,48000,False,[1,3,4,9,16])]
    for name,order,path,dynamic,rate,drc,counts in specs:
        opts=dict(order=order,path=path,dynamic=dynamic,rate=rate,counts=counts,selection=None if path=='salient' else [0,1,3,8],transform=0 if path=='salient' else 4,method=1,subbands=3,scene=True,drc=drc,rich=drc)
        cases=[];k=(order+1)**2-2
        for i,mode in enumerate((0,1,2,4,3,5,3)):
            c=basis(counts,k,i%5,mode,order=order,path=path,dynamic=dynamic);c.update(transform_index=i%4,list_mode=bool(i%2),mappings=maps(i,True if i%2 else False))
            if path!='salient':c['elements'][0]=excitation(0,order=3 if shape(order,dynamic)==16 else 2)['elements'][0]
            if i==0:c['global_mode']=mode
            cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3]);child=dict(basis(counts,k,4,1,order=order,path=path,dynamic=dynamic,block=2,grouping=0x55),transform_index=2)
        cases.append(dict(basis(counts,k,0,3,order=order,path=path,dynamic=dynamic,block=3),frame_type=2,preroll=child,transform_index=1,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
        cases.append(dict(transform_index=3));yield name,opts,cases
    opts=dict(order=2,path='salient',dynamic=False,rate=44100,counts=[16]*5,scene=True,drc=False)
    yield 'uniform16',opts,[dict(basis([16]*5,8,0,0,order=2,dense=True),global_mode=0),basis([16]*5,8,0,3,order=2,dense=True)]


def sequences():
    yield from native_controls()
    covered={(2,'salient',False),(3,'replace',False),(2,'add',True)}
    shapes=[(o,p,False) for o in (2,3) for p in ('salient','replace','add')]+[(2,p,True) for p in ('salient','replace','add')]
    grids=[[2,5,7,11,15],[16,9,4,3,1],[1,6,8,10,14]]
    for i,(order,path,dynamic) in enumerate(shapes):
        if (order,path,dynamic) in covered:continue
        counts=grids[i%3];m=(order+1)**2;opts=dict(order=order,path=path,dynamic=dynamic,rate=44100 if i%2 else 48000,counts=counts,selection=None if path=='salient' else ([0,1,3,8] if order==2 else [1,5,10,15]),transform=0 if path=='salient' else 4,method=i%3,subbands=(1,5,7)[i%3],scene=True,drc=True,rich=True)
        cases=[]
        for j,(block,mode,group) in enumerate(((1,1,0),(2,4,0x7f),(3,3,0))):
            c=basis(counts,m-2,j,mode,order=order,path=path,dynamic=dynamic,block=block,grouping=group);c.update(transform_index=j,list_mode=bool(j%2),mappings=maps(j,bool(j%2)))
            if j==0:c['drc']=dict(header=True,gains=[-3])
            if j==2:c['drc']=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])
            cases.append(c)
        cases.append(dict(transform_index=3));yield f'path-{order}-{path}-{dynamic}',opts,cases
    counts=[1]*5;opts=dict(order=3,path='salient',counts=counts,rate=48000,scene=False)
    yield 'minimum-one',opts,[dict(basis(counts,0,0,0),global_mode=0),basis(counts,15,4,3),{}]
    counts=[16,5,3,1,9];opts=dict(order=3,path='add',counts=counts,rate=44100,selection=[0,3,8,15],transform=4)
    c=basis(counts,12,0,1,path='add',block=2,grouping=0x55)
    for sc,count in enumerate(counts):
        c['elements'][4+sc]=excitation(4+sc,2,(0,0x55,0x7f)[sc%3])['elements'][4+sc]
        for b in range(count):c['descriptors'][sc][b]=descriptors(counts,(sc+b)%6,(sc+b)%16,sc,order=3,cluster=sc%4)[sc][b]
    c['elements'][15]=excitation(15,2,0x7f,gain=255,q=8191)['elements'][15]
    yield 'mixed-modes-groups-unused',opts,[dict(c,transform_index=0),dict(transform_index=3)]
    counts=[1,3,4,9,16];opts=dict(order=2,path='add',dynamic=True,counts=counts,rate=48000,selection=[0,2,4,8],transform=4,method=0,subbands=5)
    c=basis(counts,0,0,0,order=2,path='add',dynamic=True);c['elements']=[None]*16
    for sc in (0,1):
        for d in c['descriptors'][sc]:d['quantized'][0]=0
    for slot,q,gain in ((4,-4096,252),(5,-1,100),(0,-4096,252),(1,-4096,252)):
        c['elements'][slot]=excitation(slot,q=q,gain=gain)['elements'][slot]
    yield 'cross-contribution-residual',opts,[dict(c,transform_index=0,list_mode=True,mappings=maps(7,True)),dict(transform_index=3)]
    from tns_vectors import filter_spec
    from bwe2_vectors import source_case
    counts=[3,16,1,7,9];opts=dict(order=2,path='replace',counts=counts,rate=48000,selection=[0,1,3,8],transform=2)
    c=basis(counts,7,4,1,order=2,path='replace');src=source_case(0,0,upper=True,flat=True,gain=160)
    c['elements'][8]=dict(gain=160,bands=src['left'],max_sfb=49,tns=filter_spec([1,-1],direction=True),bwe2=dict(lsf=[163,24],gains=[32]))
    yield 'joint-tools-zero-bands',opts,[c,dict(elements=[{}]*9),{}]


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        fields=[];cfg=cookie(**opts,_count_fields=fields);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),cookie_counts=fields,input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures(controls=None,spatial_methods=(0,)):
    fixtures=[];spatial_cases=[]
    for name,opts,_ in (list(native_controls())[:3] if controls is None else controls):
        counts=opts['counts'];m=(opts['order']+1)**2
        opts=dict(opts,drc=True,rich=True);common=dict(order=opts['order'],path=opts['path'],dynamic=opts['dynamic'])
        a=dict(basis(counts,m-2,0,4,**common),transform_index=0,drc=dict(header=True,gains=[-3]))
        b=dict(basis(counts,m-2,0,3,**common),transform_index=2,list_mode=True,mappings=maps(2,True));b['elements'][-1]={}
        first,_=packet(a,**opts);good,t=packet(b,**opts)
        def replace(raw,at,encoded):
            wire=''.join(format(v,'08b') for v in raw);return pack(wire[:at]+encoded+wire[at+len(encoded):]).hex()
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]));outer,ot=packet(dict(b,frame_type=2,preroll=child),**opts)
        errors=dict(last_element_error=replace(good,t['elements'][-1]['start_bit_offset']+1,'1'),last_descriptor_error=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),outer_after_embedded_error=replace(outer,ot['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'))
        if opts['dynamic']:
            band=t['dynamic_selection']['mappings'][7];errors['dynamic_error']=replace(good,band['start_bit_offset']+4,bits(band['target_acn_indices'][0],4))
        fields=[];cfg=cookie(**opts,_count_fields=fields);sixteen=next(f for f in fields if f['value']==15)
        fixtures.append(dict(options=opts,cookie=cfg.hex(),cookie_counts=fields,bad_count_cookie=replace(cfg,sixteen['bit_offset'],'1111000001'),first=first.hex(),next=good.hex(),embedded_good=outer.hex(),errors=errors,internal_end_bit=t['internal_spatial_end_bit_offset']))
        for mode in range(6):
            c=basis(counts,m-2,4,mode,**common);wire,truth=spatial(c,0,order=opts['order'],path=opts['path'],counts=counts,selection=opts['selection'],transform=opts['transform'],spatial_method=opts.get('spatial_method',0))
            spatial_cases.append(dict(cookie=cfg.hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    # Low-cost full-line controls: valid descriptor entries, independent grids,
    # all five nonzero carriers; no IMDCT or Cartesian product of five counts.
    restoration=[]
    for spatial_method in spatial_methods:
        for order in (2,3):
            for start in range(1,17,5):
                counts=[min(16,start+i) for i in range(5)];opts=dict(order=order,path='salient',counts=counts)
                if spatial_method:opts['spatial_method']=spatial_method
                for block in (0,2):
                    c=basis(counts,0,0,0,order=order,block=block)
                    for sc in range(5):
                        for b,d in enumerate(c['descriptors'][sc]):d['quantized'][0]=32+sc+b
                        c['elements'][sc]=excitation(sc,block,order=order)['elements'][sc]
                    raw,truth=packet(c,**opts)
                    restoration.append(dict(cookie=cookie(**opts).hex(),packet=raw.hex(),counts=counts,block=block,grids=[boundaries(n,spatial_method) for n in counts]))
    return dict(fixtures=fixtures,spatial_cases=spatial_cases,restoration_cases=restoration)
