#!/usr/bin/env python3
"""Paired HOA release timings with 2048 prefix packets and equal verification."""
import argparse,json,platform,subprocess
from pathlib import Path
import hoa_shared_vectors as shared
import hoa_controls_vectors as controls
from hoa_access_vectors import PROFILE
from benchmark_access import paired
from caf_vectors import encode as caf_encode,sha,digest
from mp4_vectors import encode as mp4_encode
from validate import require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import sha256_file

def workloads():
    cs=[shared.ambient(4)];opts=dict(components=cs)
    yield 'ambient',shared,opts,[dict(components=[shared.marked(cs[0],172,b)]) for b in (0,1,2,3)]+[{}],4
    _,opts,seq=next(v for v in controls.sequences() if v[0]=='dynamic-controls')
    yield 'salient-dynamic',controls,opts,seq,16
    _,opts,seq=next(v for v in shared.sequences() if v[0]=='stream-hoa-channel')
    yield 'hoa-discrete',shared,opts,seq,sum(shared.parts(c,48000)['channels'] for c in opts['components'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--prefix-packets',type=int,default=2048);p.add_argument('--trials',type=int,default=5);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'binary missing/report exists');require(a.prefix_packets>=2048 and a.trials>=5,'insufficient paired samples')
    report=dict(passed=False,profile=PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),platform=platform.platform(),prefix_packets=a.prefix_packets,trials=a.trials,initial_pair_discarded=True,controls=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for name,module,opts,seq,n in workloads():
            cycle=[module.packet(c,**opts)[0] for c in seq];payloads=[cycle[i%len(cycle)] for i in range(a.prefix_packets+8)];cfg=module.cookie(**opts)
            for container in ('caf','mp4'):
                with workspace(report,name+'-'+container) as root:
                    raw=caf_encode(cfg,payloads,channels=n,variant=24,layout_tag=0)[0] if container=='caf' else mp4_encode(cfg,payloads,channels=n,variant=7)[0]
                    source=root/'input.audio';source.write_bytes(raw)
                    result=paired(a.binary.resolve(),source,root,a.prefix_packets*1024+17,1027,a.trials,n,len(payloads),profile=PROFILE)
                    result.update(kind=name,container=container,input_sha256=sha(raw));report['controls'].append(result);require(result['passed'],'fast median did not improve: '+name+'/'+container)
                print(name,container,result['speed_ratio'],flush=True)
        require(len(report['controls'])==6,'missing workload');require(source_digest()==report['source_sha256'] and sha256_file(a.binary)==report['binary_sha256'],'source/binary changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    report['conditions_sha256']=digest([dict(kind=r['kind'],container=r['container'],input_sha256=r['input_sha256']) for r in report['controls']]);write_json(a.report,report);print(json.dumps(dict(passed=report['passed'],errors=report['errors'])));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
