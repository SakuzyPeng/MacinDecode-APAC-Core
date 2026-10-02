#!/usr/bin/env python3
"""Shared-rate and multi-ASC acceptance with independent Decimal PCM and ranges."""
import argparse,hashlib,json,platform,struct,subprocess
from pathlib import Path
import hoa_shared_vectors as vectors
from hoa_shared_oracle import Decoder
from channel_oracle import spectra
from validate import require,write_json
from validate_channels import compare,merge_metrics,same_fields,digest
from validate_portable import source_digest
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate_packets import coverage
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def verify_node(report,truth,opts,metrics,name):
    require(report['packet_complete'] and report['status']=='complete','incomplete packet: '+name)
    require(report['stop_bit_offset']==truth['packet_end_bit_offset'] and report['component_end_bit_offset']==truth['core_end_bit_offset'],'outer boundaries: '+name)
    require(not report['unknown_ranges'],'unparsed bytes: '+name);coverage(report)
    actual=report['components'] if 'components' in report else [report]
    require(len(actual)==len(truth['components']),'component count: '+name)
    for i,(core,t) in enumerate(zip(actual,truth['components'])):
        if 'components' in report:
            require(core['core_complete'] and core['start_bit_offset']==t['core_start_bit_offset'] and core['end_bit_offset']==t['core_end_bit_offset'],'core boundaries: '+name)
        require(len(core['elements'])==len(t['elements']),'element count')
        for e,expected in zip(core['elements'],t['elements']):
            for key in ('present','start_bit_offset','end_bit_offset','shared_ics','cac','tns','bwe2'):
                same_fields(e[key],expected[key],name+'.'+key)
            if metrics is not None and e['present'] and e['channels']:
                value=spectra(expected,opts.get('rate',48000))['bwe2']
                result=compare([v for c in e['channels_after_bwe2'] for v in c['scaled']],[v for c in value for v in c],dict(case=name,component=i,stage='transport'))
                require(result['passed'],str(result['first_failure']));merge_metrics(metrics['transport'],result)
        if 'hoa' in core and core['hoa']:
            require(core['hoa']['hoa_complete'],'incomplete HOA core')
            same_fields(core['hoa']['spatial'],t['spatial'],name+'.spatial')
    if truth['drc']:
        require(report['drc_complete'] and not report['drc_processing_applied'],'DRC processing policy')
        same_fields(report['drc'],truth['drc'],'DRC')
    if truth.get('scene_graph') is not None:
        graph=report['packet_tail']['scene_graph'];same_fields(graph,truth['scene_graph'],'scene graph')
        require(not graph['processing_applied'],'scene renderer unexpectedly enabled')
    if truth['inner']:
        require(report['embedded_preroll'] is not None,'missing embedded frame')
        verify_node(report['embedded_preroll']['report'],truth['inner'],opts,metrics,name+'.inner')


def ranges(binary,root,opts,payloads,full,layout):
    n=len(full)//(len(payloads)*1024*4);stride=n*4;prime=31;tail=17;total=len(payloads)*1024-prime-tail
    expected=full[prime*stride:(prime+total)*stride];rows=[]
    for name,writer in [('caf',caf_encode),('mp4',mp4_encode)]:
        extra=dict(layout_tag=layout['tag'],channel_labels=[d['label'] for d in layout['descriptions']]) if name=='caf' else {}
        source=root/name;source.write_bytes(writer(vectors.cookie(**opts),payloads,rate=opts.get('rate',48000),channels=n,priming=prime,remainder=tail,**extra)[0])
        for i,(start,count) in enumerate([(0,total),(0,1031),(1024-prime,1031),(total-9,100),(total,1)]):
            out=root/(name+str(i));value=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count);raw=(out/'pcm.f32le').read_bytes();saved=min(count,total-start)
            require(raw==expected[start*stride:(start+saved)*stride] and value['saved_frames']==saved and value['input']['consistency_verified'],'container range differs: '+name)
            require(value['channel_layout']==layout,'source layout changed in container')
            rows.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    vectors.bundle(root/'trimmed',payloads,priming=prime,remainder=tail,**opts)
    command(binary,'decode-sq',root/'trimmed','--out',root/'trimmed-pcm','--start-frame',1024-prime,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[(1024-prime)*stride:(2055-prime)*stride],'packet range differs')
    return rows


def validate(binary,report,reference,only):
    frozen=json.loads((vectors.ROOT/'data/hoa-shared-vectors-v1.json').read_text());require(frozen==vectors.manifest(),'shared manifest changed')
    if reference:require(reference['passed'] and reference['vector_manifest_sha256']==frozen['sha256'],'reference identity differs')
    for identity,(name,opts,cases) in zip(frozen['cases'],vectors.sequences()):
        if only and name not in only:continue
        with workspace(report,name) as root:
            generated=[vectors.packet(case,**opts) for case in cases];payloads=[raw for raw,t in generated];vectors.bundle(root/'bundle',payloads,**opts)
            parsed=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed');require(parsed['errors']==0,'parse errors')
            rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()];require(len(rows)==len(cases),'missing rows')
            oracle=None if reference else Decoder(opts);expected=[]
            for row,(_,truth) in zip(rows,generated):
                verify_node(row,truth,opts,report['metrics'],name)
                if oracle:expected.extend(oracle.decode(truth))
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');raw=(root/'pcm/pcm.f32le').read_bytes();n=decoded['channel_count']
            require(len(raw)==len(cases)*1024*n*4 and decoded['drc_processing']==decoded['loudness_normalization']=='off','output shape/policy')
            if oracle:
                result=compare(struct.unpack('<%df'%(len(raw)//4),raw),expected,dict(case=name,stage='pcm'));require(result['passed'],str(result['first_failure']));merge_metrics(report['metrics']['pcm'],result)
            use_ranges=name.startswith('rate-tools-') or name.startswith('stream-') or name in ('trimming-declarations','legacy-future-gain-nodes','additional-2','shared-fields','rate-spatial-96000','rate-spatial-7350')
            implementation=decoded['pcm']['decoder_settings']['implementation']['value']
            stable={key:value for key,value in implementation.items() if key not in ('compiler','debug_assertions')}
            record=dict(identity,passed=True,stages_sha256=digest(rows),pcm_sha256=hashlib.sha256(raw).hexdigest(),implementation=stable,layout=decoded['channel_layout'],ranges=ranges(binary,root,opts,payloads,raw,decoded['channel_layout']) if use_ranges else [])
            if reference:require(record==next(r for r in reference['cases'] if r['index']==identity['index']),'cross-build stages differ')
            report['cases'].append(record)
        print('shared',len(report['cases']),len(frozen['cases']),name,flush=True)
    require(len(report['cases'])==(len(only) if only else len(frozen['cases'])),'missing cases')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);p.add_argument('--case',action='append');a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing/report exists');frozen=vectors.manifest()
    metric=lambda:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
    report=dict(passed=False,profile=vectors.PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),platform=platform.platform(),vector_manifest_sha256=frozen['sha256'],atol=1e-6,rtol=1e-5,cases=[],errors=[],metrics=None if a.reference_report else dict(pcm=metric(),transport=metric()),failure_directory=str(a.report.with_suffix('.failures')))
    try:
        validate(a.binary.resolve(),report,json.loads(a.reference_report.read_text()) if a.reference_report else None,a.case)
        require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    report['stage_sha256']=digest(report['cases']);write_json(a.report,report);print(json.dumps({k:report[k] for k in ('passed','metrics','errors','stage_sha256')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
