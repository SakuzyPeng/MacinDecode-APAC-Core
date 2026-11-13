"""Independent effective-subband cookies and full eight-row wire/mapping truth."""
import copy,hashlib,json
from spectrum_vectors import bits,pack,TABLES
from channel_vectors import scene_bits
from drc_vectors import header as drc_header
from hoa_vectors import canonical
import hoa_dynamic_vectors as dynamic_wire
import hoa_additive_vectors as additive_wire
from generate_hoa_dynamic_subbands_format import PROFILE,boundaries,generate


def arguments(options):
    base={k:v for k,v in options.items() if k not in ('subbands','path')}
    path=options.get('path','salient')
    if path=='add': return additive_wire,dict(base,order=2,dynamic=True)
    return dynamic_wire,dict(base,mixed=path=='replace')


def cookie(scene=True,drc=False,rich=False,*,rate=48000,path='salient',selection=None,transform=0,method=2,subbands=1):
    assert path in ('salient','replace','add') and 1<=subbands<=8
    mixed=path!='salient'; assert mixed or (selection is None and not transform)
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(16,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)+'110'+bits(int(path=='add'),1)+'111'+bits(method,2)+bits(subbands-1,4)
    wire+=bits(1,2)+bits(0,2)+bits(0,2)+bits(2,4)+bits(5,4)+bits(4 if mixed else 0,4)
    wire+=(bits(3,4)+bits(2,2))*5+bits(int(selection is not None),1)
    if selection is not None:
        assert len(selection)==4;limit=9
        for i in range(3,-1,-1):
            wire+=bits(selection[i],(limit-1).bit_length())
            if selection[i]==i:break
            limit=selection[i]+1
    if mixed:
        wire+=bits(int(transform!=0),1)
        if transform:wire+=bits(transform-1,2)
    wire+=bits(16,5)+'000'*16+'0'+bits(190,16)+bits(16,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc:wire+=drc_header(rate,rich=rich,channels=16)
    raw=pack(wire+'000');return len(raw).to_bytes(4,'big')+raw[4:]


def packet(case,**options):
    writer,args=arguments(options);raw,truth=writer.packet(case,**args);n=options.get('subbands',1);method=options.get('method',2)
    def update(t):
        d=t['dynamic_selection'];d['subband_ends']=boundaries(n,method)
        d['lines_per_window']=[v//8 if t['common_window']==2 else v for v in d['subband_ends']]
        if n<8:d.update(active_subband_count=n,subband_profile=PROFILE,format_sha256=generate()['format_sha256'])
        if t['inner']:update(t['inner'])
    update(truth);return raw,truth


def bundle(root,payloads,priming=0,remainder=0,**options):
    writer,args=arguments(options);writer.bundle(root,payloads,priming=priming,remainder=remainder,**args)
    cfg=cookie(**options);(root/'cookie.bin').write_bytes(cfg);p=root/'manifest.json';m=json.loads(p.read_text());m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest());p.write_text(json.dumps(m))


def probes():
    for n in range(1,8):
        for method in range(3):
            for rate in (44100,48000):
                block=2 if rate==44100 else 0
                c=dynamic_wire.basis(8,0,1,block=block,grouping=0x55)
                offsets=TABLES['short_offsets' if block==2 else 'long_offsets']
                c['elements'][0]['bands']={sfb:(11,[1,1],160) for sfb in range(len(offsets)-1)}
                c.update(mappings=[list(range(b,b+9)) for b in range(8)],list_mode=rate==48000)
                yield f'boundary-{n}-{method}-{rate}',dict(path='salient',subbands=n,method=method,rate=rate,scene=True,drc=False,rich=False),[c]


def native_controls():
    originals=list(dynamic_wire.native_controls())[:2]+[next(row for row in additive_wire.native_controls() if row[0]=='dynamic')]
    for path,n,method,(name,opts,cases) in zip(('salient','replace','add'),(1,3,7),(0,1,2),originals):
        opts={k:v for k,v in opts.items() if k not in ('mixed','order','dynamic')};opts.update(path=path,subbands=n,method=method)
        yield 'main-'+path,opts,cases


def sequences():
    yield from native_controls()
    for path,n in (('salient',2),('replace',4),('add',6)):
        opts=dict(path=path,subbands=n,method=2,rate=44100,scene=True,drc=True,rich=True,selection=None if path=='salient' else [0,2,4,8],transform=0 if path=='salient' else 4)
        cases=[]
        for i,(block,group) in enumerate(((1,0),(2,0),(2,0x7f),(3,0))):
            c=(additive_wire.basis(8,4,1,order=2,dynamic=True,block=block,grouping=group) if path=='add' else dynamic_wire.basis(8,4,1,mixed=path=='replace',block=block,grouping=group))
            c.update(mappings=dynamic_wire.maps(i,True),list_mode=True,transform_index=i);cases.append(c)
        cases[0]['drc']=dict(header=True,gains=[-3]);cases[2]['drc']=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])
        cases+=[dict(transform_index=3),dict(elements=[{}]*16,transform_index=0)]
        yield 'windows-'+path,opts,cases
    # Identical active row, different valid inactive rows: reports differ, audio must not.
    for changed in (False,True):
        opts=dict(path='replace',subbands=1,method=1,rate=48000,selection=[0,2,4,8],transform=2)
        c=dynamic_wire.basis(7,0,1,mixed=True);rows=dynamic_wire.maps(3,True)
        if changed:rows[1:]=dynamic_wire.maps(7,True)[1:]
        c.update(mappings=rows,list_mode=True)
        yield 'inactive-'+str(changed),opts,[c,dict(mappings=rows,list_mode=True)]
    residual=next(row for row in additive_wire.sequences() if row[0]=='cross_contribution_residual')
    opts={k:v for k,v in residual[1].items() if k not in ('order','dynamic')};opts.update(path='add',subbands=5)
    yield 'residual-five-bands',opts,residual[2]


def manifest():
    def rows(seq):
        output=[]
        for index,(kind,opts,cases) in enumerate(seq):
            generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cookie(**opts))
            for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
            output.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
        return output
    data=dict(boundary_cases=rows(probes()),cases=rows(sequences()));return dict(profile=PROFILE,sha256=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest(),**data)


def state_fixtures():
    fixtures=[];mapping_cases=[]
    for path,n in (('salient',1),('replace',3),('add',7)):
        opts=dict(path=path,subbands=n,method=2,rate=44100,selection=None if path=='salient' else [0,2,4,8],transform=0 if path=='salient' else 4,scene=True,drc=True,rich=True)
        def basis(mode):
            return additive_wire.basis(7,0,mode,order=2,dynamic=True,cluster=2) if path=='add' else dynamic_wire.basis(7,0,mode,mixed=path=='replace',cluster=2)
        a=dict(basis(4),transform_index=0,drc=dict(header=True,gains=[-3]));b=dict(basis(3),transform_index=2,list_mode=True,mappings=dynamic_wire.maps(3,True));b['elements'][15]={}
        first,_=packet(a,**opts);good,t=packet(b,**opts)
        def replace(raw,at,encoded):
            wire=''.join(format(v,'08b') for v in raw);return pack(wire[:at]+encoded+wire[at+len(encoded):]).hex()
        band=t['dynamic_selection']['mappings'][7];active=t['dynamic_selection']['mappings'][0]
        rows=copy.deepcopy(b['mappings']);rows[7]=list(range(9));alternate,_=packet(dict(b,mappings=rows),**opts)
        bitmap,bt=packet(dict(b,list_mode=False,mappings=[list(range(9))]*8),**opts);at=bt['dynamic_selection']['mappings'][7]['start_bit_offset']
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]));outer,ot=packet(dict(b,frame_type=2,preroll=child),**opts)
        errors=dict(last_element_error=replace(good,t['elements'][-1]['start_bit_offset']+1,'1'),late_spatial_error=replace(good,t['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    dynamic_error=replace(good,active['start_bit_offset']+4,bits(active['target_acn_indices'][0],4)),
                    inactive_duplicate=replace(good,band['start_bit_offset']+4,bits(band['target_acn_indices'][0],4)),
                    inactive_bitmap_few=replace(bitmap,at,'0'),inactive_bitmap_many=replace(bitmap,at+15,'1'),
                    late_tail_error=replace(good,t['tail']['ancillary_end_bit_offset']-1,'1'),
                    embedded_error=replace(outer,ot['inner_range']['start_bit_offset']+ot['inner']['spatial']['salient']['descriptors'][-1]['start_bit_offset'],'111'),
                    outer_after_embedded_error=replace(outer,ot['dynamic_selection']['mappings'][7]['start_bit_offset']+4,bits(ot['dynamic_selection']['mappings'][7]['target_acn_indices'][0],4)))
        fixtures.append(dict(options=opts,cookie=cookie(**opts).hex(),first=first.hex(),next=good.hex(),alternate=alternate.hex(),embedded_good=outer.hex(),errors=errors,
                             inactive_error_bit=band['start_bit_offset']+4,internal_end_bit=t['dynamic_selection']['start_bit_offset']))
    for n in range(1,8):
        for method in range(3):
            for listed in (False,True):
                for block in (0,2):
                    c=dict(mappings=dynamic_wire.maps(3,listed),list_mode=listed,block=block);wire,truth=dynamic_wire.dynamic(c,0,method)
                    truth.update(subband_ends=boundaries(n,method),lines_per_window=[v//8 if block==2 else v for v in boundaries(n,method)],active_subband_count=n,subband_profile=PROFILE,format_sha256=generate()['format_sha256'])
                    mapping_cases.append(dict(block=block,cookie=cookie(subbands=n,method=method).hex(),bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=fixtures,mapping_cases=mapping_cases)
