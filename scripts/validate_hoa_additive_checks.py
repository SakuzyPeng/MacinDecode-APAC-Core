#!/usr/bin/env python3
"""Only additive/affected interface checks and selected existing output fingerprints."""
import argparse,hashlib,json,os,re,struct,subprocess,sys
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from validate_channels import digest,float_bytes
from validate_hoa import nodes
from validate_hoa_dynamic_checks import legacy


def dynamic_legacy(binary,root,options,cases):
    import hoa_dynamic_vectors as v
    from validate_hoa_dynamic import check
    generated=[v.packet(c,**options) for c in cases]; v.bundle(root/'bundle',[raw for raw,_ in generated],**options)
    summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
    require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'old dynamic parse failed')
    rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]; last=0; previous=None; structure=[]
    hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','internal','mapping','hoa')}
    for row,(_,truth) in zip(rows,generated):
        last,previous=check(row,truth,last,previous,options)
        for node,t in nodes(row,truth):
            require('additive' not in node['hoa'],'old dynamic gained additive report')
            structure.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
            h=node['hoa']; dyn=h['dynamic_selection']; hashes['mapping'].update(bytes(x for row in dyn['mappings'] for x in row['target_acn_indices']))
            values=[v for d in h['spatial']['salient']['descriptors'] for v in d['restored']]; hashes['descriptors'].update(struct.pack('<'+str(len(values))+'d',*values))
            hashes['internal'].update(float_bytes([v for c in dyn['before_selection'] for v in c['scaled']])); hashes['hoa'].update(float_bytes([v for c in h['channels_after_hoa'] for v in c['scaled']]))
            for e in node['elements']:
                hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024))
                if e['present']: hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']))
    decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm'); require(decoded['backend']=='rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v2','old dynamic backend changed')
    return dict(pcm_sha256=sha256_file(root/'pcm/pcm.f32le'),state_sha256=digest(structure),**{k+'_sha256':h.hexdigest() for k,h in hashes.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','salient-reference','mixed-reference','static-reference','dynamic-reference','channels-reference'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); a.binary=a.binary.resolve(); a.test_binary=a.test_binary.resolve()
    require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'missing executable or existing report')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::tests::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','frame::hoa_additive::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True); require(proc.returncode==0,proc.stdout+proc.stderr)
            count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout); require(count and int(count[1])>0,'missing Rust checks'); r['rust'].append(dict(module=module,passed=int(count[1])))
        env=dict(os.environ,APAC_TOOL_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_hoa_additive','test_hoa_mixed','test_hoa_static_ambient','test_hoa_dynamic',
                             'test_hoa_salient','test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr); count=re.search(r'Ran (\d+) tests',proc.stderr); require(count,'missing Python checks'); r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_salient_vectors as salient
        import hoa_mixed_vectors as mixed
        import hoa_static_ambient_vectors as static
        import hoa_dynamic_vectors as dynamic
        for path,module,chosen in [(a.salient_reference,salient,{'mode_5'}),(a.mixed_reference,mixed,{'joint_tools_2','per_component_modes_groups'}),(a.static_reference,static,{'windows_drc_embedded_False','selection_only_3'}),(a.dynamic_reference,dynamic,{'mixed-main'})]:
            reference=json.loads(path.read_text()); require(reference['passed'] and not reference['errors'],'invalid legacy reference'); found=0
            for index,(kind,options,cases) in enumerate(module.sequences()):
                if kind not in chosen: continue
                with workspace(r,kind) as root:
                    current=dynamic_legacy(a.binary,root,options,cases) if module is dynamic else legacy(a.binary,root,module,options,cases,mixed=(module is mixed or (module is static and options['mixed'])),static=(module is static))
                expected=reference['cases'][index]; require(all(current[k]==expected[k] for k in current),kind+' legacy fingerprint differs')
                r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(path),**current)); found+=1
            require(found==len(chosen),'missing legacy representative')
        from channel_vectors import excitation,packet,bundle,layout
        reference=json.loads(a.channels_reference.read_text()); require(reference['passed'],'invalid channels reference')
        with workspace(r,'stereo') as root:
            cases=[excitation(2,1,(2,0x55)),dict(elements=[None]*len(layout(2)[2])),{}]; bundle(root/'bundle',[packet(c,2)[0] for c in cases],2)
            result=command(a.binary,'decode-sq',root/'bundle','--out',root/'pcm'); sha=sha256_file(root/'pcm/pcm.f32le'); expected=next(c for c in reference['cases'] if c['channels']==2)
            require(sha==expected['pcm_sha256'] and 'hoa_numeric_profile' not in result,'discrete output changed'); r['legacy'].append(dict(kind='stereo',passed=True,pcm_sha256=sha,reference_sha256=sha256_file(a.channels_reference)))
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    write_json(a.report,r); print(json.dumps(dict(passed=r['passed'],rust=sum(x['passed'] for x in r['rust']),python=r['python']['passed'] if r['python'] else None,legacy=len(r['legacy']),errors=r['errors']))); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
