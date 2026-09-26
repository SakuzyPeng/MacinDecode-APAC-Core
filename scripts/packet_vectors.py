"""Portable ASP/state vectors, serialized from wire rules and independent truth."""
import copy
import hashlib
import itertools
import json

from spectrum_vectors import bits, pack, cookie as core_cookie, bundle as core_bundle
from bwe2_vectors import packet as core_packet, source_case
from tns_vectors import filter_spec


def neutral_scene():
    def fields(values):return ''.join(bits(v,w) for v,w in values)
    def tag(word):return fields([(0,2),(word,10),(0,1),(0,1)])
    result=fields([(0,3),(0,2),(1,6),(1,1),(0,1),(1,6),(0,6),(0,6),(1,5),(1,4)])
    result+=fields([(1,6),(0,6),(1,1)])+tag(21)
    result+=fields([(1,6),(0,6),(0,6),(0,1),(0,4),(0,1),(1,1),(0,6),(0,5)])
    result+='1'+tag(21)+'00'  # group extension, preset selection data
    result+=tag(97)+bits(1,2)+bits(7,16)+'0000'  # empty language plus its flag
    result+='00000'  # composition tag/update/extension; scene tag/extension
    assert len(result)==161
    return result


def cookie(rate,scene=False,origin=None):
    header=''.join(bits(v,8) for v in core_cookie(rate))[:198]
    body=header+'0'+bits(int(scene),1)+(neutral_scene() if scene else '')+'000'
    if origin is not None:
        body+='1'+bits(3,4)+bits(4,4)
        body+=''.join(bits(v,w) for v,w in zip(origin,(8,3,10,10,8)))+'0'
    raw=pack(body+'0')
    return len(raw).to_bytes(4,'big')+raw[4:]


def shifted(value,amount):
    if isinstance(value,list):return [shifted(v,amount) for v in value]
    if not isinstance(value,dict):return value
    return {k:v+amount if k.endswith('bit_offset') and isinstance(v,int) else shifted(v,amount) for k,v in value.items()}


def packet(case,rate=48000,scene=False):
    case=copy.deepcopy(case)
    frame_type=case.get('frame_type',1)
    prefix=bits(frame_type,2);inner=None;embedded=None
    if frame_type==2:
        prefix+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            internal=case['preroll']
            if internal.get('frame_type',1)>1:raise ValueError('nested ASP preroll')
            inner,inner_truth=packet(internal,rate,scene)
            if not 0<len(inner)<=4096:raise ValueError('preroll length outside the stereo bound')
            prefix+=bits(len(inner),16)
            prefix+='0'*(-len(prefix)%8)
            start=len(prefix);prefix+=''.join(bits(v,8) for v in inner)
            embedded=dict(start_bit_offset=start,end_bit_offset=len(prefix),truth=inner_truth)
    elif 'preroll' in case:raise ValueError('preroll requires ASP type 2')
    start=len(prefix)
    if case.get('absent'):
        spectral=None;core='0'
    else:
        raw,original=core_packet(case,rate)
        core=''.join(bits(v,8) for v in raw)[2:original['bwe2']['end_bit_offset']]
        spectral=shifted(original,start-2)
    body=prefix+core;payload_end=len(body)
    body+='0'*(-len(body)%8);core_end=len(body)
    update=bool(case.get('scene_update'))
    if update and not scene:raise ValueError('scene update without scene configuration')
    if scene:body+=('1'+neutral_scene() if update else '0')
    body+='0';ancillary_end=len(body)
    raw=pack(body)
    truth=dict(frame_type=frame_type,core_start_bit_offset=start,core_payload_end_bit_offset=payload_end,
               absent=bool(case.get('absent')),spectra=spectral,embedded_preroll=embedded,
               tail=dict(core_end_bit_offset=core_end,ancillary_start_bit_offset=core_end,
                         scene_update_present=update if scene else None,neutral_scene_restatement=update,
                         trimming_present=False,ancillary_end_bit_offset=ancillary_end,packet_end_bit_offset=len(raw)*8))
    return raw,truth


def bundle(root,payloads,rate=48000,scene=False,origin=None):
    core_bundle(root,payloads,rate)
    config=cookie(rate,scene,origin)
    (root/'cookie.bin').write_bytes(config)
    path=root/'manifest.json';manifest=json.loads(path.read_text())
    manifest['file']['cookie']['value']=dict(bytes=len(config),sha256=hashlib.sha256(config).hexdigest())
    path.write_text(json.dumps(manifest))


WINDOWS=((0,0),(1,0),(2,0),(2,0x55),(2,0x7f),(3,0))


def basis(window,side='left',gain=160,independent=False):
    block,mask=window
    return dict(block=block,grouping=mask,gain=gain,independent=independent,
                **{side:{0:(1,[1,-1,1,0],gain)}})


def sequences():
    # Every pair is a valid APAC synthesis operation; block type alone chooses
    # the window. No AAC-style transition restriction is implied by the wire.
    for previous,current,side,frame_type,mode in itertools.product(range(4),range(4),('left','right'),range(3),range(3)):
        scene=mode!=0
        a=basis((previous,0),side,independent=bool(previous%2));b=basis((current,0),'right' if side=='left' else 'left')
        seq=[a,b,dict(absent=True),{}]
        for c in seq:c.update(frame_type=frame_type,scene_update=mode==2)
        yield 'window_pairs',dict(scene=scene),seq
    for window,side,count,following in itertools.product(WINDOWS,('left','right'),(1,2,5),range(4)):
        seq=[basis(window,side)]+[dict(absent=True)]*count+[basis((following,0),side),{},{}]
        yield 'absence_runs',dict(scene=True),seq
    for internal,current,previous,frame_type,mode in itertools.product(WINDOWS,WINDOWS,range(3),(0,1),range(3)):
        a=[dict(absent=True),{},basis((0,0),'right')][previous]
        inner=basis(internal,'left');inner.update(frame_type=frame_type,scene_update=mode==2)
        outer=basis(current,'right');outer.update(frame_type=2,preroll=inner,scene_update=mode==2)
        yield 'embedded_preroll',dict(scene=mode!=0),[a,outer,{},dict(absent=True)]
    for window,shared,gain,direction in itertools.product(WINDOWS,(False,True),(160,255),(False,True)):
        block,grouping=window
        c=source_case(block,grouping,upper=True,flat=True,gain=gain)
        c.update(independent=not shared,cac_gain=26 if shared else 0,right=copy.deepcopy(c['left']))
        c['left_tns']=filter_spec([1,-1,1],14 if block==2 else 49,direction,window=0)
        c['right_tns']=filter_spec([-1],14 if block==2 else 49,not direction,window=0)
        c['left_bwe2']=dict(lsf=[163,24],gains=[32]*len(core_packet(c)[1]['channels'][0]['ics']['window_groups']))
        outer=copy.deepcopy(c);outer.update(frame_type=2,preroll=c)
        yield 'joint_tools',dict(scene=True),[{},c,outer,dict(absent=True),{}]
    for origin,scene in itertools.product(((0,0,0,0,0),(255,7,1023,1023,255),(2,1,28,1,34)),(False,True)):
        yield 'content_origin',dict(scene=scene,origin=list(origin)),[basis((0,0)),dict(absent=True),basis((2,0x55),'right'),{}]


def identity(config,payloads):
    h=hashlib.sha256(config)
    for p in payloads:h.update(len(p).to_bytes(8,'little'));h.update(p)
    return h.hexdigest()


def manifest():
    rows=[]
    for rate in (48000,44100):
        for index,(kind,options,seq) in enumerate(sequences()):
            generated=[packet(c,rate,options.get('scene',False)) for c in seq]
            rows.append(dict(rate=rate,index=index,kind=kind,packets=len(seq),
                             internal_frames=sum(t['embedded_preroll'] is not None for _,t in generated),
                             input_sha256=identity(cookie(rate,**options),[p for p,_ in generated])))
    return dict(schema_version=1,profile='apac-asp-state-v1',sequences=rows,
                sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def window(source,destination,start,target,end):
    """Cut an existing bundle without consulting its original source file."""
    destination.mkdir();raw=(source/'packets.bin').read_bytes()
    index=[json.loads(line) for line in (source/'packets.jsonl').read_text().splitlines()]
    rows=[copy.deepcopy(r) for r in index if start<=r['packet_index']<end]
    parts=[raw[r['export_offset']:r['export_offset']+r['bytes']] for r in rows]
    offset=0
    for row in rows:row['export_offset']=offset;offset+=row['bytes']
    data=b''.join(parts);config=(source/'cookie.bin').read_bytes()
    (destination/'packets.bin').write_bytes(data);(destination/'cookie.bin').write_bytes(config)
    (destination/'packets.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    m=json.loads((source/'manifest.json').read_text())
    first=next(r for r in rows if r['packet_index']==target)
    m.update(start_packet=start,requested_packets=end-target,actual_packets=end-start,
             packet_data_bytes=len(data),packet_data_sha256=hashlib.sha256(data).hexdigest(),
             replay_window=dict(requested_start_packet=target,requested_packets=end-target,
                                actual_target_packets=end-target,included_preroll_packets=target-start,
                                target_raw_start=first['raw_frame_position']['value'],
                                target_raw_end=rows[-1]['raw_frame_position']['value']+rows[-1]['frames']))
    (destination/'manifest.json').write_text(json.dumps(m))

