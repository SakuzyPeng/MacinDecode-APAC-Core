#!/usr/bin/env python3
"""Affected spatial-grid interfaces and selected unchanged four-band outputs."""
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from validate_hoa_dynamic_checks import legacy
from validate_hoa_dynamic_subbands_checks import additive_legacy


def subband_legacy(binary,root,options,cases):
    import hoa_dynamic_subbands_vectors as v
    from validate_hoa_dynamic_subbands import fingerprints
    generated=[v.packet(c,**options) for c in cases];v.bundle(root/'bundle',[raw for raw,_ in generated],**options)
    r=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed');require(not r['errors'] and r['hoa_packets_complete']==len(cases),'old dynamic parse failed')
    rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()];result=fingerprints(rows,generated,options)
    for row in rows:require('component_subbands' not in row['hoa']['spatial']['salient'],'old geometry changed')
    r=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');require('hoa_salient_subband_profile' not in r['pcm']['decoder_settings']['implementation']['value'],'old metadata changed')
    return dict(result,pcm_sha256=sha256_file(root/'pcm/pcm.f32le'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','salient-reference','mixed-reference','additive-reference','subbands-reference','static-reference'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.binary=a.binary.resolve();a.test_binary=a.test_binary.resolve();require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'missing executable/report exists')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::tests::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True);require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout);require(count and int(count[1])>0,'missing Rust tests');r['rust'].append(dict(module=module,passed=int(count[1])))
        env=dict(os.environ,APAC_TOOL_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_hoa_salient_subbands','test_hoa_salient','test_hoa_mixed','test_hoa_additive','test_hoa_dynamic_subbands','test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'Ran (\d+) tests',proc.stderr);require(count,'missing Python tests');r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_salient_vectors as salient
        import hoa_mixed_vectors as mixed
        import hoa_additive_vectors as additive
        import hoa_dynamic_subbands_vectors as subbands
        import hoa_static_ambient_vectors as static
        for path,module,chosen in [(a.salient_reference,salient,{'mode_5'}),(a.mixed_reference,mixed,{'joint_tools_2'}),(a.additive_reference,additive,{'fixed3','dynamic'}),(a.subbands_reference,subbands,{'main-salient','main-replace'}),(a.static_reference,static,{'windows_drc_embedded_False'})]:
            old=json.loads(path.read_text());require(old['passed'] and not old['errors'],'invalid old reference');found=0
            for index,(kind,opts,cases) in enumerate(module.sequences()):
                if kind not in chosen:continue
                with workspace(r,kind) as root:
                    if module is additive:now=additive_legacy(a.binary,root,opts,cases)
                    elif module is subbands:now=subband_legacy(a.binary,root,opts,cases)
                    else:now=legacy(a.binary,root,module,opts,cases,mixed=module is mixed,static=module is static)
                expected=old['cases'][index];require(all(now[k]==expected[k] for k in now),kind+' old digest differs');r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(path),**now));found+=1
            require(found==len(chosen),'missing representative')
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],rust=sum(t['passed'] for t in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors'])));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
