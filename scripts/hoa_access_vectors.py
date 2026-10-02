"""HOA access controls assembled from independently written qualified streams."""
import hashlib,json
from pathlib import Path
import hoa_shared_vectors as shared
import hoa_controls_vectors as controls
import hoa_source_layout_vectors as source
import hoa_remapping_vectors as remapping
from hoa_shared_oracle import Decoder as SharedDecoder
from hoa_salient_subbands_oracle import Decoder as SpatialDecoder
from hoa_source_layout_oracle import Decoder as SourceDecoder
from hoa_remapping_oracle import Decoder as RemappingDecoder
from access_vectors import costs as channel_costs
from hoa_salient_subbands_vectors import shape
from caf_vectors import encode as caf_encode,digest,sha
from mp4_vectors import encode as mp4_encode
PROFILE='apac-hoa-access-v1'
ROOT=Path(__file__).resolve().parents[1]

def sources():
    names={'rate-spatial-48000','rate-tools-44100','stream-hoa-channel','stream-channel-hoa','stream-reverse-starts','stream-alias-triple','stream-alias-scene-large','stream-split-source','stream-additional-whole','scene-graph-position-history','shared-drc-changing-configuration','trimming-declarations'}
    for name,opts,cases in shared.sequences():
        if name in names:yield name,shared,SharedDecoder,opts,cases
    for name,opts,cases in controls.sequences():yield name,controls,SpatialDecoder,opts,cases
    for name,opts,cases in source.sequences():
        if name in ('source-expand','matrix-controls-transports-drc','n3d-labels'):yield name,source,SourceDecoder,opts,cases
    for name,opts,cases in remapping.sequences():
        if name in ('fixed-prefix-growing-core','source-matrix-transports','dynamic-n3d'):yield name,remapping,RemappingDecoder,opts,cases

def generated():
    for index,(name,module,oracle,opts,cases) in enumerate(sources()):
        cases=list(cases)+[cases[0],cases[1],{}]
        nodes=[module.packet(c,**opts) for c in cases];payloads=[p for p,t in nodes];cookie=module.cookie(**opts)
        n=opts.get('output_coefficients') or shape(opts.get('order',3),opts.get('dynamic',False),opts.get('coefficient_count'))
        if module is shared:n=opts.get('total_channels') or sum(shared.parts(c,opts.get('rate',48000))['channels'] for c in shared.effective_components(opts['components'],opts.get('rate',48000)))
        prime=(0,31,1024,2048)[index%4];tail=(0,17,127)[index%3];valid=len(nodes)*1024-prime-tail
        ranges=[('all',0,valid),('head',0,997),('middle',max(0,len(nodes)//2*1024-prime),1031),('unaligned',max(0,(len(nodes)-2)*1024-prime+17),1027),('tail',max(0,valid-19),1024),('eof',valid,1)]
        args=dict(rate=opts.get('rate',48000),channels=n,priming=prime,remainder=tail)
        caf,caf_truth=caf_encode(cookie,payloads,variant=24+index%6,layout_tag=0,**args);mp4,mp4_truth,_=mp4_encode(cookie,payloads,variant=index,**args)
        yield dict(name=name,index=index,module=module,oracle=oracle,options=opts,cookie=cookie,payloads=payloads,truths=[t for p,t in nodes],channels=n,priming=prime,remainder=tail,valid=valid,ranges=ranges,caf=caf,mp4=mp4,mp4_truth=mp4_truth)

def costs(truth):
    frames,present,numeric=costs(truth['inner']) if truth['inner'] else (0,0,0)
    for core in truth.get('components',[truth]):
        if core.get('spatial') is not None:
            present+=sum(e['present'] for e in core['elements']);numeric+=sum(bool(e['present'] and e['channels']) for e in core['elements'])
        else:
            _,p,n=channel_costs(dict(core,inner=None));present+=p;numeric+=n
    return frames+1,present,numeric

def expected_access(data,start,requested):
    frames=min(requested,data['valid']-start);raw=start+data['priming'];end=raw+frames
    processed=len(data['payloads']) if start+frames==data['valid'] else (end+1023)//1024
    prefix=processed if not frames else min(processed,max(0,raw//1024-1));rows=[costs(t) for t in data['truths'][:prefix]]
    return dict(profile=PROFILE,mode='fast',verification_scope='full_input',prefix_scanned_packets=prefix,prefix_scanned_frames=sum(v[0] for v in rows),prefix_numeric_packets=sum(v[2]>0 for v in rows),prefix_numeric_elements=sum(v[2] for v in rows),prefix_bounded_elements=sum(v[1]-v[2] for v in rows),synthesized_packets=processed-prefix,synthesis_start_packet=prefix if processed>prefix else None,external_warmup_packets=0 if not frames else int(raw//1024>0))

def manifest():
    rows=[]
    for d in generated():
        rows.append(dict(name=d['name'],channels=d['channels'],cookie=d['cookie'].hex(),packets=[p.hex() for p in d['payloads']],caf_sha256=sha(d['caf']),mp4_sha256=sha(d['mp4']),expectations_sha256=digest([expected_access(d,s,n) for _,s,n in d['ranges']])))
    return dict(profile=PROFILE,fixtures=rows,sha256=digest(rows))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args();path=ROOT/'data/hoa-access-vectors-v1.json';data=manifest()
    if a.check:assert json.loads(path.read_text())==data
    else:path.write_text(json.dumps(data,indent=2)+'\n')
