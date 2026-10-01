#!/usr/bin/env python3
"""Verify compact spatial payloads, padded native history and per-component recovery."""
import argparse,hashlib,json,subprocess,struct
from decimal import Decimal as D
from pathlib import Path
from hoa_salient_subbands_vectors import native_controls,packet,shape
from hoa_dynamic_oracle import descriptor
from hoa_static_ambient_oracle import ambient_transform
from generate_hoa_dynamic_subbands_format import boundaries
from validate_hoa_mixed_native import frames
from validate_channels import compare,merge_metrics,float_bytes
from validate import require,write_json
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate_portable import source_digest
from native_frame_trace import COMPONENT_SHA256


def f32(v):return struct.unpack('<f',struct.pack('<f',v))[0]


def verify(truth,trace,opts,*,isolated_exact=True,layout_adapter=None):
    counts=opts['counts'];maximum=max(counts,default=0);m=(opts['order']+1)**2;n=shape(opts['order'],opts.get('dynamic',False));path=opts['path'];ambient=opts.get('ambient_count',0 if path=='salient' else 4)
    dimensions=[(o+1)**2 for o in opts.get('component_orders',[opts['order']]*len(counts))]
    caps=[e for e in trace['events'] if e['kind']=='capacity'];capacity=2048*n
    require(caps and all(c['status']==0 and c['capacity_bytes']==capacity for c in caps),'native capacity differs')
    expected=[[[D(0)]*dimensions[s] for _ in range(c)] for s,c in enumerate(counts)];previous=[0.]*(len(counts)*maximum*m);metrics=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None);total=0
    recovery_metrics=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None);recovery_exact=True;semantic_exact=True;layout_diagnostics=[]
    for seq,role,t in frames(truth):
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(seq,role)]
        def one(kind):
            values=[e for e in events if e['kind']==kind];require(len(values)==1,'missing '+kind);return values[0]
        read,entry,history=one('hoa_spatial'),one('hoa_deserialize'),one('hoa_history')['after'];raw=read['after']
        require(read['status']==entry['status']==0,'native parse failed')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'spatial endpoints differ')
        require(entry['window']==t['common_window'] and entry['end']['relative_bit_offset']==t['core_end_bit_offset'],'window/core endpoint differs')
        require(history['quantization_bits']==opts.get('quantization_bits',6),'quantization precision differs')
        require(history['subbands']==counts and history['coefficient_counts']==dimensions and history['maximum_subbands']==maximum,'component dimensions differ')
        require(history['subband_tables']==[boundaries(i,opts.get('spatial_method',0)) for i in range(1,maximum+1)],'cached perceptual grids differ')
        require((history['coefficients'],history['output_channels'],history['salient'],history['ambient'])==(m,n,len(counts),ambient),'dimensions differ')
        require(bool(history['flags'][3])==(path=='add'),'combination flag differs')
        require(len(history['history'])==len(counts)*maximum*m and float_bytes(read['before']['history'])==float_bytes(previous),'padded history transition differs')
        wanted=[];actual=[]
        for spec in t['spatial'].get('salient',{}).get('descriptors',[]):
            sc,b,mode=spec['component_index'],spec['subband_index'],spec['mode'];compact=sum(counts[i]*dimensions[i] for i in range(sc))+b*dimensions[sc];padded=(sc*maximum+b)*m
            indices=spec.get('coded_coefficient_indices',list(range(len(spec['quantized']))))
            require(raw['modes'][sc][b]==mode and raw['omitted_counts'][sc][b]==(ambient if path=='replace' and mode<4 else 0),'mode/omission differs')
            require([raw['quantized'][compact+i] for i in indices]==spec['quantized'],'compact payload offset differs')
            if mode==3:require([bool(raw['signs'][compact+i]) for i in indices]==spec['signs_positive'],'signs differ')
            if mode==4:require(raw['clusters'][sc][b]==spec['cluster'],'cluster differs')
            if mode==5:require((raw['azimuth'][sc][b],raw['elevation'][sc][b])==(spec['azimuth_degrees'],spec['elevation_offset_degrees']),'angles differ')
            v=descriptor(dict(spec,quantization_bits=opts.get('quantization_bits',6)),expected[sc][b]);expected[sc][b]=v;wanted.extend(map(float,v));actual.extend(history['history'][padded:padded+dimensions[sc]])
            require(float_bytes(history['history'][padded+dimensions[sc]:padded+m])==bytes(4*(m-dimensions[sc])),'nonzero higher-order native history padding')
        measurement=compare(actual,wanted,dict(sequence=seq,role=role));merge_metrics(metrics,measurement);require(measurement['passed'],'descriptor diagnostics exceed tolerance')
        previous=history['history'];sources=[c['scaled'] for c in entry['transport']];require(len(sources)==sum(2 if typ==1 else 0 if typ==6 else 1 for typ in opts.get('tce_types',[0]*n)),'missing carriers')
        selected=t['spatial']['ambient_indices'];require(history['ambient_indices']==selected,'ambient selection differs')
        static=t['dynamic_selection'].get('internal_ambient',{}) if t['dynamic_selection'] else t['spatial'].get('ambient',{})
        index=static.get('effective_index',3)
        if opts.get('transform',0):require(history['transform_index']==index and history['transform_count']==opts['transform'],'transform differs')
        transformed=ambient_transform(sources[:ambient],index) if ambient else [];internal=[[0.]*1024 for _ in range(m)];short=t['common_window']==2
        grids=[[v//8 if short else v for v in boundaries(c,opts.get('spatial_method',0))] for c in counts]
        for line in range(1024):
            frequency=line%128 if short else line;bands=[next(b for b,end in enumerate(g) if frequency<end) for g in grids]
            for k in range(m):
                # Native unequal-grid recovery accumulates band-major, then component.
                value=0.
                for sc in sorted(range(len(counts)),key=lambda sc:(bands[sc],sc)):
                    product=f32(sources[ambient+sc][line]*history['history'][(sc*maximum+bands[sc])*m+k]);value=f32(value+product)
                if k in selected:value=f32(value+transformed[selected.index(k)][line]) if path=='add' else transformed[selected.index(k)][line]
                internal[k][line]=value
        dyn=t['dynamic_selection'];output=internal
        if dyn:
            require(history['dynamic_subbands']==opts['subbands'] and history['dynamic_ends']==dyn['subband_ends'],'dynamic grid differs')
            require(history['dynamic_maps']==[r['target_acn_indices'] for r in dyn['mappings']],'dynamic mapping differs');output=[[0.]*1024 for _ in range(16)]
            for line in range(1024):
                frequency=line%128 if short else line;b=next(b for b,end in enumerate(dyn['lines_per_window']) if frequency<end)
                for slot,acn in enumerate(dyn['mappings'][b]['target_acn_indices']):output[acn][line]=internal[slot][line]
        synth=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(seq,role)];require(len(synth)==n,'missing outputs')
        for acn,e in enumerate(synth):semantic_exact &= float_bytes(e['input'])==float_bytes(output[acn])
        if layout_adapter is not None:
            output,diagnostics=layout_adapter(t,entry,output,synth,opts)
            if diagnostics:layout_diagnostics.append(dict(sequence=seq,role=role,**diagnostics))
        for acn,e in enumerate(synth):
            require(e['channel_index']==acn,'native synthesis output ordering differs')
            exact=float_bytes(e['input'])==float_bytes(output[acn]);recovery_exact &= exact
            if isolated_exact:require(exact,f'isolated recovery differs at {seq}/{role}/ACN{acn}')
            else:
                measurement=compare(e['input'],output[acn],dict(sequence=seq,role=role,acn=acn))
                merge_metrics(recovery_metrics,measurement)
                require(measurement['passed'],f'isolated native diagnostic exceeds unchanged tolerance at {seq}/{role}/ACN{acn}')
        total+=1
    result=dict(passed=True,core_frames=total,counts=counts,capacity_bytes=capacity,cached_grid_count=maximum,compact_parameter_stride='sum of preceding component counts times coefficients',native_history_stride=maximum*m,logical_history_values=sum(n*c for n,c in zip(counts,dimensions)),parameters_boundaries_history_exact=True,isolated_recovery_bytes_exact=semantic_exact,internal_reference='reconstructed from native descriptor history and known transport inputs',descriptor_float_diagnostics=metrics)
    if not isolated_exact:result['isolated_recovery_float_diagnostics']=recovery_metrics
    if layout_adapter is not None:
        result['native_layout_diagnostics']=layout_diagnostics
        result['native_layout_reconstruction_bytes_exact']=recovery_exact
    return result


def main(*,vectors=None):
    if vectors is None:
        import hoa_salient_subbands_vectors as vectors
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path);p.add_argument('--capture',type=Path,action='append',required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();require(not a.report.exists() and (a.binary is None or a.binary.is_file()),'report exists/binary missing')
    controls={name:(opts,cases) for name,opts,cases in vectors.native_controls()};r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary) if a.binary else None,component_sha256=COMPONENT_SHA256,candidate_verified=a.binary is not None,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        require(len(a.capture)==len(controls) and {p.name for p in a.capture}==set(controls),'missing native branch')
        for root in a.capture:
            opts,cases=controls[root.name];generated=[packet(c,**opts) for c in cases];trace=json.loads((root/'native-hoa.json').read_text());require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'incomplete trace')
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,_ in generated],'packet identities differ');result=verify([t for _,t in generated],trace,opts)
            if a.binary:
                from validate_hoa_salient_subbands import check
                with workspace(r,root.name) as tmp:
                    summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--output',tmp/'parsed');require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'candidate incomplete')
                    rows=[json.loads(line)['report'] for line in (tmp/'parsed').read_text().splitlines()];last=0;previous=None
                    for row,(_,truth) in zip(rows,generated):last,previous=check(row,truth,last,previous,opts)
            result.update(capture=str(root),trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256']);r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and (a.binary is None or sha256_file(a.binary)==r['binary_sha256']),'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors'])));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
