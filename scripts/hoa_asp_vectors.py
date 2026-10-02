"""Independent ASP boundary controls; each family reuses an existing wire writer."""
import copy,hashlib,json
from pathlib import Path
import hoa_shared_vectors as shared
from spectrum_vectors import bits,pack
PROFILE='apac-asp-boundaries-v1'
ROOT=Path(__file__).resolve().parents[1]
def replace(raw,at,width,value):
    wire=''.join(bits(v,8) for v in raw);return pack(wire[:at]+bits(value,width)+wire[at+width:])
def sources():
    names={'rate-spatial-48000','stream-hoa-channel','scene-graph-combined'}
    for name,options,cases in shared.sequences():
        if name not in names:continue
        yield name,shared,options,cases
    import hoa_controls_vectors as controls
    for name,options,cases in controls.sequences():
        if name in ('frame-variable-add','dynamic-controls'):
            yield name,controls,options,cases
            if options.get('controls',{}).get('flag_b'):
                yield name+'-reuse',controls,options,[dict(cases[0],frame_type=0),dict(cases[0],frame_type=0,configuration_present=False)]
    yield 'capacity-8192',shared,dict(components=[shared.ambient(4)],custom=dict(variable=False)),[{},{}]

def generated():
    for name,module,options,cases in sources():
        first,next_case=cases[:2]
        current=copy.deepcopy(next_case);current.pop('preroll',None);current['frame_type']=0
        outer=copy.deepcopy(first);outer.update(frame_type=2,preroll=dict(first,frame_type=0))
        if name=='capacity-8192':
            child=dict(frame_type=0,custom=dict(payload=[0]*8100))
            for _ in range(3):
                raw=module.packet(child,**options)[0]
                child['custom']['payload']=[0]*(len(child['custom']['payload'])+8192-len(raw))
            assert len(module.packet(child,**options)[0])==8192
            outer['preroll']=child
        specs=dict(first=first,zero=current,embedded=outer,no_preroll=dict(first,frame_type=2))
        nodes={k:module.packet(v,**options) for k,v in specs.items()}
        nodes['three']=(replace(nodes['zero'][0],0,2,3),nodes['zero'][1])
        embedded=nodes['embedded'][0];child_size=len(module.packet(outer['preroll'],**options)[0]);width=16 if child_size<65535 else 32
        child_start=((5+width+7)//8)*8;padding=child_start-(5+width)
        nodes['inner_three']=(replace(embedded,child_start,2,3),nodes['embedded'][1])
        nodes['padded']=(replace(embedded,5+width,padding,(1<<padding)-1),nodes['embedded'][1])
        yield name,module,options,nodes,child_start

def fixtures():
    result=[]
    for name,module,options,nodes,child_start in generated():
        cookie=module.cookie(**options);embedded=nodes['embedded'][0]
        channels=sum(shared.parts(c,options.get('rate',48000))['channels'] for c in options['components']) if module is shared else options.get('output_coefficients',16 if options.get('dynamic') else (options.get('order',3)+1)**2)
        bad={'reconfiguration':replace(embedded,2,1,1),'count_two':replace(embedded,3,2,2),'count_three':replace(embedded,3,2,3),'zero_length':replace(embedded,5,16,0),'nested':replace(embedded,child_start,2,2),'oversized':replace(embedded,5,16,min(channels*2048+1,65535)),'late_tail':embedded+bytes([165]),'truncated':embedded[:-1]}
        bad['truncated_length']=embedded[:2]
        bad['truncated_extended_length']=replace(embedded,5,16,65535)[:4]
        bad['extended_oversized']=pack(bits(2,2)+'0'+bits(1,2)+bits(65535,16)+bits(65535,16))
        result.append(dict(name=name,channels=channels,cookie=cookie.hex(),**{k:v[0].hex() for k,v in nodes.items()},bad={k:v.hex() for k,v in bad.items()}))
    return dict(profile=PROFILE,fixtures=result)
def manifest():
    value=fixtures();value['sha256']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest();return value
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args();path=ROOT/'data/hoa-asp-vectors-v1.json';data=manifest()
    if a.check:assert json.loads(path.read_text())==data
    else:path.write_text(json.dumps(data,indent=2)+'\n')
