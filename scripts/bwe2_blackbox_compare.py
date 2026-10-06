#!/usr/bin/env python3
"""Separate final gain-only comparison; never imported by discovery."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from bwe2_blackbox_wire import ROOT,canonical,digest
from bwe2_blackbox_capture import atomic


def compare(out,candidate_path,validation_path,reference_path=None):
    candidate_raw=candidate_path.read_bytes();validation_raw=validation_path.read_bytes()
    candidate=json.loads(candidate_raw);validation=json.loads(validation_raw)
    if candidate.get('kind')!='frozen-bwe2-gain-candidate' or not candidate.get('all_determined'):
        raise ValueError('only complete frozen gain candidates may be compared')
    if validation.get('kind')!='bwe2-gain-heldout-validation' or not validation.get('all_passed'):
        raise ValueError('independent validation has not passed')
    if validation['candidate']!=dict(file=candidate_path.name,sha256=digest(candidate_raw)):
        raise ValueError('validation is not bound to this candidate')
    rows=candidate['rows'];checks=validation['checks'];controls=validation['controls']
    if [r['index'] for r in rows]!=list(range(64)) or any(not r['determined'] or len(r['candidates_f32'])!=1 or r['word']!=r['candidates_f32'][0] for r in rows):
        raise ValueError('incomplete or ambiguous candidate entries')
    expected={(i,s,g) for i in range(64) for s in (1001,1002,1003,1004) for g in (128,129)}
    if len(checks)!=512 or {(r['index'],r['seed'],r['carrier_gain']) for r in checks}!=expected or any(not r['passed'] for r in checks+controls):
        raise ValueError('validation coverage is incomplete')
    if len(controls)!=8 or {(r['seed'],r['carrier_gain']) for r in controls}!={(s,g) for s in (1001,1002,1003,1004) for g in (128,129)}:
        raise ValueError('missing absolute-scale controls')
    db=sqlite3.connect(f'file:{(out/"state.sqlite3").resolve()}?mode=ro',uri=True)
    try:
        for row in checks:
            state=db.execute("SELECT MIN(started) FROM attempts WHERE key=? AND status='success'",(row['key'],)).fetchone()[0]
            if state is None or not candidate['created']<state<validation['created']:
                raise ValueError('held-out evidence was not collected after candidate freezing')
    finally:db.close()
    # No reference file is opened before all prerequisite checks above pass.
    path=ROOT/'data/bwe2-format-v1.json' if reference_path is None else reference_path
    raw=path.read_bytes()
    reference=json.loads(raw)['excitation_gains_f32']
    if len(reference)!=64:raise ValueError('reference gain count differs')
    mismatches=[i for i,r in enumerate(rows) if r['word']!=reference[i]]
    return dict(schema_version=1,kind='gain-only-reference-comparison',created=time.time(),
        candidate_sha256=digest(candidate_raw),validation_sha256=digest(validation_raw),
        reference_sha256=digest(raw),compared=64,matched=64-len(mismatches),mismatch_indices=mismatches,
        eligible_gain=not mismatches,lsf_compared=False,
        qualification='gain reconstruction uses a decimal-grid hypothesis frozen from public PCM before comparison')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--validation',type=Path,required=True)
    args=parser.parse_args()
    result=compare(args.out,args.candidate,args.validation)
    target=args.out/'analyses'/('comparison-'+digest(canonical(result))[:16]+'.json')
    atomic(target,canonical(result))
    print(json.dumps(dict(path=str(target),**result),indent=2))
    if not result['eligible_gain']:raise SystemExit(1)


if __name__=='__main__':main()
