"""Independent seven/eight/nine-bit HOA inputs with high symbols and state transitions."""
import hashlib,json
from hoa_salient_subbands_vectors import cookie,packet,bundle,descriptors,shape
from hoa_salient_vectors import format_for
from hoa_order1_vectors import format_binding as previous_formats
from hoa_vectors import excitation,canonical
from spectrum_vectors import pack

PROFILE='apac-hoa-salient-quantization-v1'
NUMERIC_PROFILE='apac-hoa-salient-quantization-math-v1'
STATE_PROFILE='apac-hoa-salient-quantization-state-v1'
BACKEND='rust_hoa_salient_quantization_sq_drc_off_f64_fft_v1'


def native_controls():
    specs=[('q7-first',7,1,4,0,'salient',False),('q8-mixed',8,2,3,5,'replace',False),
           ('q9-mixed-orders',9,3,5,0,'salient',False),('q9-dynamic',9,2,4,3,'add',True)]
    for name,precision,order,salient,ambient,path,dynamic in specs:
        orders=[min(order,1+i%3) for i in range(salient)];counts=[1]*salient
        opts=dict(order=order,path=path,dynamic=dynamic,rate=44100 if precision==7 else 48000,
                  counts=counts,component_orders=orders,ambient_count=ambient,quantization_bits=precision,
                  selection=[0,4,8] if dynamic else None,transform=4 if ambient>=4 else 0,subbands=3,scene=True)
        cases=[];output_order=3 if dynamic else order
        for mode,block in ((0,0),(1,0),(2,1),(4,2),(3,3),(5,0)):
            c=excitation(ambient+salient-1,block,0x55,order=output_order)
            rows=descriptors(counts,mode,3,salient-1,order=order,component_orders=orders,quantization_bits=precision)
            rows[-1][0]['quantized'][3]=(1<<precision)-1
            c.update(descriptors=rows,transform_index=mode%4)
            if ambient:c['elements'][ambient-1]=excitation(ambient-1,block,0x55,order=output_order)['elements'][ambient-1]
            cases.append(c)
        cases.append(dict(cases[4],frame_type=2,preroll=cases[0]));cases.append({})
        yield name,opts,cases


def sequences():
    yield from native_controls()
    for precision in (7,8):
        opts=dict(order=3,path='salient',dynamic=False,rate=48000,counts=[1]*3,component_orders=[1,2,3],quantization_bits=precision,scene=False)
        rows=descriptors([1]*3,0,3,2,component_orders=[1,2,3],quantization_bits=precision)
        for row in rows:row[0]['quantized'][3]=(1<<precision)-1
        yield f'q{precision}-three-orders',opts,[dict(excitation(2),descriptors=rows),{}]


def format_binding():
    values=dict(previous_formats()['dependencies'])
    for order in (1,2,3):
        for precision in (7,8,9):values[f'order{order}_q{precision}']=format_for(order,precision)['tables_sha256']
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
    fixtures=[]
    for name,opts,cases in native_controls():
        first,t=packet(cases[0],**opts);good,gt=packet(cases[4],**opts)
        wire=''.join(format(v,'08b') for v in good);at=gt['tail']['ancillary_end_bit_offset']-1;bad=pack(wire[:at]+'1'+wire[at+1:])
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=bad.hex(),truth=t['spatial']['salient']))
    return dict(fixtures=fixtures)
