"""Independent ambient-count coverage for pure and mixed SCE transport paths."""
import hashlib,json
from hoa_salient_subbands_vectors import cookie,packet,bundle,descriptors,shape
from hoa_order1_vectors import format_binding
from hoa_vectors import excitation,canonical
from spectrum_vectors import pack

PROFILE='apac-hoa-ambient-counts-v1'
NUMERIC_PROFILE='apac-hoa-ambient-counts-math-v1'
STATE_PROFILE='apac-hoa-ambient-counts-state-v1'
BACKEND='rust_hoa_ambient_counts_sq_drc_off_f64_fft_v2'


def native_controls():
    for name,order,ambient,salient,path in [('one',2,1,2,'replace'),('three',3,3,2,'add'),('five',2,5,3,'replace'),('twelve',3,12,3,'add')]:
        opts=dict(order=order,path=path,dynamic=False,rate=48000,ambient_count=ambient,counts=[1]*salient,scene=False,transform=4 if ambient>=4 else 0)
        cases=[]
        for mode,block in [(0,0),(4,2),(3,3),(5,0)]:
            c=excitation(ambient+salient-1,block,0x55,order=order)
            c.update(descriptors=descriptors(opts['counts'],mode,ambient,salient-1,order=order),transform_index=mode%4)
            c['elements'][ambient-1]=excitation(ambient-1,block,0x55,order=order)['elements'][ambient-1];cases.append(c)
        yield name,opts,cases
    for name,order,ambient,selection,transform in [('pure-second',2,9,None,0),('pure-selected',3,5,[1,3,7,10,15],4)]:
        opts=dict(order=order,path='replace',dynamic=False,rate=48000,ambient_count=ambient,counts=[],scene=False,selection=selection,transform=transform)
        cases=[dict(excitation(ambient-1,block,0x55,order=order),transform_index=index) for index,block in [(0,0),(2,2),(3,3)]]
        yield name,opts,cases


def sequences():
    yield from native_controls()
    _,opts,_=list(native_controls())[-1]
    yield 'pure-unused-carriers',opts,[dict(excitation(15),transform_index=3),{}]
    opts=dict(order=2,path='replace',dynamic=True,rate=44100,ambient_count=3,counts=[1,3],scene=True,selection=[1,4,8],transform=0,subbands=3)
    c=excitation(4);c['descriptors']=descriptors([1,3],1,5,1,order=2)
    yield 'dynamic-three',opts,[c,dict(frame_type=2,preroll=c),{}]


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    fixtures=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-1],**opts)
        # The trimming-present flag is outside the supported zero-trim policy.
        wire=''.join(format(v,'08b') for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        bad=pack(wire[:at]+'1'+wire[at+1:])
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=bad.hex()))
    counts=[]
    for order in (1,2,3):
        n=(order+1)**2
        for ambient in range(1,n+1):
            for salient in (0,1):
                if ambient+salient>n:continue
                opts=dict(order=order,path='replace',ambient_count=ambient,counts=[1]*salient,scene=False)
                c=excitation(ambient-1,order=order);raw,_=packet(c,**opts)
                counts.append(dict(cookie=cookie(**opts).hex(),packet=raw.hex(),ambient=ambient,salient=salient))
    return dict(fixtures=fixtures,counts=counts)
