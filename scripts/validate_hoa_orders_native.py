#!/usr/bin/env python3
"""Verify new HOA order/rate boundaries from existing, bounded native captures."""
import argparse,json,subprocess
from pathlib import Path
from validate import require,write_json
from validate_channels import compare,merge_metrics
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from native_frame_trace import COMPONENT_SHA256


def check(rows,trace):
    n=trace['channels'];capacity={4:8192,9:18432,16:32768}[n]
    caps=[e for e in trace['events'] if e['kind']=='capacity'];require(caps and all(e['status']==0 and e['capacity_bytes']==capacity for e in caps),'capacity differs')
    frames=[]
    def visit(r,sequence,role):
        if r['embedded_preroll']:visit(r['embedded_preroll']['report'],sequence,'preroll')
        frames.append((r,sequence,role))
    for i,row in enumerate(rows):visit(row,i,'current')
    metrics={k:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None) for k in ('transport','descriptors','coefficients')}
    previous=None;previous_sha=None;identity=True
    for r,sequence,role in frames:
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
        reads=[e for e in events if e['kind']=='hoa_spatial'];entries=[e for e in events if e['kind']=='hoa_deserialize'];histories=[e for e in events if e['kind']=='hoa_history']
        require(len(reads)==len(entries)==len(histories)==1,'missing HOA boundary')
        read,entry,history=reads[0],entries[0],histories[0]['after'];h=r['hoa'];side=h['spatial'];salient=side.get('salient')
        require(r['packet_complete'] and read['status']==entry['status']==0,'incomplete frame')
        require((read['start']['relative_bit_offset'],read['end']['relative_bit_offset'])==(side['start_bit_offset'],side['end_bit_offset']),'spatial boundaries differ')
        require(entry['window']==h['common_window'] and entry['start']['relative_bit_offset']==r['derived']['core_frame_start_bit'] and entry['end']['relative_bit_offset']==r['component_end_bit_offset'],'common window/core boundary differs')
        require((history['output_channels'],history['coefficients'],history['salient'],history['ambient'],history['transform_count'])==(n,n,5 if salient else 0,0 if salient else n,0),'unqualified spatial dimensions/transform')
        require(history['ambient_indices']==side['ambient_indices'],'ambient map differs')
        outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
        require(len(outputs)==len(entry['transport'])==len(h['channels_after_hoa'])==n,'incomplete coefficient map')
        for k,(native,transport,element,out) in enumerate(zip(outputs,entry['transport'],r['elements'],h['channels_after_hoa'])):
            require(out['acn_index']==native['channel_index']==k,'coefficient order differs')
            if element['present']:merge_metrics(metrics['transport'],compare(element['channels_after_bwe2'][0]['scaled'],transport['scaled'],dict(sequence=sequence,role=role,channel=k)))
            else:require(not any(transport['scaled']),'absent transport not cleared')
            if not salient:require(native['input']==transport['scaled'],'ambient restoration is not identity')
            merge_metrics(metrics['coefficients'],compare(out['scaled'],native['input'],dict(sequence=sequence,role=role,coefficient=k)))
        if salient:
            identity=False
            require(history['subbands']==[4]*5 and history['coefficient_counts']==[n]*5 and history['quantization_bits']==6,'salient shape differs')
            require(history['four_subband_ends']==salient['subband_ends'],'subbands differ')
            require(read['before']['history']==(previous if previous is not None else [0.]*(20*n)),'native history order differs')
            require(salient['history_frame_sha256']==previous_sha,'candidate history source differs')
            values=[]
            for d in salient['descriptors']:
                sc,sb=d['component_index'],d['subband_index'];start=(4*sc+sb)*n;raw=read['after']
                require(d['mode']==raw['modes'][sc][sb] and d['quantized']==raw['quantized'][start:start+len(d['quantized'])],'descriptor integers differ')
                if d['mode']==3:require(d['signs_positive']==[bool(v) for v in raw['signs'][start:start+n]],'signs differ')
                if d['cluster'] is not None:require(d['cluster']==raw['clusters'][sc][sb],'cluster differs')
                if d['azimuth_degrees'] is not None:require(d['azimuth_degrees']==raw['azimuth'][sc][sb] and d['elevation_offset_degrees']==raw['elevation'][sc][sb],'direction differs')
                values.extend(d['restored'])
            merge_metrics(metrics['descriptors'],compare(values,history['history'],dict(sequence=sequence,role=role)))
            previous=history['history'];previous_sha=r['packet_sha256']
    return dict(passed=True,core_frames=len(frames),channels=n,capacity_bytes=capacity,parameters_boundaries_history_exact=True,ambient_identity_exact=identity,float_diagnostics=metrics)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--capture',type=Path,action='append',required=True);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing or report exists')
    r=dict(passed=False,qualification='native_parameters_boundaries_mapping_and_state',floating_point_comparison='diagnostic_only',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for source in a.capture:
            path=source/'native-hoa.json';trace=json.loads(path.read_text());require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'unqualified capture')
            directory=source/'packets'
            if not directory.is_dir():directory=source.parent/'packets'
            with workspace(r,source.parent.name+'-'+source.name) as root:
                count=len(trace['packets']);summary=command(a.binary,'parse-packets',directory,'--depth','hoa','--packets',count,'--output',root/'parsed')
                require(summary['hoa_packets_complete']==count and not summary['errors'],'incomplete candidate')
                rows=[json.loads(s)['report'] for s in (root/'parsed').read_text().splitlines()]
                require([r['packet_sha256'] for r in rows]==[p['packet_sha256'] for p in trace['packets']],'packet identities differ')
                result=check(rows,trace);result.update(capture=str(source),sample_rate_hz=summary['context']['transport']['sample_rate_hz'],trace_sha256=sha256_file(path),trace_script_sha256=trace['trace_script_sha256']);r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as error:r['errors'].append(str(error))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors'])));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
