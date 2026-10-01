#!/usr/bin/env python3
"""Independent per-component spatial-grid recovery and portable stage fingerprints."""
import argparse,hashlib,json,platform,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from hoa_salient_subbands_vectors import PROFILE,manifest,sequences,cookie,packet,bundle,shape
from hoa_salient_subbands_oracle import Decoder
from generate_hoa_salient_subbands_format import generate
from validate_hoa import nodes
from validate_hoa_mixed import drc_history
from validate_hoa_additive import measured
from validate_channels import same_fields,digest,float_bytes
from validate_packets import coverage
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest,float32
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def measure(total,actual,expected,location,block,slots,n,wide=False,descriptor_rows=None,descriptor_dimensions=None):
    failure=None
    try:measured(total,actual,expected,location,block,slots,n,wide)
    except Exception as error:failure=error
    if descriptor_rows is not None:
        for key in ('max_absolute_error_location','max_ulp_location','first_failure'):
            where=total.get(key)
            if where and all(where.get(k)==location.get(k) for k in ('case','packet','role')):
                remaining=where['index'];widths=descriptor_dimensions or [slots]*len(descriptor_rows)
                for di,width in enumerate(widths):
                    if remaining<width:break
                    remaining-=width
                else:raise RuntimeError('descriptor coordinate outside actual dimensions')
                k=remaining;d=descriptor_rows[di]
                where.update(component=d['component_index'],subband=d['subband_index'],slot=k)
    if failure:
        if total['first_failure'] is not None:raise RuntimeError(str(total['first_failure'])) from failure
        raise failure


def check_pure_ambient(r,t,last_mode,last_sha,opts):
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete ambient packet');coverage(r)
    if t['inner']:last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,opts)
    h=r['hoa'];m=(opts['order']+1)**2;n=shape(opts['order'],opts.get('dynamic',False));ambient=opts['ambient_count'];types=opts.get('tce_types',[0]*n);transport=sum(2 if t==1 else 0 if t==6 else 1 for t in types)
    require(h['hoa_complete'] and (h['coefficient_count'],h['core_channels'],h['transport_channels'],r['channel_count'])==(m,ambient,transport,n),'ambient dimensions differ')
    same_fields(h['spatial'],t['spatial'],'ambient spatial');require(h['spatial'].get('salient') is None,'fabricated salient data')
    if t['spatial']['coding_mode'] is not None:last_mode=t['spatial']['coding_mode']
    require(h['spatial']['effective_global_coding_mode']==last_mode,'ambient mode history differs')
    same_fields(r['packet_tail'],t['tail'],'tail');same_fields(r['drc'],t['drc'],'DRC')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'ambient endpoints differ')
    require(len(r['elements'])==len(types) and len(h['channels_after_hoa'])==n,'ambient carriers/outputs differ')
    if 'tce_types' in opts:require(h['transport_profile']=='apac-hoa-transports-v1' and h['transport_element_count']==len(types) and h['transport_format_sha256']==sha256_file(ROOT/'data/hoa-transports-format-v1.json') and r['packet_state_profile']=='apac-hoa-transports-state-v1','transport identity differs')
    for a,b in zip(r['elements'],t['elements']):
        require(a['element_complete'],'incomplete ambient carrier')
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','cac','tns','bwe2'):same_fields(a[key],b[key],key)
        if 'extension' in b:same_fields(a['extension'],b['extension'],'extension body')
        for x,y in zip(a['channels'],b['channels']):same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'ambient integers')
    for acn,c in enumerate(h['channels_after_hoa']):require(c['acn_index']==acn,'ambient ACN order differs');float32(c['scaled'])
    return last_mode,r['packet_sha256']


def check(r,t,last_mode,last_sha,opts):
    if opts.get('counts')==[]:return check_pure_ambient(r,t,last_mode,last_sha,opts)
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete HOA packet');coverage(r)
    if t['inner']:last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,opts)
    else:require(r['embedded_preroll'] is None,'fabricated inner frame')
    order=opts.get('order',3);dynamic=opts.get('dynamic',False);m=(order+1)**2;n=shape(order,dynamic);counts=opts.get('counts',[1,3,4,9,16]);path=opts.get('path','salient');h=r['hoa'];types=opts.get('tce_types',[0]*n);transport=sum(2 if t==1 else 0 if t==6 else 1 for t in types)
    require(h['hoa_complete'] and h['common_window']==t['common_window'] and (h['order'],h['coefficient_count'],h['core_channels'],h['transport_channels'],r['channel_count'])==(order,m,len(counts)+opts.get('ambient_count',0 if path=='salient' else 4),transport,n),'dimensions differ')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'core/tail boundary differs')
    same_fields(r['packet_tail'],t['tail'],'tail');same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None:last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial');require(h['spatial']['effective_global_coding_mode']==last_mode,'global mode differs')
    side=h['spatial']['salient'];require(side['history_frame_sha256']==last_sha,'history source differs')
    require([(d['component_index'],d['subband_index']) for d in side['descriptors']]==[(sc,b) for sc,c in enumerate(counts) for b in range(c)],'descriptors padded/misordered')
    orders=opts.get('component_orders',[order]*len(counts));dimensions=[(o+1)**2 for o in orders]
    require(all(len(d['restored'])==dimensions[d['component_index']] for d in side['descriptors']),'descriptor dimension differs')
    if order>3 or opts.get('quantization_bits',6)!=6 or len(counts)!=5 or list(orders)!=[order]*5:
        from hoa_component_orders_vectors import PROFILE as component_profile,component_information
        recovery_profile='apac-hoa-expanded-orders-math-v1' if order>3 else 'apac-hoa-salient-quantization-math-v1' if opts.get('quantization_bits',6)!=6 else 'apac-hoa-ambient-counts-math-v1' if opts.get('ambient_count',0 if path=='salient' else 4) not in (0,4) else 'apac-hoa-salient-counts-math-v1' if len(counts)!=5 else component_profile
        require(side['component_orders']==component_information(orders,opts.get('quantization_bits',6)) and h['numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else recovery_profile),'component order/profile differs')
        if dynamic:require(h['dynamic_selection']['recovery_numeric_profile']==recovery_profile,'dynamic base recovery differs')
        if 1 in orders:require(side['order1_profile']=='apac-hoa-salient-order1-v1','first-order support marker differs')
        else:require('order1_profile' not in side,'first-order marker leaked into old descriptors')
    else:require('component_orders' not in side,'component metadata leaked into old configuration')
    if len(set(counts))!=1:require('subband_ends' not in side and 'lines_per_window' not in side,'fabricated shared grid')
    spatial_method=opts.get('spatial_method',0)
    if spatial_method:
        from generate_hoa_salient_partition_format import PROFILE as partition_profile,generate as partition_format
        require(side['partition_method']==spatial_method and side['partition_profile']==partition_profile and side['format_sha256']==partition_format()['format_sha256'],'spatial partition differs')
    else:
        require('partition_method' not in side and 'partition_profile' not in side,'legacy partition fields changed')
        if list(counts)!=[4]*5:require(side['format_sha256']==generate()['format_sha256'],'spatial-grid format differs')
    if list(counts)!=[4]*5:require(side['subband_profile']==PROFILE,'spatial-grid profile differs')
    require(len(r['elements'])==len(types) and len(h['channels_after_hoa'])==n,'wrong channel count')
    if 'tce_types' in opts:require(h['transport_profile']=='apac-hoa-transports-v1' and h['transport_element_count']==len(types) and h['transport_format_sha256']==sha256_file(ROOT/'data/hoa-transports-format-v1.json') and r['packet_state_profile']=='apac-hoa-transports-state-v1','transport identity differs')
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','cac','tns','bwe2'):same_fields(a[key],b[key],key)
        if 'extension' in b:same_fields(a['extension'],b['extension'],'extension body')
        require(a['element_complete'] and len(a['channels'])==len(b['channels']),'carrier not fully validated')
        for x,y in zip(a['channels'],b['channels']):same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integers')
    if dynamic:
        d=h['dynamic_selection'];same_fields(d,t['dynamic_selection'],'dynamic');require(len(d['before_selection'])==m and len(d['mappings'])==8,'dynamic layout differs')
        expected=[[0.]*1024 for _ in range(n)]
        for line in range(1024):
            frequency=line%128 if t['common_window']==2 else line;band=next(b for b,end in enumerate(d['lines_per_window']) if frequency<end)
            for slot,acn in enumerate(d['mappings'][band]['target_acn_indices']):expected[acn][line]=d['before_selection'][slot]['scaled'][line]
        for c,v in zip(h['channels_after_hoa'],expected):require(float_bytes(c['scaled'])==float_bytes(v),'dynamic copy changed bytes')
    else:require('dynamic_selection' not in h,'fabricated dynamic stage')
    for acn,c in enumerate(h['channels_after_hoa']):require(c['acn_index']==acn,'ACN order changed');float32(c['scaled'])
    return last_mode,r['packet_sha256']


def fingerprints(rows,generated,opts):
    hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','internal','mapping','hoa')};structure=[];last=0;previous=None
    for row,(_,truth) in zip(rows,generated):
        last,previous=check(row,truth,last,previous,opts)
        for node,t in nodes(row,truth):
            h=node['hoa'];side=h['spatial'].get('salient') or dict(descriptors=[]);d=h.get('dynamic_selection');structure.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')});structure[-1]['component_subbands']=side.get('component_subbands')
            if opts.get('spatial_method',0):structure[-1]['partition']={k:side.get(k) for k in ('partition_method','partition_profile','format_sha256','subband_ends','lines_per_window')}
            if 'component_orders' in side:structure[-1]['component_orders']=side['component_orders']
            if 'order1_profile' in side:structure[-1]['order1_profile']=side['order1_profile']
            if 'quantization_bits' in side:structure[-1]['quantization']={k:side[k] for k in ('quantization_bits','quantization_profile')}
            values=[v for desc in side['descriptors'] for v in desc['restored']];hashes['descriptors'].update(struct.pack('<'+str(len(values))+'d',*values))
            hashes['internal'].update(float_bytes([v for c in (d['before_selection'] if d else h['channels_after_hoa']) for v in c['scaled']]));hashes['hoa'].update(float_bytes([v for c in h['channels_after_hoa'] for v in c['scaled']]))
            if d:hashes['mapping'].update(bytes(v for row in d['mappings'] for v in row['target_acn_indices']))
            for e in node['elements']:
                mapping=e['configuration'].get('transport_channels',e['configuration']['output_channels'])
                for local,_ in enumerate(mapping):
                    hashes['transport'].update(float_bytes(e['channels_after_bwe2'][local]['scaled'] if e['present'] else [0.]*1024))
                    if e['present']:hashes['quantized'].update(struct.pack('<1024i',*e['channels'][local]['quantized']))
                if 'tce_types' in opts:structure.append({k:e.get(k) for k in ('configuration','extension','start_bit_offset','end_bit_offset')})
    return dict(state_sha256=digest(structure),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})


def ranges(binary,root,kind,opts,payloads,full,index,range_kinds=None):
    if kind not in (('pure2','replace3','dynamic-add') if range_kinds is None else range_kinds):return []
    n=shape(opts['order'],opts.get('dynamic',False));stride=n*4;prime=31;remainder=17;valid=len(payloads)*1024-prime-remainder;expected=full[prime*stride:(prime+valid)*stride];middle=len(payloads)//2*1024-prime
    requests=[(0,valid),(0,1031),(middle,1031),((len(payloads)-2)*1024-prime,1024),(valid-9,100),(valid,1)];records=[]
    for name,encode in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name;source.write_bytes(encode(cookie(**opts),payloads,rate=opts['rate'],channels=n,priming=prime,remainder=remainder,variant=index,**({'layout_tag':(190<<16)|n} if name=='caf' else {}))[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+str(j));r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count);raw=(out/'pcm.f32le').read_bytes();saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs');records.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    bundle(root/'trimmed',payloads,priming=prime,remainder=remainder,**opts);r=command(binary,'decode-sq',root/'trimmed','--out',root/'trimmed-pcm','--start-frame',middle,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'bundle slice differs');return records


def validate(binary,r,reference,*,vectors=None,format_generator=generate,frozen_name='hoa-spatial-subbands-vectors-v1.json',format_name='hoa-salient-subbands-format-v1.json',metadata_checker=None,range_kinds=None):
    if vectors is None:
        import hoa_salient_subbands_vectors as vectors
    frozen=vectors.manifest();require(frozen==json.loads((ROOT/'data'/frozen_name).read_text()),'manifest changed')
    require(format_generator()==(json.loads((ROOT/'data'/format_name).read_text()) if format_name is not None else frozen['formats']),'format changed')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'],'invalid reference')
        for k in ('code_commit','source_sha256','vector_manifest_sha256','format_sha256','profile','atol','rtol'):require(reference[k]==r[k],k+' differs')
    for identity,(kind,opts,cases) in zip(frozen['cases'],vectors.sequences()):
        index=identity['index'];m=(opts.get('order',3)+1)**2;n=shape(opts.get('order',3),opts.get('dynamic',False))
        with workspace(r,kind) as root:
            generated=[vectors.packet(c,**opts) for c in cases];payloads=[raw for raw,_ in generated];cfg=cookie(**opts);bundle(root/'bundle',payloads,**opts)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed');require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'incomplete parse');rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
            record=dict(identity,passed=True,**fingerprints(rows,generated,opts));oracle=None if reference else Decoder(opts);expected_pcm=[];cursor=0
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for pi,(row,(_,truth)) in enumerate(zip(rows,generated)):
                if oracle:expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,n)
                    if oracle:
                        expected=oracle.records[cursor];cursor+=1;h=node['hoa'];d=h.get('dynamic_selection');descs=(h['spatial'].get('salient') or {}).get('descriptors',[])
                        actual=dict(transport=[e['channels_after_bwe2'][local]['scaled'] if e['present'] else [0.]*1024 for e in node['elements'] for local,_ in enumerate(e['configuration'].get('transport_channels',e['configuration']['output_channels']))],vectors=[v['restored'] for v in descs],internal=[c['scaled'] for c in (d['before_selection'] if d else h['channels_after_hoa'])],scaled=[c['scaled'] for c in h['channels_after_hoa']])
                        for stage,key in [('transport','transport'),('descriptors','vectors'),('internal','internal'),('hoa','scaled')]:
                            measure(r['metrics'][stage],[v for c in actual[key] for v in c],[v for c in expected[key] for v in c],dict(case=index,packet=pi,stage=stage,role='current' if node is row else 'embedded'),t['common_window'],m,n,stage=='descriptors',t['spatial'].get('salient',{}).get('descriptors',[]) if stage=='descriptors' else None,[len(v) for v in expected['vectors']] if stage=='descriptors' else None)
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');full=(root/'pcm/pcm.f32le').read_bytes();impl=decoded['pcm']['decoder_settings']['implementation']['value']
            if metadata_checker is not None:metadata_checker(decoded,opts,r)
            else:
                require(impl['hoa_salient_subband_counts']==list(opts.get('counts',[1,3,4,9,16])) and impl['hoa_salient_subband_profile']==PROFILE and impl['hoa_salient_subband_format_sha256']==r['format_sha256'],'subband metadata differs')
                if opts.get('spatial_method',0):require(impl['hoa_salient_partition_method']==opts['spatial_method'] and impl['hoa_salient_partition_profile']==r['profile'],'partition metadata differs')
                else:require('hoa_salient_partition_method' not in impl and 'hoa_salient_partition_profile' not in impl,'old partition metadata changed')
            require(decoded['pcm']['channels']==n and decoded['pcm']['sample_rate']==opts.get('rate',48000) and decoded['drc_processing']==decoded['loudness_normalization']=='off','output configuration differs')
            require(len(full)==len(cases)*1024*n*4 and decoded['saved_frames']==len(cases)*1024,'PCM timeline differs')
            if oracle:measured(r['metrics']['pcm'],struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),0,m,n)
            record.update(pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges(binary,root,kind,opts,payloads,full,index,range_kinds));r['implementations'][kind]=impl
            if reference:require(record==reference['cases'][index],'cross-build spatial subbands differ')
            r['cases'].append(record)
        print('spatial subbands',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main(*,vectors=None,format_generator=generate,frozen_name='hoa-spatial-subbands-vectors-v1.json',format_name='hoa-salient-subbands-format-v1.json',profile=PROFILE,metadata_checker=None,range_kinds=None):
    if vectors is None:
        import hoa_salient_subbands_vectors as vectors
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args();require(a.binary.is_file() and not a.report.exists(),'binary missing/report exists')
    r=dict(passed=False,profile=profile,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=vectors.manifest()['sha256'],format_sha256=format_generator()['format_sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','internal','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    if format_name is None:r['format_dependencies']=format_generator()['dependencies']
    try:
        validate(a.binary.resolve(),r,json.loads(a.reference_report.read_text()) if a.reference_report else None,vectors=vectors,format_generator=format_generator,frozen_name=frozen_name,format_name=format_name,metadata_checker=metadata_checker,range_kinds=range_kinds);require(len(r['cases'])==len(vectors.manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'missing cases/source changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    r['stage_sha256']=digest(r['cases']);a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f:json.dump(r,f,indent=2);f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')}));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
