"""Explicit HOA dimensions and filtered descriptor groups, independent wire truth."""
import hashlib,json,math
from hoa_salient_subbands_vectors import cookie,packet,bundle,descriptors
from hoa_expanded_orders_vectors import format_binding as previous_formats
from hoa_vectors import excitation,canonical
from spectrum_vectors import pack

PROFILE='apac-hoa-partial-domain-v1'
NUMERIC_PROFILE='apac-hoa-partial-domain-math-v1'
STATE_PROFILE='apac-hoa-partial-domain-state-v1'
BACKEND='rust_hoa_partial_domain_sq_drc_off_f64_fft_v1'

def options(n,**extra):
    profile,level=(5,0) if n<=16 else (5,1) if n<=36 else (5,2) if n<=49 else (0,0)
    return dict(order=math.isqrt(n-1),coefficient_count=n,dynamic=False,rate=48000,
                path='salient',counts=[1],ambient_count=0,scene=False,profile=profile,level=level,**extra)

def basis(opts,mode=0,block=0,component=0,coefficient=None):
    n=opts['coefficient_count'];counts=opts['counts'];ambient=opts['ambient_count']
    carrier=ambient+component if counts else ambient-1
    c=excitation(carrier,block,0x55,gain=100,order=opts['order']);c['elements']=c['elements'][:n]
    if counts:
        c['descriptors']=descriptors(counts,mode,n-1 if coefficient is None else coefficient,component,
            order=opts['order'],coefficient_count=n,quantization_bits=opts.get('quantization_bits',6))
    if counts and ambient:c['elements'][0]=excitation(0,block,0x55,gain=100,order=opts['order'])['elements'][0]
    c['transform_index']=mode%4
    return c

def native_controls():
    configs=[('two',2,'salient',0,[1],6),('explicit-square',4,'salient',0,[3],7),
             ('five-replace',5,'replace',3,[1,2],6),('seventeen-add',17,'add',4,[2,1],9),
             ('one-ambient',1,'replace',1,[],6),('five-ambient',5,'replace',5,[],6),
             ('last-dimension',121,'salient',0,[1],8)]
    for name,n,path,ambient,counts,precision in configs:
        opts=options(n);opts.update(path=path,ambient_count=ambient,counts=counts,quantization_bits=precision)
        if ambient>=4:opts['transform']=4
        if name=='five-replace':opts['selection']=[0,2,3]
        if name=='explicit-square':opts['rate']=44100
        cases=[basis(opts,mode,block) for mode,block in ((0,0),(1,1),(2,2),(3,3))]
        cases.append(dict(basis(opts,3),frame_type=2,preroll=basis(opts,1,2)))
        cases.append({})
        yield name,opts,cases

def sequences():
    yield from native_controls()
    opts=options(8);opts.update(counts=[16],spatial_method=2,quantization_bits=9,scene=True,drc=True,rich=True)
    cases=[dict(basis(opts,0),global_mode=0,drc=dict(header=True,gains=[-3])),basis(opts,2,2),
           dict(basis(opts,3),frame_type=2,preroll=basis(opts,1,3)),{}]
    yield 'eight-grids-drc',opts,cases

def format_binding():
    result=previous_formats();rules=dict(maximum_coefficients=121,salient_minimum=2,modes=[0,1,2,3],
        dictionary='containing complete order',groups='filter indices below actual count',
        matrix_direction='rejected when full_order is false')
    result['dependencies']['partial_rules']=hashlib.sha256(json.dumps(rules,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result['format_sha256']=hashlib.sha256(json.dumps(result['dependencies'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result

def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),
            truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def state_fixtures():
    fixtures=[];boundaries=[];invalid_modes=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-2],**opts);wire=''.join(format(v,'08b') for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex()))
    for n in range(1,122):
        opts=options(n)
        if n==1:opts.update(counts=[],ambient_count=1,path='replace')
        raw,_=packet(basis(opts,n%4),**opts)
        boundaries.append(dict(coefficients=n,order=opts['order'],cookie=cookie(**opts).hex(),packet=raw.hex()))
    for n in (2,4,5,17,121):
        opts=options(n);raw,t=packet(basis(opts,0),**opts);at=t['spatial']['salient']['descriptors'][0]['start_bit_offset'];wire=''.join(format(v,'08b') for v in raw)
        for mode in (4,5):invalid_modes.append(dict(cookie=cookie(**opts).hex(),packet=pack(wire[:at]+format(mode,'03b')+wire[at+3:]).hex()))
    return dict(fixtures=fixtures,boundaries=boundaries,invalid_modes=invalid_modes)
