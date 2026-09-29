#!/usr/bin/env python3
"""12/24-channel independent math, exhaustive syntax and exact portable access."""
import argparse,json,platform,subprocess
from pathlib import Path
from datetime import datetime,timezone
from layout_vectors import PROFILE,LAYOUTS,math_manifest,access_manifest,presence_manifest,sequences,access_cases,generated,digest
from validate_channels import validate as check_math
from validate_access import validate as check_access
from validate_portable import ROOT,source_digest
from validate_replay import sha256_file
from validate import require,write_json

def identity(reference,report,counts):
    require(reference.get('passed') is True and reference.get('mode')=='independent_math' and not reference.get('errors'),'unqualified independent layout reference')
    for key in ('profile','code_commit','source_sha256','vector_manifest_sha256','access_manifest_sha256','presence_manifest_sha256','atol','rtol','counts'):
        require(reference.get(key)==report.get(key),'reference identity differs: '+key)
    require(len(reference['sequences'])==counts['sequences'] and len(reference['cases'])==counts['cases'],'reference cases missing')
    require(reference.get('presence',{}).get('cases')==131328 and reference['presence'].get('passed') is True,'missing exhaustive presence evidence')
    require(all(r.get('passed') is True for k in ('sequences','cases') for r in reference[k]),'failed reference case')
    require(reference['pcm_metrics']['failed_samples']==0 and all(m['failed_samples']==0 for m in reference['metrics'].values()),'reference mathematical gate failed')
    require(reference['stage_sha256']==digest({k:reference[k] for k in ('sequences','cases','presence')}),'reference stage digest differs')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','presence-binary','report'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--reference-report',type=Path);a=p.parse_args()
    require(a.binary.is_file() and a.presence_binary.is_file() and not a.report.exists(),'binary missing or report exists')
    frozen=math_manifest();access=access_manifest();presence=presence_manifest()
    counts=dict(sequences=len(frozen['sequences']),cases=len(access['cases']),presence=131328)
    r=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
           code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),presence_binary_sha256=sha256_file(a.presence_binary),
           vector_manifest_sha256=frozen['sha256'],access_manifest_sha256=access['sha256'],presence_manifest_sha256=presence['sha256'],counts=counts,
           mode='bit_exact_replay' if a.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementations={},sequences=[],cases=[],errors=[],presence=None,
           failure_directory=str(a.report.with_suffix('.failures')),metrics=None if a.reference_report else {stage:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for stage in ('raw','cac','tns','bwe2','pcm')},
           pcm_metrics=None if a.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0))
    reference=json.loads(a.reference_report.read_text(encoding='utf-8')) if a.reference_report else None
    try:
        if reference:identity(reference,r,counts)
        require(json.loads((ROOT/'data/layout-presence-v1.json').read_text(encoding='utf-8'))==presence,'presence manifest differs')
        destination=a.report.with_suffix('.presence.json');require(not destination.exists(),'presence report exists')
        subprocess.run([str(a.presence_binary.resolve()),'--report',str(destination)],check=True)
        evidence=json.loads(destination.read_text(encoding='utf-8'));require(evidence['passed'] and evidence['cases']==131328 and evidence['vector_manifest_sha256']==presence['sha256'],'presence incomplete')
        require(evidence['layouts']==[{k:row[k] for k in ('channels','rate','cases','stage_sha256')} for row in presence['layouts']],'presence stages differ')
        r['presence']={k:v for k,v in evidence.items() if k not in ('compiler','debug_assertions')}
        check_math(a.binary.resolve(),r,reference,layouts=LAYOUTS,manifest_fn=math_manifest,manifest_path=ROOT/'data/layout-vectors-v1.json',count=counts['sequences'],sequences_fn=sequences)
        check_access(a.binary.resolve(),r,reference,layouts=LAYOUTS,manifest_fn=access_manifest,manifest_path=ROOT/'data/layout-access-vectors-v1.json',count=counts['cases'],cases_fn=access_cases,generated_fn=generated,include_bundle=True)
        require(len(r['sequences'])==counts['sequences'] and len(r['cases'])==counts['cases'],'missing required layout vectors')
        for impl in r['implementations'].values():
            require(impl['channel_layout_profile']==PROFILE,'missing layout profile')
            require((impl['compiler'],impl['debug_assertions'])==(evidence['compiler'],evidence['debug_assertions']),'presence binary toolchain/build differs')
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(a.presence_binary)==r['presence_binary_sha256'],'source or binary changed during acceptance')
        r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    r['sequences'].sort(key=lambda row:(row['channels'],row['rate'],row['index']))
    r['stage_sha256']=digest({k:r[k] for k in ('sequences','cases','presence')});write_json(a.report,r)
    print(json.dumps({k:r[k] for k in ('passed','counts','stage_sha256','metrics','pcm_metrics','errors')}));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
