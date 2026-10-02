"""Independent single-ASC SQ element writer; all coordinates come from emitted bits."""
import copy,hashlib,itertools,json
from spectrum_vectors import bits,pack,channel,bundle as base_bundle,TABLES
from packet_vectors import neutral_scene,shifted
from bwe2_vectors import packet as cpe_packet,regions,source_case
from tns_vectors import channel_payload,filter_spec
from drc_vectors import header as drc_header,payload as drc_payload

LAYOUTS={1:(100,0,[0],['Mono']),2:(101,0,[1],['L','R']),6:(121,1,[1,0,3,1],['L','R','C','LFE','Ls','Rs']),8:(128,2,[1,0,3,1,1],['L','R','C','LFE','Ls','Rs','Rls','Rrs'])}
# Additional layouts are addressable explicitly; legacy enumeration stays frozen.
EXTENDED_LAYOUTS={12:(192,3,[1,0,3,1,1,1,1],['L','R','C','LFE','Ls','Rs','Rls','Rrs','Vhl','Vhr','Ltr','Rtr']),24:(204,4,[1,0,3,1,1,0,3,1,1,0,0,1,1,0,0,1],['Lw','Rw','C','LFE2','Rls','Rrs','L','R','Cs','LFE3','Lss','Rss','Vhl','Vhr','Vhc','Ts','Ltr','Rtr','Ltm','Rtm','Ctr','Cb','Lb','Rb'])}
def layout(channels):return LAYOUTS[channels] if channels in LAYOUTS else EXTENDED_LAYOUTS[channels]
WINDOWS=((0,0),(1,0),(2,0),(2,0x55),(2,0x7f),(3,0))
PROFILE='apac-channel-state-v1'

def configuration(channels):
    first=0;out=[]
    for i,typ in enumerate(layout(channels)[2]):
        n=2 if typ==1 else 1
        out.append(dict(element_index=i,kind={0:'sce',1:'cpe',3:'lfe'}[typ],tce_type=typ,output_channels=list(range(first,first+n))));first+=n
    return out

def scene_bits(drc=False):return ('1'+neutral_scene()[1:]) if drc else neutral_scene()

def cookie(channels=8,rate=48000,scene=True,drc=False,rich=False,_parts=None):
    family,level,types,_=layout(channels)
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(31,6),(level,4),(0,1),(3 if rate==48000 else 4,6),(0,6),(channels,8),(2,8),(0,1),(1,3),(0,8),(0,3),(0,1),(len(types),5)]
    wire=''.join(bits(v,w) for v,w in fields)+''.join(bits(t,3) for t in types)+bits(family,16)+'0'
    if _parts is not None:_parts.update(codec=wire[sum(w for _,w in fields[:-3]):],channels=channels)
    wire+='0'+bits(0,3)+bits(0,2)
    wire+='0'+bits(int(scene),1)+(scene_bits(drc) if scene else '')+bits(int(drc),1)
    if drc:wire+=drc_header(rate,rich=rich,channels=channels)
    raw=pack(wire+'000');return len(raw).to_bytes(4,'big')+raw[4:]

def single(case,typ,rate,origin):
    block=case.get('block',0);grouping=case.get('grouping',0);gain=case.get('gain',160)
    bands=copy.deepcopy(case.get('bands',{}));maximum=case.get('max_sfb',max(bands,default=-1)+1)
    if maximum:bands.setdefault(maximum-1,(0,(),0))
    data,truth=channel(bands,block,grouping,gain,rate)
    truth.update(channel_index=0,stream_bit_offset=origin+2+truth.pop('header_bits'),spectral_bit_offset=origin+2+truth.pop('spectral_relative_offset'),end_bit_offset=origin+2+len(data))
    wire='10'+data;tns=[]
    if typ==0:
        raw,parameters=channel_payload(case.get('tns'),truth['ics'],origin+len(wire),rate,0);wire+=raw;tns.append(parameters)
    end=origin+len(wire);bwe=None
    if typ==0:
        enabled=case.get('bwe2_enabled',case.get('bwe2') is not None);start=origin+len(wire);wire+=bits(int(enabled),1);parameters=None
        if enabled:
            spec=case.get('bwe2') or {};indices=spec.get('lsf',[0,0]);groups=len(truth['ics']['window_groups']) if maximum else 0
            gains=spec.get('gains',[32]*groups)
            if len(gains)!=groups:raise ValueError('SCE gain groups mismatch')
            at=origin+len(wire);wire+=''.join(bits(v,9) for v in indices)+''.join(bits(v,6) for v in gains)
            parameters=dict(lsf_indices=indices,gain_indices=gains,start_bit_offset=at,end_bit_offset=origin+len(wire))
        bwe=dict(start_bit_offset=start,end_bit_offset=origin+len(wire),control_bits=[bool(enabled)],channels=[dict(channel_index=0,active=bool(enabled),parameter_source_channel=0 if enabled else None,parameters=parameters)])
    return wire,dict(channels=[truth],shared_ics=None,cac=None,tns=tns,tns_end_bit_offset=end,bwe2=bwe)

def packet(case,channels=8,rate=48000,scene=True,drc=False,rich=False):
    frame_type=case.get('frame_type',1);wire=bits(frame_type,2);inner=None;inner_range=None
    if frame_type==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            raw,inner=packet(case['preroll'],channels,rate,scene,drc,rich)
            if len(raw)<65535:wire+=bits(len(raw),16)
            else:wire+=bits(65535,16)+bits(len(raw)-65535,16)
            wire+='0'*(-len(wire)%8);start=len(wire);wire+=''.join(bits(v,8) for v in raw);inner_range=dict(start_bit_offset=start,end_bit_offset=len(wire))
    core_start=len(wire);elements=[]
    specs=case.get('elements',[{} for _ in layout(channels)[2]])
    if len(specs)!=len(layout(channels)[2]):raise ValueError('element count mismatch')
    for cfg,spec in zip(configuration(channels),specs):
        start=len(wire)
        if spec is None or spec.get('absent'):
            wire+='0';truth=dict(channels=[],shared_ics=None,cac=None,tns=[],bwe2=None,tns_end_bit_offset=len(wire));present=False
        elif cfg['tce_type']==1:
            raw,truth=cpe_packet(spec,rate);end=truth['bwe2']['end_bit_offset'];wire+=''.join(bits(v,8) for v in raw)[2:end];truth=shifted(truth,start-2);present=True
        else:
            encoded,truth=single(spec,cfg['tce_type'],rate,start);wire+=encoded;present=True
        truth.update(configuration=cfg,present=present,start_bit_offset=start,end_bit_offset=truth['tns_end_bit_offset'])
        elements.append(truth)
    core_payload_end=len(wire);wire+='0'*(-len(wire)%8);core_end=len(wire)
    if scene:wire+=('1'+scene_bits(drc) if case.get('scene_update') else '0')
    drc_truth=None
    if drc:
        spec=dict(case.get('drc',{}));spec.setdefault('rich',rich)
        encoded,drc_truth=drc_payload(spec,rate,len(wire),channels=channels);wire+=encoded
    wire+='0';ancillary_end=len(wire)
    if drc:wire+='0'
    raw=pack(wire)
    return raw,dict(frame_type=frame_type,core_start_bit_offset=core_start,core_payload_end_bit_offset=core_payload_end,
                    core_end_bit_offset=core_end,elements=elements,inner=inner,inner_range=inner_range,drc=drc_truth,
                    tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,scene_update_present=bool(case.get('scene_update')) if scene else None,
                              neutral_scene_restatement=bool(case.get('scene_update')),trimming_present=False,ancillary_end_bit_offset=ancillary_end,packet_end_bit_offset=len(raw)*8))

def bundle(root,payloads,channels=8,rate=48000,scene=True,drc=False,rich=False,priming=0,remainder=0):
    base_bundle(root,payloads,rate);config=cookie(channels,rate,scene,drc,rich);(root/'cookie.bin').write_bytes(config)
    path=root/'manifest.json';m=json.loads(path.read_text());f=m['file'];f['format']['channels']=channels
    f['layout']['value']['tag']=(layout(channels)[0]<<16)|channels
    f['cookie']['value']=dict(bytes=len(config),sha256=hashlib.sha256(config).hexdigest())
    f['packet_table']['value']=dict(valid_frames=len(payloads)*1024-priming-remainder,priming_frames=priming,remainder_frames=remainder)
    path.write_text(json.dumps(m),encoding='utf-8')

def excitation(channels,channel_index,window=(0,0),gain=160,quantized=1):
    out=[{} for _ in layout(channels)[2]]
    for cfg,spec in zip(configuration(channels),out):
        if channel_index not in cfg['output_channels']:continue
        block,group=window;spec.update(block=block,grouping=group,gain=gain)
        bands={0:(11,[quantized,0],gain)} if abs(quantized)>1 else {0:(1,[quantized,0,0,0],gain)}
        if cfg['kind']=='cpe':spec.update(independent=True,**{('left' if channel_index==cfg['output_channels'][0] else 'right'):bands})
        else:spec['bands']=bands
    return dict(elements=out)


def sequences(channels,presence_masks=None):
    count=len(layout(channels)[2]);absent=dict(elements=[None]*count)
    for ch,window in itertools.product(range(channels),WINDOWS):
        a=excitation(channels,ch,window);yield 'channel_window',dict(scene=True,drc=False,rich=False),[a,{},absent,{}]
    for mask in range(1<<count) if presence_masks is None else presence_masks:
        initial=dict(elements=[dict(left={0:(1,[1,0,0,0],160)}) if t==1 else dict(bands={0:(1,[1,0,0,0],160)}) for t in layout(channels)[2]])
        partial=dict(elements=[None if mask&(1<<i) else copy.deepcopy(e) for i,e in enumerate(initial['elements'])])
        yield 'presence_mask',dict(scene=True,drc=False,rich=False),[initial,partial,absent,{}]
    for a,b in itertools.product(range(4),repeat=2):
        x=excitation(channels,0,(a,0));y=excitation(channels,channels-1,(b,0));outer=copy.deepcopy(y);outer.update(frame_type=2,preroll=x)
        yield 'preroll_windows',dict(scene=True,drc=True,rich=True),[x,outer,absent,{}]
    for window,gain,direction in itertools.product(WINDOWS,(100,255),(False,True)):
        elems=[]
        for i,typ in enumerate(layout(channels)[2]):
            if typ==3:elems.append(dict(bands={0:(11,[8191,-16],gain)},gain=gain));continue
            c=source_case(*window,upper=True,flat=True,gain=gain)
            groups=4 if window==(2,0x55) else 1 if window[0]!=2 or window[1]==0x7f else 8
            if typ==1:
                c.update(independent=bool(i%2),right=copy.deepcopy(c['left']),cac_gain=26,left_tns=filter_spec([1],14 if window[0]==2 else 49,direction,window=0),right_tns=None,left_bwe2=dict(lsf=[163,24],gains=[32]*groups));elems.append(c)
            else:
                elems.append(dict(block=window[0],grouping=window[1],gain=gain,bands=c['left'],tns=filter_spec([-1],14 if window[0]==2 else 49,direction),bwe2=dict(lsf=[163,24],gains=[32]*groups)))
        x=dict(elements=elems,drc=dict(mode=1,gains=[-5,-6,-4],times=[1,3],header=True))
        yield 'joint_tools',dict(scene=True,drc=True,rich=True),[{},x,dict(x,frame_type=2,preroll=x),absent,{}]
    for ch,q,gain in itertools.product(range(channels),(-8191,8191),(0,255)):
        yield 'escape_gain',dict(scene=False,drc=False,rich=False),[excitation(channels,ch,gain=gain,quantized=q),absent,{}]
    for full in (False,True):
        seq=[]
        for i,value in enumerate((0,1,127,128,165,255)):
            c=excitation(channels,i%channels,(i%4,0));c['drc']=dict(header=True,metadata_only=not full,loudness_value=value,gains=[i]);seq.append(c)
        seq.extend([absent,{}]);yield 'metadata_updates',dict(scene=True,drc=True,rich=True),seq
    for window in WINDOWS:
        elems=[{} for _ in layout(channels)[2]]
        for i,t in enumerate(layout(channels)[2]):
            elems[i].update(block=window[0],grouping=window[1])
            if t==0:elems[i].update(bwe2=dict(lsf=[511,511],gains=[]))
        yield 'zero_band_extensions',dict(scene=True,drc=False,rich=False),[dict(elements=elems),absent,{}]
    # Every legal frequency band on scalar SCE/LFE paths; CPE bands retain the
    # old exhaustive matrix. This also checks boundaries adjacent to each TCE.
    for cfg in configuration(channels):
        if cfg['kind']=='cpe':continue
        for window in WINDOWS:
            for band in range(14 if window[0]==2 else 49):
                elems=[None]*count;cb=1+band%11
                values=[1,-1,0,1] if cb<=4 else [16,-17] if cb==11 else [1,-1]
                elems[cfg['element_index']]=dict(block=window[0],grouping=window[1],bands={band:(cb,values,160)},gain=160)
                yield 'scalar_band',dict(scene=False,drc=False,rich=False),[dict(elements=elems),absent]
    if count>1:
        for offset in range(len(WINDOWS)):
            elems=[]
            for i,typ in enumerate(layout(channels)[2]):
                block,mask=WINDOWS[(offset+i)%len(WINDOWS)]
                spec=dict(block=block,grouping=mask,gain=160)
                if typ==1:spec.update(independent=True,left={0:(1,[1,0,0,0],160)},right={0:(1,[-1,0,0,0],160)},right_block=(block+1)%4,right_grouping=0)
                else:spec['bands']={0:(1,[1,0,0,0],160)}
                elems.append(spec)
            yield 'mixed_element_windows',dict(scene=True,drc=False,rich=False),[dict(elements=elems),absent,{}]

def manifest():
    rows=[]
    for channels in LAYOUTS:
        for rate in (48000,44100):
            for index,(kind,options,seq) in enumerate(sequences(channels)):
                config=cookie(channels,rate,**options);generated=[packet(c,channels,rate,**options) for c in seq]
                h=hashlib.sha256(config)
                for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
                # Parameter truth includes integer coordinates; approximate writer
                # floats are excluded (the Decimal oracle defines those values).
                def canonical(v):
                    if isinstance(v,list):return [canonical(x) for x in v]
                    if isinstance(v,dict):return {k:canonical(x) for k,x in v.items() if k!='scaled'}
                    return v
                truth=json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()
                rows.append(dict(channels=channels,rate=rate,index=index,kind=kind,packets=len(seq),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(truth).hexdigest()))
    return dict(schema_version=1,profile=PROFILE,sequences=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def state_fixtures(layouts=LAYOUTS):
    result=[]
    for n in layouts:
        options=dict(scene=True,drc=True,rich=True)
        first=excitation(n,0);first['drc']=dict(header=True,loudness_value=1,gains=[-3])
        second=excitation(n,n-1,(2,0x55));second['drc']=dict(header=True,metadata_only=True,loudness_value=255,mode=1,gains=[0,0],times=[16])
        good=excitation(n,n-1);good['drc']=dict(gains=[2])
        a,_=packet(first,n,**options);late_bad,_=packet(second,n,**options);b,truth=packet(good,n,**options)
        bad=bytearray(b);bit=truth['elements'][-1]['start_bit_offset']+1;bad[bit//8]|=1<<(7-bit%8)
        result.append(dict(channels=n,cookie=cookie(n,**options).hex(),first=a.hex(),next=b.hex(),late_drc_error=late_bad.hex(),last_element_error=bytes(bad).hex()))
    return dict(schema_version=1,fixtures=result)
