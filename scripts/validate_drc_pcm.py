#!/usr/bin/env python3
"""Independent DRC-off PCM/state mathematics and exact portable acceptance."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib,json,platform,struct,subprocess,sys
from pathlib import Path
from drc_pcm_vectors import sequences,packet,cookie,bundle,manifest,oracle_truth
from packet_oracle import Decoder
from validate import require,write_json
from validate_packets import coverage,flatten,structure
from validate_bwe2 import fingerprints
from validate_cac import digest
from validate_portable import ROOT,source_digest,compare_pcm
from validate_replay import command,sha256_file
from validate_drc import workspace

COUNT=2640
MANIFEST=ROOT/'data/drc-pcm-vectors-v1.json'
PARAMETERS=dict(coefficient_location=1,gain_sequences=1,gain_sets=1,bands=1,coding_profile=0,
                interpolation='linear',full_frame=False,time_alignment=False,frame_samples=1024,time_delta_min=64)


class DrcReference:
    """Header provenance and prior nodes, driven solely by written input values."""
    def __init__(self,config,options):
        self.origin=self.metadata_origin='cookie'
        self.source=self.metadata_source=hashlib.sha256(config).hexdigest()
        self.loudness=165 if options.get('rich') else None
        self.history=False
        self.options=options
    def check(self,report,truth,case,rate):
        require(report['packet_complete'] and report['status']=='complete','incomplete DRC packet')
        require(report['stop_bit_offset']==report['packet_bytes']*8 and not report['unknown_ranges'],'packet endpoint differs')
        require(report['component_end_bit_offset']==truth['core_end_bit_offset'],'component endpoint differs')
        require(report['drc_complete'] is True and report['drc_processing_applied'] is False,'wrong DRC stage state')
        coverage(report)
        if truth['inner']:
            require(report['embedded_preroll'] is not None,'missing embedded DRC frame')
            self.check(report['embedded_preroll']['report'],truth['inner'],case['preroll'],rate)
        else:require(report['embedded_preroll'] is None,'unexpected embedded frame')
        data=report['drc'];wanted=truth['drc']
        for key,value in wanted.items():require(data[key]==value,'DRC parameter/bit truth differs: '+key)
        require(report['drc_history_sufficient']==self.history,'DRC history advanced in wrong order')
        raw,_=packet(case,rate,self.options);sha=hashlib.sha256(raw).hexdigest()
        require(report['packet_sha256']==sha,'packet identity differs')
        if wanted['header_present']:
            self.metadata_origin='packet';self.metadata_source=sha
            self.loudness=case.get('drc',{}).get('loudness_value',165 if self.options.get('rich') else None)
            if wanted['config_present']:self.origin='packet';self.source=sha
        configuration=data['configuration']
        require(configuration['parameters']==PARAMETERS,'DRC coding context differs')
        require(configuration['source']==self.origin and configuration['source_sha256']==self.source,'DRC configuration reuse differs')
        require(configuration['loudness_metadata_source']==self.metadata_origin and
                configuration['loudness_metadata_source_sha256']==self.metadata_source,'DRC metadata reuse differs')
        values=[f['value'] for f in configuration['loudness_metadata'] if f['name'].endswith('.value_a_encoded')]
        require(values==([] if self.loudness is None else [self.loudness]),'DRC loudness metadata was lost or replaced')
        self.history=any(n['time']<1024 for n in wanted['nodes'])


def drc_structure(reports):
    return [{k:r[k] for k in ('drc','drc_complete','drc_history_sufficient','drc_processing_applied')} for r in flatten(reports)]


def validate(binary,report,reference):
    frozen=json.loads(MANIFEST.read_text(encoding='utf-8'))
    require(frozen==manifest() and len(frozen['sequences'])==COUNT,'frozen DRC PCM vectors differ')
    required={(r['rate'],r['index']):r for r in frozen['sequences']}
    previous={(r['rate'],r['index']):r for r in reference['sequences']} if reference else {}
    for rate in (48000,44100):
        buckets=defaultdict(list)
        for index,(kind,options,seq) in enumerate(sequences()):buckets[json.dumps(options,sort_keys=True)].append((index,kind,options,seq))
        for bucket in buckets.values():
            for first in range(0,len(bucket),8):
                batch=bucket[first:first+8];options=batch[0][2]
                with workspace(report,str(rate)+'-'+str(batch[0][0])) as root:
                    generated=[packet(c,rate,options) for _,_,_,seq in batch for c in seq]
                    bundle(root/'packets',[p for p,_ in generated],rate,**options)
                    out=root/'packets.jsonl'
                    summary=command(binary,'parse-packets',root/'packets','--depth','packet','--packets',len(generated),'--output',out)
                    require(summary['packet_complete_packets']==len(generated) and summary['errors']==0,'incomplete packet matrix')
                    rows=[json.loads(l)['report'] for l in out.read_text(encoding='utf-8').splitlines()]
                    require(len(rows)==len(generated),'missing packet report')
                    decoded=command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                    require(decoded['complete'] and decoded['experimental'] and not decoded['native_apis_used'],'wrong PCM support scope')
                    require(decoded['drc_processing']==decoded['loudness_normalization']=='off' and decoded['drc_payloads_complete'],'processing was enabled or payloads missing')
                    impl=decoded['pcm']['decoder_settings']['implementation']['value']
                    if report['implementation'] is None:report['implementation']=impl
                    require(impl==report['implementation'],'candidate implementation changed')
                    samples=(root/'pcm/pcm.f32le').read_bytes()
                    require(len(samples)==8192*len(rows) and hashlib.sha256(samples).hexdigest()==decoded['pcm']['sha256'],'PCM frame count/hash mismatch')
                    state=DrcReference(cookie(rate,**options),options);cursor=0
                    for index,kind,_,seq in batch:
                        length=len(seq);selected=rows[cursor:cursor+length];parts=generated[cursor:cursor+length]
                        for row,(_,truth),case in zip(selected,parts,seq):state.check(row,truth,case,rate)
                        raw=samples[cursor*8192:(cursor+length)*8192]
                        record=dict(required[rate,index],packet_state_sha256=digest(structure(selected)),drc_state_sha256=digest(drc_structure(selected)),
                                    pcm_sha256=hashlib.sha256(raw).hexdigest(),passed=True,**fingerprints(flatten(selected)))
                        if reference:
                            old=previous[rate,index]
                            for key,value in record.items():require(value==old[key],'cross-build DRC PCM stage differs: '+key)
                        else:
                            decoder=Decoder();expected=[v for _,truth in parts for v in decoder.decode(oracle_truth(truth))]
                            metrics=compare_pcm(struct.unpack('<'+str(len(raw)//4)+'f',raw),expected)
                            record['pcm_metrics']=metrics
                            report['pcm_metrics']['max_absolute_error']=max(report['pcm_metrics']['max_absolute_error'],metrics['max_absolute_error'])
                            report['pcm_metrics']['max_ulp']=max(report['pcm_metrics']['max_ulp'],metrics['max_ulp'])
                            report['pcm_metrics']['failed_samples']+=metrics['failed_samples']
                            if not metrics['passed']:
                                record['passed']=False;report['sequences'].append(record)
                                raise AssertionError('independent DRC-off PCM exceeds original tolerance')
                        report['sequences'].append(record);cursor+=length
                print(f'DRC PCM {rate}: {len(report["sequences"])}',file=sys.stderr,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--reference-report',type=Path)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    fingerprint=sha256_file(args.binary)
    report=dict(schema_version=1,passed=False,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),
                binary_sha256=fingerprint,rules_version='apac-drc-off-state-v1',vector_manifest_sha256=manifest()['sha256'],
                mode='bit_exact_replay' if args.reference_report else 'independent_math',atol=1e-6,rtol=1e-5,implementation=None,
                failure_directory=str(args.report.with_suffix('.failures')),sequences=[],errors=[],
                pcm_metrics=None if args.reference_report else dict(max_absolute_error=0.,max_ulp=0,failed_samples=0))
    reference=json.loads(args.reference_report.read_text(encoding='utf-8')) if args.reference_report else None
    try:
        if reference:
            require(reference['passed'] and reference['mode']=='independent_math' and len(reference['sequences'])==COUNT and not reference['errors'],'incomplete independent reference')
            for k in ('code_commit','source_sha256','rules_version','vector_manifest_sha256','atol','rtol'):require(report[k]==reference[k],'reference identity differs: '+k)
        validate(args.binary.resolve(),report,reference)
        require(len(report['sequences'])==COUNT,'required DRC PCM sequence missing')
        require(sha256_file(args.binary)==fingerprint and source_digest()==report['source_sha256'],'source or binary changed during acceptance')
        report['passed']=True
    except Exception as error:report['errors'].append(str(error))
    report['sequences'].sort(key=lambda r:(r['rate'],r['index']))
    write_json(args.report,report)
    print(json.dumps(dict(passed=report['passed'],sequences=len(report['sequences']),metrics=report['pcm_metrics'],errors=report['errors'])))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
