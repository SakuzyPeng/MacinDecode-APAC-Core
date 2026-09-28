#!/usr/bin/env python3
"""Fast/sequential exact PCM and metadata history, independently specified work."""
import argparse,json,platform,struct,subprocess
from pathlib import Path
from datetime import datetime,timezone
from access_vectors import PROFILE,LAYOUTS,cases,generated,expected_access,manifest,digest,sha
from channel_oracle import Decoder
from validate import require,write_json
from validate_drc import workspace
from validate_portable import ROOT,source_digest,compare_pcm
from validate_replay import command,sha256_file

COUNT=852
MANIFEST=ROOT/'data/access-vectors-v1.json'

def check_fast(actual,expected):
    for key,value in expected.items():require(actual[key]==value,'fast work/state contract differs: '+key)
    for value in actual['timings_seconds'].values():require(isinstance(value,(int,float)) and value>=0,'invalid timing')

def state_identity(report):
    a=report['access'];before=a['metadata_before_output_sha256'];after=a['metadata_after_processing_sha256']
    def valid(value):return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(valid(after),'missing final metadata state evidence')
    require(valid(before) if report['saved_frames'] else before is None,'missing or fabricated output-boundary state evidence')
    return before,after

def validate(binary,report,reference):
    frozen=json.loads(MANIFEST.read_text(encoding='utf-8'));require(frozen==manifest() and len(frozen['cases'])==COUNT,'frozen access inputs differ')
    previous={(r['channels'],r['rate'],r['index']):r for r in reference['cases']} if reference else {}
    for n in LAYOUTS:
        for rate in (48000,44100):
            for index,case in enumerate(cases(n)):
                data=generated(n,rate,index,case)
                with workspace(report,f'{n}-{rate}-{index}') as root:
                    table=data['mp4_truth']['packet_table'];prime=table['priming_frames'];valid=table['valid_frames'];full=None;ranges=[]
                    for label in ('caf','mp4'):(root/label).write_bytes(data[label])
                    for name,start,count in data['ranges']:
                        expected=expected_access(data,start,count);outputs=[];access_records=[];frames=min(count,valid-start)
                        for label in ('caf','mp4'):
                            for mode in ('sequential','fast'):
                                out=root/(label+'-'+mode+'-'+name)
                                r=command(binary,'decode-sq',root/label,'--out',out,'--start-frame',start,'--frames',count,'--access',mode)
                                require(r['complete'] and r['experimental'] and not r['native_apis_used'],'wrong completion/scope')
                                require(r['integrity_checked_packets']==len(data['payloads']) and r['input']['consistency_verified'],'input verification was reduced')
                                require(r['saved_frames']==frames and r['pcm']['start_frame']==start and r['pcm']['source_packet_table']==table,'timeline changed')
                                require(r['drc_processing']==r['loudness_normalization']=='off','playback processing enabled')
                                pcm=(out/'pcm.f32le').read_bytes();require(len(pcm)==frames*n*4 and sha(pcm)==r['pcm']['sha256'],'PCM bytes/hash differ')
                                if mode=='fast':
                                    check_fast(r['access'],expected);require(r['packets']==expected['synthesized_packets'] and r['raw_frames_decoded']==r['packets']*1024,'scanned frames counted as decoded audio')
                                else:require(r['access']['prefix_scanned_packets']==0,'sequential mode scanned rather than decoded')
                                impl=r['pcm']['decoder_settings']['implementation']['value'];key=str(n)
                                if key not in report['implementations']:report['implementations'][key]=impl
                                require(report['implementations'][key]==impl,'implementation identity changed')
                                state_identity(r)
                                outputs.append(pcm);access_records.append(r['access'])
                        require(all(p==outputs[0] for p in outputs),'fast/sequential or container PCM differs')
                        if name=='all':full=outputs[0]
                        require(outputs[0]==full[start*n*4:(start+frames)*n*4],'range differs from continuous slice')
                        for key in ('metadata_before_output_sha256','metadata_after_processing_sha256'):
                            require(len({a[key] for a in access_records})==1,'metadata history/provenance differs: '+key)
                        stable={k:v for k,v in access_records[1].items() if k!='timings_seconds'}
                        require(stable=={k:v for k,v in access_records[3].items() if k!='timings_seconds'},'container access differs')
                        ranges.append(dict(name=name,start=start,frames=frames,pcm_sha256=sha(outputs[0]),access=stable,passed=True))
                    record=dict(channels=n,rate=rate,index=index,ranges=ranges,passed=True)
                    if reference:require(record==previous[n,rate,index],'cross-build access digest differs')
                    else:
                        decoder=Decoder(n);wanted=[v for t in data['truths'] for v in decoder.decode(t)][prime*n:(prime+valid)*n]
                        metrics=compare_pcm(struct.unpack('<'+str(len(full)//4)+'f',full),wanted)
                        for key in ('max_absolute_error','max_ulp'):report['pcm_metrics'][key]=max(report['pcm_metrics'][key],metrics[key])
                        report['pcm_metrics']['failed_samples']+=metrics['failed_samples']
                        if not metrics['passed']:
                            record.update(passed=False,pcm_metrics=metrics);report['cases'].append(record);raise AssertionError('independent PCM exceeds original tolerance')
                    report['cases'].append(record)
                if index%10==0:print(f'ACCESS {n}ch {rate}: {len(report["cases"])}/{COUNT}',flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),
                vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if args.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,
                failure_directory=str(args.report.with_suffix('.failures')),implementations={},cases=[],errors=[],pcm_metrics=None if args.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0))
    reference=json.loads(args.reference_report.read_text(encoding='utf-8')) if args.reference_report else None
    try:
        if reference:
            require(reference['passed'] and reference['mode']=='independent_math' and len(reference['cases'])==COUNT and not reference['errors'],'incomplete mathematical reference')
            for key in ('code_commit','source_sha256','profile','vector_manifest_sha256','atol','rtol'):require(report[key]==reference[key],'reference identity differs: '+key)
        validate(args.binary.resolve(),report,reference);require(len(report['cases'])==COUNT,'required access case missing')
        require(sha256_file(args.binary)==report['binary_sha256'] and source_digest()==report['source_sha256'],'source or binary changed during acceptance');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['stage_sha256']=digest(report['cases']);write_json(args.report,report)
    print(json.dumps({k:report[k] for k in ('passed','stage_sha256','pcm_metrics','errors')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
