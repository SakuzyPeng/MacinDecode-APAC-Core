"""First-order descriptor inputs, including empty wire descriptions and nine-slot mappings."""
import hashlib,json
from hoa_salient_subbands_vectors import cookie,packet,bundle,basis,descriptors,spatial,shape
from hoa_salient_partition_vectors import line_excitation
from hoa_component_orders_vectors import component_information,format_binding as previous_formats
from hoa_salient_vectors import format_for
from hoa_vectors import excitation,canonical
from hoa_dynamic_vectors import maps
from spectrum_vectors import bits,pack

PROFILE='apac-hoa-salient-order1-v1'
NUMERIC_PROFILE='apac-hoa-salient-order1-math-v1'


def control_cases(opts):
    orders=opts['component_orders'];counts=opts['counts'];order=opts['order'];path=opts['path'];dynamic=opts.get('dynamic',False)
    common=dict(order=order,path=path,dynamic=dynamic,component_orders=orders);cases=[];out_order=3 if dynamic else order
    for i,(mode,block) in enumerate(((0,0),(1,0),(2,1),(4,2),(3,3),(5,0),(3,0))):
        sc=i%5;k=min(3,(orders[sc]+1)**2-2);c=basis(counts,k,sc,mode,block=block,grouping=0x55,**common)
        line_excitation(c,sc,7 if block==2 else 56,opts)
        c.update(transform_index=i%4,list_mode=bool(i%2),mappings=maps(i,bool(i%2)))
        if path!='salient':c['elements'][0]=excitation(0,block,0x7f,order=out_order)['elements'][0]
        if i==0:c.update(global_mode=mode,drc=dict(header=True,gains=[-3]))
        cases.append(c)
    child=dict(basis(counts,3,4,1,block=2,grouping=0x7f,**common),transform_index=2)
    cases.append(dict(basis(counts,3,0,3,block=3,**common),frame_type=2,preroll=child,transform_index=1,
                      drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
    cases.append(dict(transform_index=3));return cases


def native_controls():
    specs=[('pure2',2,'salient',False,44100,True,[1,2,1,2,1],[1,3,4,9,16],0,None),
           ('replace3',3,'replace',False,48000,False,[1,2,3,1,3],[16,9,4,3,1],1,[0,1,2,3]),
           ('dynamic-add',2,'add',True,48000,False,[1,2,1,2,1],[4]*5,2,[0,2,4,8])]
    for name,order,path,dynamic,rate,drc,orders,counts,method,selection in specs:
        opts=dict(order=order,path=path,dynamic=dynamic,rate=rate,drc=drc,rich=drc,scene=True,component_orders=orders,counts=counts,
                  spatial_method=method,selection=selection,transform=0 if path=='salient' else 4,method=0,subbands=5)
        yield name,opts,control_cases(opts)
    opts=dict(order=3,path='replace',dynamic=False,rate=44100,drc=False,scene=True,component_orders=[1]*5,counts=[1]*5,spatial_method=0,selection=[0,1,2,3],transform=4)
    rows=descriptors([1]*5,5,3,0,component_orders=[1]*5)
    for row in rows:row[0]['quantized']=[40,44,48,52]
    yield 'fully-omitted',opts,[dict(descriptors=rows,global_mode=5,transform_index=2),dict(global_mode=3,descriptors=descriptors([1]*5,3,component_orders=[1]*5),transform_index=3),dict(global_mode=0,transform_index=1)]


def sequences():
    yield from native_controls()
    opts=dict(order=3,path='salient',dynamic=False,rate=48000,component_orders=[1]*5,counts=[1]*5,spatial_method=0,scene=True)
    c=basis([1]*5,3,4,5,component_orders=[1]*5)
    yield 'all-first-pure',opts,[c,dict(c,frame_type=2,preroll=dict(c,block=2)),{}]
    opts=dict(order=3,path='add',dynamic=False,rate=44100,component_orders=[1,2,3,1,3],counts=[1]*5,spatial_method=2,scene=True,selection=[0,3,4,15],transform=0)
    cases=[]
    for k in (3,4):
        c=basis([1]*5,k,1,0,path='add',component_orders=opts['component_orders']);c['elements']=[None]*16
        c['descriptors'][1][0]['quantized'][k]=0;c['elements'][5]=excitation(5,q=-4096,gain=252)['elements'][5]
        if k<4:c['descriptors'][0][0]['quantized'][k]=0
        c['elements'][4]=excitation(4,q=-1,gain=100)['elements'][4]
        c['elements'][1]=excitation(1,q=-4096,gain=252)['elements'][1]
        c['elements'][15]=excitation(15,q=8191,gain=255)['elements'][15];cases.append(c)
    yield 'boundary-add',opts,cases+[{}]
    opts=dict(order=2,path='replace',dynamic=True,rate=44100,component_orders=[1]*5,counts=[16]*5,spatial_method=1,scene=True,selection=[0,1,2,3],transform=4,method=2,subbands=3)
    c=basis([16]*5,3,0,4,order=2,path='replace',dynamic=True,component_orders=[1]*5,block=2,grouping=0x55)
    c['elements'][0]=excitation(0,2,0x7f)['elements'][0]
    yield 'dynamic-empty',opts,[dict(c,transform_index=0,list_mode=True,mappings=maps(7,True)),dict(global_mode=3,descriptors=descriptors([16]*5,3,order=2,component_orders=[1]*5),transform_index=3,list_mode=False,mappings=maps(1,False)),dict(global_mode=0,transform_index=1)]


def format_binding():
    values=dict(previous_formats()['dependencies'],order1=format_for(1)['tables_sha256'])
    return dict(format_sha256=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest(),dependencies=values)


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        fields=[];cfg=cookie(**opts,_order_fields=fields);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),cookie_orders=fields,input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    fixtures=[];spatial_cases=[]
    for name,original,_ in list(native_controls())[:3]:
        opts=dict(original,drc=True,rich=True);orders=opts['component_orders'];counts=opts['counts'];order=opts['order'];path=opts['path'];dynamic=opts['dynamic']
        common=dict(order=order,path=path,dynamic=dynamic,component_orders=orders)
        a=dict(basis(counts,3,0,4,**common),transform_index=0,drc=dict(header=True,gains=[-3]))
        b=dict(basis(counts,3,0,3,**common),transform_index=2,list_mode=True,mappings=maps(2,True));b['elements'][-1]={}
        first,_=packet(a,**opts);good,t=packet(b,**opts)
        def replace(raw,at,encoded):
            wire=''.join(format(v,'08b') for v in raw);return pack(wire[:at]+encoded+wire[at+len(encoded):]).hex()
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]));outer,ot=packet(dict(b,frame_type=2,preroll=child),**opts)
        errors=dict(last_element_error=replace(good,t['elements'][-1]['start_bit_offset']+1,'1'),
                    last_descriptor_error=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                    embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    outer_after_embedded_error=replace(outer,ot['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'))
        if dynamic:
            mapping=t['dynamic_selection']['mappings'][7];errors['inactive_mapping_error']=replace(good,mapping['start_bit_offset']+4,bits(mapping['target_acn_indices'][0],4))
        fields=[];cfg=cookie(**opts,_order_fields=fields)
        fixtures.append(dict(options=opts,cookie=cfg.hex(),cookie_orders=fields,bad_order_cookie=replace(cfg,fields[0]['bit_offset'],'00'),first=first.hex(),next=good.hex(),embedded_good=outer.hex(),errors=errors,internal_end_bit=t['internal_spatial_end_bit_offset']))
        for mode in range(6):
            c=basis(counts,3,4,mode,**common);wire,truth=spatial(c,0,order=order,path=path,selection=opts['selection'],transform=opts['transform'],counts=counts,spatial_method=opts['spatial_method'],component_orders=orders)
            spatial_cases.append(dict(cookie=cfg.hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    _,opts,cases=list(native_controls())[-1]
    for c in cases:
        wire,truth=spatial(c,0,order=3,path='replace',selection=opts['selection'],transform=4,counts=[1]*5,component_orders=[1]*5)
        spatial_cases.append(dict(cookie=cookie(**opts).hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=fixtures,spatial_cases=spatial_cases)
