#!/usr/bin/env python3
"""Affected interfaces and six portable legacy representatives; use same-platform metadata references."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from validate_hoa_dynamic_checks import legacy
from validate_hoa_salient_subbands import fingerprints


def main(*,first_order=False,salient_counts=False,ambient_counts=False,quantization=False,expanded_orders=False,transports=False,spatial_controls=False,dynamic_domains=False,source_layouts=False,static_remapping=False):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','partition-reference','legacy-reference',('component-reference' if first_order else 'spatial-reference')):p.add_argument('--'+name,type=Path,required=True)
    if salient_counts:p.add_argument('--order1-reference',type=Path,required=True)
    if ambient_counts:p.add_argument('--counts-reference',type=Path,required=True)
    if quantization:p.add_argument('--ambient-reference',type=Path,required=True)
    if expanded_orders:p.add_argument('--quantization-reference',type=Path,required=True)
    if transports:
        p.add_argument('--expanded-reference',type=Path,required=True)
        p.add_argument('--channel-reference',type=Path,required=True)
    if static_remapping:p.add_argument('--source-reference',type=Path,required=True)
    a=p.parse_args();a.binary=a.binary.resolve();a.test_binary=a.test_binary.resolve()
    require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'executable missing/report exists')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
           binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in (('config::hoa::remapping_tests::',) if static_remapping else ())+(('frame::hoa_source::tests::',) if source_layouts else ())+(('frame::hoa_dynamic::domains_tests::',) if dynamic_domains else ())+(('frame::hoa_controls::tests::',) if spatial_controls else ())+(('frame::hoa_transport::tests::',) if transports else ())+('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True)
            require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout)
            require(count and int(count[1])>0,'missing Rust tests');r['rust'].append(dict(module=module,passed=int(count[1])))
            print(module,count[1],flush=True)
        env=dict(os.environ,APAC_TOOL_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        modules=['test_hoa_order1','test_hoa_component_orders','test_hoa_salient_partition'] if first_order else ['test_hoa_component_orders','test_hoa_salient_partition','test_hoa_salient_subbands']
        if transports:modules.append('test_hoa_transports')
        if dynamic_domains:modules.extend(['test_hoa_dynamic','test_hoa_dynamic_subbands'])
        if source_layouts:modules.append('test_hoa_source_layouts')
        if static_remapping:modules.append('test_hoa_remapping')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v',*modules,'test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'Ran (\d+) tests',proc.stderr);require(count,'missing Python tests');r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_salient_partition_vectors as partition
        import hoa_salient_subbands_vectors as spatial
        if transports:
            import hoa_expanded_orders_vectors as expanded
            import hoa_quantization_vectors as quantization_vectors
            import hoa_salient_counts_vectors as counts
            references=((a.expanded_reference,expanded,{'zero','tenth'}),(a.quantization_reference,quantization_vectors,{'q8-mixed','q9-dynamic'}),(a.counts_reference,counts,{'first-four','dynamic-nine'}))
        elif expanded_orders:
            import hoa_quantization_vectors as quantization_vectors
            import hoa_ambient_counts_vectors as ambient_counts_vectors
            import hoa_salient_counts_vectors as counts
            references=((a.quantization_reference,quantization_vectors,{'q7-first','q8-mixed','q9-dynamic'}),(a.ambient_reference,ambient_counts_vectors,{'pure-second','twelve'}),(a.counts_reference,counts,{'first-four'}))
        elif quantization:
            import hoa_ambient_counts_vectors as ambient_counts_vectors
            import hoa_salient_counts_vectors as counts
            import hoa_order1_vectors as order1
            references=((a.ambient_reference,ambient_counts_vectors,{'one','twelve','pure-second'}),(a.counts_reference,counts,{'first-four','dynamic-nine'}),(a.order1_reference,order1,{'pure2'}))
        elif ambient_counts:
            import hoa_salient_counts_vectors as counts
            import hoa_order1_vectors as order1
            references=((a.counts_reference,counts,{'first-four','replace-twelve','dynamic-nine'}),(a.order1_reference,order1,{'pure2'}),(a.partition_reference,partition,{'pure2','dynamic-add'}))
        elif salient_counts:
            import hoa_order1_vectors as order1
            import hoa_component_orders_vectors as components
            references=((a.order1_reference,order1,{'pure2','replace3','dynamic-add'}),(a.component_reference,components,{'all-order2'}),(a.partition_reference,partition,{'pure2','dynamic-add'}))
        elif first_order:
            import hoa_component_orders_vectors as components
            references=((a.component_reference,components,{'pure','replace','add','all-order2'}),(a.partition_reference,partition,{'pure2','dynamic-add'}))
        else:references=((a.partition_reference,partition,{'pure2','replace3','fixed-add','dynamic-add'}),(a.spatial_reference,spatial,{'minimum-one'}))
        for reference,module,selected in references:
            old=json.loads(reference.read_text());require(old['passed'] and not old['errors'],'invalid reference');require(old['vector_manifest_sha256']==module.manifest()['sha256'],'old generator changed')
            found=0
            for index,(kind,opts,cases) in enumerate(module.sequences()):
                if kind not in selected:continue
                with workspace(r,kind) as root:
                    generated=[module.packet(c,**opts) for c in cases];module.bundle(root/'bundle',[raw for raw,_ in generated],**opts)
                    summary=command(a.binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
                    require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'legacy parse failed')
                    rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()];now=fingerprints(rows,generated,opts)
                    decoded=command(a.binary,'decode-sq',root/'bundle','--out',root/'pcm')
                    require(decoded['pcm']['decoder_settings']['implementation']['value']==old['implementations'][kind],'legacy implementation metadata differs; use the same platform reference')
                    now['pcm_sha256']=sha256_file(root/'pcm/pcm.f32le');require(all(now[k]==old['cases'][index][k] for k in now),'old spectrum/PCM digest differs')
                    r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(reference),**now));found+=1
            require(found==len(selected),'missing representative')
        import hoa_static_ambient_vectors as ambient
        old=json.loads(a.legacy_reference.read_text());require(old['passed'] and not old['errors'],'invalid ambient reference')
        kind='windows_drc_embedded_False';expected=next(v for v in old['legacy'] if v['kind']==kind)
        opts,cases=next((opts,cases) for name,opts,cases in ambient.sequences() if name==kind)
        with workspace(r,kind) as root:now=legacy(a.binary,root,ambient,opts,cases,static=True)
        require(all(now[k]==expected[k] for k in now),'old ambient changed');r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(a.legacy_reference),**now))
        require(len(r['legacy'])==(7 if first_order else 6) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'cases missing/source changed')
        if transports:r['channel_regression']=channel_regression(a.binary,a.channel_reference,r)
        if static_remapping:r['source_regression']=source_regression(a.binary,a.source_reference,r)
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'source or executable changed during regression')
        r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],rust=sum(t['passed'] for t in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors'])))
    return 0 if r['passed'] else 1




def channel_regression(binary,reference,report):
    import hashlib,struct
    import channel_vectors as v
    from validate_channels import parameters,flatten,check,digest,float_bytes
    old=json.loads(reference.read_text());require(old['passed'] and not old['errors'],'invalid channel reference')
    record=next(x for x in reversed(old['sequences']) if x['channels']==8 and x['rate']==48000 and x['kind']=='mixed_element_windows')
    kind,opts,cases=next(x for i,x in enumerate(v.sequences(8)) if i==record['index'])
    generated=[v.packet(c,8,48000,**opts) for c in cases]
    h=hashlib.sha256(v.cookie(8,48000,**opts))
    for raw,_ in generated:h.update(len(raw).to_bytes(8,'little'));h.update(raw)
    require(h.hexdigest()==record['input_sha256'],'channel input changed')
    with workspace(report,'legacy-discrete') as root:
        v.bundle(root/'bundle',[p for p,_ in generated],8,48000,**opts)
        summary=command(binary,'parse-packets',root/'bundle','--depth','channels','--output',root/'parsed')
        require(not summary['errors'],'channel parse failed')
        rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
        state=[];hashes={key:hashlib.sha256() for key in ('quantized','raw','cac','tns','bwe2')}
        for row,(_,truth) in zip(rows,generated):
            check(row,truth,8)
            for node,t in flatten(row,truth):
                state.append(parameters(node))
                for e in node['elements']:
                    if not e['present']:continue
                    groups={'raw':e['channels'],'cac':e['channels_after_cac'],'tns':e['channels_after_tns'],'bwe2':e['channels_after_bwe2']}
                    for local,c in enumerate(e['channels']):
                        coord=struct.pack('<II',e['configuration']['element_index'],local)
                        hashes['quantized'].update(coord+struct.pack('<1024i',*c['quantized']))
                        for stage,values in groups.items():hashes[stage].update(coord+float_bytes(values[local]['scaled']))
        decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm')
        require(decoded['pcm']['decoder_settings']['implementation']['value']==old['implementations']['8'],'channel platform metadata changed')
        now=dict(state_sha256=digest(state),pcm_sha256=sha256_file(root/'pcm/pcm.f32le'),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})
        require(all(record[k]==v for k,v in now.items()),'discrete channel stages changed')
        return dict(passed=True,reference_sha256=sha256_file(reference),**now)


def source_regression(binary,reference,report):
    import hoa_source_layout_vectors as v
    from validate_channels import digest
    old=json.loads(reference.read_text());require(old['passed'] and not old['errors'],'invalid source-layout reference')
    names={'matrix-7929862','n3d-labels','matrix-partial','dynamic-source-labels'};results=[]
    identities={c['kind']:c for c in v.manifest()['cases']}
    for name,opts,cases in v.sequences():
        if name not in names:continue
        expected=next(c for c in old['cases'] if c['kind']==name);require(expected['input_sha256']==identities[name]['input_sha256'],'source input changed')
        with workspace(report,'legacy-source-'+name) as root:
            v.bundle(root/'bundle',[v.packet(c,**opts)[0] for c in cases],**opts)
            result=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed');require(not result['errors'],'source parse failed')
            rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm')
            require(decoded['backend']==v.BACKEND and decoded['packet_state_profile']==v.STATE_PROFILE,'old source backend changed')
            require('hoa_static_remapping' not in decoded['pcm']['decoder_settings']['implementation']['value'],'static mapping leaked into old source metadata')
            actual=dict(stages_sha256=digest(rows),pcm_sha256=sha256_file(root/'pcm/pcm.f32le'))
            require(all(expected[k]==value for k,value in actual.items()),'old source layout stages changed')
            results.append(dict(name=name,passed=True,reference_sha256=sha256_file(reference),**actual))
    require(len(results)==len(names),'missing source representative');return results


if __name__=='__main__':raise SystemExit(main())
