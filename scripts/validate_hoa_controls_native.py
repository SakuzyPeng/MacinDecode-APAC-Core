#!/usr/bin/env python3
"""Check controlled native spatial flags, active layouts, history, and spectral diagnostics."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import hoa_controls_vectors as vectors
from hoa_salient_subbands_oracle import Decoder
from hoa_salient_subbands_vectors import control_values
from generate_hoa_dynamic_subbands_format import boundaries
from validate_hoa_mixed_native import frames
from validate_hoa_transports_native import native_layout
from validate_hoa_salient_subbands import check
from validate_channels import compare,merge_metrics,float_bytes
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from validate import require,write_json
from native_frame_trace import COMPONENT_SHA256

def metric():return dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)

def verify(truth,trace,options):
    oracle=Decoder(options)
    for t in truth:oracle.decode(t)
    n=16 if options.get('dynamic') else options.get('coefficient_count',(options['order']+1)**2)
    m=options.get('coefficient_count',(options['order']+1)**2);maximum=max(options['counts'],default=0);allocated=len(options['counts'])
    controls=control_values(options.get('controls'),options['path']);previous=[0.]*(allocated*maximum*m)
    metrics=dict(history=metric(),spectra=metric());diagnostics=[];total=0
    capacities=[e for e in trace['events'] if e['kind']=='capacity'];require(capacities and all(e['status']==0 and e['capacity_bytes']==n*2048 for e in capacities),'native capacity differs')
    for (sequence,role,t),expected in zip(frames(truth),oracle.records):
        opts=t.get('frame_options',options);counts=opts['counts'];dimensions=[opts.get('coefficient_count',(o+1)**2) for o in opts.get('component_orders',[opts['order']]*len(counts))]
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        def one(kind):
            found=[e for e in events if e['kind']==kind];require(len(found)==1,'missing native '+kind);return found[0]
        read=one('hoa_spatial');entry=one('hoa_deserialize');history=one('hoa_history')['after'];raw=read['after']
        require(read['status']==entry['status']==0,'native read failure')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'spatial bounds differ')
        require(entry['end']['relative_bit_offset']==t['core_end_bit_offset'],'core endpoint differs')
        require((history['salient'],history['ambient'])==(len(counts),opts['ambient_count']),'active counts differ')
        require(history['coefficient_counts']==dimensions and history['subbands']==counts,'active descriptors differ')
        require(history['ambient_indices']==t['spatial']['ambient_indices'],'active ambient indices differ')
        require(history['parameter_0']==controls['parameter_0'],'native parameter_0 differs')
        require([bool(history['flags'][i]) for i in (0,1,2,3,5,6)]==[controls[k] for k in ('flag_a','flag_b','flag_c','flag_d','flag_e','flag_f')],'native control flags differ')
        require(history['subband_tables']==[boundaries(i,opts.get('spatial_method',0),controls['flag_f']) for i in range(1,maximum+1)],'native cached boundaries differ')
        require(float_bytes(read['before']['history'])==float_bytes(previous),'native history continuity differs')
        for d in t['spatial'].get('salient',{}).get('descriptors',[]):
            sc,b=d['component_index'],d['subband_index'];start=sum(counts[i]*dimensions[i] for i in range(sc))+b*dimensions[sc]
            indices=d.get('coded_coefficient_indices',list(range(len(d['quantized']))))
            require([raw['quantized'][start+i] for i in indices]==d['quantized'],'descriptor integers differ')
            require(raw['modes'][sc][b]==d['mode'],'descriptor mode differs')
            if d['mode']==5:require((raw['azimuth'][sc][b],raw['elevation'][sc][b])==(d['azimuth_degrees'],d['elevation_offset_degrees']),'direction indices differ')
            if d['mode']==3:require([bool(raw['signs'][start+i]) for i in indices]==d['signs_positive'],'descriptor signs differ')
        wanted=[]
        cached=expected.get('history')
        if cached is None:
            # A pure flag_d control has no optional flag dictionary, but the
            # ordinary compact descriptor cache still uses the declared shape.
            cached=[[[0.]*dimensions[s] for _ in range(count)] for s,count in enumerate(counts)]
            for d,v in zip(t['spatial']['salient']['descriptors'],expected['vectors']):cached[d['component_index']][d['subband_index']]=v
        for sc in range(allocated):
            for band in range(maximum):
                values=cached[sc][band] if sc<len(cached) and band<len(cached[sc]) else []
                wanted.extend(values+[0.]*(m-len(values)))
        measured=compare(history['history'],wanted,dict(sequence=sequence,role=role));merge_metrics(metrics['history'],measured);require(measured['passed'],'native history differs from independent descriptors')
        previous=history['history'];synth=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(synth)==n,'missing native outputs')
        output,diagnostic=native_layout(t,entry,expected['scaled'],synth,dict(opts,tce_types=[0]*n))
        if diagnostic:diagnostics.append(dict(sequence=sequence,role=role,**diagnostic))
        for k,(event,wanted) in enumerate(zip(synth,output)):
            measured=compare(event['input'],wanted,dict(sequence=sequence,role=role,coefficient=k));merge_metrics(metrics['spectra'],measured);require(measured['passed'],'native spectra exceed unchanged tolerance')
        total+=1
    require(total==len(oracle.records),'native frame set differs')
    return dict(passed=True,core_frames=total,capacity_bytes=n*2048,parameters_and_boundaries_exact=True,
                independent_float_diagnostics=metrics,native_layout_diagnostics=diagnostics)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--captures',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    require(not a.report.exists(),'report exists');r=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        paths=json.loads(a.captures.read_text());controls={name:(opts,cases) for name,opts,cases in vectors.native_controls()};require(set(paths)==set(controls),'missing native control')
        for name,(opts,cases) in controls.items():
            root=a.captures.parent/paths[name];trace=json.loads((root/'native-hoa.json').read_text());require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'native capture incomplete')
            generated=[vectors.packet(c,**opts) for c in cases];require([e['packet_sha256'] for e in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,_ in generated],'native packet identities differ')
            result=verify([t for _,t in generated],trace,opts)
            with workspace(r,name) as tmp:
                summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--output',tmp/'parsed');require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'candidate incomplete')
                last=0;previous=None
                for line,(_,truth) in zip((tmp/'parsed').read_text().splitlines(),generated):last,previous=check(json.loads(line)['report'],truth,last,previous,opts)
            r['captures'].append(dict(name=name,trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256'],**result))
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors'])));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
