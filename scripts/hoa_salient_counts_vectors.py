"""Bounded salient-count inputs; previous five-component manifests are unchanged."""
import hashlib,json
from hoa_salient_subbands_vectors import cookie,packet,bundle,basis,descriptors,spatial,shape
from hoa_order1_vectors import format_binding
from hoa_vectors import excitation,canonical
from hoa_dynamic_vectors import maps
from spectrum_vectors import pack

PROFILE='apac-hoa-salient-counts-v1'
NUMERIC_PROFILE='apac-hoa-salient-counts-math-v1'
STATE_PROFILE='apac-hoa-salient-counts-state-v1'
BACKEND='rust_hoa_salient_counts_sq_drc_off_f64_fft_v1'


def native_controls():
    specs=[('first-four',1,4,'salient',False),('second-nine',2,9,'salient',False),
           ('third-sixteen',3,16,'salient',False),('replace-twelve',3,12,'replace',False),
           ('dynamic-nine',2,9,'add',True),('escape-fifteen',3,15,'salient',False)]
    for index,(name,order,count,path,dynamic) in enumerate(specs):
        counts=[1]*count;orders=[min(order,1+i%3) for i in range(count)]
        opts=dict(order=order,path=path,dynamic=dynamic,rate=44100 if index%2==0 else 48000,
                  counts=counts,component_orders=orders,spatial_method=index%3,scene=True,drc=index==0,
                  selection=None if path=='salient' else [0,1,2,3],transform=0 if path=='salient' else 4,
                  method=2,subbands=3)
        common=dict(order=order,path=path,dynamic=dynamic,component_orders=orders)
        cases=[]
        for mode,block in ((0,0),(4,2),(3,3),(5,0)):
            c=basis(counts,3,count-1,mode,block=block,grouping=0x55,**common)
            c.update(transform_index=mode%4,list_mode=True,mappings=maps(mode,True))
            if mode==0:c.update(global_mode=0,drc=dict(header=True,gains=[-3]))
            if path!='salient':c['elements'][0]=excitation(0,block,0x55,order=3)['elements'][0]
            cases.append(c)
        child=basis(counts,3,0,1,**common)
        cases.append(dict(basis(counts,3,count-1,3,**common),frame_type=2,preroll=child,transform_index=3))
        cases.append({})
        yield name,opts,cases


def sequences():
    yield from native_controls()
    opts=dict(order=3,path='salient',dynamic=False,rate=44100,counts=[16],component_orders=[2],spatial_method=0,scene=True)
    yield 'single-sixteen-bands',opts,[basis([16],8,0,2,component_orders=[2]),basis([16],8,0,3,component_orders=[2]),{}]


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        fields=[];cfg=cookie(**opts,_salient_field=fields);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),cookie_counts=fields,input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    fixtures=[];spatial_cases=[];counts=[]
    for name,opts,cases in native_controls():
        first,_=packet(cases[0],**opts);good,t=packet(cases[2],**opts);outer,ot=packet(cases[-2],**opts)
        def replace(raw,at,encoded):
            wire=''.join(format(v,'08b') for v in raw);return pack(wire[:at]+encoded+wire[at+len(encoded):]).hex()
        errors=dict(last_descriptor=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    tail=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                    embedded=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'))
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),errors=errors))
        # One mixed-mode wire layout per dimension/count boundary; all truncated bits are checked.
        c=cases[1];wire,truth=spatial(c,0,order=opts['order'],path=opts['path'],selection=opts['selection'],transform=opts['transform'],
                                   counts=opts['counts'],spatial_method=opts['spatial_method'],component_orders=opts['component_orders'])
        spatial_cases.append(dict(cookie=cookie(**opts).hex(),block=2,bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    for order in (1,2,3):
        for path in ('salient','replace','add'):
            ambient=0 if path=='salient' else 4;n=(order+1)**2
            for count in range(1,n-ambient+1):
                opts=dict(order=order,path=path,counts=[1]*count,scene=False)
                raw,t=packet(basis([1]*count,4 if path=='replace' else 3,count-1,0,order=order,path=path),**opts)
                counts.append(dict(options=opts,cookie=cookie(**opts).hex(),packet=raw.hex(),last_descriptor=t['spatial']['salient']['descriptors'][-1]))
    # Structurally valid cookies whose core exceeds the available carriers must fail qualification.
    invalid=[cookie(order=order,path='replace',counts=[1]*n,scene=False).hex() for order,n in ((2,6),(3,13))]
    return dict(fixtures=fixtures,spatial_cases=spatial_cases,counts=counts,invalid=invalid)
