"""Actual internal/output HOA domains and independent list/bitmap wire truth."""
import hashlib,json,math
from pathlib import Path
from hoa_salient_subbands_vectors import cookie,bundle,packet as sce_packet,descriptors,dynamic_domain_format_sha256
from hoa_dynamic_vectors import maps
from hoa_controls_vectors import format_binding as previous_formats
from hoa_vectors import excitation,canonical
from spectrum_vectors import bits,pack

PROFILE='apac-hoa-dynamic-domains-v1'
NUMERIC_PROFILE='apac-hoa-dynamic-domains-math-v1'
STATE_PROFILE='apac-hoa-dynamic-domains-state-v1'
BACKEND='rust_hoa_dynamic_domains_sq_drc_off_f64_fft_v2'

def options(m,n,explicit=False,**extra):
    full=not explicit and math.isqrt(m)**2==m
    profile,level=(5,0) if n<=16 else (5,1) if n<=36 else (5,2) if n<=49 else (0,0)
    result=dict(order=math.isqrt(m-1),dynamic=True,output_coefficients=n,profile=profile,level=level,rate=48000,
                counts=[1] if m>1 else [],ambient_count=0 if m>1 else 1,path='salient' if m>1 else 'replace',
                method=2,subbands=8,scene=False)
    if not full:result['coefficient_count']=m
    result.update(extra);return result

def packet(case,**opts):
    if 'tce_types' in opts:
        from hoa_transport_vectors import packet as transported
        return transported(case,**opts)
    return sce_packet(case,**opts)

def basis(opts,mode=0,block=0,rotation=0,listed=True):
    m=opts.get('coefficient_count',(opts['order']+1)**2);n=opts['output_coefficients'];counts=opts['counts'];ambient=opts['ambient_count'];target=m-1 if m<n else n-1
    c=excitation(ambient if counts else 0,block,0x55,gain=100,order=math.isqrt(n-1));c['elements']=c['elements'][:n]
    c['descriptors']=descriptors(counts,mode,target,0,order=opts['order'],component_orders=opts.get('component_orders'),coefficient_count=opts.get('coefficient_count'),quantization_bits=opts.get('quantization_bits',6))
    if m<n:c.update(mappings=maps(n-m+rotation,False,m,n),list_mode=listed)
    return c

def native_controls():
    for name,m,n in [('first-second',4,9),('third-fourth',16,25),('fifth-sixth',36,49),('partial-output',2,3),
                     ('partial-square',5,9),('identity',9,9),('prefix',49,16),('zero-first',1,4),
                     ('nonsquare-output',9,12),('wide-output',16,121)]:
        opts=options(m,n);cases=[]
        for i,(mode,block) in enumerate(((0,0),(1,2),(3,3))):
            cases.append(basis(opts,mode,block,i,bool(i%2)))
        child=basis(opts,1,2,2,True);cases.append(dict(basis(opts,3,0,3,False),frame_type=2,preroll=child));cases.append({})
        yield name,opts,cases
    yield mixed_interactions()

def mixed_interactions():
    opts=options(5,12,counts=[2,3],ambient_count=0,path='add',tce_types=[6,1,3,0],
                 controls=dict(flag_a=False,flag_b=True,flag_c=True,flag_f=False,parameter_0=2),
                 scene=True,drc=True,rich=True,method=1,subbands=3)
    signal={0:(1,[1,0,0,0],100)};cases=[]
    for i,(mode,block) in enumerate(((0,0),(1,2),(3,3))):
        active=dict(counts=[1,2],ambient_count=1,path='add',selection=[0]) if i else dict(counts=[2,3],ambient_count=0,path='salient',selection=None)
        specs=[dict(parameter=256,payload=[1,2]),dict(independent=bool(i%2),gain=100,grouping=0x55,left=signal,right=signal,cac_gain=9),
               dict(gain=100,grouping=0x55,bands=signal),dict(gain=100,grouping=0x55,bands=signal)]
        c=dict(block=block,active=active,elements=specs,list_mode=bool(i%2),mappings=maps(i,False,5,12),
               descriptors=descriptors(active['counts'],mode,4,0,order=2,coefficient_count=5))
        if i==0:c['drc']=dict(header=True,gains=[-3])
        cases.append(c)
    cases.append(dict(cases[-1],frame_type=2,preroll=cases[1]))
    return 'mixed-interactions',opts,cases

def sequences():
    yield from native_controls()
    opts=options(120,121,controls=dict(flag_f=False),subbands=3,method=1,quantization_bits=9)
    yield 'large-independent-mapping',opts,[basis(opts,0),basis(opts,3,2,1,False),dict(basis(opts,2),frame_type=2,preroll=basis(opts,1,2))]
    opts=options(9,2)
    yield 'prefix-two',opts,[basis(opts,0),basis(opts,3,2),{}]
    opts=options(9,16,explicit=True,counts=[2,3],ambient_count=4,path='add',transform=4,subbands=3,method=0,controls=dict(flag_a=False,flag_b=True,flag_c=True,flag_f=False))
    cases=[basis(opts,0),basis(opts,3,2),dict(basis(opts,3),frame_type=2,preroll=basis(opts,2,2))]
    yield 'explicit-square-controls',opts,cases

def format_binding():
    result=previous_formats();result['dependencies']['dynamic_domains']=dynamic_domain_format_sha256()
    result['dependencies']['transport']=hashlib.sha256((Path(__file__).resolve().parents[1]/'data/hoa-transports-format-v1.json').read_bytes()).hexdigest()
    result['format_sha256']=hashlib.sha256(json.dumps(result['dependencies'],sort_keys=True,separators=(',',':')).encode()).hexdigest();return result

def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),
            truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def state_fixtures():
    fixtures=[];mapping=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-2],**opts);wire=''.join(bits(v,8) for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex()))
    for m,n in ((1,2),(2,3),(4,9),(16,17),(25,33),(36,49),(37,65),(120,121),(9,4),(9,9)):
        for listed in ((True,False) if m<n else (True,)):
            opts=options(m,n);raw,t=packet(basis(opts,0,listed=listed),**opts);d=t['dynamic_selection'];errors=[];wire=''.join(bits(v,8) for v in raw)
            if m<n:
                width=(n-1).bit_length();at=d['mappings'][-1]['start_bit_offset']
                if listed:
                    if n<(1<<width):errors.append(pack(wire[:at]+bits(n,width)+wire[at+width:]).hex())
                    if m>1:
                        target=d['mappings'][-1]['target_acn_indices'][0];errors.append(pack(wire[:at+width]+bits(target,width)+wire[at+2*width:]).hex())
                else:
                    for original in ('0','1'):
                        delta=wire[at:at+n].find(original)
                        if delta>=0:errors.append(pack(wire[:at+delta]+str(1-int(original))+wire[at+delta+1:]).hex())
            start,end=d['start_bit_offset'],d['end_bit_offset']
            mapping.append(dict(internal=m,output=n,cookie=cookie(**opts).hex(),packet=raw.hex(),start=start,end=end,errors=errors,
                                mapping_bytes=pack(wire[start:end]+'10101').hex(),bits=end-start,truth=d))
    return dict(fixtures=fixtures,mapping=mapping)
