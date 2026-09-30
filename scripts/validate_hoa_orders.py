#!/usr/bin/env python3
"""Independent HOA order/rate mathematics and exact portable stage replay."""
import argparse,hashlib,json,platform,struct,subprocess
from pathlib import Path
from datetime import datetime,timezone
from hoa_orders_vectors import PROFILE,manifest,sequences,writers,arguments
from hoa_salient_oracle import Decoder as SalientDecoder
from channel_oracle import Decoder as AmbientDecoder,spectra
from validate_hoa import check as check_ambient,nodes
from validate_hoa_salient import check as check_salient,compare64
from validate_channels import compare,merge_metrics,digest,float_bytes
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def measured(actual,expected,where,channels,block,kind='f32'):
    m=(compare64 if kind=='f64' else compare)(actual,expected,where)
    f=m['first_failure']
    if f:
        i=f['index']
        if where['stage']=='descriptors':f.update(component=i//(4*channels),subband=(i//channels)%4,coefficient=i%channels)
        elif where['stage']=='hoa':f.update(coefficient=i//1024,window=(i%1024)//128 if block==2 else 0,line=i%128 if block==2 else i%1024)
        else:f.update(packet=i//(1024*channels),sample=(i//channels)%1024,coefficient=i%channels)
    return m


def validate(binary,r,reference):
    frozen=manifest();assert frozen==json.loads((ROOT/'data/hoa-orders-vectors-v1.json').read_text())
    if reference:
        assert reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases'])
        for k in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'):assert reference[k]==r[k],k
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index'];module=writers(options);args=arguments(options);n=(options['order']+1)**2
        with workspace(r,str(index)+'-'+kind) as root:
            generated=[module.packet(c,**args) for c in cases];payloads=[p for p,_ in generated];cfg=module.cookie(**args);module.bundle(root/'bundle',payloads,**args)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
            assert not summary['errors'] and summary['hoa_packets_complete']==len(cases)
            rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','hoa')};structures=[];last=0;last_sha=None
            oracle=None if reference else SalientDecoder(n) if options['salient'] else AmbientDecoder(n);expected_pcm=[];cursor=0
            for packet_index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                if options['salient']:last,last_sha=check_salient(row,truth,last,last_sha,options['order'])
                else:last=check_ambient(row,truth,last,options['order'])
                if oracle:expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    for e in node['elements']:
                        if e['present']:
                            hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']));hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled']))
                    vectors=[v for d in node['hoa']['spatial'].get('salient',{}).get('descriptors',[]) for v in d['restored']]
                    scaled=[v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]
                    hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors));hashes['hoa'].update(float_bytes(scaled))
                    if oracle:
                        if options['salient']:
                            expected=oracle.records[cursor];cursor+=1
                            m=measured(vectors,[v for d in expected['vectors'] for v in d],dict(case=index,packet=packet_index,stage='descriptors'),n,t['common_window'],'f64');merge_metrics(r['metrics']['descriptors'],m);assert m['passed'],m
                            wanted=[v for c in expected['scaled'] for v in c]
                        else:wanted=[v for e in t['elements'] for v in (spectra(e)['bwe2'][0] if e['present'] else [0.]*1024)]
                        m=measured(scaled,wanted,dict(case=index,packet=packet_index,stage='hoa'),n,t['common_window']);merge_metrics(r['metrics']['hoa'],m);assert m['passed'],m
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');full=(root/'pcm/pcm.f32le').read_bytes();impl=decoded['pcm']['decoder_settings']['implementation']['value']
            assert decoded['pcm']['channels']==n and decoded['pcm']['sample_rate']==options['rate'] and decoded['pcm']['layout']['value']['ambisonic_order']==options['order']
            assert decoded['drc_processing']==decoded['loudness_normalization']=='off'
            expected_profile='apac-hoa-salient-order2-math-v1' if options['salient'] and options['order']==2 else 'apac-hoa-salient-math-v1' if options['salient'] else 'apac-hoa-ambient-math-v1'
            assert impl['hoa_numeric_profile']==decoded['hoa_numeric_profile']==expected_profile
            if options['salient']:assert impl['hoa_format_sha256']==module.format_for(options['order'])['tables_sha256']
            r['implementations'].setdefault(str(options['order'])+'-'+expected_profile,impl)
            if oracle:
                m=measured(struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),n,0);merge_metrics(r['metrics']['pcm'],m);assert m['passed'],m
            ranges=[];prime=31;remainder=17;stride=n*4
            for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
                source=root/name;source.write_bytes(encoder(cfg,payloads,rate=options['rate'],channels=n,priming=prime,remainder=remainder,variant=index)[0]);out=root/(name+'-pcm')
                result=command(binary,'decode-sq',source,'--out',out,'--start-frame',13,'--frames',1031);raw=(out/'pcm.f32le').read_bytes()
                assert raw==full[(prime+13)*stride:(prime+13+result['saved_frames'])*stride] and result['input']['consistency_verified']
                ranges.append(dict(container=name,frames=result['saved_frames'],pcm_sha256=hashlib.sha256(raw).hexdigest()))
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges,**{k+'_sha256':h.hexdigest() for k,h in hashes.items()})
            if reference:assert record==reference['cases'][index],'cross-build HOA order mismatch'
            r['cases'].append(record)
        print('HOA orders',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args();assert a.binary.is_file() and not a.report.exists()
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None;validate(a.binary.resolve(),r,reference)
        assert len(r['cases'])==len(manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'];r['passed']=True
    except Exception as error:r['errors'].append(str(error))
    r['stage_sha256']=digest(r['cases']);a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f:json.dump(r,f,indent=2);f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')}));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
