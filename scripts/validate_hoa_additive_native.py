#!/usr/bin/env python3
"""Native additive HOA parameters, history, boundaries, capacities and isolated sum checks."""
import argparse,hashlib,json,subprocess,struct
from decimal import Decimal as D
from pathlib import Path
from hoa_additive_vectors import native_controls,packet,shape
from hoa_mixed_oracle import descriptor
from hoa_static_ambient_oracle import ambient_transform
from validate_hoa_mixed_native import frames
from validate_channels import compare,merge_metrics,float_bytes
from validate import require,write_json
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate_portable import source_digest
from native_frame_trace import COMPONENT_SHA256


def f32(v): return struct.unpack('<f',struct.pack('<f',v))[0]


def verify(truth,trace,options):
    n=shape(options['order'],options['dynamic']); slots=(options['order']+1)**2
    caps=[e for e in trace['events'] if e['kind']=='capacity']
    require(caps and all(e['status']==0 for e in caps),'native capacity request failed')
    capacities={e['capacity_bytes'] for e in caps}; require(len(capacities)==1,'capacity changed')
    previous=[0.]*(20*slots); expected_history=[[[D(0)]*slots for _ in range(4)] for _ in range(5)]; count=0
    metrics={k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('descriptors','isolated_addition')}
    exact=True
    for sequence,role,t in frames(truth):
        es=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        def one(kind):
            rows=[e for e in es if e['kind']==kind]; require(len(rows)==1,'missing '+kind); return rows[0]
        read,entry,history=one('hoa_spatial'),one('hoa_deserialize'),one('hoa_history')['after']
        require(read['status']==entry['status']==0,'native parse failed')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'spatial bit boundary differs')
        require(entry['window']==t['common_window'] and entry['end']['relative_bit_offset']==t['core_end_bit_offset'],'core boundary/window differs')
        require((history['output_channels'],history['coefficients'],history['salient'],history['ambient'])==(n,slots,5,4),'dimensions differ')
        require(history['flags'][3]==1 and history['omitted_ambient_count']==0,'addition does not disable omissions')
        selected=t['additive']['selection']; index=t['additive']['effective_transform_index']
        require(history['ambient_indices']==selected and history['transform_count']==options['transform'] and history['transform_index']==index,'ambient transform/selection differs')
        require(history['coefficient_counts']==[slots]*5 and history['subbands']==[4]*5 and history['quantization_bits']==6,'descriptor dimensions differ')
        require(history['four_subband_ends']==t['spatial']['salient']['subband_ends'],'descriptor bands differ')
        require(float_bytes(read['before']['history'])==float_bytes(previous),'history transition differs')
        values=[]; raw=read['after']
        for spec in t['spatial']['salient']['descriptors']:
            sc,sb,mode=spec['component_index'],spec['subband_index'],spec['mode']; start=(4*sc+sb)*slots; indices=spec['coded_coefficient_indices']
            require(raw['modes'][sc][sb]==mode and raw['omitted_counts'][sc][sb]==0,'mode/omission differs')
            require([raw['quantized'][start+i] for i in indices]==spec['quantized'],'coded values differ')
            if mode==3: require([bool(raw['signs'][start+i]) for i in indices]==spec['signs_positive'],'signs differ')
            if mode==4: require(raw['clusters'][sc][sb]==spec['cluster'],'cluster differs')
            if mode==5: require((raw['azimuth'][sc][sb],raw['elevation'][sc][sb])==(spec['azimuth_degrees'],spec['elevation_offset_degrees']),'direction differs')
            v=descriptor(spec,expected_history[sc][sb]); expected_history[sc][sb]=v; values.extend(map(float,v))
        m=compare(values,history['history'],dict(sequence=sequence,role=role)); merge_metrics(metrics['descriptors'],m); require(m['passed'],'native descriptor difference exceeds tolerance')
        previous=history['history']; sources=[c['scaled'] for c in entry['transport']]; require(len(sources)==n,'missing carriers')
        transformed=ambient_transform(sources[:4],index); internal=[[0.]*1024 for _ in range(slots)]
        for line in range(1024):
            frequency=line%128 if t['common_window']==2 else line
            band=next(b for b,end in enumerate(t['spatial']['salient']['lines_per_window']) if frequency<end)
            for slot in range(slots):
                salient=f32(sum(sources[4+sc][line]*history['history'][(4*sc+band)*slots+slot] for sc in range(5)))
                internal[slot][line]=f32(salient+transformed[selected.index(slot)][line]) if slot in selected else salient
        dyn=t['dynamic_selection']; expected=internal
        if dyn:
            require(history['flags'][12] and history['dynamic_subbands']==8 and history['dynamic_ends']==dyn['subband_ends'],'dynamic bands differ')
            require(history['dynamic_maps']==[row['target_acn_indices'] for row in dyn['mappings']],'wire maps differ')
            expected=[[0.]*1024 for _ in range(n)]
            for line in range(1024):
                frequency=line%128 if t['common_window']==2 else line
                band=next(b for b,end in enumerate(dyn['lines_per_window']) if frequency<end)
                for slot,acn in enumerate(dyn['mappings'][band]['target_acn_indices']): expected[acn][line]=internal[slot][line]
        outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(outputs)==n,'missing final coefficients')
        for acn,out in enumerate(outputs):
            require(out['channel_index']==acn,'wrong output order')
            m=compare(out['input'],expected[acn],dict(sequence=sequence,role=role,acn=acn)); merge_metrics(metrics['isolated_addition'],m)
            require(m['passed'],f'isolated additive recovery differs at {sequence}/{role}/ACN{acn}')
            exact=exact and float_bytes(out['input'])==float_bytes(expected[acn])
        count+=1
    return dict(passed=True,core_frames=count,capacity_bytes=capacities.pop(),parameters_boundaries_mapping_history_exact=True,isolated_bytes_exact=exact,
                internal_reference='reconstructed from native descriptor history and transport basis inputs',metrics=metrics)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--binary',type=Path); p.add_argument('--capture',type=Path,action='append',required=True); p.add_argument('--report',type=Path,required=True); a=p.parse_args()
    require(not a.report.exists() and (a.binary is None or a.binary.is_file()),'report exists or binary missing')
    controls={name:(opts,cases) for name,opts,cases in native_controls()}
    r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary) if a.binary else None,
           component_sha256=COMPONENT_SHA256,candidate_verified=a.binary is not None,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        require(len(a.capture)==len(controls) and {p.name for p in a.capture}==set(controls),'missing native branch')
        for root in a.capture:
            options,cases=controls[root.name]; generated=[packet(c,**options) for c in cases]; truth=[t for _,t in generated]; trace=json.loads((root/'native-hoa.json').read_text())
            require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'unqualified trace')
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,_ in generated],'packet identities differ')
            result=verify(truth,trace,options)
            if a.binary:
                from validate_hoa_additive import check
                with workspace(r,root.name) as temporary:
                    summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--packets',len(cases),'--output',temporary/'parsed')
                    require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'candidate incomplete')
                    rows=[json.loads(s)['report'] for s in (temporary/'parsed').read_text().splitlines()]; last=0; previous=None
                    for row,t in zip(rows,truth): last,previous=check(row,t,last,previous,options)
            result.update(capture=str(root),trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256']); r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and (a.binary is None or sha256_file(a.binary)==r['binary_sha256']),'source/binary changed'); r['passed']=True
    except Exception as error: r['errors'].append(str(error))
    write_json(a.report,r); print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors']))); return 0 if r['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
