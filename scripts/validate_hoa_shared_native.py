#!/usr/bin/env python3
"""Native public replay and separate diagnostics for the bound HOA window defect."""
import argparse,hashlib,json,struct,subprocess
from pathlib import Path
import hoa_shared_vectors as vectors
from hoa_shared_oracle import Decoder
from sq_oracle import Channel
from validate import require,write_json
from validate_channels import compare,merge_metrics
from validate_portable import source_digest
from validate_replay import sha256_file
from native_frame_trace import COMPONENT_SHA256


class DiagnosticDecoder:
    """Reconstruct only previously proved bound-reference window/routing defects."""
    def __init__(self,opts):
        self.opts=opts;self.oracle=Decoder(opts);self.diagnostics=[]
        self.windows=[];self.synthesis=[]
        for component in opts['components']:
            n=vectors.parts(component,opts.get('rate',48000))['channels']
            types=component.get('options',{}).get('tce_types',[0]*n)
            physical=sum(2 if k==1 else 0 if k==6 else 1 for k in types)
            self.windows.append([0]*physical)
            self.synthesis.append([Channel(strict_transitions=False) for _ in range(n)])
    def decode(self,truth):
        if truth['inner']:self.decode(truth['inner'])
        groups=[]
        for index,(component,oracle,t) in enumerate(zip(self.opts['components'],self.oracle.decoders,truth['components'])):
            pcm=oracle.decode(t);n=len(self.synthesis[index])
            if component.get('type',2)==2:
                block=t['common_window'];record=oracle.records[-1];cursor=0;windows=self.windows[index]
                for element in t['elements']:
                    width=len(element['configuration'].get('transport_channels',element['configuration']['output_channels']))
                    if element['present']:
                        for i,c in enumerate(element['channels']):windows[cursor+i]=c['ics']['block_type']
                    cursor+=width
                types=component.get('options',{}).get('tce_types',[0]*n)
                native_block=0 if types[0]==6 else windows[0]
                out=[];changed=False
                for i,spectrum in enumerate(record['scaled']):
                    if block==2:spectrum=[spectrum[w*128+f] for f in range(128) for w in range(8)]
                    if i<len(windows) and windows[i]==2:spectrum=[spectrum[f*8+w] for w in range(8) for f in range(128)]
                    changed|=(block==2)!=(i<len(windows) and windows[i]==2)
                    out.append(self.synthesis[index][i].render(spectrum,native_block))
                if changed or native_block!=block:self.diagnostics.append(dict(rule='apac-bound-native-hoa-window-defects-v1',component=index,common_window=block,native_window=native_block,transport_channels=len(windows),source_channels=n))
                groups.append(out)
            else:groups.append([pcm[i::n] for i in range(n)])
        n=self.oracle.channels
        if self.opts.get('scene'):
            raw=[c for group in groups for c in group];output=[[0.]*1024 for _ in raw]
            descriptors=self.opts.get('additional')
            if descriptors is not None:
                starts=[c['start'] for c in descriptors]
                spans=[(v,n if len(starts)==1 else min([s for s in starts if s>v]+[n])-v) for v in starts]
            else:
                spans=[];source=0
                for c,g in zip(self.opts['components'],groups):spans.append((c.get('start',source),len(g)));source+=len(g)
            source=0
            for target,count in spans:output[target:target+count]=raw[source:source+count];source+=count
            if len(raw)>n:self.diagnostics.append(dict(rule='apac-bound-native-scene-output-hole-v1',coded_channels=len(raw),output_channels=n))
            cursor=0;groups=[output[(cursor:=cursor+len(g))-len(g):cursor] for g in groups]
        output=[]
        for group in groups:
            if len(output)+len(group)<=n:output.extend(group)
        require(len(output)==n,'incomplete native diagnostic output')
        return [v for row in zip(*output) for v in row]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--captures',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--case',action='append');p.add_argument('--policy-evidence',type=Path);a=p.parse_args();require(not a.report.exists(),'report exists')
    report=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),component_sha256=COMPONENT_SHA256,cases=[],errors=[],diagnostics=[],policy_only=[],metrics=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None))
    try:
        require(sha256_file(Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs'))==COMPONENT_SHA256,'native component changed')
        for name,opts,cases in vectors.sequences():
            if a.case and name not in a.case:continue
            root=a.captures/name;native=json.loads((root/'native/result.json').read_text())
            require((root/'packets/cookie.bin').read_bytes()==vectors.cookie(**opts),'cookie mismatch: '+name)
            generated=[vectors.packet(c,**opts) for c in cases];require((root/'packets/packets.bin').read_bytes()==b''.join(raw for raw,t in generated),'input mismatch: '+name)
            if name=='shared-drc-metadata-effect-512':
                require(a.policy_evidence is not None,'fade syntax/selection evidence is required')
                evidence=a.policy_evidence/'fade';trace=json.loads((evidence/'trace/native-drc.json').read_text())
                cookie=vectors.cookie(**opts);parts={};vectors.cookie(**opts,_parts=parts)
                require((evidence/'packets/cookie.bin').read_bytes()==cookie,'fade evidence cookie differs')
                require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and not trace['pending_returns'] and trace['process_exit_code']==1,'fade capture is incomplete')
                headers=[e for e in trace['events'] if e['kind']=='header' and e['role']=='configuration']
                require(len(headers)==1 and headers[0]['status']==0,'fade header syntax failed')
                require(headers[0]['end']['buffer_sha256']==hashlib.sha256(cookie[12:]).hexdigest() and headers[0]['end']['relative_bit_offset']+96==parts['ancillary_end_bit_offset']-2,'fade header boundary differs')
                selection=[e for e in trace['events'] if e['kind']=='selection'];require(len(selection)==1 and selection[0]['status']!=0,'fade processing failure is unexplained')
                require(native['status']==560226676 and native['frames']==0,'fade native policy behavior changed')
                record=dict(name=name,rule='apac-bound-native-drc-none-fade-unavailable-v1',header_syntax_verified=True,pcm_compared=False,trace_sha256=sha256_file(evidence/'trace/native-drc.json'),native_result_sha256=sha256_file(root/'native/result.json'))
                report['policy_only'].append(record);report['cases'].append(record);continue
            require(native['status']==0 and native['frames']==len(cases)*1024,'native run incomplete: '+name)
            raw=(root/'native/pcm.f32le').read_bytes();samples=struct.unpack('<%df'%(len(raw)//4),raw)
            oracle=Decoder(opts);expected=[]
            for _,truth in generated:expected.extend(oracle.decode(truth))
            result=compare(samples,expected,dict(case=name,reference='independent-mathematics'));diagnostic=False
            if not result['passed']:
                oracle=DiagnosticDecoder(opts);expected=[]
                for _,truth in generated:expected.extend(oracle.decode(truth))
                require(bool(oracle.diagnostics),'unexplained native semantic difference: '+name)
                result=compare(samples,expected,dict(case=name,reference='bound-reference-diagnostic'));diagnostic=True
                for entry in oracle.diagnostics:report['diagnostics'].append(dict(case=name,**entry))
            require(result['passed'],str(result['first_failure']));merge_metrics(report['metrics'],result)
            report['cases'].append(dict(name=name,frames=native['frames'],pcm_compared=True,reference='bound-reference-diagnostic' if diagnostic else 'independent-mathematics',native_pcm_sha256=hashlib.sha256(raw).hexdigest(),native_result_sha256=sha256_file(root/'native/result.json')))
            print('native shared',name,flush=True)
        wanted={name for name,opts,cases in vectors.sequences() if not a.case or name in a.case}
        require({row['name'] for row in report['cases']}==wanted and bool(wanted),'native coverage differs')
        require(source_digest()==report['source_sha256'],'source changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    write_json(a.report,report);print(json.dumps({k:report[k] for k in ('passed','metrics','errors')}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
