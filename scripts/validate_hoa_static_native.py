#!/usr/bin/env python3
"""Bounded native proof of static selection, transform direction and bit/state boundaries."""
import argparse,hashlib,json,subprocess
from pathlib import Path
from decimal import Decimal as D
from hoa_static_ambient_vectors import native_controls,packet
from hoa_static_ambient_oracle import descriptor,ambient_transform
from validate_hoa_mixed_native import frames
from validate_channels import compare,merge_metrics,float_bytes
from validate import require,write_json
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate_portable import source_digest
from native_frame_trace import COMPONENT_SHA256


def verify(truth,trace,options):
    n=trace['channels']; ambient=4 if options['mixed'] else n; s=5 if options['mixed'] else 0
    capacity={4:8192,9:18432,16:32768}[n]; caps=[e for e in trace['events'] if e['kind']=='capacity']
    require(caps and all(e['status']==0 and e['capacity_bytes']==capacity for e in caps),'capacity differs')
    previous=[0.]*(20*n) if s else []; expected_history=[[[D(0)]*n for _ in range(4)] for _ in range(5)]
    metrics={k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','isolated_recovery','native_float_stress')}; count=0
    for sequence,role,t in frames(truth):
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        def one(kind):
            found=[e for e in events if e['kind']==kind]; require(len(found)==1,'missing '+kind); return found[0]
        read,entry,history=one('hoa_spatial'),one('hoa_deserialize'),one('hoa_history')['after']; side=t['spatial']; transform=side['ambient']
        require(read['status']==entry['status']==0,'native parse failure')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(side['start_bit_offset'],side['end_bit_offset']),'spatial boundaries differ')
        require(entry['window']==t['common_window'] and entry['end']['relative_bit_offset']==t['core_end_bit_offset'],'core window/boundary differs')
        require((history['output_channels'],history['coefficients'],history['salient'],history['ambient'],history['transform_count'])==(n,n,s,ambient,options['transform']),'shape/config differs')
        require(history['ambient_indices']==transform['selection'] and not history['flags'][3],'selection or overwrite differs')
        if options['transform']: require(history['transform_index']==transform['effective_index'],'effective transform index differs')
        require(float_bytes(read['before']['history'])==float_bytes(previous),'history continuity differs')
        if s:
            values=[]; raw=read['after']
            require(history['subbands']==[4]*5 and history['coefficient_counts']==[n]*5 and history['quantization_bits']==6,'salient dimensions differ')
            for spec in side['salient']['descriptors']:
                sc,sb,mode=spec['component_index'],spec['subband_index'],spec['mode']; start=(4*sc+sb)*n
                require(raw['modes'][sc][sb]==mode and raw['omitted_counts'][sc][sb]==(4 if mode<4 else 0),'mode/omission differs')
                require([raw['quantized'][start+k] for k in spec['coded_coefficient_indices']]==spec['quantized'],'coded integers differ')
                if mode==3: require([bool(raw['signs'][start+k]) for k in spec['coded_coefficient_indices']]==spec['signs_positive'],'signs differ')
                if mode==4: require(raw['clusters'][sc][sb]==spec['cluster'],'cluster differs')
                if mode==5: require((raw['azimuth'][sc][sb],raw['elevation'][sc][sb])==(spec['azimuth_degrees'],spec['elevation_offset_degrees']),'direction differs')
                require(all(history['history'][start+k]==0 for k in spec['ambient_omitted_coefficients']),'selected history was not cleared')
                v=descriptor(spec,expected_history[sc][sb]); expected_history[sc][sb]=v; values.extend(map(float,v))
            merge_metrics(metrics['descriptors'],compare(values,history['history'],dict(sequence=sequence,role=role)))
        previous=history['history']; outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(outputs)==len(entry['transport'])==n,'output/transport map incomplete')
        selected=transform['selection']; transformed=ambient_transform([c['scaled'] for c in entry['transport'][:ambient]],transform['effective_index'])
        for k,out in enumerate(outputs):
            require(out['channel_index']==k,'ACN mapping differs')
            if not t['elements'][k]['present']: require(not any(entry['transport'][k]['scaled']),'absent transport was not cleared')
            if k in selected: wanted=transformed[selected.index(k)]
            else:
                wanted=[]
                for line in range(1024):
                    frequency=line%128 if t['common_window']==2 else line
                    band=next(b for b,end in enumerate(side['salient']['lines_per_window']) if frequency<end)
                    wanted.append(sum(entry['transport'][4+sc]['scaled'][line]*history['history'][(4*sc+band)*n+k] for sc in range(5)))
            metric=compare(out['input'],wanted,dict(sequence=sequence,role=role,coefficient=k))
            stress=t['native_numeric_stress']; merge_metrics(metrics['native_float_stress' if stress else 'isolated_recovery'],metric)
            if not stress: require(metric['passed'],'isolated native matrix/mapping differs')
        count+=1
    return dict(passed=True,core_frames=count,channels=n,capacity_bytes=capacity,parameters_boundaries_mapping_history_exact=True,float_diagnostics=metrics)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path); p.add_argument('--capture',type=Path,action='append',required=True); p.add_argument('--report',type=Path,required=True); a=p.parse_args()
    require(not a.report.exists() and (a.binary is None or a.binary.is_file()),'report exists or binary missing')
    controls={name:(opts,cases) for name,opts,cases in native_controls()}
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary) if a.binary else None,
           component_sha256=COMPONENT_SHA256,candidate_verified=a.binary is not None,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        require(len(a.capture)==len(controls) and {x.name for x in a.capture}==set(controls),'missing bounded native capture')
        for root in a.capture:
            options,cases=controls[root.name]; generated=[packet(case,**options) for case in cases]; truth=[t for _,t in generated]; trace=json.loads((root/'native-hoa.json').read_text())
            require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'unqualified capture')
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(b).hexdigest() for b,_ in generated],'input identity differs')
            result=verify(truth,trace,options)
            if a.binary:
                from validate_hoa_static_ambient import check
                with workspace(r,root.name) as temporary:
                    summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--packets',len(cases),'--output',temporary/'parsed')
                    require(summary['hoa_packets_complete']==len(cases) and not summary['errors'],'candidate incomplete')
                    rows=[json.loads(s)['report'] for s in (temporary/'parsed').read_text().splitlines()]; last=0; previous=None
                    for row,t in zip(rows,truth): last,previous=check(row,t,last,previous,options)
            result.update(capture=str(root),trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256']); r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and (a.binary is None or sha256_file(a.binary)==r['binary_sha256']),'source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    write_json(a.report,r); print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors']))); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
