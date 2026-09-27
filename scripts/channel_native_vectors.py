"""Frozen native-probe inputs; no candidate reports contribute expected values."""
import hashlib,json
from channel_vectors import LAYOUTS,WINDOWS,configuration,excitation,sequences,packet,cookie

def groups():
    for n in LAYOUTS:
        for rate in (48000,44100):
            cases=[excitation(n,ch,w,gain=100) for ch in range(n) for w in WINDOWS]
            cases+=[dict(elements=[None if mask&(1<<i) else {} for i in range(len(LAYOUTS[n][2]))]) for mask in range(1<<len(LAYOUTS[n][2]))]
            for cfg in configuration(n):
                if cfg['kind']=='cpe':continue
                for block,mask in WINDOWS:
                    elems=[{} for _ in LAYOUTS[n][2]];elems[cfg['element_index']]=dict(block=block,grouping=mask,max_sfb=14 if block==2 else 49)
                    if cfg['kind']=='sce':elems[cfg['element_index']]['bwe2']=dict(lsf=[511,511],gains=[0]*(8 if block==2 and mask==0 else 4 if block==2 and mask==0x55 else 1))
                    cases.append(dict(elements=elems))
                if cfg['kind']=='sce':
                    elems=[{} for _ in LAYOUTS[n][2]];elems[cfg['element_index']]=dict(bwe2=dict(lsf=[511,511],gains=[]));cases.append(dict(elements=elems))
            for block in range(4):
                c=excitation(n,0,((block+1)%4,0),gain=100);c.update(frame_type=2,preroll=excitation(n,n-1,(block,0),gain=100));cases.append(c)
            for first in range(0,len(cases),8):yield f'unit-{n}-{rate}-{first}',n,rate,dict(scene=False,drc=False,rich=False),cases[first:first+8],True
            joint=[(options,seq) for kind,options,seq in sequences(n) if kind=='joint_tools']
            # Both directions/gains and all windows remain in the portable
            # matrix; these six probes isolate the additional scalar tool route.
            for i in range(0,len(joint),4):
                options,seq=joint[i];yield f'joint-{n}-{rate}-{i}',n,rate,options,seq,False
            metadata=[(options,seq) for kind,options,seq in sequences(n) if kind=='metadata_updates']
            for i,(options,seq) in enumerate(metadata):yield f'metadata-{n}-{rate}-{i}',n,rate,options,seq,False

def controls():
    for n,preset in ((1,'mono'),(6,'surround51'),(8,'surround71')):
        for rate in (48000,44100):
            for profile in ('default','none','music','speech','movie','capture'):
                for signal in ('noise','impulse'):yield dict(channels=n,preset=preset,rate=rate,profile=profile,signal=signal,duration='0.125',seed=1)

def manifest():
    rows=[]
    for label,n,rate,options,cases,hard in groups():
        config=cookie(n,rate,**options);h=hashlib.sha256(config);children=0
        for case in cases:
            raw,truth=packet(case,n,rate,**options);h.update(len(raw).to_bytes(8,'little'));h.update(raw);children+=truth['inner'] is not None
        rows.append(dict(label=label,channels=n,rate=rate,packets=len(cases),embedded_frames=children,hard_float_unit_gate=hard,input_sha256=h.hexdigest()))
    frozen=dict(probes=rows,encoder_controls=list(controls()))
    return dict(schema_version=1,**frozen,sha256=hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest())
