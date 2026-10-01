#!/usr/bin/env python3
"""Verify captured transport maps and boundaries, then reuse spatial history checks."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import hoa_transport_vectors as vectors
from native_frame_trace import COMPONENT_SHA256
from validate_hoa_mixed_native import frames
from validate_hoa_salient_subbands_native import verify
from validate_hoa_salient_subbands import check
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file
from validate import require,write_json


def native_layout(truth,entry,output,synthesis,opts):
    """Model the observed decoder's layout bugs solely for native diagnostics.

    The wire's common window defines the independent semantic reference. The
    bound decoder's inverse permutation instead walks transport ICS objects,
    and its synthesis window is taken from the first element (also extensions).
    Neither behavior is used in the production decoder or Decimal oracle.
    """
    common=truth['common_window'];transport=entry['transport']
    cursor=0
    for element in truth['elements']:
        width=len(element['configuration'].get('transport_channels',element['configuration']['output_channels']))
        for local,actual in enumerate(transport[cursor:cursor+width]):
            if element['present']:
                wanted=element['channels'][local]['ics'];ics=actual['ics']
                require(ics['block_type']==wanted['block_type'] and ics['max_sfb']==wanted['max_sfb']
                        and ics['active_window_groups']==wanted['window_groups'],'native transport ICS differs')
            else:
                require(actual['ics']['max_sfb']==0 and actual['ics']['block_type'] in range(4)
                        and not any(actual['scaled']),'native absent carrier retained spectrum')
        cursor+=width
    require(cursor==len(transport),'native carrier snapshot shape differs')
    short_outputs=[i for i,c in enumerate(transport) if c['ics']['block_type']==2]
    native_window=0 if opts['tce_types'][0]==6 else transport[0]['ics']['block_type']
    require(all(e['block']==native_window for e in synthesis),'unexplained native synthesis window')
    # P converts eight 128-line windows to frequency-major order.
    result=[]
    for k,spectrum in enumerate(output):
        v=[spectrum[w*128+f] for f in range(128) for w in range(8)] if common==2 else list(spectrum)
        if k in short_outputs:v=[v[f*8+w] for w in range(8) for f in range(128)]
        result.append(v)
    missed=[k for k in range(len(output)) if common==2 and k not in short_outputs]
    extra=[k for k in short_outputs if common!=2]
    diagnostics=None
    if missed or extra or native_window!=common:
        diagnostics=dict(rule='apac-bound-native-hoa-window-defects-v1',common_window=common,
            native_synthesis_window=native_window,output_count=len(output),transport_count=len(transport),
            missing_inverse_coefficients=missed,unexpected_inverse_coefficients=extra,
            semantic_reference='all output coefficients follow the common HOA window')
    return result,diagnostics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--captures',type=Path,required=True,help='JSON mapping control names to paths relative to this manifest')
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();require(not a.report.exists(),'report exists')
    report=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,
        captures=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        paths=json.loads(a.captures.read_text());controls={name:(opts,cases) for name,opts,cases in vectors.native_controls()}
        require(set(paths)==set(controls),'missing native transport control')
        for name,(opts,cases) in controls.items():
            root=a.captures.parent/paths[name];trace=json.loads((root/'native-hoa.json').read_text())
            require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==0,'incomplete capture')
            generated=[vectors.packet(c,**opts) for c in cases]
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,_ in generated],'capture input differs')
            truth=[t for _,t in generated];nodes={(seq,role):t for seq,role,t in frames(truth)}
            for event in trace['events']:
                if event['kind']=='core':
                    expected=[];first=0
                    for i,typ in enumerate(opts['tce_types']):
                        width=vectors.width(typ);expected.append(dict(element_index=i,element_type=typ,channels=list(range(first,first+width))));first+=width
                    require(event['elements']==expected and event['transport_channels']==first,'native carrier map differs')
                if event['kind'] not in ('element','bwe2'):continue
                t=nodes[(event['sequence'],event['role'])]['elements'][event['element_index']]
                begin,end=event['start']['relative_bit_offset'],event['end']['relative_bit_offset']
                require(event['status']==0,'element/tool failed')
                if event['kind']=='element':require((begin,end)==(t['start_bit_offset']+1,t['end_bit_offset']),'element endpoint differs')
                elif t['bwe2'] is None:require(begin==end,'LFE consumed BWE2 bits')
                else:require((begin,end)==(t['bwe2']['start_bit_offset'],t['bwe2']['end_bit_offset']),'BWE2 endpoint differs')
            result=verify(truth,trace,opts,isolated_exact=False,layout_adapter=native_layout)
            with workspace(report,name) as tmp:
                summary=command(a.binary,'parse-packets',root/'packets','--depth','hoa','--output',tmp/'parsed')
                require(not summary['errors'] and summary['hoa_packets_complete']==len(cases),'candidate incomplete')
                rows=[json.loads(line)['report'] for line in (tmp/'parsed').read_text().splitlines()];last=0;previous=None
                for row,(_,t) in zip(rows,generated):last,previous=check(row,t,last,previous,opts)
            result.update(kind=name,capture=str(root),trace_sha256=sha256_file(root/'native-hoa.json'),trace_script_sha256=trace['trace_script_sha256'],trace_dependencies=trace['trace_dependencies'])
            report['captures'].append(result)
        require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source or binary changed')
        report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    write_json(a.report,report)
    print(json.dumps(dict(passed=report['passed'],captures=len(report['captures']),errors=report['errors'])))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
