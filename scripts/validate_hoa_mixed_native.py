#!/usr/bin/env python3
"""Verify mixed HOA integer boundaries, capacity, mapping and history from bounded captures."""
import argparse, hashlib, json, struct, subprocess
from pathlib import Path
from decimal import Decimal as D
from hoa_mixed_vectors import native_controls, packet
from hoa_mixed_oracle import descriptor
from validate_channels import compare, merge_metrics, float_bytes
from validate_replay import command, sha256_file
from validate_portable import source_digest
from validate import require, write_json
from validate_drc import workspace
from native_frame_trace import COMPONENT_SHA256


def frames(truth):
    for sequence,t in enumerate(truth):
        if t['inner'] is not None: yield sequence,'preroll',t['inner']
        yield sequence,'current',t


def verify(truth, trace):
    n=trace['channels']; capacity={9:18432,16:32768}[n]
    caps=[e for e in trace['events'] if e['kind']=='capacity']
    require(caps and all(e['status']==0 and e['capacity_bytes']==capacity for e in caps),'mixed capacity differs')
    metrics={k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','isolated_recovery')}
    expected_history=[[[D(0)]*n for _ in range(4)] for _ in range(5)]; previous=[0.]*(20*n); count=0
    for sequence,role,t in frames(truth):
        es=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        def one(kind):
            found=[e for e in es if e['kind']==kind]; require(len(found)==1,'missing '+kind); return found[0]
        read,entry,history=one('hoa_spatial'),one('hoa_deserialize'),one('hoa_history')['after']
        require(read['status']==entry['status']==0,'native parse failed')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'spatial boundary differs')
        require(entry['window']==t['common_window'] and entry['end']['relative_bit_offset']==t['core_end_bit_offset'],'core boundary/window differs')
        require((history['coefficients'],history['output_channels'],history['salient'],history['ambient'],history['transform_count'])==(n,n,5,4,0),'wrong mixed dimensions')
        require(history['ambient_indices']==list(range(4)) and history['omitted_ambient_count']==4 and not history['flags'][3],'wrong ambient overwrite/omission configuration')
        require(history['subbands']==[4]*5 and history['coefficient_counts']==[n]*5 and history['quantization_bits']==6,'descriptor shape differs')
        require(history['four_subband_ends']==[32,80,216,1024],'spatial frequency boundary differs')
        require(float_bytes(read['before']['history'])==float_bytes(previous),'native history not carried across frames')
        values=[]; raw=read['after']
        for spec in t['spatial']['salient']['descriptors']:
            sc,sb,mode=spec['component_index'],spec['subband_index'],spec['mode']; start=(4*sc+sb)*n
            require(raw['modes'][sc][sb]==mode and raw['omitted_counts'][sc][sb]==(4 if mode<4 else 0),'mode/omission count differs')
            require([raw['quantized'][start+k] for k in spec['coded_coefficient_indices']]==spec['quantized'],'coded integers differ')
            if mode==3: require([bool(raw['signs'][start+k]) for k in spec['coded_coefficient_indices']]==spec['signs_positive'],'coded signs differ')
            if mode==4: require(raw['clusters'][sc][sb]==spec['cluster'],'transform index differs')
            if mode==5: require((raw['azimuth'][sc][sb],raw['elevation'][sc][sb])==(spec['azimuth_degrees'],spec['elevation_offset_degrees']),'direction differs')
            if mode<4: require(not any(history['history'][start:start+4]),'omitted ambient descriptors retained history')
            v=descriptor(spec,expected_history[sc][sb]); expected_history[sc][sb]=v; values.extend(map(float,v))
        merge_metrics(metrics['descriptors'],compare(values,history['history'],dict(sequence=sequence,role=role)))
        previous=history['history']; outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(outputs)==len(entry['transport'])==n,'incomplete transport/output map')
        for k,(out,source) in enumerate(zip(outputs,entry['transport'])):
            require(out['channel_index']==k,'non-ACN output')
            if not t['elements'][k]['present']: require(not any(source['scaled']),'absent transport not cleared')
            if k<4: require(float_bytes(out['input'])==float_bytes(source['scaled']),'ambient output is not an exact overwrite')
            else:
                wanted=[]
                for line in range(1024):
                    frequency=line%128 if t['common_window']==2 else line
                    band=next(b for b,end in enumerate(t['spatial']['salient']['lines_per_window']) if frequency<end)
                    wanted.append(sum(entry['transport'][4+sc]['scaled'][line]*history['history'][(4*sc+band)*n+k] for sc in range(5)))
                metric=compare(wanted,out['input'],dict(sequence=sequence,role=role,coefficient=k))
                merge_metrics(metrics['isolated_recovery'],metric)
                require(metric['passed'],'native mixed mapping differs with isolated descriptor/transport inputs')
        count+=1
    return dict(passed=True,core_frames=count,channels=n,capacity_bytes=capacity,parameters_boundaries_history_exact=True,
                ambient_overwrite_exact=True,float_diagnostics=metrics)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path)
    p.add_argument('--capture',type=Path,action='append',required=True); p.add_argument('--report',type=Path,required=True); a=p.parse_args()
    require(not a.report.exists() and (a.binary is None or a.binary.is_file()),'report exists or binary missing')
    controls={name:(opts,cases) for name,opts,cases in native_controls()}
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
           binary_sha256=sha256_file(a.binary) if a.binary else None,component_sha256=COMPONENT_SHA256,
           candidate_verified=a.binary is not None,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        require(len(a.capture)==2 and {x.name for x in a.capture}==set(controls),'expected the two mixed controls')
        for root in a.capture:
            options,cases=controls[root.name]; generated=[packet(case,**options) for case in cases]; truth=[t for _,t in generated]
            trace=json.loads((root/'native-hoa.json').read_text())
            require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'unqualified capture')
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(b).hexdigest() for b,_ in generated],'native packet identities differ')
            result=verify(truth,trace)
            if a.binary:
                from validate_hoa_mixed import check
                with workspace(r,root.name) as temporary:
                    summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--packets',len(cases),'--output',temporary/'parsed')
                    require(summary['hoa_packets_complete']==len(cases) and not summary['errors'],'candidate incomplete')
                    rows=[json.loads(s)['report'] for s in (temporary/'parsed').read_text().splitlines()]; last=0; previous=None
                    for row,t in zip(rows,truth): last,previous=check(row,t,last,previous,options['order'])
            result.update(capture=str(root),trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256'])
            r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and (a.binary is None or sha256_file(a.binary)==r['binary_sha256']),'source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    write_json(a.report,r); print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors']))); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
