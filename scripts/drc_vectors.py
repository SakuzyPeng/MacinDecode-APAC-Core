"""Independent APAC DRC payload writer and parameter/bit-position truth.

Normal gain delta codewords are public UniDRC format constants. Neither this
writer nor its expected nodes reads a candidate report or native float values.
"""
import copy
import hashlib
import json
from spectrum_vectors import bits,pack,cookie as core_cookie,bundle as core_bundle
from packet_vectors import neutral_scene,shifted
from bwe2_vectors import packet as core_packet

# (bit string, signed eighth-dB). Deliberately ordered by wire length.
GAIN_CODES=[('11',-1),('10',1),('001',-2),('010',0),('0000',-16),
 ('00010',-4),('01111',-3),('01110',8),('011001',-5),('011000',2),
 ('000110',3),('0001111',-8),('0110100',-7),('0110110',-6),('0110111',4),
 ('00011101',5),('000111001',-15),('011010101',-9),('011010111',6),
 ('011010100',7),('0001110000',-12),('0110101100',-11),('0110101101',-10),
 ('00011100010',-14),('00011100011',-13)]


def header(rate,metadata_only=False):
    # Version-8 APAC header, one linear profile-0 sequence, one band, no selected
    # instructions. Full encoder declarations are separate native controls.
    loudness=bits(0,20)  # two flags, two 8-bit counts, sources and extension flags
    if metadata_only:return '10'+loudness
    config='1'+bits(rate-1000,18)+'0'+bits(2,10)+'0'+bits(1,3)
    config+=bits(1,4)+'0'+'000'+bits(1,6)+bits(1,6)
    config+='00'+'100'+'1'+bits(63,11)+bits(1,4)+'00'
    config+=bits(0,8)+'000'
    return '11'+config+loudness


def cookie(rate,scene=False):
    prefix=''.join(bits(v,8) for v in core_cookie(rate))[:198]
    raw=pack(prefix+'0'+bits(int(scene),1)+(neutral_scene() if scene else '')+'1'+header(rate)+'000')
    return len(raw).to_bytes(4,'big')+raw[4:]


def delta_time(value,ratio=16):
    if value==1:return '00'
    if 2<=value<=5:return '01'+bits(value-2,2)
    if 6<=value<=13:return '10'+bits(value-6,3)
    return '11'+bits(value-14,(2*ratio-1).bit_length())


def payload(case,rate=48000,start=0):
    mode=case.get('mode',0);gains=case.get('gains',[0]);terminal=case.get('frame_end',True)
    h=header(rate,case.get('metadata_only',False)) if case.get('header') else '0'
    wire=h+bits(mode,1);header_end=start+len(h)
    times=[];deltas=[]
    if mode:
        wire+='0'*(len(gains)-1)+'1'+bits(int(terminal),1)
        cursor=-1
        values=case.get('times',[])
        assert len(values)==len(gains)-int(terminal)
        for v in values:
            at=start+len(wire);code=delta_time(v);wire+=code;cursor+=v*64;times.append(cursor)
            deltas.append(dict(value=v,bit_offset=at,bit_length=len(code)))
    else:
        assert len(gains)==1;terminal=True
    if terminal:times.append(1023)
    encoded_times=times[:]
    # APAC preserves future time coordinates; there is no reservoir rotation.
    codes={v:c for c,v in GAIN_CODES};nodes=[]
    for i,g in enumerate(gains):
        at=start+len(wire)
        if i==0:wire+=bits(int(g<0 or case.get('negative_zero',False)),1)+bits(abs(g),8)
        else:wire+=codes[g-gains[i-1]]
        nodes.append(dict(gain_eighth_db=g,time=times[i],gain_bit_offset=at,gain_bit_length=start+len(wire)-at))
    extension=case.get('extension',False);wire+='10000' if extension else '0'
    return wire,dict(start_bit_offset=start,header_end_bit_offset=header_end,end_bit_offset=start+len(wire),
                     header_present=bool(case.get('header')),config_present=bool(case.get('header')) and not case.get('metadata_only',False),
                     coding_mode=mode,frame_end=terminal,time_deltas=deltas,encoded_times=encoded_times,nodes=nodes,extension_present=extension)


def packet(case,rate=48000,scene=False):
    case=copy.deepcopy(case);frame_type=case.get('frame_type',1);wire=bits(frame_type,2);inner=None
    if frame_type==2:
        wire+='0'+bits(int('preroll' in case),2)
        if 'preroll' in case:
            child,inner=packet(case['preroll'],rate,scene)
            wire+=bits(len(child),16);wire+='0'*(-len(wire)%8)
            wire+=''.join(bits(v,8) for v in child)
    core_start=len(wire)
    if case.get('absent'):spectra=None;wire+='0'
    else:
        raw,original=core_packet(case,rate)
        wire+=''.join(bits(v,8) for v in raw)[2:original['bwe2']['end_bit_offset']]
        spectra=shifted(original,core_start-2)
    wire+='0'*(-len(wire)%8);core_end=len(wire)
    if scene:wire+=('1'+neutral_scene() if case.get('scene_update') else '0')
    drc,truth=payload(case.get('drc',{}),rate,len(wire));wire+=drc
    result=pack(wire+'0')
    return result,dict(drc=truth,spectra=spectra,inner=inner,core_end_bit_offset=core_end,frame_type=frame_type)


def bundle(root,packets,rate=48000,scene=False):
    core_bundle(root,packets,rate)
    config=cookie(rate,scene);(root/'cookie.bin').write_bytes(config)
    path=root/'manifest.json';m=json.loads(path.read_text(encoding='utf-8'))
    m['file']['cookie']['value']=dict(bytes=len(config),sha256=hashlib.sha256(config).hexdigest())
    path.write_text(json.dumps(m),encoding='utf-8')


def cases():
    from packet_vectors import basis,WINDOWS
    for mode in (0,1):
        for sign in (0,1):
            for magnitude in range(256):
                yield 'initial_gain',{},dict(absent=True,drc=dict(mode=mode,gains=[(-1 if sign else 1)*magnitude],negative_zero=bool(sign)))
    for terminal in (False,True):
        for _,delta in GAIN_CODES:
            yield 'gain_delta',{},dict(absent=True,drc=dict(mode=1,frame_end=terminal,gains=[0,delta],times=[1] if terminal else [1,1]))
    for v in range(1,33):
        yield 'time_delta',{},dict(absent=True,drc=dict(mode=1,frame_end=False,gains=[-4],times=[v]))
    for count in range(1,33):
        yield 'node_count',{},dict(absent=True,drc=dict(mode=1,frame_end=False,gains=list(range(count)),times=[1]*count))
    for window in WINDOWS:
        for independent in (False,True):
            for scene in (False,True):
                for update in ('reuse','restate','metadata','terminal'):
                    core=basis(window,independent=independent)
                    drc=dict(mode=1,gains=[-100,-99,-101],times=[1,2],
                             header=update in ('restate','metadata'),metadata_only=update=='metadata',extension=update=='terminal')
                    yield 'structure',dict(scene=scene),dict(core,scene_update=scene,drc=drc)
                    if update=='restate':
                        yield 'preroll',dict(scene=scene),dict(core,frame_type=2,preroll=dict(core,drc=drc),drc=dict(gains=[12]))


def manifest():
    rows=[]
    for rate in (48000,44100):
        for index,(kind,options,case) in enumerate(cases()):
            raw,truth=packet(case,rate,**options);config=cookie(rate,**options)
            rows.append(dict(rate=rate,index=index,kind=kind,cookie_sha256=hashlib.sha256(config).hexdigest(),
                             packet_sha256=hashlib.sha256(raw).hexdigest(),truth_sha256=hashlib.sha256(json.dumps(truth,sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(schema_version=1,rules_version='apac-drc-payload-v1',cases=rows,
                sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())
