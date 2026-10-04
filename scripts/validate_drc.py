#!/usr/bin/env python3
"""Portable DRC parser acceptance; this does not qualify media PCM support."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import shutil
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from drc_vectors import packet,cookie,bundle,cases,manifest
from validate import require,write_json
from validate_packets import coverage
from validate_portable import ROOT,LIMIT,source_digest
from validate_replay import command,sha256_file
from validate_cac import digest

COUNT=2516
MANIFEST=ROOT/'data/drc-vectors-v1.json'


def check_expected(report,truth):
    require(report['drc_complete'] and not report['drc_processing_applied'],'DRC stage incomplete or unexpectedly applied')
    require(report['status']=='partial' and report['component_end_bit_offset']==truth['core_end_bit_offset'],'DRC stage replaced packet status or component end')
    require(report['stop_bit_offset']==truth['drc']['end_bit_offset'],'DRC end differs')
    for key,value in truth['drc'].items():require(report['drc'][key]==value,'DRC truth differs: '+key)
    coverage(report)
    if truth['inner']:check_expected(report['drc_preroll'],truth['inner'])


def stage(report):
    return {k:report[k] for k in ('packet_sha256','fields','derived','stop_bit_offset','status','component_end_bit_offset','unknown_ranges',
        'drc_rules_version','drc_codebook_sha256','drc_complete','drc_payload_present','drc_history_sufficient','drc_processing_applied','drc','drc_preroll')}


def native_check(trace,truths):
    require(len(trace['packets'])==len(truths),'native packet count differs')
    expected={}
    for i,truth in enumerate(truths):
        expected[i,'current']=truth
        if truth['inner']:expected[i,'preroll']=truth['inner']
    found=defaultdict(list)
    for e in trace['events']:
        if e['kind'] in ('gain_read','header','payload'):found[e['sequence'],e['role']].append(e)
        require(e['kind']!='gain_reset','native concealed malformed DRC')
    require(set(found)==set(expected),'missing or unassociated native DRC frame')
    for key,truth in expected.items():
        d=truth['drc'];own=found[key]
        for kind,start,end in [('payload',d['start_bit_offset'],d['end_bit_offset']),('header',d['start_bit_offset'],d['header_end_bit_offset']),('gain_read',d['header_end_bit_offset'],d['end_bit_offset'])]:
            rows=[e for e in own if e['kind']==kind];require(len(rows)==1,'missing or duplicated '+kind)
            e=rows[0];require(e['status']==0,'native '+kind+' failed')
            require(e['start']['relative_bit_offset']==start and e['end']['relative_bit_offset']==end,'native DRC bit boundary differs')
            if kind=='gain_read':
                require(e['native_timing_mode_word']==1,'unverified APAC node timing mode')
                sequences=e['sequences'];require(len(sequences)==1,'native sequence count differs')
                seq=sequences[0];require(seq['coding_mode']==d['coding_mode'],'native coding mode differs')
                if d['coding_mode']:require(seq['frame_end']==d['frame_end'],'native frame-end flag differs')
                wanted=[dict(gain_db=n['gain_eighth_db']/8,slope=0.0,time=n['time']) for n in d['nodes']]
                require(seq['nodes']==wanted,'native integer nodes differ')


@contextmanager
def workspace(report,label,retain=None):
    with tempfile.TemporaryDirectory(prefix='apac-drc-') as temporary:
        root=Path(temporary)
        def preserve():
            size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            require(size<=LIMIT,'DRC failure snapshot exceeds 128 MiB')
            target=Path(report['failure_directory'])/label
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(root,target)
        try:yield root
        except Exception:
            preserve()
            raise
        else:
            if retain is not None and retain():preserve()

def run(binary,report,reference,native):
    frozen=json.loads(MANIFEST.read_text(encoding='utf-8'))
    require(frozen==manifest() and len(frozen['cases'])==COUNT,'frozen DRC vectors differ')
    old={(r['rate'],r['index']):r for r in reference['cases']} if reference else {}
    required={(r['rate'],r['index']):r for r in frozen['cases']}
    for rate in (48000,44100):
        buckets=defaultdict(list)
        for i,(kind,options,case) in enumerate(cases()):
            if native and kind=='initial_gain' and i not in (0,255,256,511,512,767,768,1023):continue
            buckets[json.dumps(options,sort_keys=True)].append((i,kind,options,case))
        for bucket in buckets.values():
            size=8 if native else 32
            for offset in range(0,len(bucket),size):
                batch=bucket[offset:offset+size];options=batch[0][2]
                with workspace(report,str(rate)+'-'+str(batch[0][0])) as root:
                    generated=[packet(c,rate,**options) for _,_,_,c in batch]
                    directory=root/'packets';bundle(directory,[p for p,_ in generated],rate,**options)
                    out=root/'report.jsonl';summary=command(binary,'parse-packets',directory,'--depth','drc','--packets',len(batch),'--output',out,allowed=(2,))
                    require(summary['drc_complete_packets']==len(batch) and summary['errors']==0,'DRC matrix incomplete')
                    rows=[json.loads(l)['report'] for l in out.read_text(encoding='utf-8').splitlines()]
                    require(len(rows)==len(batch),'missing candidate report')
                    if native:
                        from native_drc_trace import trace_bundle
                        trace=trace_bundle(binary,directory,root/'native',frames=1024*len(batch))
                        native_check(trace,[t for _,t in generated])
                    for (index,kind,_,_),row,(raw,truth) in zip(batch,rows,generated):
                        check_expected(row,truth)
                        result=dict(required[rate,index],drc_stage_sha256=digest(stage(row)),passed=True)
                        require(result['packet_sha256']==hashlib.sha256(raw).hexdigest(),'packet identity changed')
                        if reference:require(result==old[rate,index],'cross-build DRC stage differs')
                        report['cases'].append(result)
                print(f'DRC {rate}: {len(report["cases"])} cases',file=sys.stderr,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--reference-report',type=Path);p.add_argument('--native',action='store_true')
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    fingerprint=sha256_file(args.binary)
    report=dict(schema_version=1,passed=False,phase_complete=False,scope='parser_only',native=args.native,
                created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
                binary_sha256=fingerprint,failure_directory=str(args.report.with_suffix('.failures')),rules_version='apac-drc-payload-v1',vector_manifest_sha256=manifest()['sha256'],cases=[],errors=[])
    reference=json.loads(args.reference_report.read_text(encoding='utf-8')) if args.reference_report else None
    try:
        if reference:
            require(not args.native and reference['passed'] and not reference['native'] and len(reference['cases'])==COUNT,'incomplete parser reference')
            for key in ('code_commit','source_sha256','rules_version','vector_manifest_sha256'):require(report[key]==reference[key],'reference identity differs: '+key)
        run(args.binary.resolve(),report,reference,args.native)
        expected=484 if args.native else COUNT
        require(len(report['cases'])==expected,'required DRC case count missing')
        require(sha256_file(args.binary)==fingerprint,'binary changed during acceptance')
        require(source_digest()==report['source_sha256'],'source changed during acceptance')
        report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    report['cases'].sort(key=lambda r:(r['rate'],r['index']))
    write_json(args.report,report)
    print(json.dumps(dict(passed=report['passed'],cases=len(report['cases']),errors=report['errors'])))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
