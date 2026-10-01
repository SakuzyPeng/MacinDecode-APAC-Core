"""Full-order HOA zero through ten, qualified profile limits and compensated recovery."""
import hashlib,json
from pathlib import Path
from hoa_salient_subbands_vectors import cookie,packet,bundle,descriptors,shape
from hoa_salient_vectors import format_for
from hoa_quantization_vectors import format_binding as previous_formats
from hoa_vectors import excitation,canonical
from spectrum_vectors import pack

PROFILE='apac-hoa-expanded-orders-v1'
NUMERIC_PROFILE='apac-hoa-expanded-orders-math-v1'
STATE_PROFILE='apac-hoa-expanded-orders-state-v1'
BACKEND='rust_hoa_expanded_orders_sq_drc_off_f64_fft_v1'

def profile_for(order):return (0,0) if order>6 else (5,2) if order==6 else (5,1) if order>3 else (5,0)

def native_controls():
    for name,order,path,ambient,orders,precision in [('zero',0,'replace',1,[],6),('fourth',4,'salient',0,[1,4],6),
            ('sixth',6,'add',4,[2,6],9),('seventh-ambient',7,'replace',64,[],6),('tenth',10,'salient',0,[10],8)]:
        profile,level=profile_for(order);counts=[1]*len(orders)
        opts=dict(order=order,path=path,dynamic=False,rate=48000,counts=counts,component_orders=orders,ambient_count=ambient,
                  quantization_bits=precision,profile=profile,level=level,scene=False,transform=4 if ambient>=4 else 0)
        cases=[]
        for i,(mode,block) in enumerate(((0,0),(4,2),(3,3),(5,0)) if orders else ((0,0),(0,2),(0,3))):
            channel=ambient+len(orders)-1;c=excitation(channel,block,0x55,gain=100,order=order)
            if orders:c['descriptors']=descriptors(counts,mode,(orders[-1]+1)**2-1,len(orders)-1,order=order,component_orders=orders,quantization_bits=precision,cluster=i%4)
            if ambient>=4:c['elements'][0]=excitation(0,block,0x55,gain=100,order=order)['elements'][0]
            c['transform_index']=i%4;cases.append(c)
        cases.append(dict(cases[-1],frame_type=2,preroll=cases[0]));cases.append({})
        yield name,opts,cases

def sequences():
    yield from native_controls()
    opts=dict(order=4,path='salient',dynamic=False,rate=44100,counts=[1]*3,component_orders=[3]*3,scene=False,profile=5,level=1)
    c=excitation(0,gain=252,q=-4096,order=4);c['elements'][1]=excitation(1,gain=100,q=-1,order=4)['elements'][1];c['elements'][2]=excitation(2,gain=252,q=4096,order=4)['elements'][2]
    rows=descriptors([1]*3,0,order=4,component_orders=[3]*3)
    for row in rows:row[0]['quantized'][4]=0
    c['descriptors']=rows
    yield 'compensated-cancellation',opts,[c,{}]

def format_binding():
    values=dict(previous_formats()['dependencies'])
    for order in range(4,11):
        for precision in range(6,10):values[f'order{order}_q{precision}']=format_for(order,precision)['tables_sha256']
    root=Path(__file__).resolve().parents[1]/'data'
    values['expanded_math']=json.loads((root/'hoa-expanded-orders-math-v1.json').read_text())['tables_sha256']
    values['profile_limits']=hashlib.sha256((root/'hoa-profile-levels-v1.json').read_bytes()).hexdigest()
    return dict(format_sha256=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest(),dependencies=values)

def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def state_fixtures():
    fixtures=[];boundaries=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-2],**opts);wire=''.join(format(v,'08b') for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex()))
    for order in range(11):
        profile,level=profile_for(order);counts=[1] if order else [];n=(order+1)**2
        opts=dict(order=order,profile=profile,level=level,counts=counts,ambient_count=0 if order else 1,path='salient' if order else 'replace',scene=False)
        c=excitation(0,gain=100,order=order)
        if order:c['descriptors']=descriptors(counts,0,n-1,0,order=order)
        raw,_=packet(c,**opts);boundaries.append(dict(order=order,cookie=cookie(**opts).hex(),packet=raw.hex(),active_coefficient=n-1))
    # Maximum component count, escaping both count fields; one nonzero carrier.
    opts=dict(order=10,profile=0,level=0,counts=[1]*121,scene=False)
    c=excitation(120,gain=100,order=10);c['descriptors']=descriptors([1]*121,0,120,120,order=10)
    raw,_=packet(c,**opts);boundaries.append(dict(order=10,cookie=cookie(**opts).hex(),packet=raw.hex(),active_coefficient=120))
    invalid=[cookie(order=order,profile=5,level=level,counts=[],ambient_count=(order+1)**2,path='replace',scene=False).hex() for order,level in ((4,0),(6,1),(7,2),(3,3))]
    return dict(fixtures=fixtures,boundaries=boundaries,invalid=invalid)
