#!/usr/bin/env python3
"""MP4/CAF/bundle byte identity and independent per-channel mathematics."""
import argparse,json,platform,struct,subprocess
from pathlib import Path
from datetime import datetime,timezone
from mp4_vectors import PROFILE,LAYOUTS,cases,generate,manifest,digest,sha,bundle
from caf_vectors import encode as caf_encode
from channel_oracle import Decoder
from validate import require,write_json
from validate_drc import workspace
from validate_portable import ROOT,source_digest,compare_pcm
from validate_replay import command,sha256_file
from validate_caf import check_report

COUNT=852
MANIFEST=ROOT/'data/mp4-vectors-v1.json'

def check_input(actual,truth):
    for key,value in truth.items():require(actual[key]==value,'MP4 input differs: '+key)

def validate(binary,report,reference):
    frozen=json.loads(MANIFEST.read_text(encoding='utf-8'));require(frozen==manifest() and len(frozen['cases'])==COUNT,'MP4 frozen inputs differ')
    previous={(r['channels'],r['rate'],r['index']):r for r in reference['cases']} if reference else {}
    for n in LAYOUTS:
        for rate in (48000,44100):
            for index,case in enumerate(cases(n)):
                config,packets,raw,truth,oracle=generate(n,rate,index,case)
                with workspace(report,f'{n}-{rate}-{index}') as root:
                    mp4=root/'source.unknown';mp4.write_bytes(raw)
                    table=truth['packet_table'];valid=table['valid_frames'];prime=table['priming_frames']
                    bundle(root/'bundle',packets,n,rate,priming=prime,remainder=table['remainder_frames'],**case[1])
                    caf=root/'source.caf';caf.write_bytes(caf_encode(config,packets,rate,priming=prime,remainder=table['remainder_frames'],variant=index,channels=n)[0])
                    ranges=[('all',0,valid),('head',0,997),('middle',valid//2,1051),('tail',max(0,valid-1001),2048),('eof',valid,17)]
                    results=[];full=None
                    for name,start,count in ranges:
                        outputs=[]
                        for label,source in [('mp4',mp4),('caf',caf),('bundle',root/'bundle')]:
                            out=root/(label+'-'+name);args=[] if name=='all' else ['--start-frame',start,'--frames',count]
                            r=command(binary,'decode-sq',source,'--out',out,*args);check_report(r,truth,start,count)
                            if label=='mp4':
                                check_input(r['input'],truth)
                                implementation=r['pcm']['decoder_settings']['implementation']['value'];key=str(n)
                                if key not in report['implementations']:report['implementations'][key]=implementation
                                require(report['implementations'][key]==implementation,'implementation changed')
                            pcm=(out/'pcm.f32le').read_bytes()
                            require(len(pcm)==r['saved_frames']*4*n and sha(pcm)==r['pcm']['sha256'],'PCM shape/digest differs')
                            require(not (out/'.incomplete.json').exists(),'success retains failure marker');outputs.append((r,pcm))
                        require(outputs[0][1]==outputs[1][1]==outputs[2][1],'MP4/CAF/bundle PCM differs')
                        if name=='all':full=outputs[0][1]
                        require(outputs[0][1]==full[start*4*n:(start+min(count,valid-start))*4*n],'range differs from sequential slice')
                        r=outputs[0][0];results.append(dict(name=name,start=start,frames=r['saved_frames'],pcm_sha256=r['pcm']['sha256'],warmup_packets=r['warmup_packets'],decoded_packets=r['packets']))
                    record=dict(channels=n,rate=rate,index=index,input_sha256=sha(raw),container_sha256=digest(truth),ranges=results,passed=True)
                    if reference:require(record==previous[n,rate,index],'cross-build MP4 output differs')
                    else:
                        decoder=Decoder(n);expected=[v for t in oracle for v in decoder.decode(t)][prime*n:(prime+valid)*n]
                        metrics=compare_pcm(struct.unpack('<'+str(len(full)//4)+'f',full),expected)
                        for key in ('max_absolute_error','max_ulp'):report['pcm_metrics'][key]=max(report['pcm_metrics'][key],metrics[key])
                        report['pcm_metrics']['failed_samples']+=metrics['failed_samples']
                        if not metrics['passed']:
                            failure=metrics['first_failure'];sample=failure['sample'];failure.update(channel=sample%n,valid_frame=sample//n,packet_index=(prime+sample//n)//1024,packet_frame=(prime+sample//n)%1024)
                            record.update(passed=False,pcm_metrics=metrics);report['cases'].append(record)
                            raise AssertionError('MP4 independent PCM exceeds original tolerance')
                    report['cases'].append(record)
                if index%10==0:print(f'MP4 {n}ch {rate}: {len(report["cases"])}/{COUNT}',flush=True)

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
            require(reference['passed'] and reference['mode']=='independent_math' and len(reference['cases'])==COUNT and not reference['errors'],'incomplete independent reference')
            for key in ('code_commit','source_sha256','profile','vector_manifest_sha256','atol','rtol'):require(report[key]==reference[key],'reference identity differs: '+key)
        validate(args.binary.resolve(),report,reference);require(len(report['cases'])==COUNT,'required MP4 case missing')
        require(sha256_file(args.binary)==report['binary_sha256'] and source_digest()==report['source_sha256'],'source or binary changed during acceptance');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['stage_sha256']=digest(report['cases']);write_json(args.report,report)
    print(json.dumps({k:report[k] for k in ('passed','stage_sha256','pcm_metrics','errors')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
