#!/usr/bin/env python3
"""Independent packet-state mathematics and exact cross-platform acceptance."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from packet_vectors import packet,cookie,bundle,sequences,identity,manifest
from packet_oracle import Decoder
from validate import require,write_json
from validate_bwe2 import check_expected as check_bwe2,fingerprints
from validate_cac import digest
from validate_portable import ROOT,LIMIT,compare_pcm,float32,source_digest
from validate_replay import command,sha256_file

PROFILE='apac-asp-state-v1'
COUNT=2268
MANIFEST_PATH=ROOT/'data/packet-vectors-v1.json'


def coverage(report):
    spans=[(f['bit_offset'],f['bit_length']) for f in report['fields']]
    spans.extend((r['bit_offset'],r['bit_length']) for r in report['unknown_ranges'])
    position=0
    for offset,length in sorted(spans):
        require(offset==position and length>0,'packet bit coverage has a gap/overlap')
        position+=length
    require(position==report['packet_bytes']*8,'unaccounted packet bits')


def check_expected(report,truth,math=True):
    require(report['status']=='complete' and report['packet_complete'],'incomplete qualified packet')
    require(report['packet_state_profile']==PROFILE,'wrong packet-state profile')
    require(report['packet_tail']==truth['tail'],'packet tail or component boundary differs')
    require(report['component_end_bit_offset']==truth['tail']['core_end_bit_offset'],'false component endpoint')
    require(report['stop_bit_offset']==truth['tail']['packet_end_bit_offset'] and not report['unknown_ranges'],'false packet endpoint')
    fields={f['name']:f['value'] for f in report['fields']}
    require(fields['frame.type_code']==truth['frame_type'],'wrong outer frame type')
    coverage(report);metrics=[]
    if truth['absent']:
        for name in ('channels','channels_after_cac','channels_after_tns','channels_after_bwe2'):
            require(report[name]==[],'absent CPE fabricated channels: '+name)
        for name in ('spectrum_complete','cac_complete','tns_complete','bwe2_complete'):
            require(not report[name],'absent CPE fabricated a completed spectral stage')
        require(not any(f['name'].startswith('bwe2.') for f in report['fields']),'absent CPE consumed BWE2 bits')
    else:
        previous=dict(report,stop_bit_offset=truth['spectra']['bwe2']['end_bit_offset'])
        result=check_bwe2(previous,truth['spectra'],math)
        if result:metrics.append(result)
    actual=report['embedded_preroll'];wanted=truth['embedded_preroll']
    require((actual is None)==(wanted is None),'missing or extra internal preroll')
    if wanted is not None:
        for key in ('start_bit_offset','end_bit_offset'):require(actual[key]==wanted[key],'wrong internal frame span')
        metrics+=check_expected(actual['report'],wanted['truth'],math)
    return metrics


def flatten(reports):
    out=[]
    for report in reports:
        if report['embedded_preroll'] is not None:out+=flatten([report['embedded_preroll']['report']])
        out.append(report)
    return out


def structure(reports):
    return [{key:r[key] for key in ('packet_sha256','packet_bytes','status','prefix_complete','packet_complete',
             'fields','derived','stop_reason','stop_bit_offset','component_end_bit_offset','unknown_ranges','packet_tail')}
            for r in flatten(reports)]


def exact(actual,expected):
    for key,value in actual.items():
        if key.endswith('_sha256') or key in ('rate','index','kind','frames','packets','internal_frames'):
            require(value==expected[key],'packet stage fingerprint differs: '+key)


def validate_reference(reference,current):
    require(reference.get('passed') is True and reference.get('mode')=='independent_math','requires successful independent packet mathematics')
    require(reference.get('counts')=={'sequences':COUNT} and reference.get('errors')==[],'incomplete packet reference')
    for key in ('code_commit','source_sha256','state_profile','constants','vector_manifest_sha256','atol','rtol'):
        require(reference[key]==current[key],'packet reference identity differs: '+key)
    rows=reference['sequences']
    require(len(rows)==COUNT and all(r['passed'] for r in rows),'missing/failed packet cases')
    require([(r['rate'],r['index']) for r in rows]==[(rate,i) for rate in (48000,44100) for i in range(COUNT//2)],
            'reordered/duplicated/missing packet cases')


def portable(binary,report,reference):
    frozen=json.loads(MANIFEST_PATH.read_text())
    require(frozen==manifest() and len(frozen['sequences'])==COUNT,'frozen packet inputs differ')
    required={(r['rate'],r['index']):r for r in frozen['sequences']}
    old={(r['rate'],r['index']):r for r in reference['sequences']} if reference else {}
    results={}
    for rate in (48000,44100):
        buckets=defaultdict(list)
        for index,(kind,options,seq) in enumerate(sequences()):
            buckets[json.dumps(options,sort_keys=True)].append((index,kind,options,seq))
        completed=0
        for bucket in buckets.values():
            for first in range(0,len(bucket),16):
                batch=bucket[first:first+16]
                try:
                    with tempfile.TemporaryDirectory(prefix='apac-state-') as tmp:
                        root=Path(tmp);options=batch[0][2]
                        generated=[packet(c,rate,options.get('scene',False)) for _,_,_,seq in batch for c in seq]
                        bundle(root/'packets',[p for p,_ in generated],rate,**options)
                        path=root/'packets.jsonl'
                        summary=command(binary,'parse-packets',root/'packets','--depth','packet','--packets',len(generated),'--output',path)
                        rows=[json.loads(line)['report'] for line in path.read_text(encoding='utf-8').splitlines()]
                        require(summary['packet_complete_packets']==len(generated)==len(rows) and summary['errors']==0,'incomplete packet parse')
                        metrics=[check_expected(r,t,reference is None) for r,(_,t) in zip(rows,generated)]
                        decoded=command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                        require(decoded['complete'] and decoded['experimental'] and not decoded['native_apis_used'],'wrong PCM qualification')
                        require(decoded['packet_state_profile']==PROFILE and decoded['saved_frames']==1024*len(rows),'PCM time accounting differs')
                        require(decoded['embedded_preroll_frames']==sum(t['embedded_preroll'] is not None for _,t in generated),'internal frames counted on source timeline')
                        implementation=decoded['pcm']['decoder_settings']['implementation']['value']
                        if report['implementation'] is None:report['implementation']=implementation
                        require(implementation==report['implementation'],'implementation changed')
                        raw=(root/'pcm/pcm.f32le').read_bytes()
                        require(len(raw)==8192*len(rows) and hashlib.sha256(raw).hexdigest()==decoded['pcm']['sha256'],'PCM bytes/hash differ')
                        cursor=0
                        for index,kind,_,seq in batch:
                            length=len(seq);selected=rows[cursor:cursor+length];parts=generated[cursor:cursor+length]
                            record=dict(rate=rate,index=index,kind=kind,packets=length,frames=length*1024,
                                        internal_frames=sum(t['embedded_preroll'] is not None for _,t in parts),passed=True,
                                        input_sha256=identity(cookie(rate,**options),[p for p,_ in parts]),
                                        packet_state_sha256=digest(structure(selected)),**fingerprints(flatten(selected)))
                            target=required[rate,index]
                            require(all(record[k]==v for k,v in target.items()),'frozen packet identity differs')
                            pcm=raw[cursor*8192:(cursor+length)*8192]
                            record['pcm_sha256']=hashlib.sha256(pcm).hexdigest()
                            values=float32(struct.unpack('<'+str(len(pcm)//4)+'f',pcm))
                            if reference:
                                exact(record,old[rate,index])
                            else:
                                oracle=Decoder();expected=[v for _,t in parts for v in oracle.decode(t)]
                                record.update(compare_pcm(values,expected))
                                if record['first_failure']:
                                    failure=record['first_failure'];sample=failure['sample']
                                    failure.update(sequence_packet=sample//2048,channel=sample%2,frame_in_packet=(sample//2)%1024)
                                spectra=[m for group in metrics[cursor:cursor+length] for m in group]
                                record['spectral_metrics']=dict(max_absolute_error=max((m['max_absolute_error'] for m in spectra),default=0),
                                    max_ulp=max((m['max_ulp'] for m in spectra),default=0),passed=all(m['passed'] for m in spectra))
                            require((rate,index) not in results,'duplicate packet result')
                            results[rate,index]=record;cursor+=length
                except Exception as error:
                    report['errors'].append(dict(rate=rate,indices=[i for i,_,_,_ in batch],error=str(error)))
                completed+=len(batch)
                if completed%160<16:print('PACKET',rate,completed,file=sys.stderr,flush=True)
    report['sequences']=[results[key] for key in sorted(results,key=lambda k:(k[0]!=48000,k[1]))]
    require(set(results)==set(required),'missing required packet-state cases')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference-report',type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing to overwrite report')
    binary=args.binary.resolve(strict=True)
    report=dict(schema_version=1,state_profile=PROFILE,
        code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,encoding='utf-8').strip(),
        tested_worktree_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,encoding='utf-8').strip()),
        source_sha256=source_digest(),vector_manifest_sha256=sha256_file(MANIFEST_PATH),
        constants={p.name:sha256_file(p) for p in sorted((ROOT/'data').glob('*.json')) if 'vectors' not in p.name},
        tool_sha256=sha256_file(binary),platform=platform.platform(),architecture=platform.machine(),python=sys.version,
        mode='bit_exact_replay' if args.reference_report else 'independent_math',implementation=None,atol=1e-6,rtol=1e-5,
        sequences=[],errors=[],started_utc=datetime.now(timezone.utc).isoformat())
    reference=None
    try:
        if args.reference_report:
            require(args.reference_report.stat().st_size<=LIMIT,'reference exceeds 128 MiB')
            report['reference_report_sha256']=sha256_file(args.reference_report)
            reference=json.loads(args.reference_report.read_text(encoding='utf-8'));validate_reference(reference,report)
        portable(binary,report,reference)
        require(source_digest()==report['source_sha256'] and sha256_file(binary)==report['tool_sha256'],'sources or binary changed during acceptance')
        if args.reference_report:require(sha256_file(args.reference_report)==report['reference_report_sha256'],'reference changed during acceptance')
    except Exception as error:report['errors'].append(dict(stage='validation',error=str(error)))
    report['counts']=dict(sequences=len(report['sequences']))
    report['passed']=report['counts']=={'sequences':COUNT} and not report['errors'] and all(r['passed'] for r in report['sequences'])
    report['pcm_metrics']=dict(max_absolute_error=max((r.get('max_absolute_error',0) for r in report['sequences']),default=0),
        max_ulp=max((r.get('max_ulp',0) for r in report['sequences']),default=0),failed_samples=sum(r.get('failed_samples',0) for r in report['sequences'])) if reference is None else None
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    require(len(json.dumps(report).encode())<LIMIT,'report exceeds 128 MiB');write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('passed','counts','pcm_metrics','errors')}))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
