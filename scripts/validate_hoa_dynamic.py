#!/usr/bin/env python3
"""Independent reduced-slot HOA recovery, exact scatter, and portable stage fingerprints."""
import argparse,hashlib,json,platform,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from hoa_dynamic_vectors import PROFILE,manifest,sequences,cookie,packet,bundle
from hoa_dynamic_oracle import Decoder
from generate_hoa_dynamic_format import generate
from hoa_salient_vectors import format_for
from validate_hoa import nodes
from validate_hoa_mixed import drc_history
from validate_hoa_salient import compare64
from validate_channels import same_fields,compare,merge_metrics,digest,float_bytes
from validate_packets import coverage
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest,float32
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def check(r,t,last_mode,last_sha,options):
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete dynamic packet'); coverage(r)
    if t['inner'] is not None: last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,options)
    else: require(r['embedded_preroll'] is None,'fabricated preroll')
    h=r['hoa']; require(h['numeric_profile']==PROFILE and h['hoa_complete'] and h['common_window']==t['common_window'],'wrong HOA stage')
    require((h['order'],h['coefficient_count'],h['output_order'],h['output_coefficient_count'],h['transport_channels'],h['core_channels'])==(2,9,3,16,16,9 if options['mixed'] else 5),'internal/output dimensions conflated')
    require(r['packet_state_profile']=='apac-hoa-dynamic-selection-state-v1' and r['channel_count']==16,'wrong state/output scope')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'wrong core/tail boundary')
    require('mixed' not in h and 'ambient' not in h['spatial'],'internal results exposed as global ACN')
    same_fields(r['packet_tail'],t['tail'],'tail'); same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None: last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial'); require(h['spatial']['effective_global_coding_mode']==last_mode,'global mode history differs')
    require(h['spatial']['salient']['history_frame_sha256']==last_sha,'descriptor history moved with output mapping')
    dynamic=h['dynamic_selection']; same_fields(dynamic,t['dynamic_selection'],'dynamic selection')
    require(len(r['elements'])==len(h['channels_after_hoa'])==16 and len(dynamic['before_selection'])==9,'wrong spectrum dimensions')
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'): same_fields(a[key],b[key],key)
        require(a['element_complete'] and len(a['channels'])==len(b['channels']),'missing/fabricated carrier')
        for x,y in zip(a['channels'],b['channels']): same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
    for slot,spectrum in enumerate(dynamic['before_selection']):
        require(spectrum['slot_index']==slot and 'acn_index' not in spectrum,'internal slot mislabeled'); float32(spectrum['scaled'])
    for d in h['spatial']['salient']['descriptors']: require(len(d['restored'])==9,'padded descriptor')
    for result in dynamic.get('internal_ambient',{}).get('channels_after_transform',[]):
        require('slot_index' in result and 'acn_index' not in result,'internal ambient mislabeled')
    wanted=[[0.]*1024 for _ in range(16)]
    for line in range(1024):
        frequency=line%128 if t['common_window']==2 else line
        band=next(b for b,end in enumerate(t['dynamic_selection']['lines_per_window']) if frequency<end)
        for slot,acn in enumerate(t['dynamic_selection']['mappings'][band]['target_acn_indices']): wanted[acn][line]=dynamic['before_selection'][slot]['scaled'][line]
    for acn,(a,b) in enumerate(zip(h['channels_after_hoa'],wanted)):
        require(a['acn_index']==acn and float_bytes(a['scaled'])==float_bytes(b),'scatter changed Float32 bytes'); float32(a['scaled'])
    return last_mode,r['packet_sha256']


def ranges(binary,root,kind,options,cfg,payloads,full,index):
    if kind not in ('salient-main','mixed-main','windows_drc_embedded_False','windows_drc_embedded_True'): return []
    stride=64; prime=31; remainder=17; valid=len(payloads)*1024-prime-remainder
    expected=full[prime*stride:(prime+valid)*stride]; middle=(len(payloads)//2)*1024-prime
    refresh=(6 if kind.endswith('main') else 4)*1024-prime
    requests=[(0,valid),(0,1031),(middle,1031),(refresh,1024),(valid-9,100),(valid,1)]; records=[]
    for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name; source.write_bytes(encoder(cfg,payloads,rate=options['rate'],channels=16,priming=prime,remainder=remainder,variant=index)[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+'-'+str(j)); r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes(); saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs')
            records.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    trimmed=root/'trimmed'; bundle(trimmed,payloads,priming=prime,remainder=remainder,**options)
    r=command(binary,'decode-sq',trimmed,'--out',root/'trimmed-pcm','--start-frame',middle,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'bundle range differs')
    return records


def measured(actual,expected,location,block,typ='f32'):
    m=(compare64 if typ=='f64' else compare)(actual,expected,location)
    if m['first_failure']:
        f=m['first_failure']; i=f['index']; stage=location['stage']
        if stage=='descriptors': f.update(component=i//36,subband=(i//9)%4,slot=i%9)
        elif stage=='pcm': f.update(packet=i//16384,sample=(i//16)%1024,acn=i%16)
        else: f.update(channel_or_slot=i//1024,window=(i%1024)//128 if block==2 else 0,line=i%128 if block==2 else i%1024)
    return m


def validate(binary,r,reference):
    frozen=manifest(); require(frozen==json.loads((ROOT/'data/hoa-dynamic-vectors-v1.json').read_text()),'frozen vectors differ')
    fmt=generate(); require(fmt==json.loads((ROOT/'data/hoa-dynamic-format-v1.json').read_text()),'format changed')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases']),'invalid reference')
        for k in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'): require(reference[k]==r[k],k+' differs')
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']
        with workspace(r,str(index)+'-'+kind) as root:
            generated=[packet(c,**options) for c in cases]; payloads=[raw for raw,_ in generated]; cfg=cookie(**options); bundle(root/'bundle',payloads,**options)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
            require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'incomplete parse')
            rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]; require(len(rows)==len(cases),'missing reports')
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','internal','mapping','hoa')}; structures=[]; last=0; previous=None
            oracle=None if reference else Decoder(); expected_pcm=[]; cursor=0
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for packet_index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                last,previous=check(row,truth,last,previous,options)
                if oracle: expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,16)
                    structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    dynamic=node['hoa']['dynamic_selection']
                    hashes['mapping'].update(bytes(x for row in dynamic['mappings'] for x in row['target_acn_indices']))
                    vectors=[v for d in node['hoa']['spatial']['salient']['descriptors'] for v in d['restored']]
                    internal=[v for s in dynamic['before_selection'] for v in s['scaled']]; output=[v for c in node['hoa']['channels_after_hoa'] for v in c['scaled']]
                    transport=[v for e in node['elements'] for v in (e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024)]
                    hashes['descriptors'].update(struct.pack('<'+str(len(vectors))+'d',*vectors)); hashes['internal'].update(float_bytes(internal)); hashes['hoa'].update(float_bytes(output)); hashes['transport'].update(float_bytes(transport))
                    for e in node['elements']:
                        if e['present']: hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']))
                    if oracle:
                        wanted=oracle.records[cursor]; cursor+=1
                        for stage,actual,expected,typ in [('transport',transport,[v for c in wanted['transport'] for v in c],'f32'),('descriptors',vectors,[v for c in wanted['vectors'] for v in c],'f64'),('internal',internal,[v for c in wanted['internal'] for v in c],'f32'),('hoa',output,[v for c in wanted['scaled'] for v in c],'f32')]:
                            m=measured(actual,expected,dict(case=index,packet=packet_index,stage=stage),t['common_window'],typ); merge_metrics(r['metrics'][stage],m); require(m['passed'],str(m))
                    if kind=='ambient_cancellation' and packet_index==0: require(internal[0]==0.5,'lost ambient residual before dynamic selection')
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm'); full=(root/'pcm/pcm.f32le').read_bytes(); impl=decoded['pcm']['decoder_settings']['implementation']['value']
            require(decoded['pcm']['channels']==16 and decoded['pcm']['layout']['value']['ambisonic_order']==3 and decoded['pcm']['sample_rate']==options['rate'],'wrong output layout')
            require(decoded['drc_processing']==decoded['loudness_normalization']=='off' and decoded['drc_payloads_complete'],'DRC policy changed')
            require(impl['hoa_numeric_profile']==PROFILE and impl['backend']=='rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v2','wrong backend')
            require((impl['hoa_internal_order'],impl['hoa_output_order'],impl['hoa_recovery_slot_count'])==(2,3,9),'metadata dimensions conflated')
            require(impl['hoa_format_sha256']==format_for(2)['tables_sha256'] and impl['hoa_descriptor_numeric_profile']=='apac-hoa-salient-order2-math-v1','wrong descriptor dictionary/model')
            require(impl['hoa_dynamic_format_sha256']==fmt['format_sha256'],'wrong subdivision format')
            key=impl['hoa_recovery_numeric_profile']; r['implementations'].setdefault(key,impl); require(r['implementations'][key]==impl,'implementation changed')
            require(len(full)==len(cases)*16384*4 and hashlib.sha256(full).hexdigest()==decoded['pcm']['sha256'],'PCM length/hash differs')
            if oracle:
                m=measured(struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),0); merge_metrics(r['metrics']['pcm'],m); require(m['passed'],str(m))
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges(binary,root,kind,options,cfg,payloads,full,index),**{k+'_sha256':h.hexdigest() for k,h in hashes.items()})
            if reference: require(record==reference['cases'][index],'cross-build dynamic HOA differs')
            r['cases'].append(record)
        print('dynamic HOA',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)
    equivalents=[c for c in r['cases'] if c['kind'].startswith('equivalent_')]; require(len(equivalents)==2,'missing encoding equivalence case')
    for key in ('quantized_sha256','transport_sha256','descriptors_sha256','internal_sha256','mapping_sha256','hoa_sha256','pcm_sha256'): require(equivalents[0][key]==equivalents[1][key],'bitmap/list semantic difference')


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path,required=True); p.add_argument('--report',type=Path,required=True); p.add_argument('--reference-report',type=Path); a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or existing report')
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),
           vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},cases=[],
           metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','internal','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None; validate(a.binary.resolve(),r,reference)
        require(len(r['cases'])==len(manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'missing cases or source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    r['stage_sha256']=digest(r['cases']); a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f: json.dump(r,f,indent=2); f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')})); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
