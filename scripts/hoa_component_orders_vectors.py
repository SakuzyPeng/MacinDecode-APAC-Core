"""Independent ragged order-2/3 descriptor inputs in a fixed order-3 output domain."""
import hashlib,json
from pathlib import Path
from hoa_salient_subbands_vectors import cookie,packet,bundle,basis,descriptors,spatial
from hoa_salient_partition_vectors import line_excitation
from hoa_salient_vectors import format_for
from hoa_vectors import excitation,canonical
from spectrum_vectors import bits,pack

PROFILE='apac-hoa-component-orders-math-v1'
STATE_PROFILE='apac-hoa-component-orders-state-v1'
BACKEND='rust_hoa_component_orders_sq_drc_off_f64_fft_v1'


def component_information(orders,quantization_bits=6):
    return [dict(component_index=s,order=o,coefficient_count=(o+1)**2,
                 numeric_profile=('apac-hoa-salient-quantization-math-v1' if quantization_bits!=6 else f'apac-hoa-salient-order{o}-math-v1' if o in (1,2) else 'apac-hoa-salient-math-v1'),
                 format_sha256=format_for(o,quantization_bits)['tables_sha256'],**({'quantization_bits':quantization_bits} if quantization_bits!=6 else {})) for s,o in enumerate(orders)]


def control_cases(opts):
    orders=opts['component_orders'];counts=opts['counts'];path=opts['path']
    common=dict(order=3,path=path,component_orders=orders);cases=[]
    for i,(mode,block) in enumerate(((0,0),(1,0),(2,1),(4,2),(3,3),(5,0),(3,0))):
        sc=i%5;k=(orders[sc]+1)**2-2;c=basis(counts,k,sc,mode,block=block,grouping=0x55,**common)
        line_excitation(c,sc,7 if block==2 else 56,opts)
        c['transform_index']=i%4
        if path!='salient':c['elements'][0]=excitation(0,block,0x7f)['elements'][0]
        if i==0:c.update(global_mode=mode,drc=dict(header=True,gains=[-3]))
        cases.append(c)
    child=dict(basis(counts,8,4,1,block=2,grouping=0x7f,**common),transform_index=2)
    cases.append(dict(basis(counts,8,0,3,block=3,**common),frame_type=2,preroll=child,transform_index=1,
                      drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
    cases.append(dict(transform_index=3));return cases


def native_controls():
    specs=[('pure',44100,'salient',True,[2,3,2,3,3],[1,3,4,9,16],0,None),
           ('replace',48000,'replace',False,[3,2,3,2,3],[16,9,4,3,1],1,[1,5,10,15]),
           ('add',48000,'add',False,[2,3,3,2,2],[4]*5,2,[0,8,9,15])]
    for name,rate,path,drc,orders,counts,method,selection in specs:
        opts=dict(order=3,dynamic=False,path=path,rate=rate,drc=drc,rich=drc,scene=True,component_orders=orders,counts=counts,
                  spatial_method=method,selection=selection,transform=0 if path=='salient' else 4)
        yield name,opts,control_cases(opts)
    opts=dict(order=3,dynamic=False,path='salient',rate=48000,drc=False,scene=True,component_orders=[2]*5,counts=[1]*5,spatial_method=0)
    yield 'all-order2',opts,[basis([1]*5,8,4,5,component_orders=[2]*5),{}]


def sequences():
    yield from native_controls()
    for path in ('salient','replace','add'):
        orders=[3,2,3,2,3];counts=[1]*5;opts=dict(order=3,dynamic=False,path=path,rate=44100,component_orders=orders,counts=counts,spatial_method=2,scene=True,
                                             selection=None if path=='salient' else [0,8,9,15],transform=0)
        cases=[]
        for k in (8,9):
            c=basis(counts,k,0,0,path=path,component_orders=orders);c['descriptors'][0][0]['quantized'][k]=0
            c['elements'][0 if path=='salient' else 4]=excitation(0,gain=252,q=-4096)['elements'][0]
            slot=1 if path=='salient' else 5;c['elements'][slot]=excitation(slot,gain=100,q=-1)['elements'][slot]
            if k<9:c['descriptors'][1][0]['quantized'][k]=0
            if path!='salient':c['elements'][1]=excitation(1,gain=252,q=-4096 if path=='add' else 4096)['elements'][1]
            c['elements'][15]=excitation(15,gain=255,q=8191)['elements'][15]
            cases.append(c)
        yield 'boundary-'+path,opts,cases+[{}]


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        fields=[];cfg=cookie(**opts,_order_fields=fields);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),cookie_orders=fields,input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def format_binding():
    from generate_hoa_salient_subbands_format import generate as perceptual
    from generate_hoa_salient_partition_format import generate as partitions
    from generate_hoa_dynamic_format import generate as eight
    from generate_hoa_dynamic_subbands_format import generate as lower
    data=Path(__file__).resolve().parents[1]/'data'
    values=dict(descriptor_math=json.loads((data/'hoa-salient-math-v1.json').read_text())['tables_sha256'],ambient_transform=json.loads((data/'hoa-static-ambient-tables-v1.json').read_text())['format_sha256'],order2=format_for(2)['tables_sha256'],order3=format_for(3)['tables_sha256'],
                spatial_method0=perceptual()['format_sha256'],spatial_methods12=partitions()['format_sha256'],
                lower=eight()['format_sha256'],lower_counts=lower()['format_sha256'])
    return dict(format_sha256=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest(),dependencies=values)


def state_fixtures():
    from generate_hoa_dynamic_subbands_format import boundaries
    fixtures=[];spatial_cases=[];restoration=[]
    for name,original,_ in list(native_controls())[:3]:
        opts=dict(original,drc=True,rich=True);orders=opts['component_orders'];counts=opts['counts'];k=(orders[0]+1)**2-2
        common=dict(order=3,path=opts['path'],component_orders=orders)
        a=dict(basis(counts,k,0,4,**common),transform_index=0,drc=dict(header=True,gains=[-3]))
        b=dict(basis(counts,k,0,3,**common),transform_index=2);b['elements'][-1]={}
        first,_=packet(a,**opts);good,t=packet(b,**opts)
        def replace(raw,at,encoded):
            wire=''.join(format(v,'08b') for v in raw);return pack(wire[:at]+encoded+wire[at+len(encoded):]).hex()
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]));outer,ot=packet(dict(b,frame_type=2,preroll=child),**opts)
        errors=dict(last_element_error=replace(good,t['elements'][-1]['start_bit_offset']+1,'1'),
                    last_descriptor_error=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                    embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    outer_after_embedded_error=replace(outer,ot['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'))
        fields=[];cfg=cookie(**opts,_order_fields=fields)
        fixtures.append(dict(options=opts,cookie=cfg.hex(),cookie_orders=fields,bad_order_cookie=replace(cfg,fields[0]['bit_offset'],'01'),
                             first=first.hex(),next=good.hex(),embedded_good=outer.hex(),errors=errors,internal_end_bit=t['internal_spatial_end_bit_offset']))
        for mode in range(6):
            c=basis(counts,(orders[4]+1)**2-2,4,mode,**common)
            wire,truth=spatial(c,0,order=3,path=opts['path'],counts=counts,selection=opts['selection'],transform=opts['transform'],spatial_method=opts['spatial_method'],component_orders=orders)
            spatial_cases.append(dict(cookie=cfg.hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    patterns=[([2,3,2,3,3],[1,3,4,9,16],0),([3,2,3,2,3],[16,9,4,3,1],1),([2,3,3,2,2],[4]*5,2),([2]*5,[1]*5,0),([2,3,2,3,2],[16]*5,1)]
    for orders,counts,method in patterns:
        opts=dict(order=3,path='salient',component_orders=orders,counts=counts,spatial_method=method)
        for block in (0,2):
            c=basis(counts,0,0,0,component_orders=orders,block=block)
            for sc,row in enumerate(c['descriptors']):
                for b,d in enumerate(row):
                    for k in (0,8,9,15):
                        if k<len(d['quantized']):d['quantized'][k]=32+sc+b
                c['elements'][sc]=excitation(sc,block)['elements'][sc]
            raw,_=packet(c,**opts);restoration.append(dict(cookie=cookie(**opts).hex(),packet=raw.hex(),orders=orders,counts=counts,block=block,grids=[boundaries(n,method) for n in counts]))
    return dict(fixtures=fixtures,spatial_cases=spatial_cases,restoration_cases=restoration)
