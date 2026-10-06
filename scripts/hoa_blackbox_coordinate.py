#!/usr/bin/env python3
"""Supplement mode-4 codebooks using qualified proportional PCM coordinates.

No original matrix bit pattern is assigned. The original matrix candidate stays
unresolved. Export, discovery and reference comparison run in separate processes.
This entry point is outside the frozen primary producer and has its own fingerprint.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys

from hoa_blackbox_lib.common import (ROOT, GAINS, BudgetStop, EvidenceError, ExperimentError,
    IdentityError, canonical, digest, file_digest, geometry, install_discovery_guard, now,
    require, tool_fingerprint as base_fingerprint)
from hoa_blackbox_lib.store import Store, atomic_file, directory_bytes, writer_lock
from hoa_blackbox_lib.campaign_store import CampaignStorage, create_pool
from hoa_blackbox_lib.native import NativeBackend, Runner, validate_public
from hoa_blackbox_lib.engine import Engine
from hoa_blackbox_lib.maths import inverse, infer_tree, subtract, vmul

POLICY='hoa-blackbox-proportional-coordinate-supplement-v3'
LEGACY_POLICY='hoa-blackbox-proportional-coordinate-supplement-v1'
LEGACY_POLICIES=(LEGACY_POLICY,'hoa-blackbox-proportional-coordinate-supplement-v2')
SCRIPT=Path(__file__).resolve()


def fingerprint():
    return digest(canonical(dict(base_producer=base_fingerprint(),supplement=file_digest(SCRIPT),policy=POLICY)))


def progress(value):
    print(json.dumps(value,ensure_ascii=False),file=sys.stderr,flush=True)


class CoordinateStore(Store):
    def __init__(self,out,readonly=False):
        check=sqlite3.connect((Path(out).resolve()/'state.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:config=json.loads(check.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        finally:check.close()
        require(config.get('coordinate_supplement') is True,'not a coordinate supplement batch')
        super().__init__(out,readonly)
        require(self.pool is not None,'coordinate batch needs a shared pool')


class CoordinateBackend(NativeBackend):
    def __init__(self,*args,**kwargs):
        self.expected_producer=fingerprint()
        super().__init__(*args,**kwargs)

    def check(self,force=False):
        super().check(force)
        require(fingerprint()==self.expected_producer,'supplement producer changed during capture',IdentityError)


def export_coordinates(source,order):
    """Separate process: qualify R from raw q6 differences, without exporting words or matrix bits."""
    store=Store(Path(source)/'batches'/f'order{order}-q6',readonly=True)
    try:
        store.audit_evidence()
        require(store.config['order']==order and store.config['quantization_bits']==6,'coordinate source geometry differs')
        transforms={}
        for cluster in range(4):
            target=f'mode4:{cluster}'
            book=store.stage(target,'codebook');valid=store.stage(target,'validation');comparison=store.stage(target,'comparison')
            matrix=store.stage(target,'matrix');boot=store.stage(target,'bootstrap')
            if not all((book,valid,comparison,matrix,boot)):continue
            if comparison['eligible_matrix']:continue
            unresolved=[e for e in matrix['entries'] if e['float32_bits'] is None]
            if not unresolved or any(e['candidate_bits'] != [0,0x80000000] for e in unresolved):continue
            require(book['old_dictionary_consulted'] is False and matrix['old_matrix_consulted'] is False
                    and valid['status']=='passed' and comparison['eligible_codebook'] is True,'unqualified coordinate source')
            require(valid['codebook_sha256']==comparison['codebook_sha256']==digest(canonical(book))
                    and comparison['validation_sha256']==digest(canonical(valid))
                    and valid['matrix_sha256']==comparison['matrix_sha256']==digest(canonical(matrix)),
                    'coordinate source bindings differ')
            rows=[subtract(o['coefficients'],boot['baseline']['coefficients']) for o in boot['row_observations']]
            require(rows==boot['scaled_matrix'],'coordinate directions differ from frozen observations')
            policy={}
            inv,condition,residual=inverse(rows,order=order,diagnostics=policy)
            require(inv==boot['inverse'],'coordinate inverse differs')
            for observation in [boot['baseline'],*boot['row_observations']]:
                require(store.query(observation['evidence']) is not None,'coordinate evidence missing')
            transforms[str(cluster)]=dict(scaled_matrix=rows,inverse=inv,condition_inf=condition,inverse_residual_inf=residual,
                inversion_policy=policy,
                source_quantization_bits=6,source_signed_coordinate_scale=book['signed_coordinate_scale'],
                source_bootstrap_sha256=digest(canonical(boot)),source_codebook_sha256=digest(canonical(book)),
                source_validation_sha256=digest(canonical(valid)),source_comparison_sha256=digest(canonical(comparison)),
                unresolved_zero_indices=[[e['row'],e['column']] for e in unresolved],
                original_matrix_bitwise_qualified=False,original_matrix_bit_patterns_exported=False,zero_bits_assigned=False)
        return dict(schema_version=1,profile=POLICY,order=order,transforms=transforms,
                    huffman_words_included=False,native_identity=store.config['native_identity'],
                    component_sha256=store.config['native_identity']['component_sha256'],
                    architecture=store.config['native_identity']['architecture'])
    finally:store.close()


def import_calibration(store,source):
    origin=Store(source,readonly=True)
    try:
        require(origin.config['native_identity']==store.config['native_identity'],'calibration native identity differs',IdentityError)
        require((origin.config['order'],origin.config['quantization_bits'])==(store.config['order'],store.config['quantization_bits']),
                'calibration geometry differs')
        cal=origin.stage('_shared','calibration')
        require(cal is not None,'primary calibration is not frozen yet')
        keys={key for value in cal['responses'].values() for key in value['evidence'].values()}
        count=0
        for key in sorted(keys):
            receipt=origin.query(key);require(receipt is not None,'calibration evidence incomplete',EvidenceError)
            request=json.loads(origin.db.execute('SELECT request FROM queries WHERE key=?',(key,)).fetchone()[0])
            validate_public({name:origin.read_blob(sha) for name,sha in receipt['artifacts'].items()},
                            request['signature']['frames'],store.config['quantization_bits'],store.config['order'])
            if store.query(key):continue
            for identity in receipt['artifacts'].values():
                obj=origin.db.execute('SELECT * FROM objects WHERE sha=?',(identity,)).fetchone()
                registered=store.external(Path(obj['path']),offset=obj['offset'],size=obj['size'],compressed=obj['kind'] in ('gzip','external-gzip'))
                require(registered==identity,'calibration object changed',EvidenceError)
            count+=store.imported_query(key,request,receipt)
        return count
    finally:origin.close()


class CoordinateEngine(Engine):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        require(self.priors is not None and self.priors['profile']==POLICY
                and self.priors['native_identity']==self.store.config['native_identity'],
                'coordinate prior identity differs',IdentityError)
        for target in self.store.config['targets']:
            require(target.startswith('mode4:'),'coordinate supplement supports mode 4 only')
            trans=self.priors['transforms'][target[-1]]
            require(trans['zero_bits_assigned'] is False and trans['original_matrix_bit_patterns_exported'] is False
                    and 'matrix_f32' not in trans and trans['source_quantization_bits']==6,
                    'original matrix bits are forbidden in coordinate priors',EvidenceError)
            rows=trans['scaled_matrix'];require(len(rows)==self.n and all(len(r)==self.n for r in rows),'coordinate dimensions differ')
            policy={}
            inv,condition,residual=inverse(rows,order=self.order,diagnostics=policy)
            require(inv==trans['inverse'] and condition==trans['condition_inf'] and residual==trans['inverse_residual_inf'],
                    'coordinate inverse binding differs',EvidenceError)
            if self.order in (9,10):
                require(trans.get('inversion_policy')==policy,
                        'coordinate conditioning policy binding differs',EvidenceError)

    def transform(self,target):
        return self.priors['transforms'][target[-1]]

    def codebook(self,target):
        saved=self.store.stage(target,'codebook')
        if saved is not None:return saved
        trans=self.transform(target);inv=trans['inverse']
        checks=[]
        for i,pattern in enumerate(('', '1','01','10')):
            trials=[]
            for gain,extra in ((128,0),(129,0),(128,256)):
                obs=self.observe(target,'coordinate-preflight',f'{i}:{gain}:{extra}',pattern,gain,extra,True)
                obs['coordinates']=vmul(obs['coefficients'],inv);trials.append(obs)
            error=max(abs(v-trials[0]['coordinates'][j]) for t in trials[1:] for j,v in enumerate(t['coordinates']))
            require(error<=self.repeat_eps,'coordinates are unstable across amplitude or padding')
            checks.append(dict(max_deviation=error,trials=trials))
        self.store.save_stage(target,'coordinate-preflight',dict(status='passed',checks=checks,prior_sha256=self.store.config['prior_sha256']))
        def query(pattern):
            obs=self.observe(target,'codebook',pattern,pattern)
            return vmul(obs['coefficients'],inv)[0],obs['evidence']
        entries,decisions,scale=infer_tree(query,coordinate=True,symbols=self.levels)
        expected=trans['source_signed_coordinate_scale']*(1 << (self.precision-6))
        require(scale==expected,'higher-precision quantization disagrees with the frozen q6 coordinate scale')
        result=dict(schema_version=1,profile='hoa-blackbox-codebook-v1',order=self.order,quantization_bits=self.precision,
                    mode=4,book=int(target[-1]),entries=entries,decisions=decisions,signed_coordinate_scale=scale,
                    policy_sha256=digest(canonical(self.calibration)),prior_sha256=self.store.config['prior_sha256'],
                    coordinate_prior_reused=True,matrix_reused=False,group_reused=False,
                    original_matrix_bit_patterns_used=False,zero_bits_assigned=False,old_dictionary_consulted=False)
        self.store.save_stage(target,'codebook',result)
        return result

    def validate(self,target,book,matrix=None):
        saved=self.store.stage(target,'validation')
        if saved is not None:return saved
        words={e['symbol']:e['codeword'] for e in book['entries']};cases=[]
        def add(values,gain,label,line=0,padding=0,equal_to=None):
            cases.append(dict(values=values,gain=gain,label=label,line=line,equal_to=equal_to,
                              payload=self.writer.coded(4,int(target[-1]),values,words,gain,line,padding)))
        for gain in GAINS:
            for q in range(self.levels):add(self.vector(q,0),gain,f'symbol:{gain}:{q}')
            for row in range(self.n):
                for q in (0,self.negative_half,self.positive_half):add(self.vector(q,row),gain,f'basis:{gain}:{row}:{q}')
        rng=random.Random(0x484f4143)
        mixtures=[[rng.randrange(self.levels) for _ in range(self.symbols)] for _ in range(8)]
        for i,values in enumerate(mixtures):
            for gain in GAINS:add(values,gain,f'mixed:{gain}:{i}')
        for gain in GAINS:
            for q in (0,self.zero-1,self.zero,self.levels-1):
                add(self.vector(q,0),gain,f'padding:{gain}:{q}',padding=128,equal_to=f'symbol:{gain}:{q}')
        for row in range(self.n):
            for q in (0,self.positive_half):add(self.vector(q,row),128,f'line1:{row}:{q}',line=1)
        for i,values in enumerate(mixtures[:4]):add(values,129,f'line1-mixed:{i}',line=1)
        checks=[];hashes={};inv=self.transform(target)['inverse']
        requests=[(target,'validation',c['label'],c['payload'],f'{POLICY}/{target}/validation/{c["label"]}') for c in cases]
        with self.runner.batch(requests) as observations:
            for case,(key,pcm) in zip(cases,observations):
                coordinates=vmul(self.estimate(pcm,case['gain'],case['line']),inv)
                expected=[(q-self.zero)/book['signed_coordinate_scale'] for q in case['values'][:self.n]]
                error=max(abs(a-b) for a,b in zip(coordinates,expected))
                require(error<=self.repeat_eps,'normal-length coordinate validation failed: '+case['label'])
                pcm_sha=digest(pcm.tobytes());hashes[case['label']]=pcm_sha
                if case['equal_to']:require(pcm_sha==hashes[case['equal_to']],'padding changed the PCM')
                checks.append(dict(label=case['label'],evidence=key,gain=case['gain'],line=case['line'],coordinate_error=error,
                                   padding_bit_identical=bool(case['equal_to'])))
                self.capture_progress(target,'validation')
        result=dict(status='passed',codebook_sha256=digest(canonical(book)),prior_sha256=self.store.config['prior_sha256'],
                    checks=checks,matrix_sha256=None,matrix_qualified=None,matrix_reused=False,coordinate_prior_reused=True,
                    zero_bits_assigned=False,original_matrix_bit_patterns_used=False,old_dictionary_consulted=False)
        self.store.save_stage(target,'validation',result)
        return result

    def run_coordinates(self):
        self.runner.backend.check(force=True);self.store.audit_evidence();self.calibration=self.calibrate()
        for target in self.store.config['targets']:
            current=self.store.db.execute('SELECT status FROM jobs WHERE target=?',(target,)).fetchone()[0]
            if current in ('passed','failed','different'):continue
            self.store.job(target,'running')
            try:
                book=self.codebook(target);self.validate(target,book)
                self.store.job(target,'validated');self.progress(dict(target=target,status='validated'))
            except (BudgetStop,EvidenceError,IdentityError):raise
            except ExperimentError as error:
                self.store.job(target,'failed',str(error));self.progress(dict(target=target,status='failed',error=str(error)))


def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for command in ('run','resume','status'):
        s=sub.add_parser(command);s.add_argument('--out',type=Path,required=True)
        if command=='run':
            s.add_argument('--binary',type=Path,required=True)
            s.add_argument('--source-campaign',type=Path,required=True)
            s.add_argument('--order',type=int,choices=range(4,11),required=True)
            s.add_argument('--quantization-bits',type=int,choices=(7,8,9),required=True)
        if command!='status':
            s.add_argument('--jobs',type=int,choices=range(1,5))
            s.add_argument('--max-total-native-calls',type=int)
            s.add_argument('--max-total-evidence-mib',type=int)
            s.add_argument('--min-free-mib',type=int)
    e=sub.add_parser('_export');e.add_argument('--source-campaign',type=Path,required=True);e.add_argument('--order',type=int,required=True)
    c=sub.add_parser('_compare');c.add_argument('--batch',type=Path,required=True);c.add_argument('--lock-fd',type=int,required=True)
    return p


def load(root):
    path=Path(root)/'coordinate.json'
    require(path.is_file(),'coordinate supplement root does not exist',EvidenceError)
    value=json.loads(path.read_text());require(value['profile'] in (POLICY,*LEGACY_POLICIES),'supplement profile differs',EvidenceError)
    return value


def save(root,value):atomic_file(Path(root)/'coordinate.json',canonical(value))


def create_root(args):
    root=args.out.resolve()
    if root.exists():return load(root)
    calls=250000 if args.max_total_native_calls is None else args.max_total_native_calls
    mib=65536 if args.max_total_evidence_mib is None else args.max_total_evidence_mib
    free=10240 if args.min_free_mib is None else args.min_free_mib
    require(calls>0 and mib>0 and free>=1024,'invalid supplement resource limits')
    backend=CoordinateBackend(args.binary,args.quantization_bits,args.order)
    root.mkdir(parents=True);(root/'batches').mkdir()
    total=dict(max_calls=calls,max_bytes=mib*1024**2)
    create_pool(root,total)
    config=dict(schema_version=1,profile=POLICY,tool_fingerprint=fingerprint(),base_tool_fingerprint=base_fingerprint(),
                native_identity=backend.identity,binary=str(backend.binary),source_campaign=str(args.source_campaign.resolve()),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                created_utc=now(),evidence_device=root.stat().st_dev,jobs=args.jobs or 1,
                limits=dict(max_calls=4096,max_bytes=512*1024**2,min_free=free*1024**2),total_limits=total,tasks={},status='ready')
    save(root,config);return config


def prepare_batch(root,config,order,precision):
    name=f'order{order}-q{precision}';path=root/'batches'/name
    source=Path(config['source_campaign']);primary=source/'batches'/name
    # Qualification and reference records are opened only by this separate exporter.
    result=subprocess.run([sys.executable,'-B',str(SCRIPT),'_export','--source-campaign',str(source),'--order',str(order)],
                          cwd=ROOT,capture_output=True,text=True,timeout=300)
    require(result.returncode==0,'coordinate export failed: '+result.stderr[-1600:],EvidenceError)
    priors=json.loads(result.stdout)
    require(priors['native_identity']==config['native_identity'],'coordinate native identity differs',IdentityError)
    targets=[f'mode4:{c}' for c in sorted(priors['transforms'],key=int)]
    require(targets,'no zero-ambiguous matrices with qualified coordinate observations')
    with_primary=Store(primary,readonly=True)
    try:require(with_primary.stage('_shared','calibration') is not None,'primary wider calibration is not frozen yet')
    finally:with_primary.close()
    config['tasks'].setdefault(name,dict(order=order,precision=precision,targets=targets,status='initializing'))
    require(config['tasks'][name]['targets']==targets,'supplement target set changed')
    save(root,config)
    settings=dict(schema_version=1,created_utc=now(),coordinate_supplement=True,targets=targets,order=order,quantization_bits=precision,
        native_identity=config['native_identity'],tool_fingerprint=config['tool_fingerprint'],code_commit=config['code_commit'],
        campaign_root=str(root),shard_probes=128,evidence_device=config['evidence_device'],native_jobs=config['jobs'],
        limits=dict(config['limits']),prior_sha256=digest(canonical(priors)),binary=config['binary'],analysis_policy=POLICY)
    store=CoordinateStore(path) if path.exists() else CoordinateStore.create(path,settings)
    try:
        require(store.config['prior_sha256']==settings['prior_sha256'] and store.config['tool_fingerprint']==settings['tool_fingerprint'],
                'unfinished supplement configuration differs',IdentityError)
        sources=[SCRIPT,ROOT/'scripts/hoa_blackbox.py',ROOT/'data/sq-codebooks.json',*sorted((ROOT/'scripts/hoa_blackbox_lib').glob('*.py'))]
        store.set_meta('source_snapshot',{str(p.relative_to(ROOT)):store.blob(p.read_bytes()) for p in sources})
        store.save_stage('_shared','priors',priors)
        imported=import_calibration(store,primary)
        store.set_meta('primary_calibration',dict(path=str(primary),raw_observations=imported))
    finally:store.close()
    config['tasks'][name]['status']='ready';save(root,config)
    return path


def status(root):
    root=Path(root).resolve();config=load(root);batches=[]
    for name in config['tasks']:
        s=CoordinateStore(root/'batches'/name,readonly=True)
        try:batches.append(s.summary())
        finally:s.close()
    return dict(status=config['status'],tasks=config['tasks'],batches=batches,native_calls=sum(b['native_calls'] for b in batches),
                added_bytes=directory_bytes(root),native_identity=config['native_identity'],tool_fingerprint=config['tool_fingerprint'],
                limits=config['limits'],total_limits=config['total_limits'],matrix_zero_bits_assigned=False)


def compare_child(path,fd):
    result=subprocess.run([sys.executable,'-B',str(SCRIPT),'_compare','--batch',str(path),'--lock-fd',str(fd)],
                          cwd=ROOT,capture_output=True,text=True,pass_fds=(fd,))
    require(result.returncode in (0,1),'coordinate comparison process failed: '+result.stderr[-1600:],EvidenceError)
    value=json.loads(result.stdout);require('targets' in value,'coordinate comparison failed: '+str(value),EvidenceError)
    return value


def discover(root,args,new_task=None):
    root=Path(root).resolve()
    with writer_lock(root) as fd:
        config=load(root)
        require(config['tool_fingerprint']==fingerprint(),'supplement producer changed',IdentityError)
        require(root.stat().st_dev==config['evidence_device'],'supplement volume changed',IdentityError)
        if getattr(args,'source_campaign',None):require(str(args.source_campaign.resolve())==config['source_campaign'],'source campaign differs')
        if getattr(args,'binary',None):
            backend=CoordinateBackend(args.binary,args.quantization_bits,args.order)
            require(backend.identity==config['native_identity'],'supplement native identity differs',IdentityError)
        config['jobs']=args.jobs or config['jobs']
        total_updates={}
        for attr,key,scale in (('max_total_native_calls','max_calls',1),('max_total_evidence_mib','max_bytes',1024**2)):
            value=getattr(args,attr,None)
            if value is not None:
                require(value>0,'total limits must be positive');total_updates[key]=value*scale
        if args.min_free_mib is not None:
            require(args.min_free_mib>=1024,'at least 1 GiB free space is required');config['limits']['min_free']=args.min_free_mib*1024**2
        config['total_limits'].update(total_updates)
        db=sqlite3.connect(root/'evidence.sqlite3')
        with db:db.execute("UPDATE meta SET value=? WHERE key='total_limits'",(canonical(config['total_limits']).decode(),))
        db.close();config['status']='running';save(root,config)
        try:
            if new_task:prepare_batch(root,config,*new_task)
            require(config['tasks'],'no supplement tasks declared; use run with an order and precision')
            for name,item in config['tasks'].items():
                if item['status'] in ('complete','failed'):continue
                if item['status']=='initializing':prepare_batch(root,config,item['order'],item['precision'])
                path=root/'batches'/name;store=CoordinateStore(path)
                try:
                    require(store.config['tool_fingerprint']==config['tool_fingerprint'],'batch producer changed',IdentityError)
                    if total_updates or args.min_free_mib is not None:store.set_limits(**config['limits'])
                    store.recover()
                    backend=CoordinateBackend(config['binary'],item['precision'],item['order'])
                    require(backend.identity==config['native_identity'],'native environment changed',IdentityError)
                    engine=CoordinateEngine(store,Runner(store,backend,jobs=config['jobs']),progress)
                    store.set_meta('batch_status','running')
                    engine.run_coordinates()
                    store.set_meta('batch_status','validated')
                finally:store.close()
                summary=compare_child(path,fd)
                item['status']='complete' if all(t['status']=='passed' for t in summary['targets']) else 'failed'
                final_store=CoordinateStore(path)
                try:final_store.set_meta('batch_status',item['status'])
                finally:final_store.close()
                save(root,config);progress(dict(order=item['order'],precision=item['precision'],status=item['status']))
            config['status']='complete' if all(t['status']=='complete' for t in config['tasks'].values()) else 'needs_review'
        except BaseException as error:
            config['status']='budget_exhausted' if isinstance(error,BudgetStop) else 'interrupted' if isinstance(error,KeyboardInterrupt) else 'stopped'
            config['error']=str(error);save(root,config);raise
        save(root,config);return status(root)


def main():
    args=parser().parse_args()
    if args.command=='_export':
        print(canonical(export_coordinates(args.source_campaign,args.order)).decode(),end='');return 0
    if args.command=='_compare':
        from hoa_blackbox_lib.reference import compare
        store=CoordinateStore(args.batch)
        try:
            actual=os.fstat(args.lock_fd);expected=(Path(store.config['campaign_root'])/'runner.lock').stat()
            require((actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino),'wrong supplement writer lock')
            require(store.config['tool_fingerprint']==fingerprint(),'supplement comparison producer differs',IdentityError)
            store.audit_evidence();compare(store,store.config['targets']);value=store.summary()
            print(json.dumps(value));return 0 if all(t['status']=='passed' for t in value['targets']) else 1
        finally:store.close()
    if args.command=='status':print(json.dumps(status(args.out)));return 0
    install_discovery_guard()
    try:
        if args.command=='run':create_root(args)
        result=discover(args.out,args,(args.order,args.quantization_bits) if args.command=='run' else None)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='complete' else 1
    except (ExperimentError,OSError,ValueError) as error:
        print(json.dumps(dict(status='stopped',error=str(error)),ensure_ascii=False));return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='interrupted')));return 130


if __name__=='__main__':raise SystemExit(main())
