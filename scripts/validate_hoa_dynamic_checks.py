#!/usr/bin/env python3
"""Reduced-slot dynamic HOA affected checks and legacy representatives."""
import argparse, hashlib, json, os, re, struct, subprocess, sys
from pathlib import Path
from validate import require, write_json
from validate_drc import workspace
from validate_replay import command, sha256_file
from validate_portable import ROOT, source_digest
from validate_channels import digest, float_bytes
from validate_hoa import nodes


def legacy(binary,root,module,options,cases,mixed=False,static=False):
    generated=[module.packet(case,**options) for case in cases]
    module.bundle(root/'bundle',[raw for raw,_ in generated],**options)
    summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
    require(summary['hoa_packets_complete']==len(cases) and not summary['errors'],'legacy incomplete')
    rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
    hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','hoa')+ (('ambient',) if static else ())}; structures=[]
    for row,(_,truth) in zip(rows,generated):
        for node,_ in nodes(row,truth):
            require(('mixed' in node['hoa'])==mixed,'legacy mixed mapping changed'); require(('ambient' in node['hoa']['spatial'])==static,'ambient report changed'); require('dynamic_selection' not in node['hoa'],'new mapping leaked into old report')
            structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
            if mixed and not static: structures[-1]['mixed']=node['hoa']['mixed']
            if static: hashes['ambient'].update(float_bytes([v for c in node['hoa']['spatial']['ambient']['channels_after_transform'] for v in c['scaled']]))
            for e in node['elements']:
                if e['present']:
                    hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized'])); hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled']))
            vectors=[v for d in node['hoa']['spatial'].get('salient',{}).get('descriptors',[]) for v in d['restored']]
            hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors))
            hashes['hoa'].update(float_bytes([v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]))
    command(binary,'decode-sq',root/'bundle','--out',root/'pcm')
    return dict(pcm_sha256=sha256_file(root/'pcm/pcm.f32le'),state_sha256=digest(structures),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','test-binary','report','orders-reference','ambient-reference','salient-reference','channels-reference','mixed-reference','static-reference'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); a.binary=a.binary.resolve(); a.test_binary=a.test_binary.resolve()
    require(a.binary.is_file() and a.test_binary.is_file() and not a.report.exists(),'missing executable or existing report')
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
           binary_sha256=sha256_file(a.binary),test_binary_sha256=sha256_file(a.test_binary),rust=[],python=None,legacy=[],errors=[],
           failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for module in ('caf::tests::','mp4::tests::','synthesis::hoa_tests::','frame::hoa_salient::tests::','frame::hoa_ambient::tests::','frame::hoa_dynamic::tests::','synthesis::channel_tests::','synthesis::access_tests::','synthesis::drc_tests::'):
            proc=subprocess.run([str(a.test_binary),module],capture_output=True,text=True)
            require(proc.returncode==0,proc.stdout+proc.stderr); count=re.search(r'test result: ok\. (\d+) passed;',proc.stdout)
            require(count and int(count[1])>0,'missing Rust tests'); r['rust'].append(dict(module=module,passed=int(count[1])))
        env=dict(os.environ,MAPAC_BINARY=str(a.binary),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_hoa_dynamic','test_hoa_static_ambient','test_hoa_mixed','test_hoa','test_hoa_salient','test_hoa_orders',
                             'test_access.AccessTests.test_default_is_unchanged_and_fast_is_explicit_for_files'],cwd=ROOT/'scripts',env=env,capture_output=True,text=True)
        require(proc.returncode==0,proc.stdout+proc.stderr); count=re.search(r'Ran (\d+) tests',proc.stderr)
        require(count and int(count[1])==37,'missing Python tests'); r['python']=dict(passed=int(count[1]),output=proc.stderr)
        import hoa_orders_vectors as orders
        import hoa_vectors as ambient
        import hoa_salient_vectors as salient
        import hoa_mixed_vectors as mixed
        import hoa_static_ambient_vectors as static
        selections=[(a.orders_reference,orders.sequences(),{'foa_basis_0','soa_modes'},None),
                    (a.ambient_reference,ambient.sequences(),{'short_85'},ambient),
                    (a.salient_reference,salient.sequences(),{'mode_5'},salient),
                    (a.mixed_reference,mixed.sequences(),{'joint_tools_2','per_component_modes_groups'},mixed),
                    (a.static_reference,static.sequences(),{'windows_drc_embedded_False','selection_only_3'},static)]
        for path,seqs,chosen,module in selections:
            reference=json.loads(path.read_text()); require(reference['passed'] and not reference['errors'],'invalid legacy reference')
            found=0
            for index,(kind,options,cases) in enumerate(seqs):
                if kind not in chosen: continue
                writer=module or orders.writers(options); args=options if module else orders.arguments(options)
                with workspace(r,kind) as temporary: current=legacy(a.binary,temporary,writer,args,cases,mixed=(module is mixed or (module is static and options["mixed"])),static=(module is static))
                expected=reference['cases'][index]
                # The old ambient report predates descriptor fingerprints; its PCM is the unchanged contract.
                keys=current.keys() if module is not ambient else ('pcm_sha256',)
                require(all(current[k]==expected[k] for k in keys),kind+' legacy digest differs')
                r['legacy'].append(dict(kind=kind,passed=True,reference_sha256=sha256_file(path),**{k:current[k] for k in keys})); found+=1
            require(found==len(chosen),'missing legacy representative')
        from channel_vectors import excitation,packet,bundle,layout
        reference=json.loads(a.channels_reference.read_text()); require(reference['passed'],'invalid channel reference')
        for n in (2,8):
            with workspace(r,'channels'+str(n)) as temporary:
                cases=[excitation(n,n-1,(2,0x55)),dict(elements=[None]*len(layout(n)[2])),{}]
                bundle(temporary/'bundle',[packet(c,n)[0] for c in cases],n)
                result=command(a.binary,'decode-sq',temporary/'bundle','--out',temporary/'pcm'); sha=sha256_file(temporary/'pcm/pcm.f32le')
                expected=next(c for c in reference['cases'] if c['channels']==n)
                require(sha==expected['pcm_sha256'] and 'hoa_numeric_profile' not in result,'discrete output changed')
                r['legacy'].append(dict(kind='channels'+str(n),passed=True,pcm_sha256=sha,reference_sha256=sha256_file(a.channels_reference)))
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.test_binary)==r['test_binary_sha256'],'source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    write_json(a.report,r); print(json.dumps({k:r[k] for k in ('passed','rust','errors')})); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
