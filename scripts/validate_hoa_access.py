#!/usr/bin/env python3
"""Exact HOA CAF/MP4 fast ranges, state, validation scope and Decimal PCM."""
import argparse,json,platform,struct,subprocess
from pathlib import Path
import hoa_access_vectors as vectors
from caf_vectors import encode as caf_encode,digest,sha
from validate import require,write_json
from validate_drc import workspace
from validate_channels import compare,merge_metrics
from validate_access import check_fast,state_identity
from validate_portable import source_digest
from validate_replay import command,sha256_file

def failure(binary,source,out,start):
    result=subprocess.run([str(binary),'decode-sq',str(source),'--out',str(out),'--start-frame',str(start),'--frames','1','--access',out.name],capture_output=True,text=True)
    require(result.returncode!=0,'corruption was accepted')
    value=json.loads(result.stdout or result.stderr)['error']
    return {k:v for k,v in value.items() if k in ('operation','message','bit_offset','packet_index')}

def validate(binary,report,reference,only):
    frozen=vectors.manifest();require(json.loads((vectors.ROOT/'data/hoa-access-vectors-v1.json').read_text())==frozen,'access manifest changed')
    if reference:
        require(reference['passed'],'failed reference')
        for key in ('code_commit','source_sha256','vector_manifest_sha256'):require(reference[key]==report[key],'reference differs: '+key)
    for data in vectors.generated():
        name=data['name']
        if only and name not in only:continue
        with workspace(report,name) as root:
            for fmt in ('caf','mp4'):(root/fmt).write_bytes(data[fmt])
            rows=[];full=None;n=data['channels'];implementation=None
            for label,start,count in data['ranges']:
                outputs=[];states=[];fast_records=[];frames=min(count,data['valid']-start)
                for fmt in ('caf','mp4'):
                    for mode in ('sequential','fast'):
                        dest=root/(fmt+'-'+label+'-'+mode)
                        result=command(binary,'decode-sq',root/fmt,'--out',dest,'--access',mode,'--start-frame',start,'--frames',count)
                        require(result['complete'] and result['input']['consistency_verified'] and result['integrity_checked_packets']==len(data['payloads']),'reduced container integrity checks')
                        require(result['saved_frames']==frames and result['pcm']['source_packet_table']==data['mp4_truth']['packet_table'],'frame coordinates differ')
                        require(result['access']['profile']==vectors.PROFILE and result['drc_processing']==result['loudness_normalization']=='off','access/processing policy')
                        pcm=(dest/'pcm.f32le').read_bytes();require(len(pcm)==frames*n*4 and sha(pcm)==result['pcm']['sha256'],'PCM shape/hash')
                        states.append(state_identity(result));outputs.append(pcm)
                        impl={k:v for k,v in result['pcm']['decoder_settings']['implementation']['value'].items() if k not in ('compiler','debug_assertions')}
                        if implementation is None:implementation=impl
                        require(impl==implementation,'implementation changed')
                        if mode=='fast':
                            check_fast(result['access'],vectors.expected_access(data,start,count))
                            fast_records.append({k:v for k,v in result['access'].items() if k!='timings_seconds'})
                        else:require(result['access']['prefix_scanned_packets']==0,'sequential scan')
                require(all(v==outputs[0] for v in outputs),'fast PCM differs');require(all(v==states[0] for v in states),'fast metadata differs')
                require(fast_records[0]==fast_records[1],'CAF/MP4 counts differ')
                if label=='all':full=outputs[0]
                require(outputs[0]==full[start*n*4:(start+frames)*n*4],'range differs from sequential full slice')
                rows.append(dict(name=label,start=start,frames=frames,pcm_sha256=sha(outputs[0]),access=fast_records[0]))
            if not reference:
                decoder=data['oracle'](data['options']);expected=[v for t in data['truths'] for v in decoder.decode(t)][data['priming']*n:(data['priming']+data['valid'])*n]
                metric=compare(struct.unpack('<%df'%(len(full)//4),full),expected,dict(name=name));require(metric['passed'],str(metric['first_failure']));merge_metrics(report['metrics'],metric)
            # A late malformed prefix must retain the decoder's exact first error.
            bad=list(data['payloads']);bad[-3]+=bytes([165]);bad_caf=caf_encode(data['cookie'],bad,rate=data['options'].get('rate',48000),channels=n,variant=24,layout_tag=0)[0]
            (root/'bad').write_bytes(bad_caf)
            errors=[failure(binary,root/'bad',root/mode,(len(bad)-1)*1024) for mode in ('sequential','fast')];require(errors[0]==errors[1],'first error changed')
            (root/'truncated').write_bytes(data['caf'][:-1])
            for mode in ('sequential','fast'):
                p=subprocess.run([str(binary),'decode-sq',str(root/'truncated'),'--out',str(root/('truncated-'+mode)),'--access',mode,'--frames','1'],capture_output=True,text=True);require(p.returncode!=0,'truncated container accepted')
            # The directory access policy is unchanged.
            data['module'].bundle(root/'bundle',data['payloads'],**data['options'])
            p=subprocess.run([str(binary),'decode-sq',str(root/'bundle'),'--out',str(root/'directory-fast'),'--access','fast'],capture_output=True,text=True);require(p.returncode!=0 and not (root/'directory-fast').exists(),'directory fast policy changed')
            row=dict(name=name,ranges=rows,implementation=implementation,first_error=errors[0],truncated_rejected=True,directory_rejected=True)
            if reference:require(row==next(v for v in reference['cases'] if v['name']==name),'cross-build access differs')
            report['cases'].append(row)
        print('HOA ACCESS',len(report['cases']),name,flush=True)
    require(len(report['cases'])==(len(only) if only else len(frozen['fixtures'])),'missing access cases')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',required=True,type=Path);p.add_argument('--report',required=True,type=Path);p.add_argument('--reference-report',type=Path);p.add_argument('--case',action='append');a=p.parse_args()
    require(a.binary.exists() and not a.report.exists(),'binary missing/report exists')
    report=dict(passed=False,profile=vectors.PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),platform=platform.platform(),vector_manifest_sha256=vectors.manifest()['sha256'],cases=[],errors=[],metrics=None if a.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None),failure_directory=str(a.report.with_suffix('.failures')))
    try:
        validate(a.binary.resolve(),report,json.loads(a.reference_report.read_text()) if a.reference_report else None,a.case)
        require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['stage_sha256']=digest(report['cases']);write_json(a.report,report);print(json.dumps({k:report[k] for k in ('passed','metrics','errors','stage_sha256')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
