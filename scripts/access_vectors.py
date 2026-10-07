"""Independent fast-range inputs, expected stage selection and byte coordinates."""
import copy,itertools,json,hashlib
from channel_vectors import LAYOUTS,WINDOWS,cookie,packet,excitation,layout
from mp4_vectors import cases,encode as mp4_encode
from caf_vectors import encode as caf_encode,digest,sha

PROFILE='apac-sq-access-v1'

def generated(channels,rate,index,case):
    kind,options,original=case;seq=copy.deepcopy(original)
    seq += [excitation(channels,index%channels,WINDOWS[index%len(WINDOWS)],gain=166),
            excitation(channels,(index+1)%channels,WINDOWS[(index+1)%len(WINDOWS)],gain=160,quantized=-1),
            dict(elements=[None]*len(layout(channels)[2]))]
    parts=[packet(c,channels,rate,**options) for c in seq];payloads=[p for p,_ in parts]
    config=cookie(channels,rate,**options);prime=(0,1,1024,2048)[index%4];remainder=(0,1,127,1023)[index//4%4]
    kwargs=dict(rate=rate,channels=channels,priming=prime,remainder=remainder,variant=index)
    caf,caf_truth=caf_encode(config,payloads,layout_tag=(layout(channels)[0]<<16)|channels,**kwargs);mp4,mp4_truth,_=mp4_encode(config,payloads,**kwargs)
    valid=len(seq)*1024-prime-remainder
    ranges=[('all',0,valid),('head',0,997),('target',(len(original)+1)*1024+11-prime,1051),('tail',max(0,valid-1001),2048),('eof',valid,17)]
    return dict(config=config,payloads=payloads,truths=[t for _,t in parts],caf=caf,mp4=mp4,caf_truth=caf_truth,mp4_truth=mp4_truth,ranges=ranges)

def costs(truth):
    frames,present,numeric=(0,0,0) if truth['inner'] is None else costs(truth['inner'])
    for e in truth['elements']:
        if not e['present']:continue
        present+=1
        tns=any(f['order']>0 and f['start_line']<f['end_line'] for channel in e['tns'] for w in channel['windows'] for f in w['filters'])
        bwe=e['bwe2'] is not None and any(c['parameters'] is not None and source['ics']['max_sfb']>0 for c,source in zip(e['bwe2']['channels'],e['channels']))
        numeric+=int(tns or bwe)
    return frames+1,present,numeric

def expected_access(data,start,requested):
    table=data['mp4_truth']['packet_table'];prime=table['priming_frames'];valid=table['valid_frames'];frames=min(requested,valid-start)
    raw_start=start+prime;raw_end=raw_start+frames;n=len(data['payloads'])
    processed=n if start+frames==valid else (raw_end+1023)//1024
    prefix=processed if frames==0 else min(processed,max(0,raw_start//1024-1))
    rows=[costs(t) for t in data['truths'][:prefix]]
    return dict(profile=PROFILE,mode='fast',verification_scope='full_input',prefix_scanned_packets=prefix,
                prefix_scanned_frames=sum(r[0] for r in rows),prefix_numeric_packets=sum(r[2]>0 for r in rows),
                prefix_numeric_elements=sum(r[2] for r in rows),prefix_bounded_elements=sum(r[1]-r[2] for r in rows),
                synthesized_packets=processed-prefix,synthesis_start_packet=prefix if processed>prefix else None,
                external_warmup_packets=0 if frames==0 else int(raw_start//1024>0))

def manifest():
    rows=[]
    for channels,rate in itertools.product(LAYOUTS,(48000,44100)):
        for index,case in enumerate(cases(channels)):
            d=generated(channels,rate,index,case)
            truth=[dict(name=name,start=start,requested=count,access=expected_access(d,start,count)) for name,start,count in d['ranges']]
            rows.append(dict(channels=channels,rate=rate,index=index,kind=case[0],packets=len(d['payloads']),
                caf_sha256=sha(d['caf']),mp4_sha256=sha(d['mp4']),expectations_sha256=digest(truth)))
    return dict(schema_version=1,profile=PROFILE,cases=rows,sha256=digest(rows))
