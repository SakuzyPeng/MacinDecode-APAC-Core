#!/usr/bin/env python3
"""New effective-subband checks and five affected old HOA representatives."""
import argparse,hashlib,json,os,re,struct,subprocess,sys
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from validate_channels import digest,float_bytes
from validate_hoa import nodes
from validate_hoa_dynamic_checks import legacy
from validate_hoa_additive_checks import dynamic_legacy


def additive_legacy(binary,root,options,cases):
    import hoa_additive_vectors as v
    from validate_hoa_additive import check
    generated=[v.packet(c,**options) for c in cases];v.bundle(root/'bundle',[raw for raw,_ in generated],**options)
    r=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed');require(not r['errors'] and r['hoa_packets_complete']==len(cases),'old additive incomplete')
    rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()];last=0;previous=None;structure=[]
    hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','ambient','internal','mapping','hoa')}
    for row,(_,truth) in zip(rows,generated):
        last,previous=check(row,truth,last,previous,options)
        for node,t in nodes(row,truth):
            h=node['hoa'];d=h.get('dynamic_selection');require(d is None or 'active_subband_count' not in d,'old report acquired effective counts')
            structure.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
            if d:hashes['mapping'].update(bytes(v for row in d['mappings'] for v in row['target_acn_indices']))
            for key,values,wide in [('descriptors',[v for row in h['spatial']['salient']['descriptors'] for v in row['restored']],True),('ambient',[v for c in h['additive']['ambient_contributions'] for v in c['scaled']],True),
                                    ('internal',[v for c in (d['before_selection'] if d else h['channels_after_hoa']) for v in c['scaled']],False),('hoa',[v for c in h['channels_after_hoa'] for v in c['scaled']],False)]:
                hashes[key].update(struct.pack('<'+str(len(values))+'d',*values) if wide else float_bytes(values))
            for e in node['elements']:
                hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024))
                if e['present']:hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']))
    decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');impl=decoded['pcm']['decoder_settings']['implementation']['value'];require('hoa_dynamic_subband_profile' not in impl,'old metadata changed')
    return dict(pcm_sha256=sha256_file(root/'pcm/pcm.f32le'),state_sha256=digest(structure),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','dynamic-reference','additive-reference','mixed-reference'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.binary=a.binary.resolve();a.test_binary=a.test_binary.resolve();require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'missing executable or existing report')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::tests::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True);require(proc.returncode==0,proc.stdout+proc.stderr)
            count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout);require(count and int(count[1])>0,'missing Rust checks');r['rust'].append(dict(module=module,passed=int(count[1])))
        env=dict(os.environ,APAC_TOOL_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_hoa_dynamic_subbands','test_hoa_dynamic','test_hoa_additive','test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr);count=re.search(r'Ran (\d+) tests',proc.stderr);require(count,'missing Python tests');r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_dynamic_vectors as dynamic
        import hoa_additive_vectors as additive
        import hoa_mixed_vectors as mixed
        for path,module,chosen in [(a.dynamic_reference,dynamic,{'salient-main','mixed-main'}),(a.additive_reference,additive,{'dynamic','fixed2'}),(a.mixed_reference,mixed,{'joint_tools_2'})]:
            reference=json.loads(path.read_text());require(reference['passed'] and not reference['errors'],'invalid old reference');found=0
            for index,(kind,opts,cases) in enumerate(module.sequences()):
                if kind not in chosen:continue
                with workspace(r,kind) as root:
                    current=dynamic_legacy(a.binary,root,opts,cases) if module is dynamic else additive_legacy(a.binary,root,opts,cases) if module is additive else legacy(a.binary,root,module,opts,cases,mixed=True)
                expected=reference['cases'][index];require(all(current[k]==expected[k] for k in current),kind+' old fingerprint differs')
                r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(path),**current));found+=1
            require(found==len(chosen),'missing old representative')
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'source changed');r['passed']=True
    except Exception as error:r['errors'].append(str(error))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],rust=sum(t['passed'] for t in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors'])));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
