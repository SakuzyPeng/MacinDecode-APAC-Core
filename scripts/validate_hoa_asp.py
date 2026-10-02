#!/usr/bin/env python3
"""Portable ASP semantic and error boundaries, with independent Decimal PCM."""
import argparse,hashlib,json,platform,struct,subprocess
from pathlib import Path
import hoa_asp_vectors as vectors
import hoa_shared_vectors as shared
from hoa_shared_oracle import Decoder as SharedDecoder
from hoa_salient_subbands_oracle import Decoder as HoaDecoder
from validate import require,write_json
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_channels import compare,merge_metrics,digest

def boundaries(report,truth):
    require(report['packet_complete'] and not report['unknown_ranges'],'incomplete ASP packet')
    require(report['stop_bit_offset']==(truth if 'packet_end_bit_offset' in truth else truth['tail'])['packet_end_bit_offset'],'ASP packet boundary')
    if truth['inner']:
        inner=report['embedded_preroll'];require(inner['start_bit_offset']==truth['inner_range']['start_bit_offset'] if 'inner_range' in truth else True,'ASP inner start')
        boundaries(inner['report'],truth['inner'])

def run(binary,report,reference):
    frozen=vectors.manifest();require(json.loads((vectors.ROOT/'data/hoa-asp-vectors-v1.json').read_text())==frozen,'ASP manifest differs')
    if reference:
        require(reference['passed'],'failed reference')
        for key in ('code_commit','source_sha256','vector_manifest_sha256'):require(reference[key]==report[key],'reference differs: '+key)
    for index,(name,module,opts,nodes,child_start) in enumerate(vectors.generated()):
        identity=frozen['fixtures'][index]
        for variant in nodes:
            with workspace(report,name+'-'+variant) as root:
                generated=[nodes['first'],nodes[variant],nodes['zero']];payloads=[p for p,t in generated]
                module.bundle(root/'packets',payloads,**opts)
                parsed=command(binary,'parse-packets',root/'packets','--depth','hoa','--output',root/'parsed');require(parsed['errors']==0,'parse failure')
                rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()];require(len(rows)==3,'missing ASP rows')
                for row,(_,truth) in zip(rows,generated):boundaries(row,truth)
                decoded=command(binary,'decode-sq',root/'packets','--out',root/'pcm');raw=(root/'pcm/pcm.f32le').read_bytes()
                require(decoded['saved_frames']==3072 and len(raw)==3072*identity['channels']*4,'ASP frame count differs')
                if not reference:
                    oracle=(SharedDecoder if module is shared else HoaDecoder)(opts);expected=[]
                    for _,truth in generated:expected.extend(oracle.decode(truth))
                    result=compare(struct.unpack('<%df'%(len(raw)//4),raw),expected,dict(case=name,variant=variant));require(result['passed'],str(result['first_failure']));merge_metrics(report['metrics'],result)
                impl=decoded['pcm']['decoder_settings']['implementation']['value']
                record=dict(name=name,variant=variant,frames=decoded['saved_frames'],pcm_sha256=hashlib.sha256(raw).hexdigest(),stages_sha256=digest(rows),implementation={k:v for k,v in impl.items() if k not in ('compiler','debug_assertions')})
                if reference:require(record==reference['cases'][len(report['cases'])],'cross-build ASP differs')
                report['cases'].append(record)
        for kind,hexdata in identity['bad'].items():
            with workspace(report,name+'-'+kind) as root:
                module.bundle(root/'packets',[nodes['first'][0],bytes.fromhex(hexdata)],**opts)
                result=subprocess.run([str(binary),'decode-sq',str(root/'packets'),'--out',str(root/'pcm')],capture_output=True,text=True)
                require(result.returncode!=0,'accepted invalid ASP '+name+' '+kind)
                report['rejections'].append(dict(name=name,kind=kind,packet_sha256=hashlib.sha256(bytes.fromhex(hexdata)).hexdigest()))
        print('ASP',name,flush=True)
    if reference:require(report['rejections']==reference['rejections'],'error controls differ')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',required=True,type=Path);p.add_argument('--report',required=True,type=Path);p.add_argument('--reference-report',type=Path);a=p.parse_args()
    require(a.binary.exists() and not a.report.exists(),'binary missing or report exists')
    report=dict(passed=False,profile=vectors.PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),platform=platform.platform(),vector_manifest_sha256=vectors.manifest()['sha256'],cases=[],rejections=[],errors=[],metrics=None if a.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None),failure_directory=str(a.report.with_suffix('.failures')))
    try:
        run(a.binary.resolve(),report,json.loads(a.reference_report.read_text()) if a.reference_report else None)
        require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['stage_sha256']=digest(dict(cases=report['cases'],rejections=report['rejections']));write_json(a.report,report);print(json.dumps({k:report[k] for k in ('passed','errors','metrics','stage_sha256')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
