#!/usr/bin/env python3
"""Static remapping, independent Decimal PCM, and carrier-domain acceptance."""
import argparse,hashlib,json,platform,struct,subprocess
from pathlib import Path
import hoa_remapping_vectors as vectors
from hoa_remapping_oracle import Decoder
from validate import require,write_json
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from validate_packets import coverage
from validate_hoa import nodes
from validate_channels import compare,merge_metrics,same_fields,digest,float_bytes
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def measure(report,key,actual,wanted,where):
    result=compare(actual,wanted,where);merge_metrics(report['metrics'][key],result)
    require(result['passed'],str(result.get('first_failure')))


def ranges(binary,root,opts,payloads,full):
    n=opts['output_coefficients'];stride=4*n;prime=31;remainder=17
    total=len(payloads)*1024-prime-remainder;expected=full[prime*stride:(prime+total)*stride]
    requests=[(0,total),(0,1031),(1024-prime,1031),(total-9,100),(total,1)]
    records=[]
    for name,writer in [('caf',caf_encode),('mp4',mp4_encode)]:
        extra=dict(layout_tag=opts['source_layout']['tag'],channel_labels=opts['source_layout'].get('labels')) if name=='caf' else {}
        source=root/name;source.write_bytes(writer(vectors.cookie(**opts),payloads,rate=opts['rate'],channels=n,priming=prime,remainder=remainder,**extra)[0])
        for index,(start,count) in enumerate(requests):
            out=root/(name+str(index));decoded=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes();saved=min(count,total-start)
            require(raw==expected[start*stride:(start+saved)*stride] and decoded['saved_frames']==saved and decoded['input']['consistency_verified'],'source-layout range differs')
            records.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    vectors.bundle(root/'trimmed',payloads,priming=prime,remainder=remainder,**opts)
    command(binary,'decode-sq',root/'trimmed','--out',root/'trimmed-pcm','--start-frame',1024-prime,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[(1024-prime)*stride:(2055-prime)*stride],'packet directory range differs')
    return records


def validate(binary,report,reference):
    frozen=json.loads((vectors.ROOT/'data/hoa-remapping-vectors-v1.json').read_text())
    require(frozen==vectors.manifest(),'source-layout vectors changed')
    if reference:
        require(reference['passed'] and reference['vector_manifest_sha256']==frozen['sha256'] and reference['format_sha256']==frozen['format_sha256'],'reference identity differs')
    for identity,(name,opts,cases) in zip(frozen['cases'],vectors.sequences()):
        with workspace(report,name) as root:
            generated=[vectors.packet(case,**opts) for case in cases];payloads=[raw for raw,t in generated]
            vectors.bundle(root/'bundle',payloads,**opts)
            parsed=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
            require(parsed['hoa_packets_complete']==len(cases) and parsed['errors']==0,'incomplete source layout')
            rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
            require(len(rows)==len(cases),'missing parsed packet')
            oracle=None if reference else Decoder(opts);expected_pcm=[];cursor=0
            n=opts['output_coefficients'];m=opts.get('coefficient_count',(opts['order']+1)**2)
            for index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                if oracle:expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    coverage(node);h=node['hoa'];source=h.get('source_layout')
                    require(node['packet_complete'],'incomplete remapping packet')
                    same_fields(h['static_remapping'],t['static_remapping'],'static remapping')
                    scaled=source['channels'] if source else h['channels_after_hoa']
                    internal=h['channels_after_hoa'] if source or not h.get('dynamic_selection') else h['dynamic_selection']['before_selection']
                    require(node['channel_count']==n and h['coefficient_count']==m and len(internal)==m and len(scaled)==n,'remapping dimensions differ')
                    if source:
                        require(source['layout']['tag']==opts['source_layout']['tag'],'source tag differs')
                        require([d['label'] for d in source['layout']['descriptions']]==opts['source_layout'].get('labels',[]),'source labels differ')
                    active=t.get('frame_options',opts);ambient=active['ambient_count'];core=ambient+len(active['counts']);mapping=t['static_remapping']['core_to_transport']
                    physical=lambda i:mapping[i] if i<len(mapping) else i
                    transported=sum(2 if kind==1 else 0 if kind==6 else 1 for kind in opts['tce_types']);used={physical(i) for i in range(core)}
                    for role in (h.get('mixed'),h.get('additive'),h.get('dynamic_selection')):
                        if role:
                            require(role['ambient_transport_channels']==[physical(i) for i in range(ambient)] and role['salient_transport_channels']==[physical(i) for i in range(ambient,core)],'physical role map differs')
                            if 'unused_transport_channels' in role:require(role['unused_transport_channels']==[i for i in range(transported) if i not in used],'unused physical carrier set differs')
                    if h.get('dynamic_selection'):same_fields(h['dynamic_selection'],t['dynamic_selection'],'dynamic mapping')
                    require(node['component_end_bit_offset']==t['core_end_bit_offset'] and node['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'source bit boundaries differ')
                    same_fields(h['spatial'],t['spatial'],'source spatial');same_fields(node['packet_tail'],t['tail'],'tail')
                    for actual,wanted in zip(node['elements'],t['elements']):
                        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','cac','tns','bwe2'):same_fields(actual[key],wanted[key],key)
                    if oracle:
                        expected=oracle.records[cursor];cursor+=1
                        measure(report,'internal',[v for c in internal for v in c['scaled']],[v for c in expected['internal'] for v in c],dict(case=name,packet=index,stage='internal'))
                        measure(report,'source',[v for c in scaled for v in c['scaled']],[v for c in expected['scaled'] for v in c],dict(case=name,packet=index,stage='source'))
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');raw=(root/'pcm/pcm.f32le').read_bytes()
            require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'source backend/state differs')
            require(decoded['saved_frames']==len(cases)*1024 and len(raw)==len(cases)*1024*n*4,'source PCM length differs')
            require(decoded['drc_processing']==decoded['loudness_normalization']=='off','processing policy changed')
            impl=decoded['pcm']['decoder_settings']['implementation']['value']
            same_fields(impl['hoa_static_remapping'],vectors.mapping_info(opts),'PCM static mapping')
            if oracle:measure(report,'pcm',struct.unpack('<'+str(len(raw)//4)+'f',raw),expected_pcm,dict(case=name,stage='pcm'))
            selected=name in ('width-5','duplicate-star','fixed-prefix-growing-core','source-matrix-transports','dynamic-n3d')
            record=dict(identity,passed=True,stages_sha256=digest(rows),pcm_sha256=hashlib.sha256(raw).hexdigest(),ranges=ranges(binary,root,opts,payloads,raw) if selected else [])
            if reference:require(record==reference['cases'][identity['index']],'cross-build source stages differ')
            report['cases'].append(record)
        print('static remapping',len(report['cases']),len(frozen['cases']),name,flush=True)
    require(len(report['cases'])==len(frozen['cases']),'missing source cases')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing/report exists')
    manifest=vectors.manifest()
    report=dict(passed=False,profile=vectors.PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),platform=platform.platform(),vector_manifest_sha256=manifest['sha256'],format_sha256=manifest['format_sha256'],atol=1e-6,rtol=1e-5,cases=[],errors=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('internal','source','pcm')},failure_directory=str(a.report.with_suffix('.failures')))
    try:
        validate(a.binary.resolve(),report,json.loads(a.reference_report.read_text()) if a.reference_report else None)
        require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed')
        report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    report['stage_sha256']=digest(report['cases']);write_json(a.report,report)
    print(json.dumps({k:report[k] for k in ('passed','stage_sha256','metrics','errors')}));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
