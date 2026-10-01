"""Independent HOA element sequences with explicit carrier maps and bit boundaries."""
import copy
import hashlib
import json
from pathlib import Path
from spectrum_vectors import bits, pack
from channel_vectors import single, scene_bits
from bwe2_vectors import packet as cpe_packet
from drc_vectors import payload as drc_payload
from hoa_salient_subbands_vectors import cookie, bundle, shape, spatial, esc, descriptors, control_values, control_format_sha256, dynamic_domain_format_sha256
from hoa_dynamic_vectors import dynamic as mapping_wire
from generate_hoa_dynamic_subbands_format import boundaries

PROFILE = 'apac-hoa-transports-v1'
STATE_PROFILE = 'apac-hoa-transports-state-v1'
BACKEND = 'rust_hoa_transports_sq_cac_tns_bwe2_drc_off_f64_fft_v1'


def width(kind):
    return {0: 1, 1: 2, 3: 1, 6: 0}[kind]


def extension(payload, parameter, origin, empty_zero=False):
    payload=bytes(payload)
    if parameter is None:
        assert not payload
        wire='0'+bits(0 if empty_zero else 1,7)
        return wire,dict(start_bit_offset=origin,end_bit_offset=origin+8,bytes=0 if empty_zero else 1,
                         payload_parameter=None,payload_start_bit_offset=None,payload_sha256=None)
    assert not empty_zero
    body=esc(parameter,(8,8,16))+''.join(bits(v,8) for v in payload)
    size=1+len(body)//8
    while True:
        header='0'+esc(size,(7,8,16));actual=(len(header)+len(body))//8
        if actual==size:break
        size=actual
    wire=header+body
    return wire,dict(start_bit_offset=origin,end_bit_offset=origin+len(wire),bytes=size,
        payload_parameter=parameter,payload_start_bit_offset=origin+len(header)+len(esc(parameter,(8,8,16))),
        payload_sha256=hashlib.sha256(payload).hexdigest())


def cpe(spec, rate, origin):
    raw, truth = cpe_packet(spec, rate)
    wire = ''.join(bits(v, 8) for v in raw)[:truth['bwe2']['end_bit_offset']]
    # Ordinary writer truth includes ASP and each ICS window. HOA carries the
    # window once outside all elements, including independently headed CPEs.
    removed = [(0, 2), (4, 6)]
    if not truth['shared_ics']:
        at = truth['channels'][0]['end_bit_offset'] + 1
        removed.append((at, at + 2))

    def shift(value):
        if isinstance(value, list):
            return [shift(v) for v in value]
        if not isinstance(value, dict):
            return value
        return {k: origin + v - sum(max(0, min(v, end) - start) for start, end in removed)
                if k.endswith('bit_offset') and isinstance(v, int) else shift(v)
                for k, v in value.items()}

    output = ''.join(b for i, b in enumerate(wire) if not any(a <= i < z for a, z in removed))
    return output, shift(truth)


def packet(case, **opts):
    n = shape(opts.get('order', 3), opts.get('dynamic', False),opts.get('coefficient_count'),opts.get('output_coefficients'))
    if opts.get('controls') is not None:opts=dict(opts,controls=control_values(opts['controls'],opts.get('path','salient')))
    declared=opts
    if (opts.get('controls') or {}).get('flag_b'):
        opts=dict(opts,**case.get('active',{}));opts['path']='add' if opts['controls']['flag_d'] and opts['counts'] and opts['ambient_count'] else 'replace' if opts['ambient_count'] else 'salient'
    types = opts.get('tce_types', [0] * n)
    specs = case.get('elements', [None] * len(types))
    assert len(specs) == len(types)
    rate = opts.get('rate', 48000)
    typ = case.get('frame_type', 1)
    wire = bits(typ, 2)
    inner = inner_range = None
    if typ == 2:
        wire += '0' + bits(int('preroll' in case), 2)
        if 'preroll' in case:
            raw, inner = packet(case['preroll'], **declared)
            wire += esc(len(raw), (16, 16))
            wire += '0' * (-len(wire) % 8)
            at = len(wire)
            wire += ''.join(bits(v, 8) for v in raw)
            inner_range = dict(start_bit_offset=at, end_bit_offset=len(wire))
    core_start = len(wire)
    block = case.get('block', 0)
    wire += bits(block, 2)
    elements = []
    channel = 0
    for index, (kind, spec) in enumerate(zip(types, specs)):
        begin = len(wire)
        channels = list(range(channel, channel + width(kind)))
        channel += width(kind)
        present = spec is not None
        if not present:
            encoded = '0'
            truth = dict(channels=[], shared_ics=None, cac=None, tns=[], bwe2=None,
                         tns_end_bit_offset=begin + 1)
        elif kind == 6:
            body, extra = extension(spec.get('payload', []), spec.get('parameter'), begin + 1, spec.get('empty_zero', False))
            body = spec.get('body', body)
            encoded = '1' + body
            truth = dict(channels=[], shared_ics=None, cac=None, tns=[], bwe2=None,
                         tns_end_bit_offset=begin + len(encoded), extension=extra)
        elif kind == 1:
            encoded, truth = cpe(dict(spec, block=block), rate, begin)
        else:
            encoded, truth = single(dict(spec, block=block), kind, rate, begin - 2)
            encoded = encoded[:2] + encoded[4:]
        wire += encoded
        direct = not opts.get('counts') and opts.get('ambient_count',4) == n and opts.get('selection') is None and not opts.get('transform') and not (opts.get('controls') or {}).get('flag_b')
        truth.update(configuration=dict(element_index=index, kind={0:'sce',1:'cpe',3:'lfe',6:'extension'}[kind],
                                        tce_type=kind, output_channels=channels if direct else [], transport_channels=channels),
                     present=present, start_bit_offset=begin,
                     end_bit_offset=truth.pop('tns_end_bit_offset'))
        elements.append(truth)
    spatial_opts = {k:v for k,v in opts.items() if k in ('order','path','selection','transform','counts','spatial_method','component_orders','ambient_count','quantization_bits','profile','level','coefficient_count','controls','output_coefficients')}
    encoded, side = spatial(case, len(wire), **spatial_opts)
    wire += encoded
    spatial_end = len(wire)
    dynamic = None
    if opts.get('dynamic'):
        slots=opts.get('coefficient_count',(opts.get('order',3)+1)**2);active=slots<n;extended='coefficient_count' in opts or slots!=9 or n!=16
        rounded=control_values(opts.get('controls'),opts.get('path','salient'))['flag_f'];subbands=opts.get('subbands',8);method=opts.get('method',2)
        encoded,dynamic=mapping_wire(case,len(wire),method,slots,n);wire+=encoded
        dynamic['subband_ends']=boundaries(subbands,method,rounded) if active else []
        dynamic['lines_per_window']=[] if not active or block==2 and not rounded else [v//8 if block==2 else v for v in dynamic['subband_ends']]
        ambient=opts.get('ambient_count',0 if opts.get('path','salient')=='salient' else 4);core=ambient+len(opts['counts'])
        dynamic.update(internal_spatial_end_bit_offset=spatial_end,ambient_recovery_slots=side['ambient_indices'],ambient_transport_channels=list(range(ambient)),salient_transport_channels=list(range(ambient,core)),unused_transport_channels=list(range(core,channel)))
        if active and subbands<8:
            from generate_hoa_dynamic_subbands_format import generate
            dynamic.update(active_subband_count=subbands,subband_profile='apac-hoa-dynamic-subbands-v1',format_sha256=generate()['format_sha256'])
        if active and not rounded:dynamic.update(unrounded_subbands=True,format_sha256=control_format_sha256())
        if extended:dynamic.update(domain_profile='apac-hoa-dynamic-domains-v1',configured_subband_count=subbands,wire_mapping_groups=8 if active else 0,format_sha256=dynamic_domain_format_sha256())
        if 'ambient' in side:dynamic['internal_ambient']=side.pop('ambient')
        side['end_bit_offset']=len(wire)
    payload_end = len(wire)
    wire += '0' * (-len(wire) % 8)
    core_end = len(wire)
    scene = opts.get('scene', True)
    drc = opts.get('drc', False)
    if scene:
        wire += '1' + scene_bits(drc) if case.get('scene_update') else '0'
    drc_truth = None
    if drc:
        spec = dict(case.get('drc', {}))
        spec.setdefault('rich', opts.get('rich', False))
        encoded, drc_truth = drc_payload(spec, rate, len(wire), channels=n)
        wire += encoded
    wire += '0'
    ancillary_end = len(wire)
    if drc:
        wire += '0'
    return pack(wire), dict(**({'frame_options':{k:v for k,v in opts.items() if v is not None}} if opts.get('controls') is not None else {}),frame_type=typ, common_window=block, elements=elements, spatial=side,
        dynamic_selection=dynamic, inner=inner, inner_range=inner_range, drc=drc_truth,
        internal_spatial_end_bit_offset=spatial_end, core_start_bit_offset=core_start,
        core_payload_end_bit_offset=payload_end, core_end_bit_offset=core_end,
        tail=dict(core_end_bit_offset=core_end, ancillary_start_bit_offset=core_end,
                  scene_update_present=bool(case.get('scene_update')) if scene else None,
                  neutral_scene_restatement=bool(case.get('scene_update')), trimming_present=False,
                  ancillary_end_bit_offset=ancillary_end, packet_end_bit_offset=len(pack(wire))*8))


def native_controls():
    signal = {0: (1, [1, 0, 0, 0], 100)}
    for name, types in [('pair', [1, 1]), ('lfe', [0, 3, 1]), ('extension', [6, 1, 0, 0, 6])]:
        opts = dict(order=1, counts=[], path='replace', ambient_count=4, tce_types=types,
                    scene=False, rate=44100 if name == 'pair' else 48000)
        cases = []
        for block, shared in [(0, False), (1, True), (2, False), (3, True)]:
            specs = []
            for kind in types:
                if kind == 1:
                    specs.append(dict(independent=not shared, gain=100, grouping=0x55,
                                      left=signal, right=signal, cac_gain=9 if shared else 0))
                elif kind == 6:
                    specs.append({})
                else:
                    specs.append(dict(gain=100, grouping=0x55, bands=signal))
            cases.append(dict(block=block, elements=specs))
        cases.append(dict(cases[0], frame_type=2, preroll=cases[2]))
        cases.append({})
        yield name, opts, cases
    for name, types in [('compact', [0]), ('many-extensions', [6]*256+[0])]:
        opts = dict(order=3, counts=[1], path='salient', ambient_count=0, tce_types=types,
                    scene=False, rate=48000)
        c = dict(elements=[None]*len(types), descriptors=descriptors([1],0,4,order=3))
        c['elements'][-1] = dict(gain=100,bands=signal)
        yield name, opts, [c]
    opts = dict(order=1, counts=[], path='replace', ambient_count=4, tce_types=[6,1,0,0,6], scene=False, rate=48000)
    cases = []
    for parameter,payload in [(3,[0xab,0xcd]),(126,list(range(125))),(255,[141]+[i%256 for i in range(393)])]:
        cases.append(dict(elements=[dict(payload=payload,parameter=parameter),dict(independent=True,gain=100,left=signal,right=signal),
                                    dict(gain=100,bands=signal),dict(gain=100,bands=signal),{}]))
    yield 'extension-lengths', opts, cases
    template=copy.deepcopy(cases[0]);template['elements'][0]=dict(empty_zero=True)
    yield 'extension-zero',opts,[template]
    probes=[]
    for parameter,payload in [(0,[0xab,0xcd]),(1,[0xab,0xcd]),(256,[0xaf]),(66045,[])]:
        c=copy.deepcopy(template);c['elements'][0]=dict(parameter=parameter,payload=payload);probes.append(c)
    yield 'extension-parameters',opts,probes

    # A CPE crosses the ambient/salient boundary when A is odd.
    for name,order,path,ambient,counts,types,dynamic,rate in [
        ('replace',2,'replace',3,[1]*6,[1]*4+[0],False,44100),
        ('add',3,'add',5,[1]*3,[1]*8,False,48000),
        ('dynamic',2,'add',4,[1]*9,[1]*6+[0],True,48000),
    ]:
        opts = dict(order=order,path=path,ambient_count=ambient,counts=counts,tce_types=types,
                    dynamic=dynamic,rate=rate,scene=True,drc=name=='replace',rich=name=='replace',
                    selection=[0,2,4] if ambient==3 else [0,2,4,8] if ambient==4 else [0,2,4,8,15],
                    transform=4 if ambient>=4 else 0,method=1,subbands=3)
        cases=[]
        for i,(mode,block) in enumerate([(0,0),(4,1),(3,2),(5,3),(3,0)]):
            specs=[dict(independent=bool(i%2),gain=100,grouping=0x55,left=signal,right=signal,cac_gain=9) if t==1 else dict(gain=100,grouping=0x55,bands=signal) for t in types]
            c=dict(block=block,elements=specs,transform_index=i%4,descriptors=descriptors(counts,mode,4,i%len(counts),order=order),list_mode=bool(i%2))
            if i==0:c['global_mode']=mode;c['drc']=dict(header=True,gains=[-3])
            cases.append(c)
        cases.append(dict(cases[0],frame_type=2,preroll=cases[2]))
        cases.append({})
        yield name,opts,cases
    opts=dict(order=2,counts=[],path='replace',ambient_count=1,tce_types=[1],scene=False,rate=48000,selection=[8])
    c=dict(elements=[dict(independent=True,gain=100,left=signal,right={0:(1,[-1,0,0,0],100)})])
    yield 'sparse-ambient',opts,[c,{},dict(c,block=2),{}]


def sequences():
    yield from native_controls()
    from tns_vectors import filter_spec
    from bwe2_vectors import source_case
    from hoa_dynamic_vectors import maps
    opts = dict(order=1,counts=[],path='replace',ambient_count=4,tce_types=[1,0,3],scene=False,rate=44100)
    pair = source_case(2,0x55,gain=100)
    pair.update(independent=True,left_bwe2={},right_bwe2={},left_tns=filter_spec([1],length=14))
    for grouping in (0,0x55,0x7f):
        c=dict(block=2,elements=[dict(pair,grouping=grouping),None,dict(gain=100,bands={0:(1,[-1,0,0,0],100)})])
        yield 'joint-tools-'+str(grouping),opts,[c,{},dict(block=3,elements=[None,None,None]),{}]
    # Even one active component must consume all eight nine-slot mapping rows.
    opts=dict(order=2,counts=[1],path='salient',ambient_count=0,tce_types=[6,0],scene=False,rate=44100,dynamic=True,method=0,subbands=5)
    c=dict(elements=[{},dict(gain=100,bands={0:(1,[1,0,0,0],100)})],descriptors=descriptors([1],0,8,order=2),mappings=maps(1,True),list_mode=True)
    yield 'one-dynamic-component',opts,[c,dict(c,list_mode=False,mappings=maps(2,False)),{}]


def format_binding():
    from hoa_expanded_orders_vectors import format_binding as old
    values = dict(old()['dependencies'])
    values['transports'] = hashlib.sha256((Path(__file__).resolve().parents[1]/'data/hoa-transports-format-v1.json').read_bytes()).hexdigest()
    return dict(format_sha256=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest(),dependencies=values)


def manifest():
    from hoa_vectors import canonical
    rows=[]
    for index,(kind,opts,cases) in enumerate(sequences()):
        generated=[packet(c,**opts) for c in cases];h=hashlib.sha256(cookie(**opts))
        for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=opts,packets=len(cases),input_sha256=h.hexdigest(),
            truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in generated]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,formats=format_binding(),cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    fixtures=[];extensions=[]
    for name,opts,cases in sequences():
        first,t=packet(cases[0],**opts)
        current=next((c for c in cases if 'preroll' in c),cases[0])
        good,gt=packet(current,**opts)
        wire=''.join(bits(v,8) for v in good)
        last=next(e for e in reversed(gt['elements']) if e['present'])
        at=last['start_bit_offset']+1
        errors=dict(last_element=pack(wire[:at]+'1'+wire[at+1:]).hex())
        at=gt['tail']['ancillary_end_bit_offset']-1
        errors['tail']=pack(wire[:at]+'1'+wire[at+1:]).hex()
        at=gt['spatial']['start_bit_offset']+ (2 if opts.get('transform')==4 else 0)
        errors['spatial']=pack(wire[:at]+'1111'+wire[at+4:]).hex()
        if gt['inner']:
            child=next(e for e in reversed(gt['inner']['elements']) if e['present'])
            at=gt['inner_range']['start_bit_offset']+child['start_bit_offset']+1
            errors['inner']=pack(wire[:at]+'1'+wire[at+1:]).hex()
        maximum=shape(opts['order'],opts.get('dynamic',False))*2048
        errors['capacity']=pack('10001'+esc(maximum+1,(16,16))+'0000000000000000').hex()
        if gt['dynamic_selection']:
            # Last row remains mandatory even when only a prefix is active.
            at=gt['internal_spatial_end_bit_offset']
            errors['mapping']=pack(wire[:at]+'1'+'0000'*72+'00000000').hex()
        fixtures.append(dict(name=name,options=opts,cookie=cookie(**opts).hex(),first=first.hex(),good=good.hex(),errors=errors))
        for e in t['elements']:
            if 'extension' in e:extensions.append(dict(packet=first.hex(),start=e['extension']['start_bit_offset'],end=e['extension']['end_bit_offset'],expected=e['extension']))
    invalid=[]
    for types in ([0]*17,[6],[4,0,0,0],[1]):
        opts=dict(order=1,counts=[],path='replace',ambient_count=4,tce_types=types,scene=False)
        invalid.append(cookie(**opts).hex())
    _,opts,cases=next(v for v in native_controls() if v[0]=='dynamic')
    c=copy.deepcopy(cases[2])
    for e in c['elements']:
        e.update(gain=252)
        if 'left' in e:e.update(independent=True,left={0:(11,[8191,0],252)},right={0:(11,[8191,0],252)})
        else:e['bands']={0:(11,[8191,0],252)}
    raw,t=packet(c,**opts);wire=''.join(bits(v,8) for v in raw);at=t['internal_spatial_end_bit_offset']
    priority=dict(cookie=cookie(**opts).hex(),bad=pack(wire[:at]+'1'+'0000'*72+'00000000').hex())
    return dict(fixtures=fixtures,extensions=extensions,invalid=invalid,numeric_priority=priority)
