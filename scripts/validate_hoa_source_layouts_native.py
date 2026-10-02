#!/usr/bin/env python3
"""Hash-bound source layouts, full matrix constants, and isolated reference defects."""
import argparse,hashlib,json,struct,subprocess
from pathlib import Path
import hoa_source_layout_vectors as vectors
from hoa_source_layout_oracle import Decoder
from native_frame_trace import COMPONENT_SHA256
from validate import require,write_json
from validate_drc import workspace
from validate_replay import sha256_file
from validate_portable import source_digest
from validate_channels import compare,merge_metrics


def verify(a,report):
    constants=json.loads(a.constants.read_text())
    require(constants['component_sha256']==COMPONENT_SHA256 and not constants['errors'] and constants['pending_returns']==0 and constants['process_exit_code']==0,'constant capture incomplete')
    actual={hashlib.sha256(struct.pack('<'+str(len(words))+'I',*words)).hexdigest():words for words in constants['matrix_constants'].values()}
    require(actual==vectors.FORMAT['matrices'],'native matrix constants differ')
    report['constant_capture_sha256']=sha256_file(a.constants);report['matrix_tables']=len(constants['matrix_constants'])
    matrix=['matrix-'+str(e['tag']) for e in vectors.FORMAT['layouts']]
    other=['sn3d','n3d','n3d-p2','bformat','n3d-profile0','bformat-profile0','acn-labels','n3d-labels','speaker-labels','surround-p1','surround-p2','matrix-reduced','matrix-partial','matrix-single','discrete','discrete-p2','sn3d-crop','sn3d-expand-v2','custom-reordered','private-variable-v2','private-exact-v2']
    rejected={'n3d','n3d-p2','bformat','n3d-profile0','bformat-profile0','matrix-8060934','matrix-12058632'}
    for name in matrix+other:
        root=a.captures/name;truth=json.loads((root/'truth.json').read_text());trace=json.loads((root/'native-hoa.json').read_text())
        require(trace['component_sha256']==COMPONENT_SHA256 and not trace['errors'] and trace['pending_returns']==0,'capture incomplete: '+name)
        cookie=(root/'packets/cookie.bin').read_bytes();wire=''.join(format(v,'08b') for v in cookie)
        opts=vectors.options(truth['m'],(truth['family']<<16)|truth['n'] if truth['labels'] is None else 0,truth['labels'],truth['parameter'],profile=int(wire[112:118],2),level=int(wire[118:122],2))
        require(vectors.cookie(**opts)==cookie,'native cookie identity differs: '+name)
        generated=[vectors.packet(vectors.basis(opts,k),**opts) for k in sorted(set([0,truth['m']-1]))]
        with workspace(report,name) as tmp:
            proc=subprocess.run([str(a.binary),'parse-packets',str(root/'packets'),'--depth','hoa','--output',str(tmp/'parsed')],capture_output=True,text=True)
            if name in rejected:
                require(trace['process_exit_code']==1 and not trace['packets'],'rejected input entered native audio decoding')
                require(proc.returncode==2,'candidate accepted a rejected source configuration')
                parsed=[json.loads(line)['report'] for line in (tmp/'parsed').read_text().splitlines()]
                require(all(r['status']=='unsupported' and not r['packet_complete'] for r in parsed),'rejection became a malformed successful frame')
                log=(root/'debugger.log').read_text()
                require('Wrong profile index' in log or 'mTYPE_ChannelToHoaConversion' in log,'unexplained native rejection')
                report['rejections'].append(dict(name=name,stage='profile' if 'Wrong profile index' in log else 'source_initialization',trace_sha256=sha256_file(root/'native-hoa.json'),cookie_sha256=hashlib.sha256(cookie).hexdigest()))
                continue
            require(proc.returncode==0 and trace['process_exit_code']==0,'source replay failed: '+name)
            require([p['packet_sha256'] for p in trace['packets']]==[hashlib.sha256(raw).hexdigest() for raw,t in generated],'native packet identities differ: '+name)
            rows=[json.loads(line)['report'] for line in (tmp/'parsed').read_text().splitlines()]
            oracle=Decoder(opts)
            for raw,t in generated:oracle.decode(t)
            events=[e for e in trace['hoa_events'] if e['kind']=='hoa_spatial']
            require(len(events)==len(generated) and len(rows)==len(generated),'native frame set differs')
            for index,(node,event,(_,t),expected) in enumerate(zip(rows,events,generated,oracle.records)):
                require(event['status']==0 and (event['start']['relative_bit_offset'],event['end']['relative_bit_offset'])==(t['spatial']['start_bit_offset'],t['spatial']['end_bit_offset']),'native spatial boundary differs')
                h=node['hoa'];source=h.get('source_layout');candidate=[c['scaled'] for c in (source['channels'] if source else h['channels_after_hoa'])]
                measure=compare([v for c in candidate for v in c],[v for c in expected['scaled'] for v in c],dict(name=name,frame=index,stage='independent'))
                require(measure['passed'],'candidate differs from independent source math');merge_metrics(report['candidate'],measure)
                native_expected=expected['scaled']
                if name=='matrix-8323080':
                    history=[e for e in trace['hoa_events'] if e['kind']=='hoa_history'][index]['after']
                    require(history['lfe_indices']==[3,-1],'native LFE defect changed')
                    native_expected=native_expected[:3]+[[0.]*1024]+native_expected[3:7]
                    report['diagnostics'].append(dict(name=name,frame=index,rule='apac-bound-native-cicp7-lfe-index-v1',declared_lfe_index=7,native_lfe_index=3))
                synth=[e['input'] for e in trace['events'] if e['kind']=='synthesis' and e['sequence']==index]
                require(len(synth)==truth['n'],'native source outputs missing')
                measure=compare([v for c in synth for v in c],[v for c in native_expected for v in c],dict(name=name,frame=index,stage='native'))
                require(measure['passed'],'unexplained native source difference');merge_metrics(report['native'],measure)
            report['captures'].append(dict(name=name,frames=len(generated),trace_sha256=sha256_file(root/'native-hoa.json'),cookie_sha256=hashlib.sha256(cookie).hexdigest(),probe_sha256=trace.get('source_probe_sha256')))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--captures',type=Path,required=True);p.add_argument('--constants',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();a.binary=a.binary.resolve()
    require(not a.report.exists(),'report exists')
    metric=lambda:dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
    report=dict(passed=False,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,format_sha256=vectors.FORMAT['format_sha256'],captures=[],rejections=[],diagnostics=[],candidate=metric(),native=metric(),errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        verify(a,report);require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    write_json(a.report,report);print(json.dumps(dict(passed=report['passed'],captures=len(report['captures']),rejections=len(report['rejections']),errors=report['errors'])));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
