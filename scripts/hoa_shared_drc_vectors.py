"""Independent bounded UniDRC sequence declarations and gain payload truth."""
import json
from pathlib import Path
from spectrum_vectors import bits
from drc_vectors import loudness,delta_time
ROOT=Path(__file__).resolve().parents[1]
FORMAT=json.loads((ROOT/'data/hoa-shared-drc-format-v1.json').read_text())

def delta(rate):return 1<<((rate+1000)//2000).bit_length()
def sequences(sets,rate):
    mapping={};cursor=0
    for i,s in enumerate(sets):
        for _ in range(s.get('bands',1)):
            mapping[cursor]=(i,s);cursor+=1
    return mapping

def configuration(sets,config=None):
    return config if config is not None else dict(coefficients=[dict(location=1,sets=sets)])

def selected(sets,config=None):
    config=configuration(sets,config)
    if not config.get('header_present',True) or config.get('metadata_only',False):return None
    return next((c for c in config.get('coefficients',[]) if c.get('location',1)==1),None)

def legacy(sets,config=None):
    config=configuration(sets,config);c=selected(sets,config)
    if any(config.get(k) for k in ('config_extensions','loudness_extensions')) or any(config.get(k) is not None for k in ('loudness_eq','eq')):return False
    if any(i.get('effect',2) not in (2,5,32) or i.get('downmix') is not None or i.get('depends') is not None or i.get('requires_eq') for i in config.get('instructions',[])):return False
    if config.get('layout') is not None or config.get('downmix') is not None or c is None or len(config['coefficients'])!=1 or c.get('frame_samples',1024)!=1024 or len(c['sets'])!=1:return False
    s=c['sets'][0]
    return s.get('profile',0)==0 and s.get('bands',1)==1 and s.get('interpolation',True) and not s.get('full',False) and not s.get('aligned',False) and s.get('delta',64)==64

def extensions(entries):
    wire=''
    for entry in entries:
        payload=entry['bits'];width=max(4,(len(payload)-1).bit_length())
        assert 4<=width<=19 and 1<=entry['type']<=15
        wire+=bits(entry['type'],4)+bits(width-4,4)+bits(len(payload)-1,width)+payload
    return wire+bits(0,4)

def instruction(spec,channels,coefficients,downmix):
    wire=bits(spec.get('flag_a',False),1)+bits(0,4)+bits(spec.get('id',1),6)+bits(spec.get('complexity',0),4)+bits(spec.get('location',1),4)
    mix=spec.get('downmix');wire+=bits(mix is not None,1);count=channels
    if mix is not None:
        additional=mix.get('additional',[])
        wire+=bits(mix['id'],7)+bits(mix.get('apply',False),1)+bits(bool(additional),1)
        if additional:wire+=bits(len(additional),3)+''.join(bits(v,7) for v in additional)
        if mix.get('apply',False):
            if mix['id']==127 or additional:count=1
            elif mix['id']!=0:count=next(d['channels'] for d in downmix if d['id']==mix['id'])
    effect=spec.get('effect',2);wire+=bits(effect,16);special=bool(effect&0x8000);duck=bool(effect&0xc00) and not special
    if not special:
        if not duck:
            peak=spec.get('limiter_peak');wire+=bits(peak is not None,1)
            if peak is not None:wire+=bits(peak,8)
        target=spec.get('target_loudness');wire+=bits(target is not None,1)
        if target is not None:
            wire+=bits(target[0],6)+bits(len(target)>1,1)
            if len(target)>1:wire+=bits(target[1],6)
        dependency=spec.get('depends');wire+=bits(dependency is not None,1)
        wire+=bits(dependency,6) if dependency is not None else bits(spec.get('no_independent_use',False),1)
        wire+=bits(spec.get('requires_eq',False),1)
    indices=spec.get('indices',[0]*count);assert len(indices)==count
    for index in indices:
        wire+=bits(index+1,6)
        if duck:
            scale=spec.get('ducking_scale');wire+=bits(scale is not None,1)
            if scale is not None:wire+=bits(scale,4)
        wire+='0'
    if special or duck:return wire
    c=next(c for c in coefficients if c.get('location',1)==spec.get('location',1));groups=list(dict.fromkeys(i for i in indices if i>=0))
    for group in groups:
        s=c['sets'][group];bands=1 if s.get('profile',0)==3 else s.get('bands',1)
        wire+='00000'*bands
        if bands==1:wire+='0'
    return wire

def associations(spec,name,width,mandatory=False):
    value=spec.get(name)
    wire='' if mandatory else bits(value is not None,1)
    if value is None:return wire
    if isinstance(value,int):value=[value]
    wire+=bits(value[0],width)+bits(len(value)>1,1)
    if len(value)>1:wire+=bits(len(value)-1,width)+''.join(bits(v,width) for v in value[1:])
    return wire

def loudness_eq(instructions,channels):
    wire=bits(len(instructions),6)
    for s in instructions:
        wire+=bits(0,4)+bits(s.get('id',1),4)+bits(s.get('location',1),4)
        for name,width in [('downmix',7),('drc',6),('eq',6)]:wire+=associations(s,name,width)
        wire+=bits(s.get('after_drc',False),1)+bits(s.get('after_eq',False),1)
        groups=s.get('channel_groups');wire+=bits(groups is None,1)
        if groups is not None:
            assert len(groups)==channels;wire+=''.join(bits(v,6) for v in groups)
        count=1 if groups is None else max(groups,default=0);entries=s.get('groups',[{} for _ in range(count)])
        assert len(entries)==count;explicit=s.get('explicit_sequence',False);wire+=bits(explicit,1)
        for g in entries:
            if explicit:wire+=bits(g.get('sequence',0),6)
            gains=g.get('gains',[]);wire+=bits(len(gains),6)
            for e in gains:
                wire+=bits(e.get('sequence',0),6);characteristic=e.get('characteristic',0)
                wire+=bits(isinstance(characteristic,int),1)
                wire+=bits(characteristic,7) if isinstance(characteristic,int) else ''.join(bits(v,4) for v in characteristic)
                wire+=bits(e.get('frequency',0),6)+bits(e.get('scale',0),3)+bits(e.get('offset',0),5)
    return wire

def eq_configuration(spec,channels,downmix):
    delay=spec.get('delay');wire=bits(delay is not None,1)
    if delay is not None:wire+=bits(delay,8)
    blocks=spec.get('blocks',[]);wire+=bits(len(blocks),6)
    for block in blocks:
        wire+=bits(len(block),6)
        for e in block:
            gain=e.get('gain');wire+=bits(e['index'],6)+bits(gain is not None,1)
            if gain is not None:wire+=bits(gain,10)
    elements=spec.get('elements',[]);wire+=bits(len(elements),6)
    for e in elements:
        fir='fir' in e;wire+=bits(fir,1)
        if fir:
            order=e['order'];coefs=e['fir'];assert len(coefs)==order//2+1
            wire+=bits(order,7)+bits(e.get('symmetry',False),1)+''.join(bits(v,11) for v in coefs)
        else:
            unit=e.get('unit_zeros',[]);assert len(unit)%2==0
            wire+=bits(len(unit)//2,3)
            arrays=[e.get(k,[]) for k in ('real_zeros','complex_zeros','real_poles','complex_poles')]
            for a,w in zip(arrays,(6,6,4,4)):wire+=bits(len(a),w)
            wire+=''.join(bits(v,1) for v in unit)
            for a,w in zip(arrays,(1,7,1,7)):
                for radius,angle in a:wire+=bits(radius,7)+bits(angle,w)
    gains=spec.get('gains',[]);wire+=bits(len(gains),6)
    if gains:
        spline=spec.get('spline',False);fmt=spec.get('format',7);wire+=bits(spline,1)+bits(fmt,4)
        bands=spec.get('bands',1) if fmt==7 else (0,32,39,64,71,128,135)[fmt]
        if fmt==7:wire+=bits(bands-1,8)
        for g in gains:
            if spline:
                slopes=g['slopes'];wire+=bits(len(slopes)-2,5)
                wire+=''.join('1' if s is None else '0'+bits(s,4) for s in slopes)
                wire+=''.join(bits(f-1,4) for f in g['frequencies'])
                prefix,value=g.get('initial',(0,16));wire+=bits(prefix,2)+bits(value,(5,4,4,3)[prefix])
                wire+=''.join(bits(v,5) for v in g['deltas'])
            else:
                assert len(g)==bands;wire+=''.join(bits(v,9) for v in g)
    instructions=spec.get('instructions',[]);wire+=bits(len(instructions),6)
    for s in instructions:
        wire+=bits(0,4)+bits(s.get('id',1),6)+bits(s.get('complexity',0),4)
        mix=s.get('downmix');wire+=bits(mix is not None,1);count=channels
        if mix is not None:
            extra=mix.get('additional',[]);wire+=bits(mix['id'],7)+bits(mix.get('apply',False),1)+bits(bool(extra),1)
            if extra:wire+=bits(len(extra),7)+''.join(bits(v,7) for v in extra)
            if mix.get('apply',False):
                if mix['id']==127 or extra:count=1
                elif mix['id']!=0:count=next(d['channels'] for d in downmix if d['id']==mix['id'])
        wire+=associations(dict(drc=s.get('drc',0)),'drc',6,True)+bits(s.get('purpose',0),16)
        dependency=s.get('depends');wire+=bits(dependency is not None,1)
        wire+=bits(dependency,6) if dependency is not None else bits(s.get('no_independent_use',False),1)
        groups=s.get('channel_groups',[0]*count);assert len(groups)==count;wire+=''.join(bits(v,7) for v in groups)
        count=len(set(groups));cascades=s.get('cascades');wire+=bits(cascades is not None,1)
        if cascades is not None:
            assert len(cascades)==count
            for c in cascades:
                gain=c.get('gain');wire+=bits(gain is not None,1)
                if gain is not None:wire+=bits(gain,10)
                refs=c.get('blocks',[]);wire+=bits(len(refs),4)+''.join(bits(v,7) for v in refs)
            phases=s.get('phases');wire+=bits(phases is not None,1)
            if phases is not None:
                assert len(phases)==count*(count-1)//2;wire+=''.join(bits(v,1) for v in phases)
        refs=s.get('subband_indices');wire+=bits(refs is not None,1)
        if refs is not None:
            assert len(refs)==count;wire+=''.join(bits(v,6) for v in refs)
        duration=s.get('transition');wire+=bits(duration is not None,1)
        if duration is not None:wire+=bits(duration,5)
    return wire

def metadata_controls(channels=4):
    for effect in (0,1,4,8,16,64,128,256,512,1024,2048,4096,8192,16384,32768):
        instruction=dict(effect=effect)
        if effect==1024:instruction['indices']=[0]+[-1]*(channels-1)
        yield 'effect-'+str(effect),dict(instructions=[instruction])
    mixes=[dict(id=1,channels=2,layout=2),dict(id=2,channels=1,layout=1)]
    for name,mix in [('base',dict(id=0,apply=True)),('target',dict(id=1,apply=True)),('unapplied',dict(id=1)),('wildcard',dict(id=127,apply=True)),('additional',dict(id=1,apply=True,additional=[2]))]:
        yield 'downmix-'+name,dict(downmix=mixes,instructions=[dict(downmix=mix)])
    yield 'dependency',dict(instructions=[dict(id=1),dict(id=2,depends=1)])
    yield 'requires-eq',dict(instructions=[dict(requires_eq=True)])
    yield 'duck-scale',dict(instructions=[dict(effect=1024,ducking_scale=7,indices=[0]+[-1]*(channels-1))])
    for kind in ('config','loudness'):
        yield kind+'-extension',{kind+'_extensions':[dict(type=1,bits='1010011'),dict(type=15,bits='01100100'*4)]}
    yield 'loudness-eq-empty',dict(loudness_eq=[])
    yield 'loudness-eq-all',dict(loudness_eq=[{}])
    groups=[1+(i%2) for i in range(channels)]
    yield 'loudness-eq-groups',dict(loudness_eq=[dict(downmix=[0,127],drc=[1,63],eq=[0,63],after_drc=True,after_eq=True,channel_groups=groups,explicit_sequence=True,groups=[dict(sequence=0,gains=[dict(characteristic=3)]),dict(sequence=0,gains=[dict(characteristic=(0,0),frequency=7,scale=3,offset=5)])])])
    yield 'eq-empty',dict(eq={})
    yield 'eq-fir',dict(eq=dict(delay=7,blocks=[[dict(index=0,gain=500)]],elements=[dict(order=2,fir=[0,0],symmetry=True)],instructions=[dict(cascades=[dict(gain=500,blocks=[0])],transition=3)]))
    yield 'eq-iir',dict(eq=dict(blocks=[[dict(index=0)]],elements=[dict(unit_zeros=[0,1],real_zeros=[(0,0)],complex_zeros=[(0,0)],real_poles=[(0,0)],complex_poles=[(0,0)])],instructions=[dict(cascades=[dict(blocks=[0])])]))
    for fmt in range(1,8):
        bands=(0,32,39,64,71,128,135,3)[fmt]
        yield 'eq-bands-'+str(fmt),dict(eq=dict(format=fmt,bands=bands,gains=[[0]*bands],instructions=[dict(subband_indices=[0])]))
    for prefix in range(4):
        gain=dict(slopes=[None,0],frequencies=[1],initial=(prefix,0),deltas=[0])
        yield 'eq-spline-'+str(prefix),dict(eq=dict(spline=True,bands=3,gains=[gain],instructions=[dict(subband_indices=[0])]))
    yield 'eq-routing',dict(downmix=mixes,eq=dict(elements=[dict(order=0,fir=[0])],blocks=[[dict(index=0)]],gains=[[0]],instructions=[dict(downmix=dict(id=1,apply=True),drc=[1,2],channel_groups=[0,1],cascades=[dict(blocks=[0]),dict(blocks=[0])],phases=[True],subband_indices=[0,0])]))

def header(sets,rate=48000,channels=1,config=None):
    config=configuration(sets,config)
    if not config.get('header_present',True):return '0'
    if config.get('metadata_only',False):return '10'+loudness(None,channels)
    coefficients=config.get('coefficients',[])
    wire='1'+bits(rate-1000,18);layout=config.get('layout')
    wire+=bits(layout is not None,1)+bits(channels,7 if layout is not None else 10)
    if layout is not None:
        defined=layout.get('defined');wire+=bits(defined is not None,1)
        if defined is not None:
            wire+=bits(defined,8)
            if defined==0:wire+=''.join(bits(v,7) for v in layout['positions'])
    downmix=config.get('downmix');wire+=bits(downmix is not None,1)
    if downmix is not None:
        wire+=bits(len(downmix),7)
        for d in downmix:
            values=d.get('coefficients');wire+=bits(d['id'],7)+bits(d['channels'],7)+bits(d['layout'],8)+bits(values is not None,1)
            if values is not None:
                assert len(values)==d['channels']*channels
                wire+=bits(d.get('offset',0),4)+''.join(bits(v,5) for v in values)
    wire+=bits(len(coefficients),3)
    for coefficient in coefficients:
        local=coefficient['sets'];count=len(sequences(local,rate))
        wire+=bits(coefficient.get('location',1),4)+bits('frame_samples' in coefficient,1)
        if 'frame_samples' in coefficient:wire+=bits(coefficient['frame_samples']-1,15)
        wire+='000'+bits(count,6)+bits(len(local),6)
        for s in local:
            profile=s.get('profile',0);dt=s.get('delta',64);bands=s.get('bands',1)
            wire+=bits(profile,2)+bits(s.get('interpolation',True),1)+bits(s.get('full',False),1)+bits(s.get('aligned',False),1)+bits(dt is not None,1)
            if dt is not None:wire+=bits(dt-1,11)
            if profile!=3:
                wire+=bits(bands,4)
                if bands>1:wire+='0'
                wire+='00'*bands+''.join(bits(i*20,10) for i in range(1,bands))
    instructions=config.get('instructions',[])
    wire+=bits(len(instructions),8)+''.join(instruction(i,channels,coefficients,downmix or []) for i in instructions)
    eq=config.get('loudness_eq');wire+=bits(eq is not None,1)
    if eq is not None:wire+=loudness_eq(eq,channels)
    eq=config.get('eq');wire+=bits(eq is not None,1)
    if eq is not None:wire+=eq_configuration(eq,channels,downmix or [])
    extra=config.get('config_extensions',[]);wire+=bits(bool(extra),1)
    if extra:wire+=extensions(extra)
    extra=config.get('loudness_extensions',[]);meta=loudness(None,channels)
    if extra:meta=meta[:-1]+'1'+extensions(extra)
    return '11'+wire+meta

def payload(case,sets,rate,start,channels,config=None):
    config=case.get('configuration',configuration(sets,config));coefficient=selected(sets,config);local=[] if coefficient is None else coefficient['sets'];frames=1024 if coefficient is None else coefficient.get('frame_samples',1024)
    wire=header(sets,rate,channels,config) if case.get('header') else '0';header_end=start+len(wire);records=[]
    mapping=sequences(local,rate);specs=case.get('sequences',[{} for _ in mapping]);assert len(specs)==len(mapping)
    for (index,(gain_set,s)),spec in zip(mapping.items(),specs):
        at=start+len(wire);profile=s.get('profile',0);dt=s.get('delta',64);dt=delta(rate) if dt is None else dt
        offset=(dt-1)//2-dt if s.get('aligned') else -1
        mode=0 if profile==3 else spec.get('mode',0);gains=[0] if profile==3 else spec.get('gains',[0 if profile==0 else -8]);times=[];deltas=[];slopes=[];terminal=True
        if profile!=3:wire+=bits(mode,1)
        if mode:
            wire+='0'*(len(gains)-1)+'1'
            if not s.get('interpolation',True):
                slopes=spec.get('slopes',[7]*len(gains));assert len(slopes)==len(gains)
                book={r['value']:bits(r['code'],r['width']) for r in FORMAT['slopes']};wire+=''.join(book[i] for i in slopes)
            terminal=True if s.get('full') else spec.get('frame_end',True)
            if not s.get('full'):wire+=bits(terminal,1)
            values=spec.get('times',[1]*(len(gains)-int(terminal)));assert len(values)==len(gains)-int(terminal)
            cursor=offset
            for value in values:
                here=start+len(wire);code=delta_time(value,frames//dt);wire+=code;cursor+=value*dt;times.append(cursor);deltas.append(dict(value=value,bit_offset=here,bit_length=len(code)))
        if terminal:times.append(frames+offset)
        nodes=[];book={r['value']:bits(r['code'],r['width']) for r in FORMAT['clipping' if profile==2 else 'normal']}
        for i,gain in enumerate(gains):
            here=start+len(wire)
            if profile==3:pass
            elif i==0:
                if profile==0:wire+=bits(gain<0,1)+bits(abs(gain),8)
                else:wire+='0' if gain==0 else '1'+bits(-gain-1,10 if profile==1 else 8)
            else:wire+=book[gain-gains[i-1]]
            node=dict(gain_eighth_db=gain,time=times[i],gain_bit_offset=here,gain_bit_length=start+len(wire)-here)
            if slopes:node['slope_index']=slopes[i]
            nodes.append(node)
        records.append(dict(sequence_index=index,gain_set_index=gain_set,start_bit_offset=at,end_bit_offset=start+len(wire),coding_mode=mode,frame_end=terminal,time_deltas=deltas,encoded_times=times,nodes=nodes))
    extensions=[]
    if coefficient is not None:
        extra=case.get('extensions',[]);wire+=bits(bool(extra),1)
        for entry in extra:
            data=entry['bits'];width=max(4,(len(data)-1).bit_length());assert 4<=width<=11
            at=start+len(wire);wire+=bits(entry['type'],4)+bits(width-4,3)+bits(len(data)-1,width)+data
            from spectrum_vectors import pack
            import hashlib
            extensions.append(dict(extension_type=entry['type'],start_bit_offset=at,end_bit_offset=start+len(wire),payload_bits=len(data),payload_sha256=hashlib.sha256(pack(data)).hexdigest()))
        if extra:wire+=bits(0,4)
    first=records[0] if records else dict(coding_mode=0,frame_end=True,time_deltas=[],encoded_times=[],nodes=[])
    truth=dict(start_bit_offset=start,header_end_bit_offset=header_end,end_bit_offset=start+len(wire),header_present=bool(case.get('header')),config_present=bool(case.get('header')) and config.get('header_present',True) and not config.get('metadata_only',False),coding_mode=first['coding_mode'],frame_end=first['frame_end'],time_deltas=first['time_deltas'],encoded_times=first['encoded_times'],nodes=first['nodes'],extension_present=bool(extensions))
    if not legacy(sets,config):truth['sequences']=records
    if extensions:truth['gain_extensions']=extensions
    if case.get('configuration_changed'):truth['configuration_changed']=True
    return wire,truth
