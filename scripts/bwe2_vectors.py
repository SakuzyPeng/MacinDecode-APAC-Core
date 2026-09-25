"""Artificial BWE2 packets with independent wire, group and copy-range truth."""
import copy

from tns_vectors import packet as tns_packet
from spectrum_vectors import TABLES, bits, pack
from cac_vectors import windows


def regions(ics):
    short=ics['block_type']==2
    offsets=TABLES['short_offsets' if short else 'long_offsets']
    lower=offsets[ics['max_sfb']]*(8 if short else 1)<384
    size=128 if short else 1024
    source_start=16 if short else 128
    start=(32 if lower else 48) if short else (256 if lower else 384)
    count=4 if lower else 2
    return [dict(window_index=w,source_start_line=w*size+source_start,source_end_line=w*size+start,
                 target_start_line=w*size+start,target_end_line=w*size+start+(64 if short else 512),
                 repetitions=count) for w in range(8 if short else 1)]


def packet(case,rate=48000):
    raw,truth=tns_packet(case,rate)
    start=truth['tns_end_bit_offset']
    payload=''.join(bits(v,8) for v in raw)[:start]
    specs=[case.get('left_bwe2'),case.get('right_bwe2')]
    flags=list(case.get('bwe2_flags',[s is not None for s in specs]))
    if len(flags)!=2:raise ValueError('BWE2 requires two CPE control bits')
    payload+=''.join(bits(int(b),1) for b in flags)
    channels=[]
    for ch in (0,1):
        ics=truth['channels'][ch]['ics']
        active=bool(ics['max_sfb'] and (flags[ch] or (ch==1 and channels[0]['active'])))
        parameters=None;source=None
        if active:
            if flags[ch]:
                spec=specs[ch] or {}
                lsf=spec.get('lsf',[0,0]);gains=spec.get('gains',[32]*len(ics['window_groups']))
                if len(lsf)!=2 or len(gains)!=len(ics['window_groups']):raise ValueError('BWE2 parameter shape')
                at=len(payload)
                payload+=''.join(bits(v,9) for v in lsf)+''.join(bits(v,6) for v in gains)
                parameters=dict(lsf_indices=list(lsf),gain_indices=list(gains),start_bit_offset=at,end_bit_offset=len(payload))
                source=ch
            else:
                parameters=copy.deepcopy(channels[0]['parameters']);source=0
        channels.append(dict(channel_index=ch,active=active,parameter_source_channel=source,parameters=parameters))
    truth['bwe2']=dict(start_bit_offset=start,end_bit_offset=len(payload),control_bits=flags,channels=channels)
    truth['bwe2_regions']=[regions(c['ics']) if p['active'] else [] for c,p in zip(truth['channels'],channels)]
    return pack(payload)+b'\0',truth


def boundary_cases():
    for block,grouping in windows():
        for flags in ((False,False),(False,True),(True,False),(True,True)):
            for maximum in (0,1,14 if block==2 else 49):
                yield dict(kind='boundary',block=block,grouping=grouping,max_sfb=maximum,bwe2_flags=flags,
                           left={0:(1,[1,0,0,0],100)} if maximum else {},gain=100)
    for lb,lg,rb,rg in ((0,0,2,0x7f),(2,0,0,0),(2,0x55,2,0x7f),(2,0,2,0x55)):
        yield dict(kind='reuse_shape',independent=True,block=lb,grouping=lg,right_block=rb,right_grouping=rg,
                   gain=100,left={0:(1,[1,0,0,0],100)},right={0:(1,[1,0,0,0],100)},left_bwe2={})
    yield dict(kind='right_only',independent=True,left={},right={0:(1,[1,0,0,0],100)},gain=100,right_bwe2={})
    yield dict(kind='left_only',independent=True,left={0:(1,[1,0,0,0],100)},right={},gain=100,left_bwe2={})


def source_case(block=0,grouping=0,upper=False,position=None,flat=False,side='left',gain=100):
    short=block==2;offsets=TABLES['short_offsets' if short else 'long_offsets']
    cutoff=(48 if upper else 32) if short else (384 if upper else 256)
    source=16 if short else 128
    if position is None:position=source
    # The tier is selected by the coded SFB endpoint, not by an invented field.
    maximum=next(i for i,v in enumerate(offsets) if v>=cutoff) if upper or flat else next(i for i,v in enumerate(offsets) if v>position)
    bands={}
    if flat:
        # Constant power with an aperiodic sign signature keeps the LPC system
        # well conditioned and makes incorrect copy mappings visible.
        state=0x42574532
        for sfb in range(maximum):
            values=[]
            for _ in range(offsets[sfb+1]-offsets[sfb]):
                state=(1664525*state+1013904223)&0xffffffff
                values.append(1 if state&0x80000000 else -1)
            bands[sfb]=(1,values,gain)
    else:
        sfb=next(i for i in range(len(offsets)-1) if offsets[i]<=position<offsets[i+1])
        values=[0]*(offsets[sfb+1]-offsets[sfb]);values[position-offsets[sfb]]=1
        bands[sfb]=(1,values,gain)
    case=dict(block=block,grouping=grouping,gain=gain,max_sfb=maximum,**{side:bands})
    case[side+'_bwe2']={}
    return case


def conditioning_cases():
    # Interior dictionary pairs missed by the axis sweeps. Their small target
    # envelopes expose cancellation in expanded LPC coefficients at both ends
    # of the frequency grid; quarter-step gains exercise Float32 rounding.
    for indices in ((163,24),(67,492),(482,48)):
        for block,grouping in ((0,0),(2,0x55)):
            for upper in (False,True):
                for gain in (159,255):
                    for side in ('left','right'):
                        case=source_case(block,grouping,upper,flat=True,side=side,gain=gain)
                        case[side+'_bwe2']=dict(lsf=list(indices),gains=[63]*(4 if block==2 else 1))
                        yield dict(case,kind='lpc_conditioning')


def cases():
    yield from boundary_cases()
    for axis in (0,1):
        for partner in (0,511):
            for value in range(512):
                indices=[partner,partner];indices[axis]=value
                case=source_case();case['left_bwe2']=dict(lsf=indices)
                yield dict(case,kind='codebook_stress')
    for block,grouping in windows():
        for gain_index in range(64):
            for side in ('left','right'):
                case=source_case(block,grouping,upper=bool(gain_index%2),flat=True,side=side)
                count=len(packet(case)[1]['channels'][0]['ics']['window_groups'])
                case[side+'_bwe2']=dict(gains=[gain_index]*count)
                yield dict(case,kind='gain_control')
    for block,grouping in ((0,0),(2,0x55)):
        for upper in (False,True):
            start=16 if block==2 else 128
            end=(48 if upper else 32) if block==2 else (384 if upper else 256)
            for position in range(start,end):
                for side in ('left','right'):
                    yield dict(source_case(block,grouping,upper,position,side=side),kind='copy_stress')
    for block,grouping in windows():
        for upper in (False,True):
            for flags in ((False,True),(True,False),(True,True)):
                case=source_case(block,grouping,upper,flat=True)
                case['right']=copy.deepcopy(case['left']);case['bwe2_flags']=flags
                count=len(packet(case)[1]['channels'][0]['ics']['window_groups'])
                case['left_bwe2']=dict(gains=[5+6*g for g in range(count)])
                case['right_bwe2']=dict(lsf=[0,0],gains=[63-5*g for g in range(count)])
                yield dict(case,kind='window_control')
        offsets=TABLES['short_offsets' if block==2 else 'long_offsets']
        for maximum in range(1,len(offsets)):
            case=source_case(block,grouping,position=min(offsets[maximum]-1,16 if block==2 else 128))
            case['max_sfb']=maximum
            yield dict(case,kind='band_stress')
        for gain in (0,160,255):
            for indices in ((0,511),(511,0),(511,511)):
                case=source_case(block,grouping,upper=True,gain=gain)
                sfb=next(iter(case['left']));width=len(case['left'][sfb][1]);values=[0]*width
                source=16 if block==2 else 128;position=source-TABLES['short_offsets' if block==2 else 'long_offsets'][sfb]
                values[position:position+2]=[8191,-8190]
                case['left'][sfb]=(11,values,gain)
                case['left_bwe2']=dict(lsf=list(indices),gains=[63]*len(packet(case)[1]['channels'][0]['ics']['window_groups']))
                yield dict(case,kind='escape_pressure')
        for direction in (False,True):
            from tns_vectors import filter_spec
            case=source_case(block,grouping,upper=True,flat=True,gain=160)
            case['right']=copy.deepcopy(case['left']);case['cac_gain']=26
            case['left_tns']=filter_spec([1,-1,1],14 if block==2 else 49,direction,window=0)
            case['right_tns']=filter_spec([-1,1,-1],14 if block==2 else 49,not direction,window=0)
            case['right_bwe2']=dict(lsf=[511,511]);case['left_bwe2']=dict(lsf=[0,0])
            yield dict(case,kind='combined_pressure')
    # Source-only, encoded high-frequency-only and out-of-copy-region values.
    for block,grouping in windows():
        case=source_case(block,grouping,upper=True)
        sfb=13 if block==2 else 48;case['left'][sfb]=(1,[1,0,0,0],100)
        case['max_sfb']=sfb+1
        yield dict(case,kind='preserved_high_band')
        zero=dict(block=block,grouping=grouping,max_sfb=sfb+1,left_bwe2={},left={sfb:(1,[1,0,0,0],100)},gain=100)
        yield dict(zero,kind='zero_source')
    yield from conditioning_cases()


def sequences():
    for case in cases():
        block=case.get('block',0)
        if case.get('right_block',block)!=block:
            # Mixed long/short headers need independent histories on each side;
            # these are covered structurally and in the dedicated switch sequence.
            continue
        if block==0:seq=[{},case,case,{},{}]
        else:
            seq=[{},dict(block=1),dict(block=2),dict(block=3),{},{}];seq[block]=case
        yield case['kind'],seq
    enabled=source_case();disabled=dict(enabled,bwe2_flags=(False,False))
    independent=dict(enabled,independent=True,right=copy.deepcopy(enabled['left']),right_bwe2={})
    yield 'tool_header_switch',[{},enabled,disabled,independent,enabled,{},{}]
    left_short=source_case(2,0,upper=True);left_short.update(independent=True,right_block=0,right={0:(1,[1,0,0,0],100)})
    yield 'mixed_window_history',[{},dict(independent=True,block=1,right_block=0),left_short,dict(independent=True,block=3,right_block=0),{},{}]
