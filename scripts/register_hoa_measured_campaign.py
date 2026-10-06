#!/usr/bin/env python3
"""Register one fully qualified order from a frozen black-box campaign.

This is a separate production-source step, never imported by discovery. Without
--write it prepares and checks all replacements but leaves source files alone.
The output report is not final application acceptance: run the generators and
Rust regressions before marking the order applied in the campaign.
"""
import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import pprint
import re

from hoa_blackbox_lib.common import ROOT, TARGETS, canonical, digest, require
from hoa_blackbox_lib.store import Store, atomic_file, writer_lock
from hoa_blackbox_lib.campaign import read as read_campaign
import hoa_measured_high_order_sources as registry


def values_sha(value):
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()


def filename(key):
    order,q,mode,book=key
    suffix=f'-book{book}' if mode==2 else f'-cluster{book}' if mode==4 else ''
    return f'hoa-salient-order{order}-q{q}-mode{mode}{suffix}-measured-v1.json'


def audit_mode1_validation(checks, precision):
    """Require normal-packet carrier holdout as well as all-symbol coverage."""
    indexed = {check['label']: check for check in checks}
    require(len(indexed) == len(checks), 'duplicate mode-1 validation label')
    levels = 1 << precision
    zero = levels // 2
    expected = {}
    for gain in (128, 129):
        for symbol in range(levels):
            expected[f'symbol:{gain}:{symbol}'] = (gain, 0)
        for i in range(8):
            expected[f'mixed:{gain}:{i}'] = (gain, 0)
        for symbol in (0, zero-1, zero, levels-1):
            expected[f'padding:{gain}:{symbol}'] = (gain, 0)
        for symbol in (0, zero//2, zero, 3*zero//2, levels-1):
            expected[f'line1-symbol:{gain}:{symbol}'] = (gain, 1)
    for i in range(4):
        expected[f'line1-mixed:{i}'] = (129, 1)
    require(expected.keys() <= indexed.keys(), 'mode-1 normal-packet validation coverage is incomplete')
    for label, (gain, line) in expected.items():
        check = indexed[label]
        require((check.get('gain'), check.get('line')) == (gain, line),
                'mode-1 validation carrier or gain differs')
        if line == 0:
            require(check.get('pcm_bit_identical_to_mode0') is True,
                    'mode-1 primary carrier differs from mode 0')
        else:
            ratio = check.get('max_heldout_rms_to_limit_ratio')
            require(type(ratio) in (int, float) and math.isfinite(ratio) and 0 <= ratio <= 1,
                    'mode-1 held-out RMS margin is missing or exceeded')
        if label.startswith('padding:'):
            require(check.get('padding_bit_identical') is True,
                    'mode-1 padding or fresh decoder check is missing')


def audit_order(root,order):
    config=read_campaign(root)
    require(config['orders'][str(order)]['status']=='qualified',
            'order is not fully qualified; all 32 codebooks and four matrices are required')
    outputs,matrices,batches,candidates={}, {}, {}, {}
    totals=dict(native_calls=0,validation_items=0,unique_validation_observations=set())
    for q in range(6,10):
        store=Store(root/'batches'/f'order{order}-q{q}',readonly=True)
        try:
            require(store.config['tool_fingerprint']==config['tool_fingerprint']
                    and store.config['native_identity']==config['native_identity'],'producer identity differs')
            require(all(t['status']=='passed' for t in store.summary()['targets']),'unqualified target')
            store.audit_evidence()
            valid_keys={r[0] for r in store.db.execute("SELECT key FROM queries WHERE state='passed'")}
            def evidence(keys):
                require(keys and all(key in valid_keys for key in keys),'missing successful observation for a frozen result')
            calibration=store.stage('_shared','calibration')
            require(calibration and calibration['old_dictionary_consulted'] is False,'calibration is missing')
            for response in calibration['responses'].values():
                evidence(list(response['evidence'].values()))
                evidence([response['reference']])
            if q==6:
                preflight=store.stage('_shared','preflight')
                require(preflight and preflight['status']=='passed'
                        and len(preflight['checks'])==2*(order+1)**2,'mode-0 channel preflight is incomplete')
                evidence([c['evidence'] for c in preflight['checks']])
            stages={(r['target'],r['name']):r['rowid'] for r in store.db.execute('SELECT rowid,target,name FROM stages')}
            identity=config['native_identity']
            system=dict(line.split(':',1) for line in identity['os_version'].splitlines())
            source=dict(experiment='hoa-blackbox-orders4-10-v1', code_commit=store.config['code_commit'],
                tool_revision='uncommitted source snapshot identified by tool_fingerprint',
                tool_fingerprint=store.config['tool_fingerprint'],analysis_policy=calibration['profile'],
                policy_sha256=digest(canonical(calibration)),native_identity_sha256=digest(canonical(identity)),
                binary_sha256=identity['binary_sha256'],component='AudioCodecs 7.0',
                component_sha256=identity['component_sha256'],
                system_version=f'macOS {system["ProductVersion"].strip()} / {system["BuildVersion"].strip()}',
                architecture=identity['architecture'],candidate_frozen_before_comparison=True)
            if q>6:source['qualified_prior_sha256']=store.config['prior_sha256']
            batches[order,q]=source
            totals['native_calls']+=store.summary()['native_calls']
            for target in TARGETS:
                book=store.stage(target,'codebook');validation=store.stage(target,'validation');comparison=store.stage(target,'comparison')
                require(book and validation and comparison,'missing frozen stage')
                require(book['old_dictionary_consulted'] is False and book['order']==order and book['quantization_bits']==q,
                        'candidate identity differs')
                require(validation['status']=='passed' and comparison['eligible_codebook'] is True,
                        'codebook is not eligible')
                require(stages['_shared','calibration']<stages[target,'codebook']<stages[target,'validation']<stages[target,'comparison'],
                        'freeze, validation and comparison order differs')
                bsha,vsha,csha=(digest(canonical(v)) for v in (book,validation,comparison))
                require(validation['codebook_sha256']==comparison['codebook_sha256']==bsha
                        and comparison['validation_sha256']==vsha,'qualification binding differs')
                mode,number=book['mode'],book.get('book',book.get('cluster'))
                key=(order,q,mode,number)
                values=[[e['bit_length'],int(e['codeword'],2)] for e in book['entries']]
                for entry in book['entries']:evidence(entry['evidence'])
                record=dict(candidate=bsha,book_values=values_sha(values),validation=vsha,comparison=csha)
                if q==6 and mode in (2,3):
                    require(comparison.get('group_exact') is True,'coefficient group differs')
                    record.update(group_values=values_sha(book['coefficient_group']),layout=book['layout_sha256'])
                outputs[key]=record;candidates[key]=canonical(book)
                checks=validation['checks']
                evidence([c['evidence'] for c in checks])
                labels={c['label'] for c in checks}
                expected={f'symbol:{gain}:{symbol}'+(f':{positive}' if mode in (2,3) else '')
                          for gain in (128,129) for symbol in range(1<<q)
                          for positive in ((True,False) if mode==3 else (True,))}
                require(expected<=labels,'normal-length symbol coverage is incomplete')
                if mode==1:
                    audit_mode1_validation(checks,q)
                totals['validation_items']+=len(checks)
                totals['unique_validation_observations'].update(c['evidence'] for c in checks)
                if q==6 and mode==4:
                    matrix=store.stage(target,'matrix')
                    require(matrix and matrix['old_matrix_consulted'] is False
                            and comparison['eligible_matrix'] is True and comparison['matrix_exact'] is True,
                            'matrix is not eligible')
                    msha=digest(canonical(matrix))
                    require(validation['matrix_sha256']==comparison['matrix_sha256']==msha
                            and stages[target,'matrix']<stages[target,'validation'],'matrix binding differs')
                    entries=matrix['entries'];n=(order+1)**2
                    evidence([v['evidence'] for v in matrix['observations'].values()])
                    require(len(entries)==validation['matrix_qualified']==n*n and all(e['float32_bits'] is not None for e in entries),
                            'matrix remains partial')
                    matrices[order,number]=dict(candidate=msha,codebook_candidate=bsha,
                        matrix_values=values_sha([e['float32_bits'] for e in entries]),validation=vsha,comparison=csha,
                        max_half_width=max(e['empirical_half_width'] for e in entries),checks=len(checks))
                    candidates[order,number]=canonical(matrix)
        finally:
            store.close()
    require(len(outputs)==32 and len(matrices)==4,'order coverage incomplete')
    totals['unique_validation_observations']=len(totals['unique_validation_observations'])
    return outputs,matrices,batches,candidates,totals


def rust_sources(outputs,matrices,group_users):
    string=lambda v:json.dumps(v)
    rows=['// BEGIN HIGHER MEASURED SOURCES',
          'const HIGHER_CODEBOOKS: &[(usize, u8, usize, usize, &str, &str)] = &[']
    for key,record in sorted(outputs.items()):
        o,q,m,b=key
        rows.append(f'    ({o}, {q}, {m}, {b}, {string(filename(key))}, {string(record["book_values"])}),')
    rows+= ['];','const HIGHER_MATRIX_SHA256: &[(usize, usize, &str)] = &[']
    for (o,c),record in sorted(matrices.items()):rows.append(f'    ({o}, {c}, {string(record["matrix_values"])}),')
    rows+= ['];','const HIGHER_DIRECT_BOOKS: &[DirectBook] = &[']
    for key,record in sorted(outputs.items()):
        o,q,m,b=key
        if q!=6 or m not in (2,3):continue
        users=', '.join(f'({mode}, {book})' for mode,book in group_users[m,b])
        rows.append('    DirectBook { '+f'order: {o}, mode: {m}, book: {b}, file: {string(filename(key))}, '
                    +f'book_sha256: {string(record["book_values"])}, group_sha256: {string(record["group_values"])}, group_users: &[{users}]'+' },')
    rows+= ['];','// END HIGHER MEASURED SOURCES']
    return '\n'.join(rows)


def prepare(root,order):
    outputs,matrices,batches,candidates,totals=audit_order(root,order)
    for existing,incoming in ((registry.HIGHER_OUTPUTS,outputs),(registry.HIGHER_MATRICES,matrices),(registry.HIGHER_BATCH_SOURCES,batches)):
        for key,value in incoming.items():
            require(key not in existing or existing[key]==value,'existing registered source differs')
        existing.update(incoming)
    import generate_hoa_salient_measured as books
    import generate_hoa_salient_measured_matrix as transforms
    books=importlib.reload(books);transforms=importlib.reload(transforms)
    import generate_hoa_salient_measured_groups as groups
    from hoa_salient_format import DATA,format_name,shared_name,json_bytes,expand_format
    prepared={}
    measurements={q:[books.from_candidate(candidates[key]) for key in sorted(outputs) if key[1]==q] for q in range(6,10)}
    matrix_measurements=[transforms.from_candidate(candidates[key]) for key in sorted(matrices)]
    for q,items in measurements.items():
        for item in items:prepared[DATA/books.MEASURED_FILES[order,q,item['mode'],item['book']]]=json_bytes(item)
    for item in matrix_measurements:prepared[DATA/transforms.MEASURED_FILES[order,item['cluster']]]=json_bytes(item)
    stored={q:json.loads((DATA/format_name(order,q)).read_text()) for q in range(6,10)}
    shared=json.loads((DATA/shared_name(order)).read_text())
    before={q:expand_format(value,shared)['tables_sha256'] for q,value in stored.items()}
    for q in range(6,10):stored[q],shared=books.regenerate(stored[q],shared,measurements[q])
    stored,shared=transforms.regenerate(stored,shared,matrix_measurements)
    stored,shared=groups.regenerate(stored,shared,[m for m in measurements[6] if m['mode'] in (2,3)])
    after={q:expand_format(value,shared)['tables_sha256'] for q,value in stored.items()}
    require(before==after,'source replacement changed numerical semantics')
    for q,value in stored.items():prepared[DATA/format_name(order,q)]=json_bytes(value)
    prepared[DATA/shared_name(order)]=json_bytes(shared)
    registry_path=ROOT/'scripts/hoa_measured_high_order_sources.py'
    original=registry_path.read_text();function=original[original.index('def higher_source'):]
    header=original[:original.index('HIGHER_OUTPUTS =')]
    rendered=header+'\n\n'.join(f'{name} = {pprint.pformat(value,sort_dicts=True,width=110)}' for name,value in (
        ('HIGHER_OUTPUTS',registry.HIGHER_OUTPUTS),('HIGHER_MATRICES',registry.HIGHER_MATRICES),('HIGHER_BATCH_SOURCES',registry.HIGHER_BATCH_SOURCES)))
    prepared[registry_path]=(rendered+'\n\n\n'+function).encode()
    rust=ROOT/'crates/apac-core/build/salient.rs'
    text=rust.read_text();block=rust_sources(registry.HIGHER_OUTPUTS,registry.HIGHER_MATRICES,groups.GROUP_USERS)
    text,count=re.subn(r'// BEGIN HIGHER MEASURED SOURCES.*?// END HIGHER MEASURED SOURCES',lambda _:block,text,flags=re.S)
    require(count==1,'Rust registration marker missing')
    prepared[rust]=text.encode()
    report=dict(order=order,status='prepared',codebooks=32,matrices=4,coefficient_groups=3,
                semantic_digests_unchanged=True,semantic_digests=after,counts=totals,
                files={str(p.relative_to(ROOT)):digest(raw) for p,raw in prepared.items()})
    return prepared,report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--order',type=int,choices=range(4,11),required=True)
    p.add_argument('--out',type=Path,required=True,help='new preparation report')
    p.add_argument('--write',action='store_true')
    args=p.parse_args()
    require(not args.write,
            'higher-order source registration is disabled: production HOA support ends at order 3; '
            'omit --write for historical evidence review')
    require(not args.out.exists(),'report already exists')
    with writer_lock(args.campaign):
        prepared,report=prepare(args.campaign.resolve(),args.order)
        if args.write:
            for path,raw in prepared.items():atomic_file(path,raw)
            report['status']='applied_pending_validation'
        atomic_file(args.out,canonical(report))
    print(json.dumps(dict(order=args.order,status=report['status'],files=len(prepared),counts=report['counts'])))


if __name__=='__main__':main()
