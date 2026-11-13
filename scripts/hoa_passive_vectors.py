"""Independent passive scene/renderer declarations; no renderer or Apple input."""
import copy,struct
from spectrum_vectors import bits,pack
from hoa_salient_subbands_vectors import esc

def sequence(values):return ''.join(bits(v,w) for v,w in values)
def floating(value):return bits(int.from_bytes(struct.pack('>f',value),'big'),32)
def position(spec,full=True,state=None):
    state=dict(state or {});wire=''
    if full:
        state.update(parent_dynamic=spec.get('parent_dynamic',False),range_dynamic=spec.get('range_dynamic',False),position_precision=min(spec.get('position_precision',0)*2,24),rotation_precision=min(spec.get('rotation_precision',0)*2,24),parent=spec.get('parent',0),polar=False)
        wire+=bits(state['parent_dynamic'],1)+bits(state['range_dynamic'],1)+bits(spec.get('position_precision',0),4)+bits(spec.get('rotation_precision',0),4)
    change_parent=full or (state['parent_dynamic'] and 'parent' in spec)
    if not full and state['parent_dynamic']:wire+=bits(change_parent,1)
    if change_parent:
        state['parent']=spec.get('parent',0);wire+=bits(state['parent']==0,1)
        if state['parent']:wire+=bits(state['parent'],6)
    change_range=full or (state['range_dynamic'] and 'range' in spec)
    if not full and state['range_dynamic']:wire+=bits(change_range,1)
    if change_range:wire+=bits(spec.get('range',0),4)
    value=spec.get('position');wire+=bits(value is not None,1)
    if value is not None:
        delta=not full and spec.get('position_delta',False)
        if not full:wire+=bits(delta,1)
        if not delta:state['polar']=spec.get('polar',False);wire+=bits(state['polar'],1)
        widths=(7,6,5) if state['polar'] else (6,6,6)
        assert len(value)==3
        for i,(v,w) in enumerate(zip(value,widths)):
            if delta and state['polar'] and state['position_precision']==0 and i==2:
                assert v==0
            else:wire+=bits(v,w+state['position_precision']-(4 if delta else 0))
    value=spec.get('rotation');wire+=bits(value is not None,1)
    if value is not None:
        delta=not full and spec.get('rotation_delta',False)
        if not full:wire+=bits(delta,1)
        assert len(value)==4
        wire+=''.join(bits(v,8+state['rotation_precision']-(4 if delta else 0)) for v in value)
    extra=spec.get('extensions',[]);wire+=bits(bool(extra),1)
    if extra:
        from hoa_shared_drc_vectors import extensions
        wire+=extensions(extra)
    return wire,state

def renderer_data(parameters):
    return esc(len(parameters),(4,7,10))+''.join(esc(p['id'],(4,7,10))+p['data'] for p in parameters)

def metadata(spec):
    compression=spec.get('compression');wire=bits(compression is not None,1)
    if compression is not None:
        data=compression.get('data',b'');wire+=bits(compression['type'],3)+esc(len(data),(4,8,16))+''.join(bits(v,8) for v in data)
    params=spec.get('parameters');wire+='1'+bits(params is not None,1)
    if params is not None:wire+=renderer_data(params)
    groups=spec.get('groups',[]);wire+=esc(len(groups),(4,7,10))
    for g in groups:
        wire+=esc(g.get('id',0),(4,7,10))+bits(g.get('default',False),1)
        if not g.get('default',False):
            typed='type' in g;wire+=bits(typed,1)
            if typed:
                wire+=esc(g.get('component',0),(4,7,10))+bits(g['type'],3)+esc(g.get('parameter_0',0),(4,7,10))
                if g['type'] in (1,4):
                    value=g.get('parameter_1');wire+=bits(value is not None,1)
                    if value is not None:wire+=esc(value,(4,7,10))
            else:
                refs=g.get('channels',[0]);wire+=esc(len(refs)-1,(4,7,10))+bits('first_channel' in g,1)
                wire+=esc(g['first_channel'],(4,7,10)) if 'first_channel' in g else ''.join(esc(v,(4,7,10)) for v in refs)
        data=g.get('data');wire+=bits(data is not None,1)
        if data is not None:wire+=renderer_data(data)
    return wire

def global_controls():
    for i in range(4):yield f'global-{i}',i,bits(1,5 if i==3 else 1)
    yield 'authoring',4,sequence([(1,3),(1,8),(2,8),(3,8),(2,3),(4,8),(5,8),(6,8)])
    yield 'global-position-cartesian',5,sequence([(1,8),(0,1),(2,4),(1,1),(2,8),(3,8),(4,8),(5,7)])
    yield 'global-position-polar',5,sequence([(1,8),(1,1),(0,1),(2,9),(3,8),(4,7),(5,8)])
    yield 'production',6,sequence([(1,2),(2,2),(3,3)])+('0'+bits(7,7)*5)*9+bits(7,7)+'10100101'
    yield 'hrtf-none',7,'0'+bits(0,3)
    yield 'hrtf-index',7,'1'+floating(1.)+bits(1,3)+bits(2,8)
    yield 'hrtf-resource',7,'0'+bits(2,3)+bits(0,2)+esc(0,(1,3,8))*2+bits(0,3)
    yield 'global-resource',8,bits(1,2)+esc(9,(1,3,8))+esc(10,(1,3,8))
    text=b'generic\0';speaker=sequence([(1,4),(10,9),(20,8),(0,1),(2,4),(3,7),(4,3)])
    yield 'speaker-resource',9,bits(0,2)+esc(0,(1,3,8))*2+''.join(bits(v,8) for v in text)+bits(0,4)+bits(1,8)+speaker
    yield 'distance',10,bits(1,3)+floating(1.)+floating(2.)+floating(3.)+'0'
    yield 'global-origin',11,position(dict(position=[1,2,3]))[0]
    yield 'global-index',12,'1'+esc(7,(3,6,10))
    yield 'global-stream-resource',12,'0'+bits(0,2)+esc(1,(1,3,8))*2+esc(3,(3,6,10))+esc(4,(4,8,16))
    yield 'global-parameter13',13,bits(17,6)
    yield 'global-parameter14',14,bits(0,4)+bits(13,8)
    yield 'bed-headphones',15,bits(1,4)+bits(1,8)+bits(2,3)+bits(4,3)+'00'
    yield 'global-opaque',23,esc(1,(4,8,16))+bits(0xa53c,16)

def renderer_controls():
    for kind in (0,18,20):yield f'position-{kind}',kind,position(dict(position=[1,2,3],polar=True))[0]
    yield 'spread-legacy',1,sequence([(1,1),(1,1),(5,9),(6,8),(7,6)])
    yield 'spread-precise',1,sequence([(0,1),(1,1),(3,4),(1,1),(2,4),(3,12),(4,12),(5,12)])
    for kind,width in ((2,7),(3,8),(6,1),(8,3),(14,1),(15,3),(16,9),(21,1),(23,16)):
        yield f'scalar-{kind}',kind,bits(1,width)
    yield 'optional4',4,'1'+bits(3,7)
    yield 'parameter5',5,sequence([(3,8),(1,1),(4,8)])
    yield 'region-predefined',7,sequence([(1,1),(0,1),(0,1),(1,4),(1,1),(1,4)])
    yield 'region-cartesian',7,sequence([(1,1),(0,1),(1,1),(1,4),(0,1)])+bits(128,8)*6
    yield 'region-polar',7,sequence([(1,1),(0,1),(0,1),(1,4),(0,1)])+sequence([(256,9),(256,9),(128,8),(128,8)])
    reverb=''.join('1'+bits(1,w) for w in (10,10,8,9,12))+'0'+('1'+bits(1,7))*3
    yield 'reverb-index',9,bits(1,3)+bits(1,10)+reverb
    yield 'reverb-resource',9,bits(2,3)+bits(0,2)+esc(0,(1,3,8))*2+bits(1,10)+reverb
    yield 'reverb-geometry',9,bits(3,3)+bits(0,2)+esc(0,(1,3,8))*2+bits(0,3)+floating(0.)*32
    yield 'parameter10',10,bits(1,3)+'1'+bits(1,7)
    for kind in (1,2):
        prefix=bits(kind,3)+(bits(0,2)+esc(0,(1,3,8))*2 if kind==2 else '')
        yield f'radiation-resource-{kind}',11,prefix+bits(1,10)
    for width_code,width in enumerate((8,16,32)):
        prefix=bits(3,3)+bits(width_code,2)
        for pattern in (1,2):
            # Read-side order is unity, frequency; encoder disagreement is retained separately.
            body='0'+esc(127,(10,13,17))
            body+=('0'+bits(1,width)*2) if pattern==1 else (bits(1,9)*2+bits(1,width))
            yield f'radiation-{width}-{pattern}',11,prefix+bits(pattern,5)+esc(0,(3,6,9))+body
        yield f'radiation-{width}-3',11,prefix+bits(3,5)+'0'+bits(1,width)+bits(1,9)
    yield 'radiation-compact',11,bits(3,3)+bits(0,2)+bits(1,5)+esc(0,(3,6,9))+'0'+esc(127,(10,13,17))+'1'+bits(1,3)+bits(1,8)
    yield 'radiation-opaque',11,bits(3,3)+bits(0,2)+bits(4,5)+esc(0,(4,8,16))+bits(0xa5,8)
    yield 'radiation-coefficients',11,bits(4,3)+esc(1,(3,6,9))+esc(0,(6,9,12))+esc(1,(6,9,12))+floating(1.)*3
    yield 'parameter12',12,'01'+bits(1,9)
    yield 'parameter13',13,'01'+bits(1,3)
    yield 'region17-predefined',17,'11'+bits(1,4)
    yield 'region17-cartesian',17,'101'+bits(128,8)*6
    yield 'region17-polar',17,'100'+sequence([(256,9),(256,9),(128,8),(128,8),(128,8),(128,8)])
    yield 'parameter19',19,'0'+bits(1,4)+bits(1,8)
    for location in (0,2):yield f'matrix-resource-{location}',22,bits(location,2)+esc(0,(1,3,8))*2
    for float_values in (False,True):
        data=bits(1,2)+bits(1,4)+bits((190<<16)|4,32)+bits(float_values,1)
        data+=(floating(1.) if not float_values else '')
        data+=(floating(0.) if float_values else bits(0,16))*16
        yield 'matrix-inline-'+str(int(float_values)),22,data
    yield 'parameter24',24,bits(1,2)+'1'+bits(1,16)
    yield 'renderer-opaque',47,esc(1,(4,8,16))+bits(0xa53c,16)

def controls(include_unimplemented=False):
    yield 'empty',{}
    yield 'compression-lzw',dict(compression=dict(type=0,data=list(pack(bits(7,5)+bits(0,3)))))
    for kind in (1,2):
        body=esc(0,(3,8,16))+esc(0,(5,8,16))+bits(7,5)+bits(1,8)+bits(3,8)+'0'
        yield 'compression-probability-'+str(kind),dict(compression=dict(type=kind,data=list(pack(body))))
    body=esc(0,(3,8,16))+esc(0,(5,8,16))+bits(31,5)+floating(.25)+floating(.75)+'1'+floating(.5)
    yield 'compression-probability-float',dict(compression=dict(type=1,data=list(pack(body))))
    body=esc(0,(3,8,16))+esc(0,(5,8,16))+esc(0,(5,8,16))+'0'+esc(0,(5,8,16))+'1'
    yield 'compression-huffman',dict(compression=dict(type=3,data=list(pack(body))))
    yield 'compression-opaque',dict(compression=dict(type=7,data=[0xa5,0x3c]))
    for name,kind,data in global_controls():
        if name=='hrtf-resource' and not include_unimplemented:continue
        yield name,dict(parameters=[dict(id=kind,data=data)])
    for name,kind,data in renderer_controls():yield name,dict(groups=[dict(id=0,type=2,parameter_0=1,data=[dict(id=kind,data=data)])])
