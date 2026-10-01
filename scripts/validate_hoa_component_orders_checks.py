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


def main(*,first_order=False,salient_counts=False):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','partition-reference','legacy-reference',('component-reference' if first_order else 'spatial-reference')):p.add_argument('--'+name,type=Path,required=True)
    if salient_counts:p.add_argument('--order1-reference',type=Path,required=True)
    a=p.parse_args();a.binary=a.binary.resolve();a.test_binary=a.test_binary.resolve()
    require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'executable missing/report exists')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
           binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True)
            require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout)
            require(count and int(count[1])>0,'missing Rust tests');r['rust'].append(dict(module=module,passed=int(count[1])))
            print(module,count[1],flush=True)
        env=dict(os.environ,APAC_TOOL_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        modules=['test_hoa_order1','test_hoa_component_orders','test_hoa_salient_partition'] if first_order else ['test_hoa_component_orders','test_hoa_salient_partition','test_hoa_salient_subbands']
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v',*modules,'test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'Ran (\d+) tests',proc.stderr);require(count,'missing Python tests');r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_salient_partition_vectors as partition
        import hoa_salient_subbands_vectors as spatial
        if salient_counts:
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
        r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],rust=sum(t['passed'] for t in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors'])))
    return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
