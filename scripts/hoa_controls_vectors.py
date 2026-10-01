"""Independent spatial-control payloads and explicit active-frame configurations."""
import hashlib,json,math
from pathlib import Path
from hoa_salient_subbands_vectors import cookie,packet,bundle,descriptors,control_values,control_format_sha256
from hoa_expanded_orders_vectors import format_binding as previous_formats
from hoa_vectors import excitation,canonical
from spectrum_vectors import pack,TABLES

PROFILE='apac-hoa-spatial-controls-v1'
NUMERIC_PROFILE='apac-hoa-spatial-controls-math-v1'
STATE_PROFILE='apac-hoa-spatial-controls-state-v1'
BACKEND='rust_hoa_spatial_controls_sq_drc_off_f64_fft_v1'

def options(**extra):
    result=dict(order=2,rate=48000,path='salient',counts=[3],ambient_count=0,scene=False)
    result.update(extra);return result

def basis(opts,mode=0,block=0,active=None,dense=False,coefficient=3):
    effective=dict(opts,**(active or {}));counts=effective['counts'];ambient=effective['ambient_count'];n=effective.get('coefficient_count',(opts['order']+1)**2)
    carrier=ambient if counts else 0
    c=excitation(carrier,block,0x55,gain=100,order=3 if opts.get('dynamic') else opts['order'])
    c['elements']=c['elements'][:16 if opts.get('dynamic') else n]
    dimension=effective.get('coefficient_count',((effective.get('component_orders') or [opts['order']])[0]+1)**2)
    c['descriptors']=descriptors(counts,mode,min(coefficient,dimension-1),0,order=opts['order'],component_orders=effective.get('component_orders'),
        coefficient_count=opts.get('coefficient_count'),quantization_bits=opts.get('quantization_bits',6))
    if active is not None:c['active']=active
    if dense:
        ends=TABLES['short_offsets' if block==2 else 'long_offsets'];c['elements'][carrier]['bands']={i:(11,[1]*(ends[i+1]-ends[i]),100) for i in range(len(ends)-1)}
    c['transform_index']=mode%4
    return c

def native_controls():
    for name,ctrl,method,counts in [('mean',{'flag_a':False},0,[3]),('direction',{'flag_e':False},0,[1]),
                                  ('unrounded-0',{'flag_f':False},0,[16]),('unrounded-1',{'flag_f':False},1,[16]),
                                  ('unrounded-2',{'flag_f':False},2,[16]),('conversion-2',{'parameter_0':2},0,[1])]:
        opts=options(controls=ctrl,counts=counts,spatial_method=method)
        modes=(5,3,5) if name=='direction' else (0,2,3)
        cases=[basis(opts,m,b,dense=name.startswith('unrounded')) for m,b in zip(modes,(0,2,3))]
        cases.append(dict(basis(opts,3),frame_type=2,preroll=basis(opts,modes[0],2)))
        cases.append({})
        yield name,opts,cases
    opts=options(path='add',counts=[1],controls={})
    yield 'pure-add',opts,[basis(opts,0),basis(opts,3,2),{}]
    for variable,addition in ((False,False),(True,False),(True,True)):
        opts=options(counts=[3,3],controls=dict(flag_b=True,flag_c=variable,flag_a=not addition),transform=4,path='add' if addition else 'salient')
        changed=dict(counts=[1] if variable else [3],component_orders=[1] if variable else [2],ambient_count=2,path='replace',selection=[0,3])
        back=dict(counts=[3,3],component_orders=[2,2],ambient_count=0,path='salient',selection=None)
        silent=dict(counts=[],ambient_count=4,path='replace',selection=None)
        cases=[basis(opts,0),basis(opts,3,active=changed),basis(opts,3,active=back),basis(opts,0,active=silent),
               dict(basis(opts,3,active=back),frame_type=0),dict(basis(opts,3,active=back),configuration_present=False,frame_type=0),
               dict(basis(opts,3,active=back),frame_type=2,preroll=basis(opts,0,2,active=changed))]
        yield 'frame-variable-add' if addition else 'frame-variable' if variable else 'frame-fixed',opts,cases

    opts=options(counts=[],ambient_count=9,path='replace',spatial_method=3,controls=dict(parameter_0=2))
    yield 'inactive-partition',opts,[basis(opts,0),basis(opts,0,2),{}]

def sequences():
    yield from native_controls()
    opts=options(coefficient_count=5,counts=[2,3],controls=dict(flag_a=False,flag_b=True,flag_c=True,flag_f=False,parameter_0=2))
    changed=dict(counts=[1],ambient_count=2,path='replace',selection=[0,3])
    yield 'combined-partial',opts,[basis(opts,0),basis(opts,2,2,active=changed),dict(basis(opts,3),frame_type=2,preroll=basis(opts,1,3,active=changed)),{}]
    opts=options(dynamic=True,controls=dict(flag_a=False,flag_e=False,flag_f=False),method=1,subbands=3)
    yield 'dynamic-controls',opts,[basis(opts,5),basis(opts,3,2),dict(basis(opts,3),frame_type=2,preroll=basis(opts,5,2)),{}]

def format_binding():
    result=previous_formats();result['dependencies']['spatial_controls']=control_format_sha256()
    result['dependencies']['frame_state_v2']=hashlib.sha256((Path(__file__).resolve().parents[1]/'data/hoa-frame-configuration-state-v2.json').read_bytes()).hexdigest()
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
    fixtures=[];invalid=[];invalid_frames=[]
    for name,opts,cases in sequences():
        first,first_truth=packet(cases[0],**opts);good,t=packet(cases[-2],**opts);wire=''.join(format(v,'08b') for v in good);at=t['tail']['ancillary_end_bit_offset']-1
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),bad=pack(wire[:at]+'1'+wire[at+1:]).hex(),
            packets=[packet(c,**opts)[0].hex() for c in cases],frame_configuration=first_truth['spatial'].get('frame_configuration')))
    for p in (0,3):invalid.append(cookie(**options(controls=dict(parameter_0=p))).hex())
    opts=options(counts=[3],controls=dict(flag_b=True,flag_c=True))
    for typ in (1,2):
        invalid_frames.append(dict(cookie=cookie(**opts).hex(),packet=packet(dict(basis(opts),frame_type=typ,configuration_present=False),**opts)[0].hex()))
    opts=options(counts=[3,3],controls=dict(flag_b=True,flag_c=True))
    changed=dict(counts=[1],component_orders=[1],ambient_count=2,path='replace',selection=[0,3])
    back=dict(counts=[3,3],component_orders=[2,2],ambient_count=0,path='salient',selection=None)
    inactive=dict(counts=[],ambient_count=4,path='replace')
    cases=[basis(opts,0,coefficient=8),basis(opts,3,active=changed,coefficient=8),basis(opts,3,active=back,coefficient=8),
           basis(opts,0,active=inactive,coefficient=8),basis(opts,3,active=back,coefficient=8)]
    history=dict(cookie=cookie(**opts).hex(),packets=[packet(c,**opts)[0].hex() for c in cases])
    opts=options(counts=[2,3],controls=dict(flag_b=True,flag_c=True));stride_cases=[]
    for i,counts in enumerate(([2,3],[1,2],[2,3])):
        c=basis(opts,0 if i==0 else 3,active=dict(counts=counts,ambient_count=0,path='salient'),coefficient=8)
        c['descriptors']=descriptors(counts,0 if i==0 else 3,8,1,order=2);c['frame_type']=1 if i==0 else 0;stride_cases.append(c)
    stride=dict(cookie=cookie(**opts).hex(),packets=[packet(c,**opts)[0].hex() for c in stride_cases])
    return dict(fixtures=fixtures,invalid=invalid,invalid_frames=invalid_frames,coefficient_history=history,stride_history=stride)
