#!/usr/bin/env python3
"""Small independent ambient HOA matrix and exact cross-build stage replay."""
import argparse,json,platform,struct,subprocess,hashlib
from pathlib import Path
from datetime import datetime,timezone
from hoa_vectors import PROFILE,cookie,packet,bundle,sequences,manifest
from channel_oracle import Decoder,spectra
from validate_channels import same_fields,compare,merge_metrics,digest,float_bytes
from validate_packets import coverage
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode

def check(r,t,last_mode,order=3):
    n=(order+1)**2
    assert r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'];coverage(r)
    h=r['hoa'];assert h['numeric_profile']==PROFILE and h['hoa_complete'] and h['common_window']==t['common_window']
    assert (h['coefficient_count'],h['core_channels'],h['transport_channels'],h['order'],h['channel_order'],h['normalization'])==(n,n,n,order,'ACN','SN3D')
    if t['inner'] is not None:last_mode=check(r['embedded_preroll']['report'],t['inner'],last_mode,order)
    else:assert r['embedded_preroll'] is None
    if t['spatial']['coding_mode'] is not None:last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],dict(t['spatial'],effective_global_coding_mode=last_mode),'spatial');same_fields(r['packet_tail'],t['tail'],'tail')
    assert r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset']
    assert len(r['elements'])==n and len(h['channels_after_hoa'])==n
    for a,b,out in zip(r['elements'],t['elements'],h['channels_after_hoa']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'):same_fields(a[key],b[key],key)
        assert a['element_complete'] and a['spectrum_complete']==b['present'] and a['coding_type']==(0 if b['present'] else None)
        assert len(a['channels'])==len(b['channels']);assert out['acn_index']==b['configuration']['element_index']
        for x,y in zip(a['channels'],b['channels']):same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
        assert out['scaled']==(a['channels_after_bwe2'][0]['scaled'] if b['present'] else [0.]*1024)
    if t['drc'] is None:assert r['drc'] is None
    else:same_fields(r['drc'],t['drc'],'DRC')
    return last_mode

def nodes(r,t):
    if t['inner'] is not None:yield from nodes(r['embedded_preroll']['report'],t['inner'])
    yield r,t

def validate(binary,report,reference):
    frozen=manifest();assert frozen==json.loads((ROOT/'data/hoa-ambient-vectors-v1.json').read_text())
    assert len(frozen['cases'])==39
    if reference:
        assert reference['passed'] and reference['mode']=='independent_math' and len(reference['cases'])==39 and not reference['errors']
        for key in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'):assert reference[key]==report[key],key
    for frozen_case,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=frozen_case['index']
        with workspace(report,f'{index}-{kind}') as root:
            generated=[packet(c,**options) for c in cases];config=cookie(**options);payloads=[raw for raw,t in generated]
            bundle(root/'bundle',payloads,**options)
            p=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parse.jsonl')
            assert p['errors']==0 and p['hoa_packets_complete']==len(cases)
            rows=[json.loads(s)['report'] for s in (root/'parse.jsonl').read_text().splitlines()]
            parsed=[];last=0;hashes={name:hashlib.sha256() for name in ('quantized','raw','tns','bwe2','hoa')}
            for i,(r,(_,t)) in enumerate(zip(rows,generated)):
                last=check(r,t,last)
                for current,truth in nodes(r,t):
                    parsed.append({k:current[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    for actual,wanted in zip(current['elements'],truth['elements']):
                        if not wanted['present']:continue
                        native=None if reference else spectra(wanted);channel=actual['configuration']['element_index']
                        hashes['quantized'].update(struct.pack('<1024i',*actual['channels'][0]['quantized']))
                        for stage,values in [('raw',actual['channels'][0]['scaled']),('tns',actual['channels_after_tns'][0]['scaled']),('bwe2',actual['channels_after_bwe2'][0]['scaled']),('hoa',current['hoa']['channels_after_hoa'][channel]['scaled'])]:
                            hashes[stage].update(float_bytes(values))
                            if native is not None:
                                metric=compare(values,native['bwe2' if stage=='hoa' else stage][0],dict(case=index,packet=i,channel=channel,stage=stage));merge_metrics(report['metrics'][stage],metric);assert metric['passed'],metric
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');full=(root/'pcm/pcm.f32le').read_bytes();impl=decoded['pcm']['decoder_settings']['implementation']['value']
            assert impl['hoa_numeric_profile']==PROFILE and decoded['pcm']['layout']['value']['ambisonic_order']==3
            assert decoded['drc_processing']==decoded['loudness_normalization']=='off'
            if report['implementation'] is None:report['implementation']=impl
            assert impl==report['implementation']
            if not reference:
                decoder=Decoder(16);expected=[v for _,truth in generated for v in decoder.decode(truth)]
                metric=compare(struct.unpack('<'+str(len(full)//4)+'f',full),expected,dict(case=index,stage='pcm'));merge_metrics(report['metrics']['pcm'],metric);assert metric['passed'],metric
            # One independent container pair per semantic sequence; small slices
            # cover range clipping without a large Cartesian matrix.
            prime=31;remainder=17;valid=len(cases)*1024-prime-remainder;ranges=[]
            for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
                src=root/name;src.write_bytes(encoder(config,payloads,rate=48000,channels=16,priming=prime,remainder=remainder,variant=index)[0])
                out=root/(name+'-pcm');r=command(binary,'decode-sq',src,'--out',out,'--start-frame',13,'--frames',1031)
                raw=(out/'pcm.f32le').read_bytes();assert raw==full[(prime+13)*64:(prime+13+r['saved_frames'])*64]
                assert r['input']['consistency_verified'] and r['saved_frames']==min(1031,valid-13)
                ranges.append(dict(container=name,pcm_sha256=hashlib.sha256(raw).hexdigest(),frames=r['saved_frames']))
            record=dict(frozen_case,passed=True,state_sha256=digest(parsed),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges,**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})
            if reference:assert record==reference['cases'][index],'cross-build HOA differs'
            report['cases'].append(record)
        print('HOA',index+1,'/39',kind,flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args()
    assert a.binary.is_file() and not a.report.exists(),'missing binary or existing report'
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,
           implementation=None,cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('raw','tns','bwe2','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None
        validate(a.binary.resolve(),r,reference);assert len(r['cases'])==39
        assert source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed';r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    r['stage_sha256']=digest(r['cases']);a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x',encoding='utf-8') as f:json.dump(r,f,indent=2);f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','errors','metrics')}));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
