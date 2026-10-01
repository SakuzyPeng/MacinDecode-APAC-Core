#!/usr/bin/env python3
"""Independent additive HOA mathematics, container windows and cross-build fingerprints."""
import argparse,hashlib,json,platform,struct,subprocess,math
from datetime import datetime,timezone
from pathlib import Path
from hoa_additive_vectors import PROFILE,manifest,sequences,cookie,packet,bundle,shape
from hoa_additive_oracle import Decoder
from generate_hoa_dynamic_format import generate
from hoa_salient_vectors import format_for
from validate_hoa import nodes
from validate_hoa_mixed import drc_history
from validate_channels import same_fields,digest,float_bytes
from validate_packets import coverage
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import ROOT,source_digest,float32
from caf_vectors import encode as caf_encode
from mp4_vectors import encode as mp4_encode


def check(r,t,last_mode,last_sha,options):
    dynamic=options.get('dynamic',False); order=options.get('order',3); n=shape(order,dynamic); slots=(order+1)**2
    require(r['packet_complete'] and r['status']=='complete' and not r['unknown_ranges'],'incomplete additive packet'); coverage(r)
    if t['inner'] is not None: last_mode,last_sha=check(r['embedded_preroll']['report'],t['inner'],last_mode,last_sha,options)
    else: require(r['embedded_preroll'] is None,'fabricated preroll')
    h=r['hoa']; require(h['numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else PROFILE) and h['hoa_complete'] and h['common_window']==t['common_window'],'wrong HOA stage')
    require((h['order'],h['coefficient_count'],h['transport_channels'],h['core_channels'],r['channel_count'])==(order,slots,n,9,n),'dimensions differ')
    require(r['packet_state_profile']=='apac-hoa-additive-state-v1','wrong state scope')
    require(r['component_end_bit_offset']==t['core_end_bit_offset'] and r['stop_bit_offset']==t['tail']['packet_end_bit_offset'],'core/tail boundary differs')
    same_fields(r['packet_tail'],t['tail'],'tail'); same_fields(r['drc'],t['drc'],'DRC')
    if t['spatial']['coding_mode'] is not None: last_mode=t['spatial']['coding_mode']
    same_fields(h['spatial'],t['spatial'],'spatial'); same_fields(h['additive'],t['additive'],'addition')
    require(h['spatial']['effective_global_coding_mode']==last_mode and h['spatial']['salient']['history_frame_sha256']==last_sha,'descriptor history differs')
    require(len(r['elements'])==len(h['channels_after_hoa'])==n,'wrong spectra count')
    for a,b in zip(r['elements'],t['elements']):
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics','tns','bwe2'): same_fields(a[key],b[key],key)
        require(a['element_complete'] and len(a['channels'])==len(b['channels']),'carrier missing')
        for x,y in zip(a['channels'],b['channels']): same_fields(x,{k:v for k,v in y.items() if k!='scaled'},'integer spectrum')
    for d in h['spatial']['salient']['descriptors']:
        require(len(d['restored'])==slots and d['ambient_omitted_coefficients']==[],'additive descriptor was omitted')
        if d['mode']<4: require(d['coded_coefficient_indices']==list(range(slots)),'full descriptor not read')
    contributions=h['additive']['ambient_contributions']; require(len(contributions)==4,'missing Float64 contributions')
    for slot,c in enumerate(contributions):
        require(c['transport_slot']==slot and c['recovery_index']==t['additive']['selection'][slot] and len(c['scaled'])==1024,'wrong diagnostic coordinates')
    if dynamic:
        require(h['output_order']==3 and h['output_coefficient_count']==16 and 'mixed' not in h and 'ambient' not in h['spatial'],'dynamic coordinates conflated')
        dyn=h['dynamic_selection']; same_fields(dyn,t['dynamic_selection'],'dynamic')
        require(len(dyn['before_selection'])==9,'wrong internal count')
        wanted=[[0.]*1024 for _ in range(16)]
        for i,c in enumerate(dyn['before_selection']): require(c['slot_index']==i and 'acn_index' not in c,'internal slot mislabeled')
        for line in range(1024):
            frequency=line%128 if t['common_window']==2 else line
            band=next(b for b,end in enumerate(dyn['lines_per_window']) if frequency<end)
            for slot,acn in enumerate(dyn['mappings'][band]['target_acn_indices']): wanted[acn][line]=dyn['before_selection'][slot]['scaled'][line]
        for acn,(a,b) in enumerate(zip(h['channels_after_hoa'],wanted)): require(a['acn_index']==acn and float_bytes(a['scaled'])==float_bytes(b),'scatter changed bytes')
    else:
        require('dynamic_selection' not in h and 'output_order' not in h,'new dimensions leaked')
        require(h['mixed']['ambient_output_coefficients']==t['additive']['selection'],'fixed selection differs')
    for i,c in enumerate(h['channels_after_hoa']): require(c['acn_index']==i,'ACN order differs'); float32(c['scaled'])
    return last_mode,r['packet_sha256']


def ranges(binary,root,kind,options,cfg,payloads,full,index):
    if kind not in ('fixed2','fixed3','dynamic'): return []
    n=shape(options.get('order',3),options.get('dynamic',False)); stride=n*4; prime=31; remainder=17
    valid=len(payloads)*1024-prime-remainder; expected=full[prime*stride:(prime+valid)*stride]; middle=(len(payloads)//2)*1024-prime
    requests=[(0,valid),(0,1031),(middle,1031),(7*1024-prime,1024),(valid-9,100),(valid,1)]; records=[]
    for name,encoder in [('caf',caf_encode),('mp4',mp4_encode)]:
        source=root/name; source.write_bytes(encoder(cfg,payloads,rate=options.get('rate',48000),channels=n,priming=prime,remainder=remainder,variant=index)[0])
        for j,(start,count) in enumerate(requests):
            out=root/(name+'-'+str(j)); r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
            raw=(out/'pcm.f32le').read_bytes(); saved=min(count,valid-start)
            require(raw==expected[start*stride:(start+saved)*stride] and r['saved_frames']==saved and r['input']['consistency_verified'],'container range differs')
            records.append(dict(container=name,start=start,frames=saved,pcm_sha256=hashlib.sha256(raw).hexdigest()))
    trimmed=root/'trimmed'; bundle(trimmed,payloads,priming=prime,remainder=remainder,**options)
    r=command(binary,'decode-sq',trimmed,'--out',root/'trimmed-pcm','--start-frame',middle,'--frames',1031)
    require((root/'trimmed-pcm/pcm.f32le').read_bytes()==expected[middle*stride:(middle+r['saved_frames'])*stride],'bundle range differs')
    return records


def measured(total,actual,expected,location,block,slots,n,wide=False):
    require(len(actual)==len(expected),'numeric shape differs')
    def coordinate(i,a,b):
        out=dict(location,index=i,candidate=a,reference=b)
        if location['stage']=='descriptors': out.update(component=i//(4*slots),subband=(i//slots)%4,slot=i%slots)
        elif location['stage']=='pcm': out.update(packet=i//(1024*n),sample=(i//n)%1024,acn=i%n)
        else: out.update(channel_or_slot=i//1024,window=(i%1024)//128 if block==2 else 0,line=i%128 if block==2 else i%1024)
        return out
    def word(v):
        value=struct.unpack('<Q' if wide else '<I',struct.pack('<d' if wide else '<f',v))[0]; sign=1<<(63 if wide else 31)
        return (2*sign-1)-value if value&sign else sign+value
    for i,(a,b) in enumerate(zip(actual,expected)):
        require(math.isfinite(a) and math.isfinite(b),'nonfinite numerical result')
        error=abs(a-b); ulp=abs(word(a)-word(b)) if error else 0
        for key,value in (('max_absolute_error',error),('max_ulp',ulp)):
            if total.get(key+'_location') is None or value>total[key]: total[key]=value; total[key+'_location']=coordinate(i,a,b)
        if error>1e-6+1e-5*abs(b):
            total['failed_samples']+=1
            if total['first_failure'] is None: total['first_failure']=coordinate(i,a,b)
    require(not total['failed_samples'],str(total['first_failure']))


def validate(binary,r,reference):
    frozen=manifest(); require(frozen==json.loads((ROOT/'data/hoa-additive-vectors-v1.json').read_text()),'frozen vectors differ')
    if reference:
        require(reference['passed'] and reference['mode']=='independent_math' and not reference['errors'] and len(reference['cases'])==len(frozen['cases']),'invalid reference')
        for k in ('code_commit','source_sha256','vector_manifest_sha256','profile','atol','rtol'): require(reference[k]==r[k],k+' differs')
    for identity,(kind,options,cases) in zip(frozen['cases'],sequences()):
        index=identity['index']; order=options.get('order',3); dyn=options.get('dynamic',False); slots=(order+1)**2; n=shape(order,dyn)
        with workspace(r,str(index)+'-'+kind) as root:
            generated=[packet(c,**options) for c in cases]; payloads=[raw for raw,_ in generated]; cfg=cookie(**options); bundle(root/'bundle',payloads,**options)
            summary=command(binary,'parse-packets',root/'bundle','--depth','hoa','--packets',len(cases),'--output',root/'parsed')
            require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'incomplete parse')
            rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]; require(len(rows)==len(cases),'missing reports')
            hashes={k:hashlib.sha256() for k in ('quantized','transport','descriptors','ambient','internal','mapping','hoa')}; structures=[]; last=0; previous=None
            oracle=None if reference else Decoder(slots,n); expected_pcm=[]; cursor=0
            history=dict(origin='cookie',source=hashlib.sha256(cfg).hexdigest(),metadata_origin='cookie',metadata_source=hashlib.sha256(cfg).hexdigest(),history=False)
            for packet_index,(row,(_,truth)) in enumerate(zip(rows,generated)):
                last,previous=check(row,truth,last,previous,options)
                if oracle: expected_pcm.extend(oracle.decode(truth))
                for node,t in nodes(row,truth):
                    drc_history(node,t,history,n)
                    structures.append({k:node[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','drc','drc_history_sufficient')})
                    h=node['hoa']; dynamic=h.get('dynamic_selection'); internal_spectra=dynamic['before_selection'] if dyn else h['channels_after_hoa']
                    if dyn: hashes['mapping'].update(bytes(x for row in dynamic['mappings'] for x in row['target_acn_indices']))
                    vectors=[v for d in h['spatial']['salient']['descriptors'] for v in d['restored']]
                    internal=[v for s in internal_spectra for v in s['scaled']]; output=[v for c in h['channels_after_hoa'] for v in c['scaled']]
                    ambient=[v for c in h['additive']['ambient_contributions'] for v in c['scaled']]
                    transport=[v for e in node['elements'] for v in (e['channels_after_bwe2'][0]['scaled'] if e['present'] else [0.]*1024)]
                    for key,value,wide in [('descriptors',vectors,True),('ambient',ambient,True),('internal',internal,False),('hoa',output,False),('transport',transport,False)]:
                        hashes[key].update(struct.pack('<'+str(len(value))+'d',*value) if wide else float_bytes(value))
                    for e in node['elements']:
                        if e['present']: hashes['quantized'].update(struct.pack('<1024i',*e['channels'][0]['quantized']))
                    if oracle:
                        wanted=oracle.records[cursor]; cursor+=1
                        for stage,actual,expected,wide in [('transport',transport,wanted['transport'],False),('descriptors',vectors,wanted['vectors'],True),('ambient',ambient,wanted['ambient'],True),('internal',internal,wanted['internal'],False),('hoa',output,wanted['scaled'],False)]:
                            measured(r['metrics'][stage],actual,[v for c in expected for v in c],dict(case=index,packet=packet_index,stage=stage,role='current' if node is row else 'embedded'),t['common_window'],slots,n,wide)
                    if kind=='cross_contribution_residual' and packet_index<4: require(internal[0]==1.,'lost cross-contribution unit residual')
            decoded=command(binary,'decode-sq',root/'bundle','--out',root/'pcm'); full=(root/'pcm/pcm.f32le').read_bytes(); impl=decoded['pcm']['decoder_settings']['implementation']['value']
            require(decoded['pcm']['channels']==n and decoded['pcm']['layout']['value']['ambisonic_order']==(3 if dyn else order) and decoded['pcm']['sample_rate']==options.get('rate',48000),'wrong output layout')
            require(decoded['drc_processing']==decoded['loudness_normalization']=='off' and decoded['drc_payloads_complete'],'DRC policy changed')
            require(impl['hoa_recovery_numeric_profile']==PROFILE and impl['backend']=='rust_hoa_additive_sq_drc_off_f64_fft_v1' and impl['hoa_ambient_combination']=='add','wrong backend')
            require(impl['hoa_format_sha256']==format_for(order)['tables_sha256'],'descriptor dictionary changed')
            if dyn: require(impl['hoa_dynamic_format_sha256']==generate()['format_sha256'] and impl['hoa_internal_order']==2 and impl['hoa_output_order']==3,'dynamic rule changed')
            r['implementations'].setdefault(kind,impl); require(r['implementations'][kind]==impl,'implementation changed')
            require(len(full)==len(cases)*1024*n*4 and hashlib.sha256(full).hexdigest()==decoded['pcm']['sha256'],'PCM length/hash differs')
            if oracle: measured(r['metrics']['pcm'],struct.unpack('<'+str(len(full)//4)+'f',full),expected_pcm,dict(case=index,stage='pcm'),0,slots,n)
            record=dict(identity,passed=True,state_sha256=digest(structures),pcm_sha256=hashlib.sha256(full).hexdigest(),ranges=ranges(binary,root,kind,options,cfg,payloads,full,index),**{k+'_sha256':h.hexdigest() for k,h in hashes.items()})
            if reference: require(record==reference['cases'][index],'cross-build additive HOA differs')
            r['cases'].append(record)
        print('additive HOA',index+1,'/'+str(len(frozen['cases'])),kind,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path,required=True); p.add_argument('--report',type=Path,required=True); p.add_argument('--reference-report',type=Path); a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or existing report')
    r=dict(passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),
           vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},cases=[],
           metrics=None if a.reference_report else {k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','ambient','internal','hoa','pcm')},errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        reference=json.loads(a.reference_report.read_text()) if a.reference_report else None; validate(a.binary.resolve(),r,reference)
        require(len(r['cases'])==len(manifest()['cases']) and source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'missing cases or source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    r['stage_sha256']=digest(r['cases']); a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x') as f: json.dump(r,f,indent=2); f.write('\n')
    print(json.dumps({k:r[k] for k in ('passed','stage_sha256','metrics','errors')})); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
