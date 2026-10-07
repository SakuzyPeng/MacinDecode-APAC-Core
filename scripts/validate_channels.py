#!/usr/bin/env python3
"""Independent multi-element SQ mathematics and portable exact stage acceptance."""
import argparse,copy,hashlib,json,math,platform,struct,subprocess,sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
from channel_vectors import LAYOUTS,PROFILE,layout,sequences,packet,cookie,bundle,manifest
from channel_oracle import Decoder,spectra
from validate import require,write_json
from validate_portable import ROOT,source_digest
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate_packets import coverage
from validate_spectra import ulp

MANIFEST=ROOT/'data/channel-vectors-v1.json'
COUNT=2742

def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
def float_bytes(values):return struct.pack('<'+str(len(values))+'f',*values)
def compare(actual,expected,location):
    require(len(actual)==len(expected),'numeric shape differs')
    maximum=0.;distance=0;failed=0;first=None
    for i,(a,b) in enumerate(zip(actual,expected)):
        require(math.isfinite(a) and math.isfinite(b),'nonfinite numeric value')
        error=abs(a-b);maximum=max(maximum,error)
        if error:distance=max(distance,ulp(a,b))
        if error>1e-6+1e-5*abs(b):
            failed+=1
            if first is None:first=dict(location,index=i,candidate=a,reference=b)
    return dict(passed=not failed,max_absolute_error=maximum,max_ulp=distance,failed_samples=failed,first_failure=first)

def same_fields(actual,expected,context):
    if isinstance(expected,dict):
        for key,value in expected.items():
            require(key in actual,context+' missing '+key);same_fields(actual[key],value,context+'.'+key)
    elif isinstance(expected,list):
        require(len(actual)==len(expected),context+' count differs')
        for i,(a,b) in enumerate(zip(actual,expected)):same_fields(a,b,context+f'[{i}]')
    else:require(actual==expected,context+' differs')

def check(report,truth,channels):
    require(report['packet_complete'] and report['status']=='complete','incomplete channel packet')
    require(report['channel_count']==channels and report['channel_labels']==layout(channels)[3],'channel map differs')
    require(report['packet_state_profile']==PROFILE,'wrong channel state profile')
    profile='apac-channel-layout-v3' if channels==16 else 'apac-channel-layout-v2' if channels in (12,24) else None
    require(report.get('channel_layout_profile')==profile,'wrong layout profile')
    require(report['component_end_bit_offset']==truth['core_end_bit_offset'] and report['stop_bit_offset']==truth['tail']['packet_end_bit_offset'],'core/packet endpoint differs')
    require(not report['unknown_ranges'],'complete report retains unknown bits');coverage(report)
    same_fields(report['packet_tail'],truth['tail'],'tail')
    require(len(report['elements'])==len(truth['elements']),'element count differs')
    for actual,wanted in zip(report['elements'],truth['elements']):
        cfg=wanted['configuration'];name=f'element {cfg["element_index"]}'
        for key in ('configuration','present','start_bit_offset','end_bit_offset','shared_ics'):same_fields(actual[key],wanted[key],name+'.'+key)
        require(actual['element_complete'] and actual['spectrum_complete']==wanted['present'],'wrong element completion')
        require(actual['coding_type']==(0 if wanted['present'] else None),'wrong coding type')
        require(actual['tns_applicable']==(wanted['present'] and cfg['kind']!='lfe'),'wrong TNS applicability')
        require(actual['bwe2_applicable']==(wanted['present'] and cfg['kind']!='lfe'),'wrong BWE applicability')
        require(actual['bwe2_complete']==actual['bwe2_applicable'],'wrong BWE completion')
        for key in ('cac','tns','bwe2'):same_fields(actual[key],wanted[key],name+'.'+key)
        require(len(actual['channels'])==len(wanted['channels']),'fabricated or missing encoded channel')
        for a,b in zip(actual['channels'],wanted['channels']):same_fields(a,{k:v for k,v in b.items() if k!='scaled'},name+'.channel')
        expected_count=len(wanted['channels'])
        for key in ('channels_after_cac','channels_after_tns','channels_after_bwe2'):require(len(actual[key])==expected_count,name+'.'+key+' count differs')
    if truth['drc'] is None:require(report['drc'] is None and report['drc_complete'] is None,'fabricated DRC')
    else:
        same_fields(report['drc'],truth['drc'],'DRC')
        require(report['drc_complete'] and not report['drc_processing_applied'],'wrong DRC completion/policy')
    if truth['inner'] is None:require(report['embedded_preroll'] is None,'fabricated preroll')
    else:
        same_fields(report['embedded_preroll'],truth['inner_range'],'preroll range')
        check(report['embedded_preroll']['report'],truth['inner'],channels)

def flatten(report,truth):
    if truth['inner'] is not None:yield from flatten(report['embedded_preroll']['report'],truth['inner'])
    yield report,truth

def parameters(report):
    return {k:report[k] for k in ('fields','derived','status','stop_bit_offset','component_end_bit_offset','packet_tail','channel_count','channel_labels','drc','drc_complete','drc_history_sufficient','drc_processing_applied')}

class History:
    def __init__(self,config,options,channels):
        self.origin=self.metadata_origin='cookie';self.source=self.metadata_source=sha(config);self.history=False;self.options=options;self.channels=channels;self.loudness=165 if options['rich'] else None
    def check(self,r,truth,case,rate):
        if truth['inner'] is not None:self.check(r['embedded_preroll']['report'],truth['inner'],case['preroll'],rate)
        if truth['drc'] is None:return
        expected=truth['drc'];data=r['drc'];raw,_=packet(case,self.channels,rate,**self.options)
        require(r['packet_sha256']==sha(raw),'packet identity differs')
        require(r['drc_history_sufficient']==self.history,'DRC history order differs')
        if expected['header_present']:
            self.metadata_origin='packet';self.metadata_source=sha(raw)
            self.loudness=case.get('drc',{}).get('loudness_value',165 if self.options['rich'] else None)
            if expected['config_present']:self.origin='packet';self.source=sha(raw)
        cfg=data['configuration']
        require((cfg['source'],cfg['source_sha256'],cfg['loudness_metadata_source'],cfg['loudness_metadata_source_sha256'])==(self.origin,self.source,self.metadata_origin,self.metadata_source),'DRC provenance differs')
        values=[f['value'] for f in cfg['loudness_metadata'] if f['name'].endswith('.value_a_encoded')]
        require(values==([] if self.loudness is None else [self.loudness]),'DRC metadata lost')
        base=[f['value'] for f in cfg['fields'] if f['name'].endswith('.base_channel_count')];require(base==[self.channels],'DRC base channels differ')
        self.history=any(n['time']<1024 for n in expected['nodes'])

def batches(values,limit=24):
    batch=[];frames=0
    for row in values:
        if batch and frames+len(row[3])>limit:yield batch;batch=[];frames=0
        batch.append(row);frames+=len(row[3])
    if batch:yield batch

def validate(binary,report,reference,*,layouts=LAYOUTS,manifest_fn=manifest,manifest_path=MANIFEST,count=COUNT,sequences_fn=sequences):
    frozen=json.loads(manifest_path.read_text(encoding='utf-8'));require(frozen==manifest_fn() and len(frozen['sequences'])==count,'frozen channel vectors differ')
    records={(r['channels'],r['rate'],r['index']):r for r in frozen['sequences']}
    previous={(r['channels'],r['rate'],r['index']):r for r in reference['sequences']} if reference else {}
    for channels in layouts:
        for rate in (48000,44100):
            buckets=defaultdict(list)
            for index,(kind,options,seq) in enumerate(sequences_fn(channels)):buckets[json.dumps(options,sort_keys=True)].append((index,kind,options,seq))
            for group in buckets.values():
                for batch in batches(group):
                    options=batch[0][2]
                    with workspace(report,f'{channels}-{rate}-{batch[0][0]}') as root:
                        generated=[packet(c,channels,rate,**options) for _,_,_,seq in batch for c in seq]
                        config=cookie(channels,rate,**options);bundle(root/'packets',[p for p,_ in generated],channels,rate,**options)
                        summary=command(binary,'parse-packets',root/'packets','--depth','channels','--packets',len(generated),'--output',root/'packets.jsonl')
                        require(summary['channel_packets_complete']==len(generated) and summary['errors']==0,'channel parse incomplete')
                        rows=[json.loads(l)['report'] for l in (root/'packets.jsonl').read_text(encoding='utf-8').splitlines()];require(len(rows)==len(generated),'missing packets')
                        decoded=command(binary,'decode-sq',root/'packets','--out',root/'pcm');implementation=decoded['pcm']['decoder_settings']['implementation']['value']
                        key=str(channels)
                        if key not in report['implementations']:report['implementations'][key]=implementation
                        require(implementation==report['implementations'][key],'implementation changed')
                        require(decoded['complete'] and decoded['experimental'] and not decoded['native_apis_used'],'wrong decoder scope')
                        require(decoded['pcm']['channels']==channels and decoded['saved_frames']==1024*len(rows),'PCM shape differs')
                        require(decoded['drc_processing']==decoded['loudness_normalization']=='off','processing enabled')
                        pcm=(root/'pcm/pcm.f32le').read_bytes();require(len(pcm)==channels*4*1024*len(rows) and sha(pcm)==decoded['pcm']['sha256'],'PCM bytes/hash differs')
                        history=History(config,options,channels);cursor=0
                        for index,kind,_,seq in batch:
                            length=len(seq);chosen=rows[cursor:cursor+length];parts=generated[cursor:cursor+length]
                            state=[];hashes={name:hashlib.sha256() for name in ('quantized','raw','cac','tns','bwe2')}
                            for frame_index,(r,(_,truth),case) in enumerate(zip(chosen,parts,seq)):
                                check(r,truth,channels);history.check(r,truth,case,rate)
                                for actual,expected in flatten(r,truth):
                                    state.append(parameters(actual))
                                    for a,b in zip(actual['elements'],expected['elements']):
                                        if not b['present']:continue
                                        model=spectra(b) if not reference else None
                                        groups={'raw':a['channels'],'cac':a['channels_after_cac'],'tns':a['channels_after_tns'],'bwe2':a['channels_after_bwe2']}
                                        for local,c in enumerate(a['channels']):
                                            coord=struct.pack('<II',a['configuration']['element_index'],local)
                                            hashes['quantized'].update(coord+struct.pack('<1024i',*c['quantized']))
                                            for stage,values in groups.items():
                                                raw=float_bytes(values[local]['scaled']);hashes[stage].update(coord+raw)
                                                if model is not None:
                                                    metric=compare(values[local]['scaled'],model[stage][local],dict(channels=channels,rate=rate,index=index,packet=frame_index,element=a['configuration']['element_index'],channel=a['configuration']['output_channels'][local],stage=stage))
                                                    merge_metrics(report['metrics'][stage],metric);require(metric['passed'],'independent '+stage+' spectrum differs')
                            first=cursor*1024*channels*4;raw=pcm[first:first+length*1024*channels*4]
                            record=dict(records[channels,rate,index],state_sha256=digest(state),pcm_sha256=sha(raw),passed=True,**{name+'_sha256':h.hexdigest() for name,h in hashes.items()})
                            if reference:require(record==previous[channels,rate,index],'cross-build channel stages differ')
                            else:
                                decoder=Decoder(channels);expected=[v for _,truth in parts for v in decoder.decode(truth)]
                                metric=compare(struct.unpack('<'+str(len(raw)//4)+'f',raw),expected,dict(channels=channels,rate=rate,index=index,stage='pcm'))
                                if metric['first_failure']:
                                    failure=metric['first_failure'];sample=failure['index'];failure.update(sequence_index=index,packet=sample//(channels*1024),frame=sample//channels%1024,channel=sample%channels)
                                merge_metrics(report['metrics']['pcm'],metric)
                                if not metric['passed']:record['passed']=False;report['sequences'].append(record);raise AssertionError('independent PCM differs')
                            report['sequences'].append(record);cursor+=length
                    print(f'CHANNELS {channels} {rate}: {len(report["sequences"])}/{count}',flush=True)

def merge_metrics(total,item):
    total['max_absolute_error']=max(total['max_absolute_error'],item['max_absolute_error']);total['max_ulp']=max(total['max_ulp'],item['max_ulp']);total['failed_samples']+=item['failed_samples']
    if total['first_failure'] is None and item['first_failure'] is not None:total['first_failure']=item['first_failure']

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),
                vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if args.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},
                failure_directory=str(args.report.with_suffix('.failures')),sequences=[],errors=[],metrics={stage:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for stage in ('raw','cac','tns','bwe2','pcm')} if not args.reference_report else None)
    reference=json.loads(args.reference_report.read_text(encoding='utf-8')) if args.reference_report else None
    try:
        if reference:
            require(reference['passed'] and reference['mode']=='independent_math' and len(reference['sequences'])==COUNT and not reference['errors'],'incomplete independent reference')
            for key in ('profile','code_commit','source_sha256','vector_manifest_sha256','atol','rtol'):require(reference[key]==report[key],'reference identity differs: '+key)
        validate(args.binary.resolve(),report,reference)
        require(len(report['sequences'])==COUNT,'required channel sequence missing')
        require(source_digest()==report['source_sha256'] and sha256_file(args.binary)==report['binary_sha256'],'source or binary changed during acceptance')
        report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['sequences'].sort(key=lambda r:(r['channels'],r['rate'],r['index']));report['stage_sha256']=digest(report['sequences']);write_json(args.report,report)
    print(json.dumps({k:report[k] for k in ('passed','stage_sha256','metrics','errors')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
