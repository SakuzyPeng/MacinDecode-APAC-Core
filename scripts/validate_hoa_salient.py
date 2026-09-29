#!/usr/bin/env python3
"""Focused salient HOA Decimal mathematics and exact cross-build replay."""
import argparse,hashlib,json,math,platform,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from hoa_salient_vectors import PROFILE,manifest,sequences,packet,cookie,bundle
from hoa_salient_oracle import Decoder
from validate_channels import same_fields,compare,merge_metrics,digest,float_bytes
from validate_hoa import nodes
from validate_packets import coverage
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def compare64(actual,expected,where):
    result=compare(actual,expected,where)
    def ordered(v):
        n=struct.unpack('<Q',struct.pack('<d',v))[0]
        return (0xffffffffffffffff-n) if n>>63 else (n+0x8000000000000000)
    result['max_ulp']=max((abs(ordered(a)-ordered(b)) for a,b in zip(actual,expected) if a!=b),default=0)
    return result


def check(r,t,last_mode,last_sha):
    assert r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'];coverage(r)
    if t['inner'] is not None:last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha)
    else:assert r['embedded_preroll'] is None
    h=r['hoa'];assert h['numeric_profile']==PROFILE and h['hoa_complete'] and h['common_window']==t['common_window']
    assert (h['coefficient_count'],h['core_channels'],h['transport_channels'],h['order'],h['channel_order'],h['normalization'])==(16,5,16,3,'ACN','SN3D')
    assert r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset']
    same_fields(r['packet_tail'],t['tail'],'tail');same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None:last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial');assert h['spatial']['effective_global_coding_mode']==last_mode
    assert h['spatial']['salient']['history_frame_sha256']==last_sha
    assert len(r['elements'])==len(h['channels_after_hoa'])==16
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'):same_fields(a[key],b[key],key)
        assert a['element_complete'] and len(a['channels'])==len(b['channels'])
        for x,y in zip(a['channels'],b['channels']):same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
    return last_mode,r['packet_sha256']


def validate(binary,report,reference):
    frozen=manifest();assert frozen==json.loads((ROOT/'data/hoa-salient-vectors-v1.json').read_text())
    if reference:
        assert reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases'])
        for key in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'):assert reference[key]==report[key],key
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']
        with workspace(report,str(index)+'-'+kind) as root:
            generated=[packet(case,**options) for case in cases];payloads=[p for p,_ in generated];cfg=cookie(**options)
            bundle(root/'bundle',payloads,**options)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parse.jsonl')
            assert summary['errors']==0 and summary['hoa_packets_complete']==len(cases)
            rows=[json.loads(s)['report'] for s in (root/'parse.jsonl').read_text().splitlines()]
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','hoa')};structures=[];last=0;last_sha=None
            oracle=None if reference else Decoder();expected_pcm=[];record_cursor=0
            for i,(r,(_,truth)) in enumerate(zip(rows,generated)):
                last,last_sha=check(r,truth,last,last_sha)
                if oracle is not None:expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(r,truth):
                    structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    vectors=[v for d in node['hoa']['spatial']['salient']['descriptors'] for v in d['restored']]
                    values=[v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]
                    hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors));hashes['hoa'].update(float_bytes(values))
                    for element in node['elements']:
                        if element['present']:
                            hashes['quantized'].update(struct.pack('<1024i',*element['channels'][0]['quantized']))
                            hashes['transport'].update(float_bytes(element['channels_after_bwe2'][0]['scaled']))
                    if oracle is not None:
                        expected=oracle.records[record_cursor];record_cursor+=1
                        for stage,actual,wanted,comparator in [('descriptors',vectors,[v for d in expected['vectors'] for v in d],compare64),('hoa',values,[v for c in expected['scaled'] for v in c],compare)]:
                            metric=comparator(actual,wanted,dict(case=index,packet=i,stage=stage));merge_metrics(report['metrics'][stage],metric);assert metric['passed'],metric
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm');full=(root/'pcm/pcm.f32le').read_bytes();impl=decoded['pcm']['decoder_settings']['implementation']['value']
            assert impl['hoa_numeric_profile']==PROFILE and impl['backend']=='rust_hoa_salient_sq_drc_off_f64_fft_v1' and decoded['pcm']['layout']['value']['ambisonic_order']==3
            assert decoded['drc_processing']==decoded['loudness_normalization']=='off'
            if report['implementation'] is None:report['implementation']=impl
            assert report['implementation']==impl
            if oracle is not None:
                metric=compare(struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'));merge_metrics(report['metrics']['pcm'],metric);assert metric['passed'],metric
            ranges=[];prime=31;remainder=17
            for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
                source=root/name;source.write_bytes(encoder(cfg,payloads,rate=48000,channels=16,priming=prime,remainder=remainder,variant=index)[0]);out=root/(name+'-pcm')
                result=command(binary,'decode-sq',source,'--out',out,'--start-frame',13,'--frames',1031);raw=(out/'pcm.f32le').read_bytes()
                assert raw==full[(prime+13)*64:(prime+13+result['saved_frames'])*64] and result['input']['consistency_verified']
                ranges.append(dict(container=name,pcm_sha256=hashlib.sha256(raw).hexdigest(),frames=result['saved_frames']))
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges,**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})
            if reference:assert record==reference['cases'][index],'cross-build salient HOA differs'
            report['cases'].append(record)
        print('salient HOA',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path);a=p.parse_args()
    assert a.binary.is_file() and not a.report.exists()
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementation=None,cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None
        validate(a.binary.resolve(),r,reference);assert len(r['cases'])==len(manifest()['cases'])
        assert source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'];r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    r['stage_sha256']=digest(r['cases']);a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f:json.dump(r,f,indent=2);f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','errors','metrics')}));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
