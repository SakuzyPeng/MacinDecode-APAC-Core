"""Independent source-layout wire controls and bounded matrix input domains."""
import hashlib
import json
import math
from pathlib import Path
from hoa_salient_subbands_vectors import cookie, bundle, descriptors, shape
from hoa_transport_vectors import packet
from hoa_vectors import canonical
from spectrum_vectors import pack

ROOT = Path(__file__).resolve().parents[1]
FORMAT = json.loads((ROOT / 'data/hoa-source-layout-format-v1.json').read_text())
PROFILE = FORMAT['format_profile']
BACKEND = 'rust_hoa_source_layout_sq_drc_off_f64_fft_v1'
STATE_PROFILE = 'apac-hoa-source-layout-state-v1'


def options(m, tag=0, labels=None, parameter=1, **extra):
    n = len(labels) if tag == 0 else tag & 65535
    profile, level = (5, 0) if n <= 16 else (5, 1) if n <= 36 else (5, 2) if n <= 49 else (0, 0)
    order = math.isqrt(m - 1)
    result = dict(order=order, output_coefficients=n, source_layout=dict(tag=tag),
                  counts=[1] if m > 1 else [], ambient_count=0 if m > 1 else 1,
                  path='salient' if m > 1 else 'replace', scene=False, rate=48000,
                  controls=dict(parameter_0=parameter), profile=profile, level=level,
                  tce_types=[0] * n)
    if labels is not None: result['source_layout']['labels'] = labels
    if (order + 1) ** 2 != m: result['coefficient_count'] = m
    result.update(extra)
    return result


def basis(opts, k=0, mode=0, block=0):
    n=opts['output_coefficients'];m=opts.get('coefficient_count',(opts['order']+1)**2)
    case=dict(block=block, elements=[dict(gain=100,grouping=0x55,bands={0:(1,[1,0,0,0],100)})]+[None]*(n-1))
    if opts['counts']:
        case['descriptors']=descriptors(opts['counts'],mode,k,order=opts['order'],coefficient_count=opts.get('coefficient_count'),controls=opts.get('controls'))
    return case


def sequences():
    for entry in FORMAT['layouts']:
        if not entry['matrix_available'] or entry['tag'] not in FORMAT['accepted_layout_tags']: continue
        m=entry['matrix_columns'];opts=options(m,entry['tag'],parameter=0)
        cases=[basis(opts,k) for k in range(m)]
        cases += [basis(opts,m-1,1,1),basis(opts,0,3,2),
                  dict(basis(opts,m-1,2,3),frame_type=2,preroll=basis(opts,0,1,2)),{}]
        yield 'matrix-'+str(entry['tag']),opts,cases
    for name,m,n,tag,labels,param in [
        ('n3d-labels',4,4,0,[(3<<16)|i for i in range(4)],2),
        ('sn3d-labels',5,5,0,[(2<<16)|i for i in range(5)],1),
        ('reordered-labels',4,4,0,[131075,131072,131074,131073],2),
        ('speaker-labels',4,4,0,[1,2,5,6],1),
        ('discrete',9,9,(147<<16)|9,None,1),
        ('source-crop',9,4,(190<<16)|4,None,1),
        ('source-expand',4,9,(190<<16)|9,None,2),
        ('matrix-partial',5,6,(121<<16)|6,None,0),
        ('matrix-reduced',4,6,(121<<16)|6,None,0),
        ('matrix-single',1,2,(101<<16)|2,None,0),
        ('lfe-position',9,6,(121<<16)|6,None,1),
        ('lfe-retained',9,6,(121<<16)|6,None,2),
        ('lfe-tag-order',9,8,(127<<16)|8,None,1),
        ('matrix-unavailable-position',9,6,(123<<16)|6,None,1),
        ('private-variable',4,4,(61441<<16)|4,None,1),
        ('private-fixed',4,5,(61472<<16)|5,None,1),
        ('n3d-last-coefficient',121,121,0,[(3<<16)|i for i in range(121)],2),
    ]:
        opts=options(m,tag,labels,param,rate=44100 if n%2 else 48000)
        cases=[basis(opts,k) for k in (range(m) if m<=25 else (0,31,32,63,64,119,120))]
        cases += [basis(opts,m-1,1,1),basis(opts,m-1,3,2),
                  dict(basis(opts,0,2,3),frame_type=2,preroll=basis(opts,m-1,1,2)),{}]
        yield name,opts,cases
    opts=options(4,labels=[(3<<16)|i for i in range(9)],parameter=2,dynamic=True)
    cases=[dict(basis(opts,k),list_mode=True,mappings=[[(i+k)%9 for i in range(4)] for _ in range(8)]) for k in range(4)]
    cases += [dict(basis(opts,3,3,2),list_mode=False,mappings=[[0,2,5,8] for _ in range(8)]),{}]
    yield 'dynamic-source-labels',opts,cases
    signal={0:(1,[1,0,0,0],100)}
    opts=options(9,(121<<16)|6,parameter=0,counts=[2,1],ambient_count=3,path='replace',scene=True,drc=True,rich=True,tce_types=[6,1,3,0,0,0],controls=dict(parameter_0=0,flag_b=True,flag_c=True,flag_f=False))
    cases=[]
    for block,mode in [(0,0),(1,1),(2,3),(3,2)]:
        case=dict(block=block,elements=[dict(parameter=3,payload=[171]),dict(independent=block%2==0,gain=100,grouping=0x55,left=signal,right=signal,cac_gain=9),dict(gain=100,grouping=0x55,bands=signal),dict(gain=100,grouping=0x55,bands=signal),dict(gain=100,grouping=0x55,bands=signal),None],descriptors=descriptors([2,1],mode,8,order=2))
        cases.append(case)
    cases[0]['drc']=dict(header=True,gains=[-3])
    cases.append(dict(cases[3],frame_type=2,preroll=cases[2]))
    cases.append({})
    yield 'matrix-controls-transports-drc',opts,cases


def manifest():
    rows=[]
    for index,(name,opts,cases) in enumerate(sequences()):
        cfg=cookie(**opts);h=hashlib.sha256(cfg);truth=[]
        for case in cases:
            raw,t=packet(case,**opts);h.update(len(raw).to_bytes(8,'little'));h.update(raw);truth.append(t)
        rows.append(dict(index=index,kind=name,options=opts,packets=len(cases),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical(truth),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,format_sha256=FORMAT['format_sha256'],cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    result=[]
    for name,opts,cases in sequences():
        first,_=packet(cases[0],**opts);good,t=packet(cases[-2],**opts)
        wire=''.join(format(b,'08b') for b in good);at=t['tail']['ancillary_end_bit_offset']-1
        result.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex()))
    return dict(fixtures=result)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    for name,data in [('hoa-source-layout-vectors-v1.json',manifest()),('hoa-source-layout-state-v1.json',state_fixtures())]:
        path=ROOT/'data'/name
        if a.check: assert json.loads(path.read_text())==data
        else:path.write_text(json.dumps(data,indent=2)+'\n')
