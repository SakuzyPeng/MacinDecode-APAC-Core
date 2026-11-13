"""DRC-off sequences and independent identity-plus-packet mathematics inputs."""
import copy
import hashlib
import json
from drc_vectors import packet as payload_packet,cookie,cases,bundle
from packet_vectors import basis,WINDOWS,sequences as packet_sequences,identity
from tns_vectors import filter_spec


def decorate(case,rich,encoder_tail=True):
    out=copy.deepcopy(case);out['encoder_tail']=encoder_tail
    out.setdefault('drc',{})['rich']=rich
    if 'preroll' in out:out['preroll']=decorate(out['preroll'],rich,encoder_tail)
    return out


def packet(case,rate,options):
    return payload_packet(decorate(case,options.get('rich',False)),rate,options.get('scene',False))


def oracle_truth(truth):
    return dict(absent=truth['spectra'] is None,spectra=truth['spectra'],
                embedded_preroll=dict(truth=oracle_truth(truth['inner'])) if truth['inner'] else None)


def sequences():
    for i,(kind,options,case) in enumerate(cases()):
        rich=bool(i%2);options=dict(options,rich=rich,scene_drc_flag=bool(options.get('scene') and rich))
        initial=basis(WINDOWS[i%len(WINDOWS)],'right' if i%2 else 'left',gain=255 if i%5==0 else 160,independent=bool(i%3))
        initial['drc']=dict(gains=[-100])
        yield kind,options,[initial,case,dict(absent=True)]
    for kind,options,seq in packet_sequences():
        if kind!='joint_tools':continue
        for i,case in enumerate(seq):case['drc']=dict(mode=1,gains=[-16,-17,-15],times=[1,3],header=i%2==1,loudness_value=128+i)
        yield kind,dict(scene=True,scene_drc_flag=True,rich=True),seq
    for window in WINDOWS:
        for sign in (-1,1):
            c=dict(block=window[0],grouping=window[1],gain=255,left={0:(11,[8191*sign,-16],255)},
                   right={0:(11,[-8191*sign,17],255)},independent=sign<0,cac_gain=26 if sign>0 else 0,
                   left_tns=filter_spec([1],14 if window[0]==2 else 49,False,window=0),
                   drc=dict(mode=1,gains=[-255,-256],times=[1]))
            yield 'escape_pressure',dict(scene=True,scene_drc_flag=True,rich=True),[c,dict(absent=True),{}]
    for full in (False,True):
        seq=[dict(basis((i%4,0),'right' if i%2 else 'left',independent=bool(i%2)),
                  drc=dict(header=True,metadata_only=not full,loudness_value=v,gains=[i]))
             for i,v in enumerate((0,1,127,128,165,255))]
        seq.extend([dict(absent=True),{}])
        yield 'metadata_updates',dict(scene=True,scene_drc_flag=True,rich=True),seq


def manifest():
    records=[]
    for rate in (48000,44100):
        for index,(kind,options,seq) in enumerate(sequences()):
            generated=[packet(c,rate,options) for c in seq]
            records.append(dict(rate=rate,index=index,kind=kind,packets=len(seq),
                input_sha256=identity(cookie(rate,**options),[p for p,_ in generated]),
                truth_sha256=hashlib.sha256(json.dumps([t for _,t in generated],sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(schema_version=1,rules_version='apac-drc-off-state-v1',sequences=records,
                sha256=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest())
