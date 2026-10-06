#!/usr/bin/env python3
"""Package qualified BWE2 gains while preserving the original LSF sources."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct

DATA = Path(__file__).resolve().parents[1]/'data'
MEASURED_FILE = 'bwe2-gains-measured-v1.json'
FORMAT_FILE = 'bwe2-format-v1.json'
PROFILE = 'apac-bwe2-gains-measured-v1'
WORDS_SHA256 = 'b4089e46f670df5bb84923eba409db45b7b117f5f910c816b48de197cdc5fe5d'
TABLES_SHA256 = '651850263d6adf1c2e5c2285910dc1fd6f0921293b6c0e7578a0cb780384a661'
CANDIDATE_SHA256 = '635013a9af09c886571d173202d83a8fc23d414624c0b79e74dc5c69242af397'
VALIDATION_SHA256 = '58234d783b9a03deae9bf50c0609350b2cb39f51db180b746d5fd99177fcca8d'
COMPARISON_SHA256 = 'f0fb171bfa9cadb2a660c8dcbc2da823fcbb2626b1d8bff70ecaf9da3705087a'
ORIGINAL_SOURCE = dict(component='AudioCodecs 7.0',system='macOS 27.0 / 26A428',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    architecture='x86_64',method='shared encoder/decoder LSF and excitation-gain wire dictionaries')
SOURCE = dict(
    method='public AudioConverter PCM black-box reconstruction with an independently inferred decimal grid',
    experiment='bwe2-blackbox-v1',
    measurement_code_commit='8de23429ad2d45a07814b09805623ddec5c887dc',
    measurement_used_uncommitted_tool_extension=True,
    packaged_tool_commit='9468a2e246d1728a9924be690d9b4719772ec18d',
    policy='bwe2-pcm-observability-v1',
    candidate_tool_fingerprint='8ea7997360002d6aa7262fe35eb4962f9056c66499c8c7278d3a4da93b76bf5a',
    validation_tool_fingerprint='253de20f5961a89417e61ee012879a0f2d6bb8885b9a2b3bf5e52fd47938fd0f',
    binary_sha256='8878d0c03c0889a70c7d352ce4a084e0f495478239f6c40c58cb3663ee302500',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428', architecture='arm64', sample_rate=48000,
    candidate_sha256=CANDIDATE_SHA256, validation_sha256=VALIDATION_SHA256,
    comparison_sha256=COMPARISON_SHA256, candidate_frozen_before_validation=True,
    comparison_after_validation=True, old_values_used_in_discovery=False,
    decimal_grid_assumed=True, decimal_grid_step='0.00001',
    grid_qualification='inferred from discovery PCM and tested with independent held-out inputs before reference comparison',
    independent_validation_cases=512, absolute_scale_controls=8,
    batch_native_calls_before_comparison=2340, unresolved_entries=0,
)


def require(condition, message):
    if not condition:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode()


def measured_words(measurement):
    require(measurement.get('schema_version')==1 and measurement.get('profile')==PROFILE,'incompatible measured gain schema')
    require(measurement.get('count')==64 and measurement.get('storage')=='index-order-float32-bits','measured gain scope differs')
    require(measurement.get('source')==SOURCE,'measured gain provenance differs')
    words=measurement.get('excitation_gains_f32')
    require(isinstance(words,list) and len(words)==64,'measured gains need 64 entries')
    require(all(type(w) is int and 0<=w<=0xffffffff for w in words),'invalid Float32 word')
    values=[struct.unpack('<f',struct.pack('<I',w))[0] for w in words]
    require(all(math.isfinite(v) and v>0 for v in values) and all(a<b for a,b in zip(values,values[1:])),
            'gain values must be finite positive and strictly increasing')
    require(sha(struct.pack('<64I',*words))==measurement.get('gains_sha256')==WORDS_SHA256,'measured gain digest differs')
    return words


def from_evidence(candidate_raw,validation_raw,comparison_raw):
    require(sha(candidate_raw)==CANDIDATE_SHA256,'unverified frozen gain candidate')
    require(sha(validation_raw)==VALIDATION_SHA256,'unverified gain validation')
    require(sha(comparison_raw)==COMPARISON_SHA256,'unverified gain comparison')
    candidate,validation,comparison=map(json.loads,(candidate_raw,validation_raw,comparison_raw))
    require(candidate['kind']=='frozen-bwe2-gain-candidate' and candidate['all_determined'] is True,
            'gain candidate is incomplete')
    require(candidate['policy']==SOURCE['policy'] and candidate['hypothesis']['decimal_grid']==SOURCE['decimal_grid_step'],
            'gain discovery policy differs')
    require(candidate['producer']['sha256']==SOURCE['candidate_tool_fingerprint']
            and validation['producer']['sha256']==SOURCE['validation_tool_fingerprint'],'gain producer differs')
    for document in (candidate,validation):
        native=document['status']['native_identity']
        require(all(native[k]==SOURCE[k] for k in ('binary_sha256','component_sha256','architecture')),
                'gain native identity differs')
    require(validation['kind']=='bwe2-gain-heldout-validation' and validation['all_passed'] is True
            and validation['candidate']['sha256']==CANDIDATE_SHA256,'gain validation is incomplete')
    checks,controls=validation['checks'],validation['controls']
    coverage={(i,s,g) for i in range(64) for s in (1001,1002,1003,1004) for g in (128,129)}
    require(len(checks)==512 and {(r['index'],r['seed'],r['carrier_gain']) for r in checks}==coverage
            and all(r['passed'] is True for r in checks),'gain symbol validation coverage differs')
    require(len(controls)==8 and {(r['seed'],r['carrier_gain']) for r in controls}=={(s,g) for s in (1001,1002,1003,1004) for g in (128,129)}
            and all(r['passed'] is True for r in controls),'gain scale validation coverage differs')
    require(comparison['kind']=='gain-only-reference-comparison' and comparison['eligible_gain'] is True
            and comparison['candidate_sha256']==CANDIDATE_SHA256 and comparison['validation_sha256']==VALIDATION_SHA256
            and comparison['compared']==comparison['matched']==64 and comparison['mismatch_indices']==[]
            and comparison['lsf_compared'] is False,'gain comparison is incomplete')
    require(candidate['created']<validation['created']<comparison['created'],'gain freeze/validation/comparison order differs')
    rows=candidate['rows']
    require(len(rows)==64,'gain candidate dimensions differ')
    for i,row in enumerate(rows):
        require(row['index']==i and row['determined'] is True and row['word'] is not None
                and row['candidates_f32']==[row['word']],'gain candidate remains ambiguous')
    value=dict(schema_version=1,profile=PROFILE,count=64,storage='index-order-float32-bits',
        source=copy.deepcopy(SOURCE),gains_sha256=WORDS_SHA256,excitation_gains_f32=[r['word'] for r in rows])
    measured_words(value)
    return value


def load_measurement():
    value=json.loads((DATA/MEASURED_FILE).read_bytes());measured_words(value);return value


def regenerate(stored,measurement):
    require(stored.get('schema_version')==1 and stored.get('format_profile')=='apac-bwe2-format-v1','format scope differs')
    result=copy.deepcopy(stored)
    result['excitation_gains_f32']=list(measured_words(measurement))
    semantic={name:result[name] for name in ('lsf_codebooks_f32','excitation_gains_f32')}
    actual=sha(json.dumps(semantic,sort_keys=True,separators=(',',':')).encode())
    require(actual==result['tables_sha256']==TABLES_SHA256,'BWE2 semantic digest differs outside measured gains')
    original=result['source']
    require(original.get('original_observation',original)==ORIGINAL_SOURCE,'original LSF provenance differs')
    if 'original_observation' not in original:original=dict(original_observation=original)
    original.update(method='mixed sources with measured excitation-gain replacement',
        remaining_tables=dict(lsf_codebooks_f32='original_observation'),
        gain_replacement=dict(source_file=MEASURED_FILE,source_sha256=sha(json_bytes(measurement)),
                              gains_sha256=WORDS_SHA256,method=SOURCE['method']))
    result['source']=original
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check',action='store_true');action.add_argument('--write',action='store_true')
    for name in ('candidate','validation','comparison'):parser.add_argument('--'+name,type=Path)
    args=parser.parse_args();inputs=(args.candidate,args.validation,args.comparison)
    if any(p is not None for p in inputs) and not all(p is not None for p in inputs):
        parser.error('--candidate, --validation and --comparison must be supplied together')
    measurement=from_evidence(*(p.read_bytes() for p in inputs)) if args.candidate else load_measurement()
    stored=json.loads((DATA/FORMAT_FILE).read_bytes())
    documents=((MEASURED_FILE,measurement),(FORMAT_FILE,regenerate(stored,measurement)))
    for name,value in documents:
        raw=json_bytes(value)
        if args.check:require((DATA/name).read_bytes()==raw,name+' differs from measured source')
        else:(DATA/name).write_bytes(raw)
        print(name,sha(raw))


if __name__=='__main__':main()
