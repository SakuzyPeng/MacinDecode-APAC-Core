"""Static carrier remapping: independent wire generation and finite graph truth."""
import hashlib,json,math
from pathlib import Path
from hoa_source_layout_vectors import options as source_options
from hoa_salient_subbands_vectors import cookie,bundle,descriptors
from hoa_transport_vectors import packet as transport_packet
from hoa_vectors import canonical
from spectrum_vectors import pack

ROOT=Path(__file__).resolve().parents[1]
PROFILE='apac-hoa-static-remapping-v1'
BACKEND='rust_hoa_static_remapping_sq_drc_off_f64_fft_v2'
STATE_PROFILE='apac-hoa-static-remapping-state-v1'
FORMAT_SHA256=hashlib.sha256((ROOT/'data/hoa-static-remapping-format-v1.json').read_bytes()).hexdigest()


def effective(links):
    n=len(links)
    if not n or any(i<0 or i>=n for i in links):raise ValueError('index outside core')
    if len(set(links))==n:return sorted(range(n),key=lambda source:links[source])
    order=list(range(n))
    # Absorbing finite-state powers determine each transposition endpoint;
    # this does not run the production pointer-following loop.
    for threshold in range(n):
        jump=[i if i<=threshold else links[i] for i in range(n)]
        at=links[threshold];power=n
        while power:
            if power&1:at=jump[at]
            jump=[jump[i] for i in jump];power>>=1
        if at>threshold:raise ValueError('nonterminating graph')
        order=[order[at] if i==threshold else order[threshold] if i==at else order[i] for i in range(n)]
    return order


def mapping_info(opts):
    n=opts['output_coefficients'];wire=opts['remapping']
    return dict(format_profile=PROFILE,format_sha256=FORMAT_SHA256,wire_index_width=(n-1).bit_length(),wire_indices=wire,core_to_transport=effective(wire),ignored_tail=opts.get('remapping_tail',[0]*(n-len(wire))))


def packet(case,**opts):
    raw,truth=transport_packet(case,**opts);mapping=mapping_info(opts)
    def decorate(t):
        t['static_remapping']=mapping
        if t['inner']:decorate(t['inner'])
        active=t.get('frame_options',opts);a=active['ambient_count'];core=a+len(active['counts'])
        physical=lambda slot:mapping['core_to_transport'][slot] if slot<len(mapping['core_to_transport']) else slot
        if t.get('dynamic_selection'):
            d=t['dynamic_selection'];d['ambient_transport_channels']=[physical(i) for i in range(a)];d['salient_transport_channels']=[physical(i) for i in range(a,core)]
            transported=sum(2 if v==1 else 0 if v==6 else 1 for v in opts['tce_types'])
            used={physical(i) for i in range(core)};d['unused_transport_channels']=[i for i in range(transported) if i not in used]
    decorate(truth);return raw,truth


def options(n,k,**extra):
    opts=source_options(n,(190<<16)|n,counts=[],ambient_count=k,path='replace',scene=True)
    opts.update(remapping=list(range(1,k))+[0],remapping_tail=[(1<<(n-1).bit_length())-1]*(n-k))
    opts.update(extra);return opts


def marked(opts,block=0,mode=0,active=None):
    now=dict(opts,**(active or {}));types=opts['tce_types'];core=now['ambient_count']+len(now['counts']);elements=[];slot=0
    for kind in types:
        signal=lambda i:{0:(1,[1,0,0,0],100+4*(i%20))}
        if kind==6:elements.append(dict(parameter=3,payload=[171]));continue
        if kind==1:
            elements.append(dict(independent=block%2==0,gain=100,grouping=0x55,left=signal(slot),right=signal(slot+1),cac_gain=9) if slot<core else None);slot+=2
        else:
            elements.append(dict(gain=100+4*(slot%20),grouping=0x55,bands=signal(slot)) if slot<core else None);slot+=1
    case=dict(block=block,elements=elements)
    if now['counts']:
        m=now.get('coefficient_count',(now['order']+1)**2)
        case['descriptors']=descriptors(now['counts'],mode,m-1,order=now['order'],coefficient_count=now.get('coefficient_count'),quantization_bits=now.get('quantization_bits',6))
    if active:case['active']=active
    return case


def sequences():
    for n in (1,2,4,5,8,16,17,32,33,64,65,121):
        opts=options(n,min(4,n));cases=[marked(opts,block) for block in (0,1,2,3)]
        cases.extend([dict(marked(opts),frame_type=2,preroll=marked(opts,2)),{}])
        yield 'width-'+str(n),opts,cases
    for name,mapping in [('identity',[0,1,2,3]),('duplicate-root',[0,0,2,3]),('duplicate-star',[0,0,0,0])]:
        opts=options(4,4,remapping=mapping);yield name,opts,[marked(opts),marked(opts,2),{}]
    opts=options(121,121);last=marked(opts);last['elements'][:-1]=[None]*120
    yield 'full-core-121',opts,[marked(opts),last,dict(last,block=2),dict(last,block=3),{}]
    for path in ('replace','add'):
        opts=options(9,5,counts=[1,3,2],ambient_count=2,path=path,rate=44100,remapping=[1,2,3,4,0],remapping_tail=[15]*4)
        yield 'mixed-'+path,opts,[marked(opts,block,mode) for block,mode in [(0,0),(1,1),(2,3),(3,2)]]+[{}]
    opts=options(9,5,counts=[2],ambient_count=4,path='add',transform=4)
    yield 'ambient-transform',opts,[dict(marked(opts,index,index%4),transform_index=index) for index in range(4)]+[dict(transform_index=3)]
    opts=options(16,6,counts=[2,1],ambient_count=4,path='replace',controls=dict(flag_b=True,flag_c=True),remapping=[1,2,3,4,5,0],remapping_tail=[15]*10,drc=True,rich=True)
    first=marked(opts);first['drc']=dict(header=True,gains=[-3]);active=dict(counts=[1],ambient_count=6)
    yield 'fixed-prefix-growing-core',opts,[first,marked(opts,1,1,active),marked(opts,2,3,active),marked(opts,2,3,dict(counts=[1],ambient_count=2)),dict(marked(opts,3,2),frame_type=2,preroll=marked(opts,2,1,active)),{}]
    opts=source_options(9,(121<<16)|6,parameter=0,counts=[2,1],ambient_count=2,path='replace',scene=True,remapping=[1,2,3,0],remapping_tail=[7,7],tce_types=[6,1,3,0,0,0])
    yield 'source-matrix-transports',opts,[marked(opts,block,mode) for block,mode in [(0,0),(1,1),(2,3),(3,2)]]+[dict(marked(opts),frame_type=2,preroll=marked(opts,2,1)),{}]
    opts=source_options(4,labels=[196608+i for i in range(9)],parameter=2,dynamic=True,counts=[1,2],ambient_count=1,path='replace',scene=True,remapping=[0,0,0],remapping_tail=[15]*6,subbands=3)
    cases=[marked(opts,block,mode) for block,mode in [(0,0),(1,1),(2,3),(3,2)]]
    for i,c in enumerate(cases):c.update(list_mode=True,mappings=[[(j+i)%9 for j in range(4)] for _ in range(8)])
    yield 'dynamic-n3d',opts,cases+[{}]


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);h=hashlib.sha256(cfg);truth=[]
        for c in cases:
            raw,t=packet(c,**opts);h.update(len(raw).to_bytes(8,'little'));h.update(raw);truth.append(t)
        rows.append(dict(index=index,kind=kind,options=opts,packets=len(cases),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical(truth),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,format_sha256=FORMAT_SHA256,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    result=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-2],**opts);wire=''.join(format(v,'08b') for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        result.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex(),mapping=mapping_info(opts)))
    return dict(fixtures=result)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    for name,data in [('hoa-remapping-vectors-v1.json',manifest()),('hoa-remapping-state-v1.json',state_fixtures())]:
        path=ROOT/'data'/name
        if a.check:assert json.loads(path.read_text())==data
        else:path.write_text(json.dumps(data,indent=2)+'\n')
