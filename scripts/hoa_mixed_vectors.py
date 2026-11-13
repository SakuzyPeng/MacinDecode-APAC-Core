"""Independent fixed salient5/ambient4 writer and explicit wire/mapping truth."""
import copy, hashlib, json
from pathlib import Path
from spectrum_vectors import bits, pack
from channel_vectors import single, scene_bits
from drc_vectors import header as drc_header, payload as drc_payload
from hoa_vectors import bundle as ambient_bundle, canonical, excitation
from hoa_salient_vectors import descriptors, format_for, ENDS

PROFILE = 'apac-hoa-mixed-math-v1'


def cookie(scene=True, drc=False, rich=False, *, order=3, rate=48000):
    assert order in (2, 3) and rate in (44100, 48000)
    n = (order + 1)**2
    fields = [(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),
              (5,6),(0,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(n,8),
              (2,8),(0,1),(1,3),(0,8),(2,3)]
    wire = ''.join(bits(v,w) for v,w in fields)
    wire += '1100110'+bits(1,2)+bits(0,2)+bits(0,2)+bits(order,4)+bits(5,4)+bits(4,4)
    wire += (bits(3,4)+bits(order,2))*5+'00'+bits(n,5)+'000'*n
    wire += '0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)
    wire += '0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc: wire += drc_header(rate, rich=rich, channels=n)
    raw = pack(wire+'000')
    return len(raw).to_bytes(4,'big')+raw[4:]


def mapping(order):
    n = (order+1)**2
    return dict(ambient_transport_channels=list(range(4)), salient_transport_channels=list(range(4,9)),
                ambient_output_coefficients=list(range(4)), unused_transport_channels=list(range(9,n)),
                descriptor_numeric_profile='apac-hoa-salient-order2-math-v1' if order==2 else 'apac-hoa-salient-math-v1')


def spatial(case, origin, *, order=3, selection=None):
    selection=list(range(4)) if selection is None else list(selection)
    n=(order+1)**2; fmt=format_for(order); global_mode=case.get('global_mode')
    wire=bits(int(global_mode is not None),1)
    if global_mode is not None: wire+=bits(global_mode,3)
    result=[]
    rows=case.get('descriptors',descriptors(order=order)); assert len(rows)==5
    for sc,row in enumerate(rows):
        assert len(row)==4
        for sb,spec in enumerate(row):
            start=origin+len(wire); mode=spec.get('mode',0)
            if global_mode is None: wire+=bits(mode,3)
            else: assert mode==global_mode
            q=list(spec.get('quantized',[0 if mode==3 else 32]*n))
            signs=list(spec.get('signs_positive',[True]*n)); cluster=None; angles=(None,None)
            omitted=selection if mode<4 else []; coded=set()
            def huff(book,index):
                length,code=book[index]; return bits(code,length)
            if mode==0:
                for i in range(n):
                    if i not in omitted: wire+=bits(q[i],6); coded.add(i)
            elif mode==5:
                angles=spec.get('angles',(0,90)); wire+=bits(angles[0],9)+bits(angles[1],8)
                for i in range(4): wire+=huff(fmt['modes'][1]['codebooks'][0],q[i]); coded.add(i)
            else:
                table=fmt['modes'][mode]
                if mode==4: cluster=spec.get('cluster',0); wire+=bits(cluster,2)
                for book,group in enumerate(table['groups']):
                    if cluster is not None and cluster!=book: continue
                    for i in group:
                        if i in omitted: continue
                        wire+=huff(table['codebooks'][book],q[i]); coded.add(i)
                        if table['signs']: wire+=bits(int(signs[i]),1)
            indices=sorted(coded)
            result.append(dict(component_index=sc,subband_index=sb,mode=mode,start_bit_offset=start,
                               end_bit_offset=origin+len(wire),quantized=[q[i] for i in indices],
                               signs_positive=[signs[i] for i in indices] if mode==3 else [],
                               coded_coefficient_indices=indices,ambient_omitted_coefficients=omitted,
                               cluster=cluster,azimuth_degrees=angles[0],elevation_offset_degrees=angles[1]))
    return wire,dict(start_bit_offset=origin,end_bit_offset=origin+len(wire),single_coding_mode=global_mode is not None,
                     coding_mode=global_mode,ambient_indices=selection,
                     salient=dict(subband_ends=ENDS,descriptors=result))


def packet(case, scene=True, drc=False, rich=False, *, order=3, rate=48000):
    n=(order+1)**2; typ=case.get('frame_type',1); wire=bits(typ,2); inner=None; inner_range=None
    if typ==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],scene,drc,rich,order=order,rate=rate)
            wire+=bits(len(raw),16); wire+='0'*(-len(wire)%8); start=len(wire)
            wire+=''.join(bits(v,8) for v in raw); inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire); block=case.get('block',0); wire+=bits(block,2); elements=[]
    specs=case.get('elements',[None]*n); assert len(specs)==n
    for i,spec in enumerate(specs):
        start=len(wire)
        if spec is None:
            wire+='0'; truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,end_bit_offset=len(wire)); present=False
        else:
            encoded,truth=single(dict(spec,block=block),0,rate,start-2); wire+=encoded[:2]+encoded[4:]
            truth['end_bit_offset']=truth.pop('tns_end_bit_offset'); present=True
        truth.update(configuration=dict(element_index=i,kind='sce',tce_type=0,output_channels=[],transport_channels=[i]),
                     present=present,start_bit_offset=start); elements.append(truth)
    encoded,side=spatial(case,len(wire),order=order); wire+=encoded; payload_end=len(wire)
    side['salient']['lines_per_window']=[x//8 if block==2 else x for x in ENDS]
    wire+='0'*(-len(wire)%8); core_end=len(wire)
    if scene: wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{})); spec.setdefault('rich',rich)
        encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=n); wire+=encoded
    wire+='0'; end=len(wire)
    if drc: wire+='0'
    raw=pack(wire)
    return raw,dict(frame_type=typ,common_window=block,elements=elements,spatial=side,mixed=mapping(order),inner=inner,
                    inner_range=inner_range,drc=drc_truth,core_start_bit_offset=core_start,
                    core_payload_end_bit_offset=payload_end,core_end_bit_offset=core_end,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,
                              scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,
                              ancillary_end_bit_offset=end,packet_end_bit_offset=len(raw)*8))


def bundle(root, payloads, scene=True, drc=False, rich=False, priming=0, remainder=0, *, order=3, rate=48000):
    ambient_bundle(root,payloads,scene,drc,rich,priming,remainder,order=order,rate=rate)
    cfg=cookie(scene,drc,rich,order=order,rate=rate); (root/'cookie.bin').write_bytes(cfg)
    path=root/'manifest.json'; m=json.loads(path.read_text())
    m['file']['cookie']['value']=dict(bytes=len(cfg),sha256=hashlib.sha256(cfg).hexdigest()); path.write_text(json.dumps(m))


def basis(coefficient=4, component=0, mode=1, *, order=3, ambient=None, **options):
    case=excitation(component+4,options.pop('block',0),options.pop('grouping',0),options.pop('gain',160),options.pop('q',1),order=order)
    case['descriptors']=descriptors(mode,coefficient,component,order=order,**options)
    if ambient is not None:
        for i in range(4): case['elements'][i]=copy.deepcopy(excitation(i,q=ambient[i],order=order)['elements'][i])
    return case


def native_controls():
    for order,rate,drc in ((2,44100,True),(3,48000,False)):
        options=dict(order=order,rate=rate,scene=True,drc=drc,rich=drc)
        cases=[basis((order+1)**2-1,4,mode,order=order,ambient=[1,-1,1,-1],cluster=2,angles=(37,121)) for mode in (0,1,2,4,3,5,3)]
        cases[0]['drc']=dict(header=True,gains=[-3])
        cases.append(basis(4,0,1,order=order,block=1))
        cases.append(basis(5,2,2,order=order,block=2,grouping=0x55,ambient=[1,1,-1,-1]))
        child=basis(6,3,4,order=order,block=2,grouping=0x7f,cluster=1)
        cases.append(dict(basis(7,1,3,order=order,block=3),frame_type=2,preroll=child,
                          drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2])))
        if order==3: cases[-1]['elements'][15]=excitation(15,order=3)['elements'][15]
        cases.append({})
        yield 'mixed'+str(order)+'-'+str(rate),options,cases


def sequences():
    yield from native_controls()
    from spectrum_vectors import TABLES
    from tns_vectors import filter_spec
    from bwe2_vectors import source_case
    for order,rate in ((2,48000),(3,44100)):
        n=(order+1)**2; options=dict(order=order,rate=rate,scene=True,drc=False,rich=False)
        cases=[]
        for slot in range(9):
            if slot<4: case=excitation(slot,q=(-1 if slot%2 else 1),order=order)
            else:
                case=basis(4+(slot-4)%(n-4),slot-4,1,order=order)
                # One nonzero component reaches every high-order output coefficient.
                if slot==8:
                    for row in case['descriptors'][4]: row['quantized']=[48 if k%2 else 16 for k in range(n)]
            cases.append(case)
        yield 'core_and_output_basis_'+str(order),options,cases+[{}]
        edges=[]
        for block in (0,2):
            case=basis(4,0,2,order=order,block=block,grouping=0x55,ambient=[1,-1,1,-1])
            ends=[x//8 if block==2 else x for x in ENDS]; offsets=TABLES['short_offsets' if block==2 else 'long_offsets']; bands={}
            for point in [0]+[v for end in ends[:-1] for v in (end-1,end)]+[ends[-1]-1]:
                sfb=next(i for i in range(len(offsets)-1) if offsets[i]<=point<offsets[i+1]); q=[0,0]; q[(point-offsets[sfb])%2]=1; bands[sfb]=(11,q,160)
            case['elements'][4]['bands']=bands
            for sb,row in enumerate(case['descriptors'][0]): row['quantized']=[48 if k==4+sb else 32 for k in range(n)]
            if block==2:
                for k in range(4): case['elements'][k]['grouping']=(0,0x55,0x7f,0)[k]
            edges.append(case)
        yield 'subband_boundaries_'+str(order),options,edges+[{}]
        ambient_only=dict(elements=[None]*n); salient_only=basis(n-1,4,5,order=order,angles=(90,90))
        for k in range(4): ambient_only['elements'][k]=excitation(k,q=1,order=order)['elements'][k]
        unused=dict(elements=[None]*n)
        if order==3:
            for k in range(9,16): unused['elements'][k]=excitation(k,q=(-1 if k%2 else 1),order=order)['elements'][k]
        yield 'absent_and_unused_'+str(order),options,[salient_only,ambient_only,unused,{},salient_only]
        source=source_case(0,0,upper=True,flat=True,gain=160)
        case=basis(n-1,4,1,order=order)
        for k in (0,8): case['elements'][k]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=(k==8)),bwe2=dict(lsf=[163,24],gains=[32]))
        yield 'joint_tools_'+str(order),options,[case,{},{}]
        case=basis(n-1,0,4,order=order,cluster=3,gain=255,q=8191)
        case['elements'][5]=copy.deepcopy(excitation(5,gain=255,q=-8191,order=order)['elements'][5]); case['descriptors'][1]=copy.deepcopy(case['descriptors'][0])
        case['elements'][0]=excitation(0,gain=255,q=-8191,order=order)['elements'][0]
        yield 'escape_cancellation_'+str(order),options,[case,basis(4,4,3,order=order,gain=0,q=-8191),{}]
    case=basis(15,0,0,order=3,block=2,grouping=0)
    for sc in range(5):
        case['elements'][sc+4]=excitation(sc+4,2,(0,0x55,0x7f)[sc%3],order=3)['elements'][sc+4]
        for sb in range(4):
            mode=(sc+sb)%6; case['descriptors'][sc][sb]=descriptors(mode,4+sc,sc,cluster=sc%4,angles=(511,255))[sc][sb]
    yield 'per_component_modes_groups',dict(order=3,rate=48000,scene=True,drc=False,rich=False),[case,{},{}]
    case=dict(block=2,elements=[None]*9); case['elements'][8]=dict(grouping=0x55,bwe2=dict(lsf=[511,511],gains=[]))
    yield 'zero_sfb_bwe2',dict(order=2,rate=44100,scene=False,drc=False,rich=False),[case,{},{}]


def manifest():
    rows=[]
    for index,(kind,options,cases) in enumerate(sequences()):
        parts=[packet(case,**options) for case in cases]; h=hashlib.sha256(cookie(**options))
        for raw,_ in parts: h.update(len(raw).to_bytes(8,'little')); h.update(raw)
        truth_sha=hashlib.sha256(json.dumps(canonical([t for _,t in parts]),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        rows.append(dict(index=index,kind=kind,configuration=options,packets=len(parts),input_sha256=h.hexdigest(),truth_sha256=truth_sha))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    rows=[]; spatial_cases=[]
    for order,rate in ((2,44100),(3,48000)):
        n=(order+1)**2; options=dict(order=order,rate=rate,drc=True,rich=True)
        a=basis(n-1,4,4,order=order,cluster=2,ambient=[1,-1,1,-1]); a['drc']=dict(header=True,gains=[-3])
        b=basis(4,0,3,order=order); b['elements'][-1]={}
        first,_=packet(a,**options); good,t=packet(b,**options)
        bad=bytearray(good); p=t['elements'][-1]['start_bit_offset']+1; bad[p//8]|=1<<(7-p%8)
        def position(truth): return truth['spatial']['salient']['descriptors'][-1]['start_bit_offset']
        def broken(raw,p):
            raw=bytearray(raw)
            for i in range(3): raw[(p+i)//8]|=1<<(7-(p+i)%8)
            return raw.hex()
        tail=bytearray(good); p=t['tail']['ancillary_end_bit_offset']-1; tail[p//8]|=1<<(7-p%8)
        child=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))
        outer,ot=packet(dict(b,frame_type=2,preroll=child),**options)
        rows.append(dict(order=order,rate=rate,channels=n,cookie=cookie(**options).hex(),first=first.hex(),next=good.hex(),
                         last_element_error=bad.hex(),late_spatial_error=broken(good,position(t)),late_tail_error=tail.hex(),
                         embedded_error=broken(outer,ot['inner_range']['start_bit_offset']+position(ot['inner'])),
                         outer_after_embedded_error=broken(outer,position(ot)),embedded_good=outer.hex()))
        for mode in range(6):
            case=basis(n-1,4,mode,order=order,cluster=3,angles=(37,121)); case['global_mode']=mode
            wire,truth=spatial(case,0,order=order)
            spatial_cases.append(dict(order=order,mode=mode,bytes=pack(wire+'10101').hex(),bits=len(wire),truth=truth))
    return dict(fixtures=rows,spatial_cases=spatial_cases)
