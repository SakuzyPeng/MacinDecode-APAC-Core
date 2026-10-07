#!/usr/bin/env python3
"""Affected Rust/Python contracts and five unchanged HOA representatives."""
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from validate_hoa_dynamic_checks import legacy
from validate_hoa_salient_subbands import fingerprints


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','spatial-reference','legacy-reference'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.binary=a.binary.resolve();a.test_binary=a.test_binary.resolve()
    require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'executable missing/report exists')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
           binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::tests::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True)
            require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout)
            require(count and int(count[1])>0,'missing Rust tests');r['rust'].append(dict(module=module,passed=int(count[1])))
            print(module,count[1],flush=True)
        env=dict(os.environ,MAPAC_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_hoa_salient_partition','test_hoa_salient_subbands','test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'Ran (\d+) tests',proc.stderr);require(count,'missing Python tests')
        r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_salient_subbands_vectors as spatial
        old=json.loads(a.spatial_reference.read_text());require(old['passed'] and not old['errors'],'invalid spatial reference')
        require(old['vector_manifest_sha256']==spatial.manifest()['sha256'],'old generator changed')
        for index,(kind,opts,cases) in enumerate(spatial.sequences()):
            if kind not in ('pure2','replace3','dynamic-add'):continue
            with workspace(r,kind) as root:
                generated=[spatial.packet(c,**opts) for c in cases];spatial.bundle(root/'bundle',[raw for raw,_ in generated],**opts)
                summary=command(a.binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
                require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'old spatial parse failed')
                rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()];now=fingerprints(rows,generated,opts)
                decoded=command(a.binary,'decode-sq',root/'bundle','--out',root/'pcm')
                require(decoded['pcm']['decoder_settings']['implementation']['value']==old['implementations'][kind],'old implementation metadata changed')
                now['pcm_sha256']=sha256_file(root/'pcm/pcm.f32le')
                require(all(now[k]==old['cases'][index][k] for k in now),'old spatial digest differs')
                r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(a.spatial_reference),**now))
        import hoa_salient_vectors as salient
        import hoa_static_ambient_vectors as ambient
        checked=json.loads(a.legacy_reference.read_text());require(checked['passed'] and not checked['errors'],'invalid legacy checks')
        previous={v['kind']:v for v in checked['legacy']}
        for module,kind in ((salient,'mode_5'),(ambient,'windows_drc_embedded_False')):
            chosen=[(opts,cases) for name,opts,cases in module.sequences() if name==kind];require(len(chosen)==1,'missing old representative')
            with workspace(r,kind) as root:now=legacy(a.binary,root,module,*chosen[0],static=module is ambient)
            require(all(now[k]==previous[kind][k] for k in now),'old four-band/ambient digest differs')
            r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(a.legacy_reference),**now))
        require(len(r['legacy'])==5 and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'cases missing/source changed')
        r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],rust=sum(t['passed'] for t in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors'])))
    return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
