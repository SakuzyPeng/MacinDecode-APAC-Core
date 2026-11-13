"""Independent semantic inputs for the two additional salient spatial partitions."""
import hashlib,json
from hoa_salient_subbands_vectors import cookie,packet,bundle,basis,descriptors,shape
from hoa_vectors import excitation,canonical
from hoa_dynamic_vectors import maps
from spectrum_vectors import TABLES
from generate_hoa_dynamic_subbands_format import boundaries
from generate_hoa_salient_partition_format import PROFILE


def line_excitation(case,component,frequency,opts):
    slot=(0 if opts['path']=='salient' else 4)+component
    offsets=TABLES['short_offsets' if case.get('block',0)==2 else 'long_offsets']
    band=next(i for i,end in enumerate(offsets[1:]) if frequency<end)
    q=[0]*(offsets[band+1]-offsets[band]);q[frequency-offsets[band]]=1
    case['elements'][slot]['bands']={band:(1,q,160)}


def control_cases(opts):
    counts=opts['counts'];order=opts['order'];path=opts['path'];dynamic=opts['dynamic'];m=(order+1)**2
    common=dict(order=order,path=path,dynamic=dynamic);cases=[]
    for i,(mode,block) in enumerate(((0,0),(4,1),(3,2),(5,3),(3,0))):
        sc=i%5;c=basis(counts,m-2,sc,mode,block=block,grouping=0x55,**common)
        ends=boundaries(counts[sc],opts['spatial_method']);end=ends[min(i,len(ends)-1)]
        frequency=(end//8 if block==2 else end)-1
        line_excitation(c,sc,frequency,opts)
        c.update(transform_index=i%4,list_mode=bool(i%2),mappings=maps(i,bool(i%2)))
        if path!='salient':c['elements'][0]=excitation(0,block,0x7f,order=3 if shape(order,dynamic)==16 else 2)['elements'][0]
        if i==0:c.update(global_mode=mode,drc=dict(header=True,gains=[-3]))
        cases.append(c)
    child=dict(basis(counts,m-2,4,1,block=2,grouping=0x7f,**common),transform_index=2)
    cases.append(dict(basis(counts,m-2,0,3,block=3,**common),frame_type=2,preroll=child,transform_index=1,
                      drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
    cases.append(dict(transform_index=3));return cases


def native_controls():
    specs=[('pure2',2,'salient',False,44100,True,1,[1,3,4,9,16],2,8),
           ('replace3',3,'replace',False,48000,False,2,[16,9,4,3,1],2,8),
           ('dynamic-add',2,'add',True,48000,False,1,[16]*5,2,3),
           ('dynamic-replace',2,'replace',True,44100,False,2,[16]*5,0,7)]
    for name,order,path,dynamic,rate,drc,spatial_method,counts,method,subbands in specs:
        opts=dict(order=order,path=path,dynamic=dynamic,rate=rate,drc=drc,rich=drc,scene=True,
                  spatial_method=spatial_method,counts=counts,method=method,subbands=subbands,
                  selection=None if path=='salient' else [0,1,3,8],transform=0 if path=='salient' else 4)
        yield name,opts,control_cases(opts)


def sequences():
    yield from native_controls()
    for name,opts in [('fixed-add',dict(order=3,path='add',dynamic=False,rate=44100,spatial_method=1,counts=[4]*5,selection=[1,5,10,15],transform=4)),
                      ('dynamic-pure',dict(order=2,path='salient',dynamic=True,rate=48000,spatial_method=2,counts=[1]*5,selection=None,transform=0,method=1,subbands=5))]:
        opts.update(scene=True,drc=False,rich=False);yield name,opts,control_cases(opts)


def manifest():
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        fields=[];cfg=cookie(**opts,_count_fields=fields);generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),cookie_counts=fields,input_sha256=h.hexdigest(),
                         truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    from hoa_salient_subbands_vectors import state_fixtures as shared
    return shared(controls=list(native_controls()),spatial_methods=(1,2))
