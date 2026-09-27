#!/usr/bin/env python3
"""CAF/bundle exact PCM, independent mathematics and cross-build acceptance."""
import argparse,json,platform,struct,subprocess
from pathlib import Path
from datetime import datetime,timezone
from caf_vectors import PROFILE,cases,generate,make_bundle,manifest,digest,sha
from packet_oracle import Decoder
from validate import require,write_json
from validate_drc import workspace
from validate_portable import ROOT,source_digest,compare_pcm
from validate_replay import command,sha256_file

COUNT=280
MANIFEST=ROOT/'data/caf-vectors-v1.json'

def check_input(actual,truth):
    for key,value in truth.items():require(actual[key]==value,'CAF input differs: '+key)

def check_report(report,truth,start,requested):
    table=truth['packet_table'];valid=table['valid_frames'];frames=min(requested,valid-start)
    require(report['complete'] and report['experimental'] and not report['native_apis_used'],'wrong decoder completion/scope')
    require(report['drc_processing']==report['loudness_normalization']=='off','unexpected processing')
    require(report['integrity_checked_packets']==truth['packet_count'],'unused input was not verified')
    require(report['saved_frames']==frames and report['pcm']['frames']==frames and report['pcm']['start_frame']==start,'wrong valid-frame crop')
    require(report['warmup_packets']==(start+table['priming_frames'])//1024,'wrong sequential warmup')
    require(report['pcm']['source_packet_table']==table,'packet table was altered')
    require(report['pcm']['source_cookie_sha256']==truth['cookie_sha256'],'cookie changed')

def validate(binary,report,reference):
    frozen=json.loads(MANIFEST.read_text(encoding='utf-8'))
    require(frozen==manifest() and len(frozen['cases'])==COUNT,'CAF frozen inputs differ')
    previous={(r['rate'],r['index']):r for r in reference['cases']} if reference else {}
    for rate in (48000,44100):
        for index,case in enumerate(cases()):
            config,packets,raw,truth,oracle=generate(case,rate,index)
            with workspace(report,f'{rate}-{index}') as root:
                caf=root/'source.audio';caf.write_bytes(raw)
                make_bundle(root/'bundle',case,rate,packets,truth)
                valid=truth['packet_table']['valid_frames'];prime=truth['packet_table']['priming_frames']
                ranges=[('all',0,valid),('head',0,997),('middle',valid//2,1051),('tail',max(0,valid-1001),2048),('eof',valid,17)]
                results=[];full=None
                for name,start,count in ranges:
                    actual=[]
                    for label,source in [('caf',caf),('bundle',root/'bundle')]:
                        out=root/(label+'-'+name)
                        args=[] if name=='all' else ['--start-frame',start,'--frames',count]
                        r=command(binary,'decode-sq',source,'--out',out,*args)
                        check_report(r,truth,start,count)
                        if label=='caf':
                            check_input(r['input'],truth)
                            implementation=r['pcm']['decoder_settings']['implementation']['value']
                            if report['implementation'] is None:report['implementation']=implementation
                            require(report['implementation']==implementation,'candidate implementation changed')
                        pcm=(out/'pcm.f32le').read_bytes()
                        require(len(pcm)==r['saved_frames']*8 and sha(pcm)==r['pcm']['sha256'],'PCM output bytes/hash differ')
                        require(not (out/'.incomplete.json').exists(),'complete output retains failure marker')
                        actual.append((r,pcm))
                    require(actual[0][1]==actual[1][1],'CAF and bundle PCM differ')
                    if name=='all':full=actual[0][1]
                    require(actual[0][1]==full[start*8:(start+min(count,valid-start))*8],'range differs from sequential slice')
                    r=actual[0][0];results.append(dict(name=name,start=start,frames=r['saved_frames'],pcm_sha256=r['pcm']['sha256'],warmup_packets=r['warmup_packets'],decoded_packets=r['packets']))
                record=dict(rate=rate,index=index,input_sha256=sha(raw),container_sha256=digest(truth),ranges=results,passed=True)
                if reference:require(record==previous[rate,index],'cross-build CAF output differs')
                else:
                    decoder=Decoder();expected=[v for t in oracle for v in decoder.decode(t)][prime*2:(prime+valid)*2]
                    metrics=compare_pcm(struct.unpack('<'+str(len(full)//4)+'f',full),expected)
                    for key in ('max_absolute_error','max_ulp'):report['pcm_metrics'][key]=max(report['pcm_metrics'][key],metrics[key])
                    report['pcm_metrics']['failed_samples']+=metrics['failed_samples']
                    if not metrics['passed']:
                        failure=metrics['first_failure'];failure.update(valid_frame=failure['sample']//2,packet_index=(prime+failure['sample']//2)//1024,packet_frame=(prime+failure['sample']//2)%1024)
                        record.update(passed=False,pcm_metrics=metrics);report['cases'].append(record)
                        raise AssertionError('CAF independent PCM math exceeds original tolerance')
                report['cases'].append(record)
            if index%10==0:print(f'CAF {rate}: {len(report["cases"])}/{COUNT}',flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--reference-report',type=Path)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),
                vector_manifest_sha256=manifest()['sha256'],mode='bit_exact_replay' if args.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,
                failure_directory=str(args.report.with_suffix('.failures')),implementation=None,cases=[],errors=[],pcm_metrics=None if args.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0))
    reference=json.loads(args.reference_report.read_text(encoding='utf-8')) if args.reference_report else None
    try:
        if reference:
            require(reference['passed'] and reference['mode']=='independent_math' and len(reference['cases'])==COUNT and not reference['errors'],'incomplete independent reference')
            for key in ('code_commit','source_sha256','profile','vector_manifest_sha256','atol','rtol'):require(report[key]==reference[key],'reference identity differs: '+key)
        validate(args.binary.resolve(),report,reference)
        require(len(report['cases'])==COUNT,'required CAF case missing')
        require(sha256_file(args.binary)==report['binary_sha256'] and source_digest()==report['source_sha256'],'source or binary changed during acceptance')
        report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['stage_sha256']=digest(report['cases']);write_json(args.report,report)
    print(json.dumps({k:report[k] for k in ('passed','stage_sha256','pcm_metrics','errors')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
