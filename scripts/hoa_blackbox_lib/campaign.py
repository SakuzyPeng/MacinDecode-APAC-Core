"""Order-by-order campaign orchestration; discovery never reads reference tables."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from .common import (ROOT, TARGETS, DEFAULT_LIMITS, BudgetStop, EvidenceError, ExperimentError,
                     IdentityError, canonical, digest, file_digest, geometry, now, require,
                     tool_fingerprint)
from .store import Store, atomic_file, directory_bytes, writer_lock

PLAN = dict(orders=list(range(4,11)), precisions=[6,7,8,9], shard_probes=128,
            q6_targets=['mode1','mode4:0','mode2:0','mode2:1','mode3','mode4:1','mode4:2','mode4:3'],
            wider_targets=list(TARGETS), application_unit='whole_order', expected_codebooks=224,
            expected_matrices=28, expected_symbols=53760, expected_matrix_entries=158480)


def make_plan(orders=None):
    orders=list(range(4,11)) if orders is None else list(orders)
    require(orders and orders==sorted(set(orders)) and all(type(o) is int and 4<=o<=10 for o in orders),
            'campaign orders must be unique and increasing within 4–10')
    return dict(PLAN,orders=orders,expected_codebooks=32*len(orders),expected_matrices=4*len(orders),
                expected_symbols=7680*len(orders),expected_matrix_entries=sum(4*(o+1)**4 for o in orders))


def add_commands(sub):
    for name in ('campaign-run','campaign-resume','campaign-status'):
        p = sub.add_parser(name)
        p.add_argument('--out', type=Path, required=True)
        if name != 'campaign-status':
            p.add_argument('--binary', type=Path)
            p.add_argument('--jobs', type=int, choices=range(1,5))
            p.add_argument('--max-native-calls', type=int)
            p.add_argument('--max-evidence-mib', type=int)
            p.add_argument('--min-free-mib', type=int)
            p.add_argument('--max-total-native-calls',type=int)
            p.add_argument('--max-total-evidence-mib',type=int)
        if name == 'campaign-run':
            p.add_argument('--orders',type=int,nargs='+',choices=range(4,11),help='explicit ascending subset; default 4–10')
            p.add_argument('--conditioning-pilot',action='store_true',
                           help='stop after order9 q6 mode4:0 validation/comparison before expanding')
            p.add_argument('--evidence-campaign',type=Path,nargs='+',
                           help='import compatible raw observations from earlier campaigns, without candidates')


def read(root):
    path = Path(root)/'campaign.json'
    require(path.is_file(), 'campaign does not exist', EvidenceError)
    value = json.loads(path.read_text())
    require(value['schema_version'] == 1 and value['plan'] == make_plan(value['plan']['orders']), 'campaign plan changed', EvidenceError)
    return value


def save(root, value):
    atomic_file(Path(root)/'campaign.json', canonical(value))


def snapshot(store):
    paths = [ROOT/'scripts/hoa_blackbox.py', ROOT/'data/sq-codebooks.json']
    paths += sorted((ROOT/'scripts/hoa_blackbox_lib').glob('*.py'))
    store.set_meta('source_snapshot', {str(p.relative_to(ROOT)):store.blob(p.read_bytes()) for p in paths})


def create(args, limits):
    from .native import NativeBackend
    from .campaign_store import create_pool
    require(args.binary is not None, '--binary is required to start a campaign')
    plan=make_plan(args.orders)
    controlled_pilot=bool(getattr(args,'conditioning_pilot',False))
    require(not controlled_pilot or plan['orders'][0]==9,
            'conditioning pilot must start with order 9')
    total_limits = dict(max_calls=1000000 if args.max_total_native_calls is None else args.max_total_native_calls,
                       max_bytes=(262144 if args.max_total_evidence_mib is None else args.max_total_evidence_mib)*1024**2)
    require(all(v>0 for v in total_limits.values()), 'campaign total limits must be positive')
    backend = NativeBackend(args.binary, order=4)
    sources = [p.resolve() for p in (args.evidence_campaign or [])]
    for source in sources:
        old = read(source)
        require(old['native_identity'] == backend.identity, 'import campaign native identity differs', IdentityError)
        require(old['status'] != 'budget_exhausted', 'cannot bypass an exhausted campaign budget by importing it', BudgetStop)
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root/'batches').mkdir()
    create_pool(root,total_limits)
    config = dict(schema_version=1, created_utc=now(), code_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(), tool_fingerprint=tool_fingerprint(),
        native_identity=backend.identity, binary=str(backend.binary), plan=plan, limits=limits,
        total_limits=total_limits,evidence_device=root.stat().st_dev,
        evidence_campaigns=[str(p) for p in sources], jobs=args.jobs or 4, status='ready',
        orders={str(o):dict(status='pending',application='pending') for o in plan['orders']})
    if controlled_pilot:
        config['conditioning_pilot']=dict(order=9,precision=6,target='mode4:0',status='pending')
    save(root, config)


def status(root):
    root = Path(root).resolve()
    config = read(root)
    batches = []
    for path in sorted((root/'batches').glob('order*-q*')):
        store = Store(path, readonly=True)
        try:
            batches.append(store.summary())
        finally:
            store.close()
    db = sqlite3.connect((root/'evidence.sqlite3').as_uri()+'?mode=ro', uri=True)
    try:
        pool = dict(objects=db.execute('SELECT COUNT(*) FROM objects').fetchone()[0],
                    observations=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],
                    compressed_bytes=db.execute('SELECT COALESCE(SUM(stored_bytes),0) FROM objects').fetchone()[0])
    finally:
        db.close()
    return dict(status=config['status'], orders=config['orders'], batches=batches, pool=pool,
                native_calls=sum(b['native_calls'] for b in batches), limits=config['limits'],total_limits=config['total_limits'],
                added_bytes=directory_bytes(root), available_bytes=shutil.disk_usage(root).free,
                tool_fingerprint=config['tool_fingerprint'], native_identity=config['native_identity'])


def export_priors(order, q6):
    result = subprocess.run([sys.executable,'-B','-m','hoa_blackbox_lib.priors','--order',str(order),
                             '--evidence',str(q6)],cwd=ROOT/'scripts',capture_output=True,text=True,timeout=300)
    require(result.returncode == 0, 'qualified geometry export failed: '+result.stderr[-1600:], EvidenceError)
    return json.loads(result.stdout)


def open_batch(root, config, order, precision, priors=None):
    path = root/'batches'/f'order{order}-q{precision}'
    if path.exists():
        store = Store(path)
        require(store.config['tool_fingerprint'] == config['tool_fingerprint']
                and store.config['native_identity'] == config['native_identity'], 'campaign batch identity changed', IdentityError)
        return store
    batch = dict(schema_version=1, created_utc=now(), code_commit=config['code_commit'],
                 tool_fingerprint=config['tool_fingerprint'], native_identity=config['native_identity'],
                 binary=config['binary'], native_jobs=config['jobs'], limits=dict(config['limits']),
                 order=order, quantization_bits=precision, targets=list(TARGETS),
                 prior_sha256=digest(canonical(priors)) if priors else None,
                 campaign_root=str(root), evidence_device=config['evidence_device'],
                 shard_probes=PLAN['shard_probes'], limits_scope='planned_shard')
    store = Store.create(path,batch)
    snapshot(store)
    from .native import import_batch
    for source in config.get('evidence_campaigns',[]):
        previous = Path(source)/'batches'/path.name
        if previous.exists():
            import_batch(store,previous)
    if priors is not None:
        store.save_stage('_shared','priors',priors)
    return store


def compare_batch(path, targets, lock_fd):
    command = [sys.executable,'-B',str(ROOT/'scripts/hoa_blackbox.py'),'compare',
               '--out',str(path),'--targets',*targets]
    env = dict(os.environ, HOA_CAMPAIGN_LOCK_FD=str(lock_fd))
    result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,env=env,pass_fds=(lock_fd,))
    require(result.returncode in (0,1), 'comparison process failed: '+result.stderr[-1600:], EvidenceError)
    summary = json.loads(result.stdout)
    require('targets' in summary, 'comparison failed: '+str(summary), EvidenceError)
    return {r['target']:r['status'] for r in summary['targets']}


def authorize_batch(store):
    """Child compare inherits the campaign lock; ordinary batch writes refuse it."""
    if not store.config.get('campaign_root'):
        return
    try:
        fd = int(os.environ['HOA_CAMPAIGN_LOCK_FD'])
        actual = os.fstat(fd)
        expected = (Path(store.config['campaign_root'])/'runner.lock').stat()
        require((actual.st_dev,actual.st_ino) == (expected.st_dev,expected.st_ino), 'wrong campaign lock', EvidenceError)
    except (KeyError, ValueError, OSError) as error:
        raise EvidenceError('use campaign-resume for campaign batches') from error


def ready_for(target, priors):
    if target == 'mode1':
        return True
    if target.startswith('mode4:'):
        return target[-1] in priors['matrices']
    if target.startswith('mode2:'):
        return all(k in priors['groups'] for k in ('2:0','2:1'))
    return '3:0' in priors['groups']


def finish_conditioning_pilot(root, config, store, outcome):
    """Freeze the pilot receipt without opening any reference values."""
    pilot=config['conditioning_pilot']
    require((store.config['order'],store.config['quantization_bits'])==(9,6), 'pilot batch scope differs')
    target=pilot['target']
    try:
        require(outcome in ('passed','partial'), 'conditioning pilot target failed')
        book=store.stage(target,'codebook');matrix=store.stage(target,'matrix')
        valid=store.stage(target,'validation');boot=store.stage(target,'bootstrap')
        require(all((book,matrix,valid,boot)) and valid['status']=='passed','conditioning pilot is incomplete')
        require((book['order'],book['quantization_bits'],book['mode'],book['book'])==(9,6,4,0),
                'conditioning pilot candidate scope differs')
        require(valid['codebook_sha256']==digest(canonical(book))
                and valid['matrix_sha256']==digest(canonical(matrix)), 'pilot validation binding differs')
        from .maths import validate_words
        validate_words(book['entries'],64)
        policy=boot['inversion_policy']
        require(policy['policy']=='order9-10-condition128-gram-v1' and policy['order']==9
                and policy['condition_inf_limit']==128
                and policy['normalized_gram_residual_inf_limit']==1e-3
                and policy['inverse_residual_inf_limit']==1e-10
                and boot['condition_inf']<=128 and boot['inverse_residual_inf']<=1e-10
                and policy['normalized_gram_residual_inf']<=1e-3, 'pilot conditioning policy differs')
        entries=matrix['entries']
        require((matrix['order'],matrix['rows'],matrix['columns'],len(entries))==(9,100,100,10000),
                'pilot matrix dimensions differ')
        require(all(5e-8<=e['empirical_half_width']<2.5e-7 for e in entries), 'pilot matrix precision failed')
        unresolved=[e for e in entries if e['float32_bits'] is None]
        require(all(e['candidate_bits']==[0,0x80000000] for e in unresolved),
                'pilot matrix has uncertainty beyond signed zero')
        require(valid['matrix_qualified']==len(entries)-len(unresolved), 'pilot matrix qualification count differs')
        hashes=dict(store.db.execute('SELECT name,sha FROM stages WHERE target=?',(target,)))
        require(all(name in hashes for name in ('bootstrap','codebook','matrix','validation','comparison')),
                'pilot frozen comparison is missing')
        receipt=dict(status='passed',order=9,precision=6,target=target,tool_fingerprint=config['tool_fingerprint'],
                     native_identity_sha256=digest(canonical(config['native_identity'])),stage_hashes=hashes,
                     inversion_policy=policy,condition_inf=boot['condition_inf'],
                     inverse_residual_inf=boot['inverse_residual_inf'],
                     matrix_qualified=len(entries)-len(unresolved),matrix_total=len(entries),
                     unresolved_zero_indices=[[e['row'],e['column']] for e in unresolved],
                     original_zero_bits_assigned=False,comparison_outcome=outcome,
                     max_empirical_half_width=max(e['empirical_half_width'] for e in entries),
                     validation_checks=len(valid['checks']))
        raw=canonical(receipt)
        atomic_file(Path(root)/'conditioning-pilot.json',raw)
        pilot.update(status='passed',receipt_sha256=digest(raw))
        config['status']='pilot_complete'
    except ExperimentError as error:
        pilot.update(status='failed',error=str(error))
        config['orders']['9']['status']='pilot_failed'
        config['status']='pilot_failed'
    save(root,config)


def check_conditioning_pilot(root, config):
    pilot=config.get('conditioning_pilot')
    if pilot is None:
        return
    require((pilot['order'],pilot['precision'],pilot['target'])==(9,6,'mode4:0'), 'conditioning pilot scope changed')
    require(pilot['status'] in ('pending','passed'), 'conditioning pilot did not pass')
    if pilot['status']=='pending':
        return
    raw=(Path(root)/'conditioning-pilot.json').read_bytes()
    require(digest(raw)==pilot['receipt_sha256'], 'conditioning pilot receipt changed', EvidenceError)
    receipt=json.loads(raw)
    require(receipt['status']=='passed' and receipt['tool_fingerprint']==config['tool_fingerprint']
            and receipt['native_identity_sha256']==digest(canonical(config['native_identity'])),
            'conditioning pilot identity differs', IdentityError)
    db=sqlite3.connect((Path(root)/'batches/order9-q6/state.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        hashes=dict(db.execute('SELECT name,sha FROM stages WHERE target=?',('mode4:0',)))
    finally:db.close()
    require(hashes==receipt['stage_hashes'], 'conditioning pilot frozen stages changed', EvidenceError)


def run(args, progress):
    from .engine import Engine
    from .native import NativeBackend, Runner
    root = args.out.resolve()
    with writer_lock(root) as lock_fd:
        config = read(root)
        require(root.stat().st_dev == config['evidence_device'], 'campaign evidence volume changed', IdentityError)
        require(config['tool_fingerprint'] == tool_fingerprint(),
                'campaign tool or strategy changed; preserve old campaign evidence', IdentityError)
        check_conditioning_pilot(root,config)
        binary = args.binary or config['binary']
        backend = NativeBackend(binary,order=4)
        require(backend.identity == config['native_identity'], 'campaign native environment changed', IdentityError)
        updates = {}
        for arg,key,scale in (('max_native_calls','max_calls',1),('max_evidence_mib','max_bytes',1024**2),('min_free_mib','min_free',1024**2)):
            value = getattr(args,arg,None)
            if value is not None:
                require(value > 0 and (arg != 'min_free_mib' or value >= 1024), 'invalid campaign resource limit')
                updates[key] = value*scale
        config['limits'].update(updates)
        total_updates={}
        for arg,key,scale in (('max_total_native_calls','max_calls',1),('max_total_evidence_mib','max_bytes',1024**2)):
            value=getattr(args,arg,None)
            if value is not None:
                require(value>0,'campaign total limits must be positive')
                total_updates[key]=value*scale
        if total_updates:
            config['total_limits'].update(total_updates)
            db=sqlite3.connect(root/'evidence.sqlite3')
            with db:
                db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(config['total_limits']).decode(),'total_limits'))
            db.close()
        config['jobs'] = args.jobs or config['jobs']
        config['status'] = 'running'
        save(root,config)
        try:
            for order in config['plan']['orders']:
                state = config['orders'][str(order)]
                if state['application'] == 'applied':
                    continue
                if state['status'] == 'pilot_failed':
                    raise ExperimentError(f'order-{order} pilot failed; preserve evidence and revise the discovery strategy')
                if state['status'] == 'qualified':
                    config['status'] = 'awaiting_application'
                    save(root,config)
                    return status(root)
                state['status'] = 'running'
                save(root,config)
                progress(dict(order=order,stage='order_start',available_bytes=shutil.disk_usage(root).free))
                q6 = root/'batches'/f'order{order}-q6'
                for precision in config['plan']['precisions']:
                    priors = export_priors(order,q6) if precision > 6 else None
                    store = open_batch(root,config,order,precision,priors)
                    try:
                        if updates or total_updates:
                            store.set_limits(**(updates or dict(config['limits'])))
                        store.recover()
                        backend = NativeBackend(binary,precision,order)
                        runner = Runner(store,backend,jobs=config['jobs'])
                        engine = Engine(store,runner,progress)
                        if precision == 6:
                            engine.preflight()
                        targets = config['plan']['q6_targets'] if precision == 6 else config['plan']['wider_targets']
                        for target in targets:
                            rows = {r['target']:r['status'] for r in store.summary()['targets']}
                            pilot=config.get('conditioning_pilot')
                            pilot_target=pilot is not None and (order,precision,target)==(9,6,'mode4:0')
                            if rows[target] in ('passed','failed','partial','different','blocked'):
                                if pilot_target and pilot['status']=='pending':
                                    finish_conditioning_pilot(root,config,store,rows[target])
                                    return status(root)
                                continue
                            selected = ['mode2:0','mode2:1'] if target.startswith('mode2:') else [target]
                            if precision > 6 and not ready_for(target,priors):
                                for t in selected:
                                    store.job(t,'blocked','qualified six-bit geometry is unavailable')
                                continue
                            engine.run(targets=selected)
                            path = store.out
                            store.close()
                            store = None
                            outcome = compare_batch(path,selected,lock_fd)
                            store = Store(path)
                            runner = Runner(store,backend,jobs=config['jobs'])
                            engine = Engine(store,runner,progress)
                            progress(dict(order=order,precision=precision,stage='target_complete',targets={t:outcome[t] for t in selected}))
                            if pilot_target and pilot['status']=='pending':
                                finish_conditioning_pilot(root,config,store,outcome[target])
                                return status(root)
                            if order == 4 and precision == 6 and target in ('mode1','mode4:0') and outcome[target] != 'passed':
                                state['status'] = 'pilot_failed'
                                config['status'] = 'pilot_failed'
                                save(root,config)
                                return status(root)
                        store.set_meta('batch_status','complete' if all(r['status']=='passed' for r in store.summary()['targets']) else 'needs_review')
                    finally:
                        if store is not None:
                            store.close()
                summaries = [b for b in status(root)['batches'] if b['order'] == order]
                complete = len(summaries)==4 and all(all(t['status']=='passed' for t in b['targets']) for b in summaries)
                state['status'] = 'qualified' if complete else 'partial'
                state['native_calls'] = sum(b['native_calls'] for b in summaries)
                state['evidence_bytes'] = sum(b['added_bytes'] for b in summaries)
                save(root,config)
                if complete:
                    config['status'] = 'awaiting_application'
                    save(root,config)
                    return status(root)
            config['status'] = 'complete' if all(v['application']=='applied' for v in config['orders'].values()) else 'needs_review'
        except BaseException as error:
            config['status'] = 'budget_exhausted' if isinstance(error,BudgetStop) else 'interrupted' if isinstance(error,KeyboardInterrupt) else 'stopped'
            config['error'] = str(error)
            save(root,config)
            raise
        save(root,config)
        return status(root)


def record_application(root, order, report):
    """Called by the separately verified production-source application step."""
    root = Path(root).resolve()
    with writer_lock(root):
        config = read(root)
        require(config['orders'][str(order)]['status']=='qualified', 'order is not fully qualified')
        raw = Path(report).read_bytes()
        result = json.loads(raw)
        require(result['order']==order and result['status']=='passed'
                and result['codebooks']==32 and result['matrices']==4
                and result['semantic_digests_unchanged'] is True, 'application verification is incomplete')
        config['orders'][str(order)].update(application='applied',application_report_sha256=digest(raw))
        config['status']='ready'
        save(root,config)
