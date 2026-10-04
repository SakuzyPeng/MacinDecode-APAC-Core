#!/usr/bin/env python3
"""Static ambient Decimal mathematics, container ranges and exact six-build replay."""
import argparse,hashlib,json,platform,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from hoa_static_ambient_vectors import PROFILE,manifest,sequences,cookie,packet,bundle
from hoa_static_ambient_oracle import Decoder
from generate_hoa_static_ambient_tables import generate
from validate_hoa import nodes
from validate_hoa_orders import measured
from validate_hoa_mixed import drc_history
from validate_channels import same_fields,compare,merge_metrics,digest,float_bytes
from validate_packets import coverage
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest,float32
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def check(r,t,last_mode,last_sha,options):
    order=options['order']; n=(order+1)**2; mixed=options['mixed']
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete static ambient packet'); coverage(r)
    if t['inner'] is not None: last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,options)
    else: require(r['embedded_preroll'] is None,'fabricated preroll')
    h=r['hoa']; require(h['numeric_profile']==PROFILE and h['hoa_complete'] and h['common_window']==t['common_window'],'wrong HOA stage')
    require((h['coefficient_count'],h['core_channels'],h['transport_channels'],h['order'],h['channel_order'],h['normalization'])==(n,9 if mixed else n,n,order,'ACN','SN3D'),'wrong dimensions')
    require(r['packet_state_profile']=='apac-hoa-static-ambient-state-v1','wrong state rule')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'core/tail boundary differs')
    if mixed: same_fields(h['mixed'],t['mixed'],'mixed map')
    else: require('mixed' not in h,'fabricated mixed mapping')
    same_fields(r['packet_tail'],t['tail'],'tail'); same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None: last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial'); require(h['spatial']['effective_global_coding_mode']==last_mode,'global history differs')
    if mixed: require(h['spatial']['salient']['history_frame_sha256']==last_sha,'descriptor history source differs')
    require(len(r['elements'])==len(h['channels_after_hoa'])==n,'wrong output dimensions')
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'): same_fields(a[key],b[key],key)
        require(a['element_complete'] and len(a['channels'])==len(b['channels']),'missing/fabricated stream')
        for x,y in zip(a['channels'],b['channels']): same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
    ambient=h['spatial']['ambient']; selected=ambient['selection']; transformed=ambient['channels_after_transform']
    require(len(transformed)==len(selected),'wrong ambient result count')
    for slot,(acn,a) in enumerate(zip(selected,transformed)):
        require((a['transport_slot'],a['acn_index'])==(slot,acn),'wrong selected output')
        require(float_bytes(a['scaled'])==float_bytes(h['channels_after_hoa'][acn]['scaled']),'ambient did not overwrite selected coefficient')
        if slot>=4 or ambient['effective_index']==3:
            source=r['elements'][slot]['channels_after_bwe2'][0]['scaled'] if r['elements'][slot]['present'] else [0.]*1024
            require(float_bytes(a['scaled'])==float_bytes(source),'identity/untransformed ambient changed')
    for k,out in enumerate(h['channels_after_hoa']): require(out['acn_index']==k,'wrong ACN index'); float32(out['scaled'])
    for d in h['spatial'].get('salient',{}).get('descriptors',[]):
        require(len(d['restored'])==n and all(d['restored'][k]==0. for k in d['ambient_omitted_coefficients']),'invalid selected descriptor history')
    return last_mode,r['packet_sha256']


def ranges(binary,root,kind,options,cfg,payloads,full,index):
    # Cover each shape/rate with one representative, not the parameter product.
    if kind not in ('ambient1-switch','mixed3-selection','mixed2-fixed','ambient3-fixed','fixed_1','fixed_2','fixed_3','selection_only_3'): return []
    n=(options['order']+1)**2; stride=n*4; prime=31; remainder=17; valid=1024*len(payloads)-prime-remainder
    expected=full[prime*stride:(prime+valid)*stride]; results=[]
    middle=min(valid-1,1024*(len(payloads)//2)); refresh=min(valid-1,18*1024-prime) if kind=='ambient1-switch' else middle
    requests=[(0,valid),(0,1031),(middle,1031),(refresh,1024),(valid-9,100),(valid,1)]
    for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name; source.write_bytes(encoder(cfg,payloads,rate=options['rate'],channels=n,priming=prime,remainder=remainder,variant=index)[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+'-'+str(j)); r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes(); saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs')
            results.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    trimmed=root/'trimmed'; bundle(trimmed,payloads,priming=prime,remainder=remainder,**options)
    r=command(binary,'decode-sq',trimmed,'--out',root/'trimmed-pcm','--start-frame',middle,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'bundle range differs')
    return results


def validate(binary,r,reference):
    frozen=manifest(); require(frozen==json.loads((ROOT/'data/hoa-static-ambient-vectors-v1.json').read_text()),'frozen vectors differ')
    tables=generate(); require(tables==json.loads((ROOT/'data/hoa-static-ambient-tables-v1.json').read_text()),'transform constants differ')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases']),'invalid reference')
        for k in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'): require(reference[k]==r[k],k+' differs')
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']; n=(options['order']+1)**2
        with workspace(r,str(index)+'-'+kind) as root:
            generated=[packet(c,**options) for c in cases]; payloads=[raw for raw,_ in generated]; cfg=cookie(**options); bundle(root/'bundle',payloads,**options)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
            require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'parse incomplete')
            rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]; require(len(rows)==len(cases),'missing reports')
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','ambient','hoa')}; structures=[]; last=0; previous=None
            oracle=None if reference else Decoder(n); expected_pcm=[]; cursor=0
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for packet_index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                last,previous=check(row,truth,last,previous,options)
                if oracle: expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,n)
                    structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    vectors=[v for d in node['hoa']['spatial'].get('salient',{}).get('descriptors',[]) for v in d['restored']]
                    ambient=[v for a in node['hoa']['spatial']['ambient']['channels_after_transform'] for v in a['scaled']]
                    scaled=[v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]
                    hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors)); hashes['ambient'].update(float_bytes(ambient)); hashes['hoa'].update(float_bytes(scaled))
                    for e in node['elements']:
                        if e['present']: hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized'])); hashes['transport'].update(float_bytes(e['channels_after_bwe2'][0]['scaled']))
                    if oracle:
                        expected=oracle.records[cursor]; cursor+=1
                        for stage,actual,wanted,typ in [('descriptors',vectors,[v for d in expected['vectors'] for v in d],'f64'),('ambient',ambient,[v for a in expected['ambient'] for v in a],'f32'),('hoa',scaled,[v for c in expected['scaled'] for v in c],'f32')]:
                            metric=measured(actual,wanted,dict(case=index,packet=packet_index,stage=stage),n,t['common_window'],typ)
                            if stage=='ambient' and metric['first_failure']:
                                f=metric['first_failure']; slot=f['index']//1024; f.update(ambient_slot=slot,coefficient=t['spatial']['ambient']['selection'][slot],window=(f['index']%1024)//128 if t['common_window']==2 else 0,line=f['index']%128 if t['common_window']==2 else f['index']%1024)
                            merge_metrics(r['metrics'][stage],metric); require(metric['passed'],str(metric))
                    if t['native_numeric_stress']: require(node['hoa']['channels_after_hoa'][0]['scaled'][0]==0.5,'lost low-amplitude cancellation residual')
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm'); full=(root/'pcm/pcm.f32le').read_bytes(); impl=decoded['pcm']['decoder_settings']['implementation']['value']
            require(decoded['pcm']['channels']==n and decoded['pcm']['sample_rate']==options['rate'],'wrong PCM dimensions')
            require(decoded['drc_processing']==decoded['loudness_normalization']=='off' and decoded['drc_payloads_complete'],'wrong DRC policy')
            require(impl['hoa_numeric_profile']==PROFILE and impl['backend']=='rust_hoa_static_ambient_sq_drc_off_f64_fft_v2','wrong backend')
            require(impl['hoa_ambient_format_sha256']==tables['format_sha256'] and impl['hoa_ambient_tables_sha256']==tables['tables_sha256'],'constants changed')
            key=str(options['order'])+'-'+str(options['mixed']); r['implementations'].setdefault(key,impl); require(r['implementations'][key]==impl,'implementation changed')
            require(len(full)==len(cases)*1024*n*4 and hashlib.sha256(full).hexdigest()==decoded['pcm']['sha256'],'PCM length/hash differs')
            if oracle:
                metric=measured(struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),n,0)
                merge_metrics(r['metrics']['pcm'],metric); require(metric['passed'],str(metric))
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges(binary,root,kind,options,cfg,payloads,full,index),**{k+'_sha256':v.hexdigest() for k,v in hashes.items()})
            if reference: require(record==reference['cases'][index],'cross-build static ambient differs')
            r['cases'].append(record)
        print('static ambient',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path,required=True); p.add_argument('--report',type=Path,required=True); p.add_argument('--reference-report',type=Path); a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or existing report')
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,
           implementations={},cases=[],metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','ambient','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None; validate(a.binary.resolve(),r,reference)
        require(len(r['cases'])==len(manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'missing cases or source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    r['stage_sha256']=digest(r['cases']); a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f: json.dump(r,f,indent=2); f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')})); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
