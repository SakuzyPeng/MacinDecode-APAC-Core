#!/usr/bin/env python3
"""Lightweight boundary/scatter truth plus representative independent HOA PCM sequences."""
import argparse,hashlib,json,platform,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from hoa_dynamic_subbands_vectors import PROFILE,manifest,probes,sequences,cookie,packet,bundle,arguments
from generate_hoa_dynamic_subbands_format import generate
from hoa_dynamic_oracle import Decoder as DynamicOracle
from hoa_additive_oracle import Decoder as AdditiveOracle
from validate_hoa_dynamic import check as check_dynamic
from validate_hoa_additive import check as check_additive,measured
from validate_hoa import nodes
from validate_hoa_mixed import drc_history
from validate_channels import digest,float_bytes
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def check(r,t,last_mode,last_sha,options):
    _,args=arguments(options); function=check_additive if options['path']=='add' else check_dynamic
    result=function(r,t,last_mode,last_sha,args)
    d=r['hoa']['dynamic_selection']; n=options['subbands']
    require(d['active_subband_count']==n and d['subband_profile']==PROFILE and d['format_sha256']==generate()['format_sha256'],'subband metadata differs')
    require(len(d['subband_ends'])==len(d['lines_per_window'])==n and len(d['mappings'])==8,'wire rows conflated with effective bands')
    return result


def fingerprints(rows,generated,options):
    hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','internal','mapping','hoa')}; structures=[];last=0;previous=None
    for row,(_,truth) in zip(rows,generated):
        last,previous=check(row,truth,last,previous,options)
        for node,t in nodes(row,truth):
            structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
            h=node['hoa']; d=h['dynamic_selection'];hashes['mapping'].update(bytes(v for row in d['mappings'] for v in row['target_acn_indices']))
            vectors=[v for row in h['spatial']['salient']['descriptors'] for v in row['restored']];hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors))
            hashes['internal'].update(float_bytes([v for c in d['before_selection'] for v in c['scaled']]))
            hashes['hoa'].update(float_bytes([v for c in h['channels_after_hoa'] for v in c['scaled']]))
            for e in node['elements']:
                hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024))
                if e['present']:hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']))
    return dict(state_sha256=digest(structures),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})


def parsed(binary,root,options,cases):
    generated=[packet(c,**options) for c in cases];bundle(root/'bundle',[raw for raw,_ in generated],**options)
    summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
    require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'incomplete parse')
    rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()];require(len(rows)==len(cases),'missing reports')
    return generated,rows


def ranges(binary,root,kind,options,payloads,full,index):
    if not kind.startswith('main-'):return []
    stride=64;prime=31;remainder=17;valid=len(payloads)*1024-prime-remainder
    expected=full[prime*stride:(prime+valid)*stride];middle=(len(payloads)//2)*1024-prime;inner=(len(payloads)-2)*1024-prime
    requests=[(0,valid),(0,1031),(middle,1031),(inner,1024),(valid-9,100),(valid,1)];records=[]
    for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name;source.write_bytes(encoder(cookie(**options),payloads,rate=options.get('rate',48000),channels=16,priming=prime,remainder=remainder,variant=index)[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+'-'+str(j));r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes();saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs')
            records.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    bundle(root/'trimmed',payloads,priming=prime,remainder=remainder,**options)
    r=command(binary,'decode-sq',root/'trimmed','--out',root/'trimmed-pcm','--start-frame',middle,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'bundle range differs')
    return records


def validate(binary,r,reference):
    frozen=manifest();require(frozen==json.loads((ROOT/'data/hoa-subbands-vectors-v1.json').read_text()),'frozen vectors differ')
    require(generate()==json.loads((ROOT/'data/hoa-dynamic-format-v2.json').read_text()),'format v2 differs')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'],'invalid reference')
        for key in ('code_commit','source_sha256','profile','vector_manifest_sha256','format_sha256','atol','rtol'):require(reference[key]==r[key],key+' differs')
    for identity,(kind,options,cases) in zip(frozen['boundary_cases'],probes()):
        with workspace(r,kind) as root:
            generated,rows=parsed(binary,root,options,cases);record=dict(identity,passed=True,pcm_decoded=False,**fingerprints(rows,generated,options))
            h=rows[0]['hoa'];d=h['dynamic_selection']
            # The writer puts q=1 at explicitly recorded sparse positions. All
            # other lines are zero; sf=160 and V=48/32-1 give q*16384 exactly.
            quantized=generated[0][1]['elements'][0]['channels'][0]['quantized']
            require(all(q in (0,1) for q in quantized),'unqualified basis integers')
            known=[q*16384. for q in quantized]
            for slot,c in enumerate(d['before_selection']):require(float_bytes(c['scaled'])==float_bytes(known if slot==8 else [0.]*1024),'known internal basis differs')
            for line in range(1024):
                frequency=line%128 if cases[0]['block']==2 else line
                band=next(b for b,end in enumerate(generated[0][1]['dynamic_selection']['lines_per_window']) if frequency<end)
                acn=8+band
                for out,c in enumerate(h['channels_after_hoa']):require(c['scaled'][line]==(known[line] if out==acn else 0.),f'boundary placement differs at {kind}/{line}/{out}')
            if reference:require(record==reference['boundary_cases'][identity['index']],'cross-build boundary differs')
            r['boundary_cases'].append(record)
    print('effective subband boundary probes',len(r['boundary_cases']),flush=True)
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']
        with workspace(r,kind) as root:
            generated,rows=parsed(binary,root,options,cases);record=dict(identity,passed=True,**fingerprints(rows,generated,options))
            oracle=None if reference else AdditiveOracle(9,16) if options['path']=='add' else DynamicOracle()
            expected_pcm=[];cursor=0;cfg=cookie(**options)
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for pi,(row,(_,truth)) in enumerate(zip(rows,generated)):
                if oracle:expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,16)
                    if oracle:
                        expected=oracle.records[cursor];cursor+=1;h=node['hoa'];d=h['dynamic_selection']
                        actual=dict(transport=[e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024 for e in node['elements']],
                                    vectors=[desc['restored'] for desc in h['spatial']['salient']['descriptors']],internal=[c['scaled'] for c in d['before_selection']],scaled=[c['scaled'] for c in h['channels_after_hoa']])
                        for stage,key in [('transport','transport'),('descriptors','vectors'),('internal','internal'),('hoa','scaled')]:
                            measured(r['metrics'][stage],[v for c in actual[key] for v in c],[v for c in expected[key] for v in c],dict(case=index,packet=pi,stage=stage,role='current' if node is row else 'embedded'),t['common_window'],9,16,stage=='descriptors')
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');full=(root/'pcm/pcm.f32le').read_bytes();impl=decoded['pcm']['decoder_settings']['implementation']['value']
            require(impl['hoa_dynamic_subband_profile']==PROFILE and impl['hoa_dynamic_subband_count']==options['subbands'] and impl['hoa_dynamic_format_sha256']==r['format_sha256'],'implementation profile differs')
            require(impl['hoa_numeric_profile']=='apac-hoa-dynamic-selection-math-v1' and impl['backend']==('rust_hoa_additive_sq_drc_off_f64_fft_v1' if options['path']=='add' else 'rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v1'),'existing numeric/backend rule changed')
            require(decoded['pcm']['channels']==16 and decoded['pcm']['layout']['value']['ambisonic_order']==3 and decoded['drc_processing']==decoded['loudness_normalization']=='off','layout/processing changed')
            require(len(full)==len(cases)*65536 and decoded['saved_frames']==len(cases)*1024,'timeline differs')
            if oracle:measured(r['metrics']['pcm'],struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),0,9,16)
            record.update(pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges(binary,root,kind,options,[raw for raw,_ in generated],full,index))
            r['implementations'][kind]=impl
            if reference:require(record==reference['cases'][index],'cross-build sequence differs')
            r['cases'].append(record)
        print('effective subband sequences',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)
    pair=[c for c in r['cases'] if c['kind'].startswith('inactive-')];require(len(pair)==2,'inactive row controls missing')
    require(pair[0]['pcm_sha256']==pair[1]['pcm_sha256'] and pair[0]['hoa_sha256']==pair[1]['hoa_sha256'] and pair[0]['mapping_sha256']!=pair[1]['mapping_sha256'],'unused wire rows affected audio or were discarded')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing or report exists')
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),
           vector_manifest_sha256=manifest()['sha256'],format_sha256=generate()['format_sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},boundary_cases=[],cases=[],
           metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','internal','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None;validate(a.binary.resolve(),r,reference)
        frozen=manifest();require(len(r['cases'])==len(frozen['cases']) and len(r['boundary_cases'])==42 and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'missing cases/source changed');r['passed']=True
    except Exception as error:r['errors'].append(str(error))
    r['stage_sha256']=digest(dict(boundary_cases=r['boundary_cases'],cases=r['cases']));a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f:json.dump(r,f,indent=2);f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')}));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
