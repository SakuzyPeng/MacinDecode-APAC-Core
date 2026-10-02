#!/usr/bin/env python3
"""Native remapping snapshots and separate bound-reference history/window diagnostics."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import hoa_remapping_vectors as vectors
from hoa_source_layout_oracle import Decoder as SourceDecoder
from native_hoa_history_model import NativeHistoryDecoder,RULE as HISTORY_RULE
from validate_hoa_mixed_native import frames
from validate_hoa_transports_native import native_layout
from native_frame_trace import COMPONENT_SHA256
from validate import require,write_json
from validate_channels import compare,merge_metrics,float_bytes
from validate_portable import source_digest
from validate_replay import sha256_file

NAMES={'width-1','width-5','width-17','width-33','width-65','width-121','full-core-121','duplicate-root','duplicate-star','mixed-add','ambient-transform','fixed-prefix-growing-core','source-matrix-transports','dynamic-n3d'}


class DiagnosticDecoder(NativeHistoryDecoder):
    source_layout=SourceDecoder.source_layout
    def __init__(self,opts):
        super().__init__(opts);self.mapping=vectors.effective(opts['remapping'])
    def core_sources(self,sources,truth,opts):
        return [sources[i] for i in self.mapping]+sources[len(self.mapping):]


def verify(root,report):
    found=set()
    for name,opts,cases in vectors.sequences():
        if name not in NAMES:continue
        found.add(name);capture=root/(name+'-v2' if name=='source-matrix-transports' else name);trace=json.loads((capture/'native-hoa.json').read_text())
        require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and trace['pending_returns']==0 and trace['process_exit_code']==0,'incomplete native capture: '+name)
        require((capture/'packets/cookie.bin').read_bytes()==vectors.cookie(**opts),'native cookie differs')
        generated=[vectors.packet(c,**opts) for c in cases]
        require([r['packet_sha256'] for r in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,t in generated],'native packets differ')
        oracle=DiagnosticDecoder(opts)
        for raw,t in generated:oracle.decode(t)
        total=0
        for (sequence,role,t),expected in zip(frames([t for raw,t in generated]),oracle.records):
            mapping=[e for e in trace['remapping'] if (e['sequence'],e['role'])==(sequence,role)]
            require(len(mapping)==1,'native mapping event missing');mapping=mapping[0]
            require(mapping['status']==0 and mapping['wire_indices']==opts['remapping'],'native remapping prefix differs')
            order=vectors.effective(opts['remapping'])+list(range(len(opts['remapping']),len(mapping['before'])))
            require(float_bytes(mapping['after'])==float_bytes([mapping['before'][i] for i in order]),'native carrier permutation differs')
            own=[e for e in trace['hoa_events'] if (e['sequence'],e['role'])==(sequence,role)]
            reads=[e for e in own if e['kind']=='hoa_spatial'];entries=[e for e in own if e['kind']=='hoa_deserialize']
            require(len(reads)==len(entries)==1 and reads[0]['status']==entries[0]['status']==0,'missing native spatial read')
            require((reads[0]['start']['relative_bit_offset'],reads[0]['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'native spatial boundaries differ')
            require(entries[0]['end']['relative_bit_offset']==t['core_end_bit_offset'],'native core boundary differs')
            synth=[e for e in trace['events'] if e['kind']=='synthesis' and (e['sequence'],e['role'])==(sequence,role)]
            require(len(synth)==opts['output_coefficients'],'missing native outputs')
            wanted,diagnostic=native_layout(t,entries[0],expected['scaled'],synth,opts)
            where=dict(name=name,sequence=sequence,role=role)
            if diagnostic:report['diagnostics'].append(dict(where,**diagnostic))
            result=compare([v for e in synth for v in e['input']],[v for c in wanted for v in c],where)
            require(result['passed'],'unexplained native spectrum difference: '+str(result['first_failure']));merge_metrics(report['metrics'],result);total+=1
        require(total==len(oracle.records) and len(trace['remapping'])==total,'native frame set differs')
        report['captures'].append(dict(name=name,core_frames=total,trace_sha256=sha256_file(capture/'native-hoa.json'),history_rule=HISTORY_RULE if opts.get('controls',{}).get('flag_b') else None))
    require(found==NAMES,'missing native controls')


def verify_boundaries(root,report):
    for name in ('identity','cycle','swap','duplicate-root','duplicate-star','tail-max','tail-zero','zero-width','bad-core-index','nonterminating'):
        path=root/name;truth=json.loads((path/'truth.json').read_text())
        if name=='nonterminating':
            require(truth['nonterminating'] and not (path/'native-hoa.json').exists(),'nonterminating native input was executed')
            try:vectors.effective(truth['mapping'])
            except ValueError as error:require(str(error)=='nonterminating graph','wrong graph rejection')
            else:raise RuntimeError('nonterminating graph accepted')
            report['boundaries'].append(dict(name=name,evidence='pseudocode and independent finite-state cycle proof',native_executed=False));continue
        trace=json.loads((path/'native-hoa.json').read_text())
        require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and trace['pending_returns']==0,'boundary capture incomplete')
        if name=='bad-core-index':
            require(trace['process_exit_code']==1 and not trace['remapping'],'out-of-core index reached remapping')
            require('Invalid mRemappingArray bitstream' in (path/'debugger.log').read_text(),'unexplained index rejection')
        else:
            require(trace['process_exit_code']==0 and len(trace['remapping'])==1,'boundary input rejected')
            event=trace['remapping'][0];mapping=vectors.effective(truth['mapping'])
            require(event['wire_indices']==truth['mapping'],'tail entered the active mapping')
            expected=[event['before'][i] for i in mapping]+event['before'][truth['k']:]
            require(float_bytes(expected)==float_bytes(event['after']),'boundary permutation differs')
        report['boundaries'].append(dict(name=name,trace_sha256=sha256_file(path/'native-hoa.json'),native_executed=True,accepted=name!='bad-core-index'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--captures',type=Path,required=True);p.add_argument('--boundaries',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();require(not a.report.exists(),'report exists')
    report=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),component_sha256=COMPONENT_SHA256,format_sha256=vectors.FORMAT_SHA256,captures=[],boundaries=[],diagnostics=[],errors=[],metrics=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None))
    try:verify(a.captures,report);verify_boundaries(a.boundaries,report);require(source_digest()==report['source_sha256'],'source changed');report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    write_json(a.report,report);print(json.dumps(dict(passed=report['passed'],captures=len(report['captures']),diagnostics=len(report['diagnostics']),errors=report['errors'])));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
