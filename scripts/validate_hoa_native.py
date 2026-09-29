#!/usr/bin/env python3
"""Reuse bounded native captures; verify only the new HOA grammar and restore rule."""
import argparse,json,hashlib,subprocess
from pathlib import Path
from datetime import datetime,timezone
from validate_channels_native import native_check
from validate_channels import compare,merge_metrics
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from native_frame_trace import COMPONENT_SHA256
from validate import require,write_json

def check(rows,trace):
    result=native_check(rows,trace);expected={}
    def visit(r,sequence,role):
        if r['embedded_preroll']:visit(r['embedded_preroll']['report'],sequence,'preroll')
        expected[sequence,role]=r
    for i,r in enumerate(rows):visit(r,i,'current')
    metrics=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
    for key,r in expected.items():
        events=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==key]
        entries=[e for e in events if e['kind']=='hoa_deserialize'];spatial=[e for e in events if e['kind']=='hoa_spatial']
        require(len(entries)==len(spatial)==1,'missing HOA stage');entry=entries[0];data=spatial[0];h=r['hoa']
        require(entry['status']==data['status']==0,'native HOA failed')
        require(entry['window']==h['common_window'],'common window differs')
        require(entry['start']['relative_bit_offset']==r['derived']['core_frame_start_bit'],'HOA common header start differs')
        require(entry['end']['relative_bit_offset']==r['component_end_bit_offset'],'HOA deserialize end differs')
        require((data['start']['relative_bit_offset'],data['end']['relative_bit_offset'])==(h['spatial']['start_bit_offset'],h['spatial']['end_bit_offset']),'spatial payload boundary differs')
        after=data['after'];require((after['salient'],after['ambient'],after['coefficients'],after['output_channels'],after['transform_count'])==(0,16,16,16,0),'native spatial configuration changed')
        require(after['ambient_indices']==h['spatial']['ambient_indices'] and after['coding_mode']==h['spatial']['effective_global_coding_mode'],'spatial parameters differ')
        outputs=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==key]
        require(len(outputs)==len(entry['transport'])==16,'missing HOA coefficients')
        for ch,(a,b) in enumerate(zip(outputs,entry['transport'])):
            require(a['input']==b['scaled'],'native ambient restoration is not identity')
            merge_metrics(metrics,compare(h['channels_after_hoa'][ch]['scaled'],a['input'],dict(sequence=key[0],role=key[1],channel=ch,stage='hoa')))
    result.update(hoa_numeric_metrics=metrics,hoa_frames=len(expected),identity_restore_exact=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--capture',type=Path,action='append',required=True);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing or report exists')
    r=dict(passed=False,qualification='native_parameters_boundaries_and_identity_restore',floating_point_comparison='diagnostic_only',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,created_at=datetime.now(timezone.utc).isoformat(),captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for source in a.capture:
            trace_file=source/'native-hoa.json';trace=json.loads(trace_file.read_text());require(trace['component_sha256']==COMPONENT_SHA256 and trace['process_exit_code']==0 and not trace['errors'] and not trace['pending_returns'],'unqualified native capture')
            with workspace(r,source.name) as root:
                manifest=json.loads((source/'packets/manifest.json').read_text());count=manifest['actual_packets'];summary=command(a.binary,'parse-packets',source/'packets','--depth','hoa','--packets',count,'--output',root/'parsed')
                require(summary['hoa_packets_complete']==count and summary['errors']==0,'incomplete candidate parse')
                rows=[json.loads(l)['report'] for l in (root/'parsed').read_text().splitlines()]
                result=check(rows,trace);result.update(capture=source.name,trace_sha256=sha256_file(trace_file),native_trace_script_sha256=trace['trace_script_sha256']);r['captures'].append(result)
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],captures=len(r['captures']),errors=r['errors'])));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
