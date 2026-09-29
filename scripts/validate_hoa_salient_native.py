#!/usr/bin/env python3
"""Reuse bounded salient captures: exact parameters/state boundaries, float diagnostics."""
import argparse,json,subprocess
from pathlib import Path
from validate import require,write_json
from validate_channels import compare,merge_metrics
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from native_frame_trace import COMPONENT_SHA256


def check(rows,trace):
    capacities=[e for e in trace['events'] if e['kind']=='capacity']
    require(capacities and all(e['status']==0 and e['capacity_bytes']==32768 for e in capacities),'unverified salient capacity')
    metrics={k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','coefficients')}
    frames=[]
    def visit(r,sequence,role):
        if r['embedded_preroll']:visit(r['embedded_preroll']['report'],sequence,'preroll')
        frames.append((r,sequence,role))
    for i,r in enumerate(rows):visit(r,i,'current')
    previous=[0.]*320;previous_sha=None
    for r,sequence,role in frames:
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        spatial=[e for e in events if e['kind']=='hoa_spatial'];entries=[e for e in events if e['kind']=='hoa_deserialize'];history=[e for e in events if e['kind']=='hoa_history']
        require(len(spatial)==len(entries)==len(history)==1,'missing salient boundary or state')
        s=spatial[0];entry=entries[0];after=history[0]['after'];h=r['hoa'];data=h['spatial'];side=data['salient']
        require(entry['status']==s['status']==0 and r['packet_complete'],'incomplete native/candidate frame')
        require(entry['window']==h['common_window'],'common window differs')
        require(entry['start']['relative_bit_offset']==r['derived']['core_frame_start_bit'] and entry['end']['relative_bit_offset']==r['component_end_bit_offset'],'core boundaries differ')
        require((s['start']['relative_bit_offset'],s['end']['relative_bit_offset'])==(data['start_bit_offset'],data['end_bit_offset']),'spatial bit range differs')
        require(s['before']['history']==previous,'native history did not advance in frame order')
        require(side['history_frame_sha256']==previous_sha,'candidate history source differs')
        require((after['salient'],after['ambient'],after['coefficients'],after['quantization_bits'],after['subbands'],after['coefficient_counts'])==(5,0,16,6,[4]*5,[16]*5),'unqualified spatial configuration')
        require(after['four_subband_ends']==side['subband_ends'],'subband endpoints differ')
        vectors=[]
        for d in side['descriptors']:
            sc,sb=d['component_index'],d['subband_index'];start=(sc*4+sb)*16
            require(d['mode']==s['after']['modes'][sc][sb],'mode differs')
            require(d['quantized']==s['after']['quantized'][start:start+len(d['quantized'])],'quantized descriptor differs')
            if d['mode']==3:require(d['signs_positive']==[bool(v) for v in s['after']['signs'][start:start+16]],'descriptor sign differs')
            if d['cluster'] is not None:require(d['cluster']==s['after']['clusters'][sc][sb],'transform cluster differs')
            if d['azimuth_degrees'] is not None:
                require(d['azimuth_degrees']==s['after']['azimuth'][sc][sb] and d['elevation_offset_degrees']==s['after']['elevation'][sc][sb],'direction indices differ')
            vectors.extend(d['restored'])
        merge_metrics(metrics['descriptors'],compare(vectors,after['history'],dict(sequence=sequence,role=role,stage='descriptors')))
        for element,native in zip(r['elements'],entry['transport']):
            if element['present']:merge_metrics(metrics['transport'],compare(element['channels_after_bwe2'][0]['scaled'],native['scaled'],dict(sequence=sequence,role=role,channel=native['channel_index'],stage='transport')))
            else:require(not any(native['scaled']),'absent transport was not cleared')
        outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(outputs)==len(h['channels_after_hoa'])==16,'incomplete output coefficient mapping')
        for k,(candidate,native) in enumerate(zip(h['channels_after_hoa'],outputs)):
            require(candidate['acn_index']==native['channel_index']==k,'output coefficient order differs')
            merge_metrics(metrics['coefficients'],compare(candidate['scaled'],native['input'],dict(sequence=sequence,role=role,coefficient=k,stage='coefficients')))
        previous=after['history'];previous_sha=r['packet_sha256']
    return dict(passed=True,core_frames=len(frames),capacity_bytes=32768,parameters_boundaries_history_exact=True,float_diagnostics=metrics)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--capture',type=Path,action='append',required=True);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or report exists')
    r=dict(passed=False,qualification='native_parameters_boundaries_mapping_and_state',floating_point_comparison='diagnostic_only',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for source in a.capture:
            path=source/'native-hoa.json';trace=json.loads(path.read_text());require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'unqualified capture')
            directory=source/'packets'
            if not directory.is_dir():directory=source.parent/'packets'
            with workspace(r,source.parent.name+'-'+source.name) as root:
                count=len(trace['packets']);summary=command(a.binary,'parse-packets',directory,'--depth','hoa','--packets',count,'--output',root/'parsed')
                require(summary['hoa_packets_complete']==count and not summary['errors'],'incomplete parse')
                rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]
                require([row['packet_sha256'] for row in rows]==[e['packet_sha256'] for e in trace['packets']],'capture packet identity differs')
                result=check(rows,trace);result.update(capture=str(source),trace_sha256=sha256_file(path),trace_script_sha256=trace['trace_script_sha256']);r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors'])));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
