"""Small fixed-order/rate extension vectors; legacy generator defaults stay frozen."""
import copy,hashlib,json
from hoa_vectors import excitation,canonical
import hoa_vectors as ambient
import hoa_salient_vectors as salient
from spectrum_vectors import TABLES
from tns_vectors import filter_spec
from bwe2_vectors import source_case

PROFILE='apac-hoa-orders-validation-v1'


def writers(options):
    return salient if options['salient'] else ambient


def arguments(options):
    return {k:v for k,v in options.items() if k!='salient'}


def sequences():
    def options(order,rate,drc=False,sal=None):
        return dict(order=order,rate=rate,salient=(order==2) if sal is None else sal,scene=True,drc=drc,rich=drc)
    for k in range(4):
        yield 'foa_basis_'+str(k),options(1,48000 if k%2==0 else 44100),[excitation(k,order=1),dict(elements=[None]*4),{}]
    for k in range(9):
        yield 'soa_basis_'+str(k),options(2,48000 if k%2==0 else 44100),[salient.basis(k,k%5,1,order=2),{},{}]
    yield 'soa_modes',options(2,44100),[dict(salient.basis(8,4,m,order=2,cluster=2,angles=(37,121)),global_mode=m) for m in range(6)]+[salient.basis(8,4,3,order=2),{}]
    yield 'soa_clusters_directions',options(2,48000),[salient.basis(8,0,4,order=2,cluster=c) for c in range(4)]+[salient.basis(4,2,5,order=2,angles=a) for a in ((0,0),(90,90),(359,180),(511,255))]+[{}]
    for order,rate in ((1,48000),(2,44100)):
        seq=[]
        for block,mask in ((1,0),(2,0),(2,0x55),(2,0x7f),(3,0)):
            c=excitation(3,block,mask,order=1) if order==1 else salient.basis(8,4,2,order=2,block=block,grouping=mask)
            seq.append(c)
        yield 'window_transitions_'+str(order),options(order,rate),seq+[{}]
    boundaries=[]
    for block in (0,2):
        c=salient.basis(0,0,2,order=2,block=block,grouping=0x55);ends=[v//8 if block==2 else v for v in salient.ENDS];offsets=TABLES['short_offsets' if block==2 else 'long_offsets'];bands={}
        for point in [0]+[v for end in ends[:-1] for v in (end-1,end)]+[ends[-1]-1]:
            sfb=next(i for i in range(len(offsets)-1) if offsets[i]<=point<offsets[i+1]);q=[0,0];q[(point-offsets[sfb])%2]=1;bands[sfb]=(11,q,160)
        c['elements'][0]['bands']=bands
        for sc in range(5):
            for sb in range(4):c['descriptors'][sc][sb]['quantized']=[48 if sc==0 and k==2*sb else 32 for k in range(9)]
        boundaries.append(c)
    yield 'soa_subband_boundaries',options(2,48000),boundaries+[{}]
    unused=salient.basis(8,8,1,order=2);unused['descriptors']=salient.descriptors(1,8,0,order=2)
    yield 'soa_unused_transport',options(2,44100),[unused,salient.basis(8,4,1,order=2),{}]
    for order,rate in ((1,44100),(2,48000)):
        n=(order+1)**2
        a=excitation(n-1,order=order) if order==1 else salient.basis(8,4,1,order=2)
        b=excitation(0,3,order=order) if order==1 else salient.basis(8,0,3,order=2,block=3)
        child=excitation(1,2,0x55,order=order) if order==1 else salient.basis(6,1,4,order=2,block=2,grouping=0x55,cluster=1)
        a['drc']=dict(header=True,gains=[-3]);b.update(frame_type=2,preroll=child,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))
        yield 'drc_embedded_'+str(order),options(order,rate,True),[a,b,dict(elements=[None]*n),{}]
    for sal in (False,True):
        a=salient.basis(15,4,1) if sal else excitation(15)
        b=salient.basis(10,2,3,block=2,grouping=0x55) if sal else excitation(0,2,0x55)
        yield 'hoa3_44100_'+str(sal),options(3,44100,True,sal),[a,dict(b,frame_type=2,preroll=a),{},{}]
    for order,rate in ((1,48000),(2,44100)):
        c=excitation(3,order=1) if order==1 else salient.basis(8,4,1,order=2);source=source_case(0,0,upper=True,flat=True,gain=160);slot=3 if order==1 else 4
        c['elements'][slot]=dict(gain=160,bands=source['left'],max_sfb=49,tns=filter_spec([1,-1],direction=True),bwe2=dict(lsf=[163,24],gains=[32]))
        yield 'tns_bwe2_rate_'+str(rate),options(order,rate),[c,{},{}]
    c=salient.basis(8,0,4,order=2,cluster=3,gain=255,q=8191)
    c['elements'][1]=copy.deepcopy(excitation(1,gain=255,q=-8191,order=2)['elements'][1]);c['descriptors'][1]=copy.deepcopy(c['descriptors'][0])
    yield 'soa_escape_cancellation',options(2,48000),[c,salient.basis(8,4,3,order=2,gain=0,q=-8191),{}]
    c=dict(block=2,elements=[None]*9);c['elements'][8]=dict(grouping=0x55,bwe2=dict(lsf=[511,511],gains=[]))
    yield 'soa_zero_sfb_bwe2',options(2,44100),[c,{},{}]


def manifest():
    rows=[]
    for index,(kind,options,cases) in enumerate(sequences()):
        module=writers(options);args=arguments(options);cfg=module.cookie(**args);parts=[module.packet(c,**args) for c in cases];h=hashlib.sha256(cfg)
        for raw,_ in parts:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(index=index,kind=kind,configuration=options,packets=len(parts),input_sha256=h.hexdigest(),truth_sha256=hashlib.sha256(json.dumps(canonical([t for _,t in parts]),sort_keys=True,separators=(',',':')).encode()).hexdigest()))
    return dict(profile=PROFILE,cases=rows,sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def state_fixtures():
    rows=[]
    for order,rate in ((1,44100),(2,48000)):
        n=(order+1)**2;module=ambient if order==1 else salient;args=dict(order=order,rate=rate,drc=True,rich=True)
        a=excitation(n-1,order=order) if order==1 else salient.basis(n-1,4,1,order=2)
        a['drc']=dict(header=True,gains=[-3]);a['mode']=3
        b=excitation(0,order=order) if order==1 else salient.basis(n-1,0,3,order=2);b['elements'][-1]={};b['mode']=1
        first,_=module.packet(a,**args);good,t=module.packet(b,**args)
        bad=bytearray(good);p=t['elements'][-1]['start_bit_offset']+1;bad[p//8]|=1<<(7-p%8)
        def position(truth):return truth['spatial']['start_bit_offset']+1 if order==1 else truth['spatial']['salient']['descriptors'][-1]['start_bit_offset']
        def broken(raw,p):
            value=bytearray(raw)
            for i in range(3):value[(p+i)//8]|=1<<(7-(p+i)%8)
            return value.hex()
        tail=bytearray(good);p=t['tail']['ancillary_end_bit_offset']-1;tail[p//8]|=1<<(7-p%8)
        outer,ot=module.packet(dict(b,frame_type=2,preroll=dict(a,drc=dict(header=True,metadata_only=True,loudness_value=255,gains=[2]))),**args)
        rows.append(dict(order=order,rate=rate,channels=n,cookie=module.cookie(**args).hex(),first=first.hex(),next=good.hex(),last_element_error=bad.hex(),late_spatial_error=broken(good,position(t)),late_tail_error=tail.hex(),embedded_error=broken(outer,ot['inner_range']['start_bit_offset']+position(ot['inner'])),outer_after_embedded_error=broken(outer,position(ot))))
    return dict(fixtures=rows)
