#!/usr/bin/env python3
"""Independent mixed-HOA mathematics and exact portable stage fingerprints."""
import argparse, hashlib, json, platform, struct, subprocess
from datetime import datetime, timezone
from pathlib import Path
from hoa_mixed_vectors import PROFILE, manifest, sequences, cookie, packet, bundle
from hoa_mixed_oracle import Decoder
from hoa_salient_vectors import format_for
from validate_hoa import nodes
from validate_hoa_orders import measured
from validate_channels import same_fields, merge_metrics, digest, float_bytes
from validate_packets import coverage
from validate import require
from validate_drc import workspace
from validate_replay import command, sha256_file
from validate_portable import ROOT, source_digest, float32
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def check(r,t,last_mode,last_sha,order):
    n=(order+1)**2
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete mixed packet'); coverage(r)
    if t['inner'] is not None: last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,order)
    else: require(r['embedded_preroll'] is None,'fabricated preroll')
    h=r['hoa']; require(h['numeric_profile']==PROFILE and h['hoa_complete'] and h['common_window']==t['common_window'],'wrong HOA stage')
    require((h['coefficient_count'],h['core_channels'],h['transport_channels'],h['order'],h['channel_order'],h['normalization'])==(n,9,n,order,'ACN','SN3D'),'wrong mixed dimensions')
    require(r['packet_state_profile']=='apac-hoa-mixed-state-v1','wrong mixed state profile')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'wrong core/tail boundary')
    same_fields(h['mixed'],t['mixed'],'mixed map'); same_fields(r['packet_tail'],t['tail'],'tail'); same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None: last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial'); require(h['spatial']['effective_global_coding_mode']==last_mode,'global mode history differs')
    require(h['spatial']['salient']['history_frame_sha256']==last_sha,'descriptor history source differs')
    require(len(r['elements'])==len(h['channels_after_hoa'])==n,'wrong dimensions')
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'): same_fields(a[key],b[key],key)
        require(a['element_complete'] and len(a['channels'])==len(b['channels']),'missing/fabricated stream')
        for x,y in zip(a['channels'],b['channels']): same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
    for k,out in enumerate(h['channels_after_hoa']):
        require(out['acn_index']==k,'wrong ACN index'); float32(out['scaled'])
        if k<4:
            source=r['elements'][k]['channels_after_bwe2'][0]['scaled'] if r['elements'][k]['present'] else [0.]*1024
            require(float_bytes(out['scaled'])==float_bytes(source),'ambient did not overwrite salient')
    for d in h['spatial']['salient']['descriptors']:
        require(len(d['restored'])==n,'padded descriptor')
        require(all(d['restored'][k]==0. for k in d['ambient_omitted_coefficients']),'omitted descriptors retained history')
    return last_mode,r['packet_sha256']


def drc_history(r,t,state,n):
    if t['drc'] is None: return
    expected=t['drc']; data=r['drc']; sha=r['packet_sha256']
    require(r['drc_complete'] and not r['drc_processing_applied'] and r['drc_history_sufficient']==state['history'],'DRC state/policy differs')
    if expected['header_present']:
        state['metadata_origin']='packet'; state['metadata_source']=sha
        if expected['config_present']: state['origin']='packet'; state['source']=sha
    cfg=data['configuration']
    require((cfg['source'],cfg['source_sha256'],cfg['loudness_metadata_source'],cfg['loudness_metadata_source_sha256'])==
            (state['origin'],state['source'],state['metadata_origin'],state['metadata_source']),'DRC source history differs')
    require([f['value'] for f in cfg['fields'] if f['name'].endswith('.base_channel_count')]==[n],'DRC count uses core rather than output channels')
    state['history']=any(node['time']<1024 for node in expected['nodes'])


def input_ranges(binary,root,kind,options,cfg,payloads,full,index):
    # Exercise every range behavior on the four order/rate representatives only.
    if not (kind.startswith('mixed') or kind.startswith('core_and_output_basis')): return []
    n=(options['order']+1)**2; stride=4*n; prime=31; remainder=17
    valid=1024*len(payloads)-prime-remainder; expected=full[prime*stride:(prime+valid)*stride]; result=[]
    # A refresh request starts at the outer packet carrying the embedded frame.
    middle=(len(payloads)//2)*1024-prime
    refresh=9*1024-prime if kind.startswith('mixed') else middle
    requests=[(0,valid),(0,1031),(middle,1031),(refresh,1024),(valid-9,100),(valid,1)]
    for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name; source.write_bytes(encoder(cfg,payloads,rate=options['rate'],channels=n,priming=prime,remainder=remainder,variant=index)[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+'-'+str(j)); r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes(); saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs')
            result.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
        out=root/(name+'-bundle'); trimmed=root/(name+'-trimmed-bundle'); bundle(trimmed,payloads,priming=prime,remainder=remainder,**options)
        r=command(binary,'decode-sq',trimmed,'--out',out,'--start-frame',middle,'--frames',1031)
        require((out/'pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'packet-directory range differs')
    return result


def validate(binary,r,reference):
    frozen=manifest(); require(frozen==json.loads((ROOT/'data/hoa-mixed-vectors-v1.json').read_text()),'frozen vectors differ')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases']),'invalid reference')
        for key in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'): require(reference[key]==r[key],key+' differs')
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']; n=(options['order']+1)**2
        with workspace(r,str(index)+'-'+kind) as root:
            generated=[packet(case,**options) for case in cases]; payloads=[p for p,_ in generated]; cfg=cookie(**options)
            bundle(root/'bundle',payloads,**options)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
            require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'parse incomplete')
            rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]
            require(len(rows)==len(cases),'missing reports')
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','hoa')}; structures=[]; last=0; last_sha=None
            oracle=None if reference else Decoder(n); expected_pcm=[]; cursor=0
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for packet_index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                last,last_sha=check(row,truth,last,last_sha,options['order'])
                if oracle: expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,n)
                    structure={k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')}
                    structure['mixed']=node['hoa']['mixed']; structures.append(structure)
                    vectors=[v for d in node['hoa']['spatial']['salient']['descriptors'] for v in d['restored']]
                    scaled=[v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]
                    hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors)); hashes['hoa'].update(float_bytes(scaled))
                    for e in node['elements']:
                        if e['present']:
                            hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized'])); hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled']))
                    if oracle:
                        expected=oracle.records[cursor]; cursor+=1
                        for stage,actual,wanted,typ in [('descriptors',vectors,[v for d in expected['vectors'] for v in d],'f64'),('hoa',scaled,[v for c in expected['scaled'] for v in c],'f32')]:
                            metric=measured(actual,wanted,dict(case=index,packet=packet_index,stage=stage),n,t['common_window'],typ)
                            merge_metrics(r['metrics'][stage],metric); require(metric['passed'],str(metric))
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm'); full=(root/'pcm/pcm.f32le').read_bytes(); impl=decoded['pcm']['decoder_settings']['implementation']['value']
            require(decoded['pcm']['channels']==n and decoded['pcm']['sample_rate']==options['rate'] and decoded['pcm']['layout']['value']['ambisonic_order']==options['order'],'wrong PCM shape')
            require(decoded['drc_processing']==decoded['loudness_normalization']=='off' and decoded['drc_payloads_complete'],'wrong processing policy')
            require(impl['hoa_numeric_profile']==decoded['hoa_numeric_profile']==PROFILE and impl['backend']=='rust_hoa_mixed_sq_drc_off_f64_fft_v2','wrong mixed backend')
            require(impl['hoa_format_sha256']==format_for(options['order'])['tables_sha256'],'format changed')
            require(impl['hoa_descriptor_numeric_profile']==rows[0]['hoa']['mixed']['descriptor_numeric_profile'],'descriptor model not identified')
            key=str(options['order']); r['implementations'].setdefault(key,impl); require(r['implementations'][key]==impl,'implementation changed')
            require(len(full)==len(cases)*1024*n*4 and hashlib.sha256(full).hexdigest()==decoded['pcm']['sha256'],'wrong PCM length/hash')
            if oracle:
                metric=measured(struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),n,0)
                merge_metrics(r['metrics']['pcm'],metric); require(metric['passed'],str(metric))
            ranges=input_ranges(binary,root,kind,options,cfg,payloads,full,index)
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges,**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})
            if reference: require(record==reference['cases'][index],'cross-build mixed HOA differs')
            r['cases'].append(record)
        print('mixed HOA',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path,required=True); p.add_argument('--report',type=Path,required=True); p.add_argument('--reference-report',type=Path); a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or existing report')
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',
           atol=1e-6,rtol=1e-5,implementations={},cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','hoa','pcm')},
           errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None
        validate(a.binary.resolve(),r,reference)
        require(len(r['cases'])==len(manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'incomplete matrix or changed source/binary'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    r['stage_sha256']=digest(r['cases']); a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f: json.dump(r,f,indent=2); f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')})); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
