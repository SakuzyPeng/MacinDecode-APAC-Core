#!/usr/bin/env python3
"""Read-only PCM reanalysis and a bounded public-encoder observability pilot."""
import argparse
import fcntl
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback

os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
# Installs the same target-dictionary access guard as the original discovery.
import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import Capture,atomic,native_identity,volume_identity
from bwe2_blackbox_math import pcm,spectrum,fit_envelope,envelope
from bwe2_blackbox_wire import ROOT,Writer,canonical,digest,source
from bwe2_lsf_refine import fit_fixed_gain,relative_factorization,condition,joint_envelope_fit

MIB=1024**2
FROZEN_GAIN='freeze-635013a9af09c886.json'
FROZEN_GAIN_SHA='635013a9af09c886571d173202d83a8fc23d414624c0b79e74dc5c69242af397'
LSF_PILOT='lsf-probe-1a8a04be0459864f.json'


def log(message,**data):print(json.dumps(dict(message=message,**data),ensure_ascii=False),flush=True)


class Experiment:
    def __init__(self,args):
        self.out=args.out.resolve();self.out.mkdir(parents=True,exist_ok=True)
        self.lock=(self.out/'writer.lock').open('a');fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.binary=args.binary.resolve();self.identity=native_identity(self.binary)
        path=self.out/'manifest.json'
        if path.exists():self.config=json.loads(path.read_bytes())
        else:
            if args.evidence is None or args.mount is None:raise ValueError('new experiment needs --mount and --evidence')
            pin=volume_identity(args.mount.resolve());evidence=args.evidence.resolve()
            if not evidence.is_relative_to(args.mount.resolve()) or evidence.exists():raise ValueError('evidence directory must be new on the mounted volume')
            evidence.mkdir(parents=True)
            atomic(evidence/'volume-pin.json',canonical(pin))
            self.config=dict(native_identity=self.identity,binary=str(self.binary),volume=pin,evidence=str(evidence),
                max_native_calls=4096,max_evidence_bytes=512*MIB,min_free_bytes=1024*MIB,timeout_seconds=30,
                source_observations=str(args.observations.resolve()),created=time.time(),
                code_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip())
            atomic(path,canonical(self.config))
        if self.config['native_identity']!=self.identity:raise RuntimeError('native identity changed')
        self.evidence=Path(self.config['evidence']);self.check()
        self.db=sqlite3.connect(self.out/'state.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY,label TEXT,request BLOB,status TEXT,native_credits INTEGER,started REAL,result BLOB)')
        self.db.execute("UPDATE commands SET status='interrupted' WHERE status='running'");self.db.commit()
        self.source_root=Path(self.config['source_observations'])
        old=json.loads((self.source_root/'manifest.json').read_bytes())
        if old['native_identity']!=self.identity or old['volume']!=self.config['volume']:raise RuntimeError('old observations belong to a different native environment or volume')
        self.pool=Path(old['evidence'])/'objects'
        self.source_db=sqlite3.connect(f'file:{self.source_root/"state.sqlite3"}?mode=ro',uri=True)
        paths=[ROOT/'scripts'/name for name in ('bwe2_lsf_probe.py','bwe2_lsf_refine.py','bwe2_encoder_settings.py','bwe2_blackbox.py','bwe2_blackbox_math.py','bwe2_blackbox_wire.py','bwe2_blackbox_capture.py')]
        self.producer={p.name:digest(p.read_bytes()) for p in paths}
        snapshots=self.out/'producers'/digest(canonical(self.producer));snapshots.mkdir(parents=True,exist_ok=True)
        for p in paths:
            if not (snapshots/p.name).exists():atomic(snapshots/p.name,p.read_bytes())

    def check(self):
        pin=self.config['volume'];mount=Path(pin['mount'])
        if not mount.is_mount() or not self.evidence.is_dir() or self.evidence.stat().st_dev!=mount.stat().st_dev:raise RuntimeError('external evidence volume disconnected')
        if volume_identity(mount)!=pin or json.loads((self.evidence/'volume-pin.json').read_bytes())!=pin:raise RuntimeError('external volume identity differs')
        if native_identity(self.binary)!=self.identity:raise RuntimeError('native identity changed')
        for p in (self.out,self.evidence):
            if __import__('shutil').disk_usage(p).free<self.config['min_free_bytes']+4*MIB:raise RuntimeError('free-space floor reached')

    def load_pcm(self,key):
        self.check()
        row=self.source_db.execute('SELECT receipt FROM observations WHERE key=?',(key,)).fetchone()
        if row is None:raise RuntimeError('missing original observation')
        receipt=json.loads(row[0]);h=receipt['artifacts']['native/pcm.f32le']
        raw=gzip.decompress((self.pool/(h+'.gz')).read_bytes())
        if digest(raw)!=h or receipt['native_identity']!=self.identity:raise RuntimeError('original evidence differs')
        spec=json.loads(self.source_db.execute('SELECT spec FROM uses WHERE key=? LIMIT 1',(key,)).fetchone()[0])
        return raw,spec,h

    def command(self,label,arguments,credits=0,allowed=(0,),executable=None):
        self.check()
        executable=self.binary if executable is None else Path(executable)
        identity=dict(arguments=arguments,native_identity=self.identity)
        if executable!=self.binary:identity['executable']=str(executable)
        request=canonical(identity)
        cached=self.db.execute("SELECT result FROM commands WHERE label=? AND request=? AND status='success' ORDER BY id DESC LIMIT 1",(label,request)).fetchone()
        if cached:
            value=json.loads(cached[0])
            for name,entry in value['files'].items():
                p=self.evidence/name
                if not p.is_file() or digest(p.read_bytes())!=entry['sha256']:raise RuntimeError('encoder evidence damaged')
            return value
        used=self.native_total()
        size=self.evidence_charge()
        if used+credits>self.config['max_native_calls'] or size+4*MIB>self.config['max_evidence_bytes']:raise RuntimeError('pilot budget exhausted')
        ident=self.db.execute('INSERT INTO commands(label,request,status,native_credits,started) VALUES (?,?,?,?,?)',
            (label,request,'running',credits,time.time())).lastrowid;self.db.commit()
        before={str(p.relative_to(self.evidence)) for p in self.evidence.rglob('*') if p.is_file()}
        records=self.evidence/'commands';records.mkdir(exist_ok=True)
        stem=records/f'{ident:04d}'
        atomic(stem.with_suffix('.request.json'),request)
        started=time.monotonic()
        try:
            proc=subprocess.Popen([str(executable),*map(str,arguments)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:stdout,stderr=proc.communicate(timeout=self.config['timeout_seconds'])
            except BaseException:
                proc.kill();stdout,stderr=proc.communicate()
                atomic(stem.with_suffix('.stdout'),stdout);atomic(stem.with_suffix('.stderr'),stderr)
                raise
            atomic(stem.with_suffix('.stdout'),stdout);atomic(stem.with_suffix('.stderr'),stderr)
            self.check()
            files={str(p.relative_to(self.evidence)):dict(sha256=digest(p.read_bytes()),bytes=p.stat().st_size)
                for p in self.evidence.rglob('*') if p.is_file() and str(p.relative_to(self.evidence)) not in before}
            result=dict(returncode=proc.returncode,seconds=time.monotonic()-started,files=files)
            status='success' if proc.returncode in allowed else 'failed'
            self.db.execute('UPDATE commands SET status=?,result=? WHERE id=?',(status,canonical(result),ident));self.db.commit()
            return result
        except BaseException:
            result=dict(error=traceback.format_exc())
            self.db.execute("UPDATE commands SET status='failed',result=? WHERE id=?",(canonical(result),ident));self.db.commit();raise

    def save(self,stage,value):
        self.check()
        if any(digest((ROOT/'scripts'/name).read_bytes())!=sha for name,sha in self.producer.items()):raise RuntimeError('analysis producer changed')
        value.update(created=time.time(),producer=self.producer,native_identity=self.identity,
            commands=dict(self.db.execute('SELECT status,COUNT(*) FROM commands GROUP BY status')),
            native_credits=self.db.execute('SELECT COALESCE(SUM(native_credits),0) FROM commands').fetchone()[0])
        directory=self.out/'results';directory.mkdir(exist_ok=True)
        path=directory/(stage+'-'+digest(canonical(value))[:16]+'.json');atomic(path,canonical(value))
        log('result saved',path=str(path));return path

    def native_total(self):
        total=self.db.execute('SELECT COALESCE(SUM(native_credits),0) FROM commands').fetchone()[0]
        decoder=self.out/'decoder'/'state.sqlite3'
        if decoder.exists():
            db=sqlite3.connect(f'file:{decoder}?mode=ro',uri=True)
            try:total+=db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            finally:db.close()
        return total

    def evidence_charge(self):
        total=sum(p.stat().st_size for p in self.evidence.rglob('*') if p.is_file())
        database=self.out/'decoder'/'state.sqlite3'
        folder=self.evidence/'decoder'
        if database.exists() and folder.exists():
            db=sqlite3.connect(f'file:{database}?mode=ro',uri=True)
            try:
                raw=db.execute('SELECT COALESCE(SUM(bytes),0) FROM objects').fetchone()[0]
                raw+=db.execute('SELECT COALESCE(SUM(reserved_bytes),0) FROM attempts').fetchone()[0]
            finally:db.close()
            stored=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
            total+=max(0,raw-stored)
        return total

    def close(self):
        self.source_db.close();self.db.close();self.lock.close()


def refine(exp):
    frozen=(exp.source_root/'analyses'/FROZEN_GAIN).read_bytes()
    if digest(frozen)!=FROZEN_GAIN_SHA:raise RuntimeError('qualified gain candidate hash differs')
    row=json.loads(frozen)['rows'][63]
    if row['index']!=63 or not row['determined']:raise RuntimeError('gain prior incomplete')
    gain=float(np.array([row['word']],dtype='<u4').view('<f4')[0])
    pilot_path=exp.source_root/'analyses'/LSF_PILOT
    old=json.loads(pilot_path.read_bytes());scale=old['calibration']['scale']
    rows=[]
    for original in old['estimates']:
        raw,spec,pcm_sha=exp.load_pcm(original['key'])
        if spec['parameters']!=[*original['pair'],63]:raise RuntimeError('probe parameters differ')
        values=spectrum(raw,scale)
        carrier=np.array(source('comb',spec['seed']),dtype=float)*2**((spec['gain']-100)/4)
        free=fit_fixed_gain(values,carrier,original['lsf'],gain,spacing=1.)
        bounded=fit_fixed_gain(values,carrier,free['lsf'],gain,spacing=50.)
        rows.append(dict(pair=original['pair'],key=original['key'],pcm_sha256=pcm_sha,
            old_transfer_rms=original['relative_transfer_rms'],free=free,bounded=bounded))
        if len(rows)%7==0:log('refined pairs',count=len(rows),last_pair=original['pair'])
    relative=relative_factorization([dict(pair=r['pair'],lsf=r['free']['lsf']) for r in rows])
    return dict(kind='fixed-gain-lsf-reanalysis',gain_prior_sha256=FROZEN_GAIN_SHA,gain_index=63,gain=gain,
                prior_analysis_sha256=digest(pilot_path.read_bytes()),estimates=rows,relative_model=relative,
                eligible_lsf=False,qualification='reanalysis of existing observations; no original LSF stage anchored')


def encoder(exp,count):
    settings=[dict(name='default-noise',signal='noise'),dict(name='default-sweep',signal='sweep'),
        dict(name='q0-noise',signal='noise',quality=0),dict(name='q127-noise',signal='noise',quality=127),
        *[dict(name=f'br{rate}-{signal}',signal=signal,bitrate=rate) for rate in (16000,32000,64000,128000) for signal in ('noise','sweep')]]
    rows=[]
    for spec in settings[:count]:
        path=exp.evidence/spec['name']
        args=['fixture','--out',str(path),'--layout','stereo','--sample-rate','48000','--duration','0.25','--seed','621',
              '--signals',spec['signal'],'--drc-configuration','none','--max-output-mib','2']
        for option in ('bitrate','quality'):
            if option in spec:args.extend(['--'+option,str(spec[option])])
        # fixture runs both a public encoder and its public reference decoder.
        result=exp.command(spec['name']+'-fixture',args,credits=2)
        row=dict(spec=spec,fixture_returncode=result['returncode'])
        if result['returncode']==0:
            manifest=json.loads((path/spec['signal']/'manifest.json').read_bytes())
            row['actual_encoder_settings']=manifest['actual_encoder_settings']
            row['cookie']=manifest['encoded']['cookie']
            packet_dir=exp.evidence/(spec['name']+'-packets')
            result=exp.command(spec['name']+'-dump',['dump',str(path/spec['signal']/'encoded.caf'),'--out',str(packet_dir),
                '--packets','32','--max-output-mib','2'])
            row['dump_returncode']=result['returncode']
            if result['returncode']==0:
                report=exp.evidence/(spec['name']+'-tns.jsonl')
                result=exp.command(spec['name']+'-syntax',['parse-packets',str(packet_dir),'--depth','tns','--packets','32',
                    '--output',str(report),'--max-output-mib','2'],allowed=(0,1,2))
                row['syntax_returncode']=result['returncode']
                row['syntax_file']=str(report.relative_to(exp.evidence))
                if report.exists():row['bwe2_syntax']=read_bwe_syntax(packet_dir,report)
        rows.append(row);log('encoder setting',name=spec['name'],returncode=row['fixture_returncode'])
    return dict(kind='public-encoder-pilot',cases=rows,full_lsf_identification=False)


def read_bwe_syntax(packet_dir,report):
    """Only flags and literal indices after a qualified TNS prefix are read."""
    packets=(packet_dir/'packets.bin').read_bytes()
    entries={r['packet_index']:r for r in map(json.loads,(packet_dir/'packets.jsonl').read_text().splitlines())}
    rows=[]
    for row in map(json.loads,report.read_text().splitlines()):
        record=row.get('report',{})
        if any(name in record for name in ('bwe2','channels_after_bwe2','conditioned_lsf','target_lpc')):
            raise RuntimeError('syntax discovery received target-value output')
        if not record.get('tns_complete'):
            rows.append(dict(packet_index=row['packet_index'],parsed=False,reason=record.get('stop_reason',row['status'])));continue
        item=entries[row['packet_index']]
        raw=packets[item['export_offset']:item['export_offset']+item['bytes']]
        if digest(raw)!=item['sha256'] or record['packet_sha256']!=item['sha256']:
            raise RuntimeError('syntax and packet identities differ')
        cursor=record['stop_bit_offset']
        def take(width):
            nonlocal cursor
            if cursor+width>len(raw)*8:raise RuntimeError('BWE2 syntax truncated')
            value=0
            for offset in range(cursor,cursor+width):value=value*2+((raw[offset//8]>>(7-offset%8))&1)
            cursor+=width;return value
        if len(record['channels'])!=2:raise RuntimeError('pilot expects a stereo CPE')
        flags=[take(1),take(1)];channels=[]
        for ch in range(2):
            ics=record['channels'][ch]['ics']
            active=bool(ics['max_sfb'] and (flags[ch] or (ch==1 and channels[0]['active'])))
            parameters=None
            if active:
                if flags[ch]:parameters=dict(lsf_indices=[take(9),take(9)],gains=[take(6) for _ in ics['window_groups']])
                else:
                    parameters=channels[0]['parameters']
                    if len(parameters['gains'])<len(ics['window_groups']):raise RuntimeError('undefined reused BWE2 gain groups')
            channels.append(dict(channel=ch,active=active,parameters=parameters))
        rows.append(dict(packet_index=row['packet_index'],parsed=True,flags=flags,channels=channels,end_bit=cursor))
    active=[c for row in rows if row['parsed'] for c in row['channels'] if c['active']]
    return dict(frames=len(rows),parsed_frames=sum(row['parsed'] for row in rows),
                active_channel_frames=len(active),unique_pairs=sorted({tuple(c['parameters']['lsf_indices']) for c in active}),records=rows)


def joint_holdout(exp):
    inputs=max((exp.out/'results').glob('refine-*.json'),key=lambda p:p.stat().st_mtime_ns)
    old=json.loads(inputs.read_bytes());gain=old['gain']
    pilot=json.loads((exp.source_root/'analyses'/LSF_PILOT).read_bytes());scale=pilot['calibration']['scale']
    train_pairs=[(203,j) for j in (0,17,401)]+[(i,314) for i in (0,17,401)]
    heldout_pairs=[(203,314)]+[(203,j) for j in (73,127,256,511)]+[(i,314) for i in (73,127,256,511)]
    plan=dict(train_pairs=train_pairs,heldout_pairs=heldout_pairs,carrier_gains=[128,129],seed=721,
        relative_transfer_rms_limit=1e-5,model='relative additive tables then known conditioning',
        note='experimental model validation; success alone cannot fix the original stage offset')
    plan_file=exp.out/'joint-plan.json'
    if plan_file.exists():
        if plan_file.read_bytes()!=canonical(plan):raise RuntimeError('joint trial plan changed')
    else:atomic(plan_file,canonical(plan))
    capture=Capture(exp.out/'decoder',exp.binary,exp.evidence/'decoder',Path(exp.config['volume']['mount']))
    collected={};spectra={}
    try:
        def collect(pair,replicate):
            measurements=[]
            for amplitude in (128,129):
                exp.check()
                count=sum(capture.status()['attempts'].values())+exp.db.execute('SELECT COALESCE(SUM(native_credits),0) FROM commands').fetchone()[0]
                if count>=exp.config['max_native_calls'] or exp.evidence_charge()+2*MIB>exp.config['max_evidence_bytes']:
                    raise RuntimeError('combined native/evidence budget exhausted')
                spec=dict(source='comb',seed=721,gain=amplitude,parameters=[*pair,63])
                key,raw=capture.probe(f'joint-{pair}-{amplitude}',spec,replicate)
                values=spectrum(raw,scale);carrier=np.array(source('comb',721),dtype=float)*2**((amplitude-100)/4)
                initial=fit_envelope(values,carrier)
                fit=fit_fixed_gain(values,carrier,initial['lsf'],gain,spacing=1.)
                measurements.append(dict(key=key,carrier_gain=amplitude,fit=fit))
                spectra[(pair,amplitude)]=(values,carrier)
            collected[pair]=measurements
            log('joint trial pair',pair=pair,calls=sum(capture.status()['attempts'].values()))
        for pair in train_pairs:collect(pair,'joint-lsf-v1')
        training=[dict(pair=r['pair'],lsf=r['free']['lsf']) for r in old['estimates']]
        training += [dict(pair=pair,lsf=np.mean([r['fit']['lsf'] for r in collected[pair]],axis=0).tolist()) for pair in train_pairs]
        model=relative_factorization(training)
        model_document=dict(model=model,gain=gain,plan=plan,training_analysis_sha256=digest(inputs.read_bytes()),
                            training_keys=[r['key'] for pair in train_pairs for r in collected[pair]])
        model_id=digest(canonical(model_document))
        directory=exp.out/'models';directory.mkdir(exist_ok=True)
        model_path=directory/(model_id+'.json')
        if not model_path.exists():atomic(model_path,canonical(dict(created=time.time(),**model_document)))
        frozen=json.loads(model_path.read_bytes())
        if canonical({k:frozen[k] for k in model_document})!=canonical(model_document):raise RuntimeError('frozen relative model changed')
        model_sha=digest(model_path.read_bytes())
        for pair in heldout_pairs:collect(pair,'joint-lsf-after-freeze-'+model_sha)
        first=np.array(model['first']);second=np.array(model['second'])
        checks=[]
        for pair in heldout_pairs:
            a=model['first_indices'].index(pair[0]);b=model['second_indices'].index(pair[1])
            predicted=condition(first[a]+second[b]);target=envelope(predicted)
            for measurement in collected[pair]:
                amplitude=measurement['carrier_gain'];values,carrier=spectra[(pair,amplitude)]
                bins=np.arange(1,512,2);actual=values[256+bins]/carrier[128+bins%128]
                estimated=gain/target[bins]
                rms=float(np.sqrt(np.mean((estimated/actual-1)**2)))
                checks.append(dict(pair=pair,carrier_gain=amplitude,key=measurement['key'],relative_transfer_rms=rms,
                    max_frequency_difference=float(np.max(abs(predicted-measurement['fit']['lsf']))),
                    passed=rms<=plan['relative_transfer_rms_limit']))
                first_capture=capture.db.execute("SELECT MIN(started) FROM attempts WHERE key=? AND status='success'",(measurement['key'],)).fetchone()[0]
                if first_capture is None or first_capture<=frozen['created']:raise RuntimeError('validation predates model freezing')
        result=dict(kind='relative-lsf-heldout',plan=plan,training_analysis=dict(file=inputs.name,sha256=digest(inputs.read_bytes())),
            collected=[dict(pair=pair,measurements=rows) for pair,rows in collected.items()],model=model,checks=checks,
            frozen_model=dict(file=str(model_path.relative_to(exp.out)),sha256=model_sha),
            all_passed=all(r['passed'] for r in checks),original_tables_identified=False,eligible_lsf=False,
            decoder_status=capture.status())
        capture.audit()
        return result
    finally:capture.close()


class CalibrationWriter(Writer):
    def program(self,spec):
        if spec.get('source')!='basis-mixture':return super().program(spec)
        values=[0]*1024
        for line,value in spec['coefficients']:
            if line%2!=1 or not 257<=line<768 or value not in (-1,1):raise ValueError('invalid basis mixture')
            if values[line]:raise ValueError('duplicate mixture coefficient')
            values[line]=value
        return [self.packet(values,gain=128),self.packet([0]*1024)]


def native_basis(exp):
    capture=Capture(exp.out/'decoder',exp.binary,exp.evidence/'decoder',Path(exp.config['volume']['mount']))
    capture.writer=CalibrationWriter()
    try:
        def probe(label,spec,replicate):
            exp.check()
            if exp.native_total()>=exp.config['max_native_calls'] or exp.evidence_charge()+2*MIB>exp.config['max_evidence_bytes']:
                raise RuntimeError('combined basis budget exhausted')
            return capture.probe(label,spec,replicate)
        lines=list(range(257,768,2));columns=[];references=[]
        for line in lines:
            key,raw=probe(f'basis-line-{line}',dict(source='tone',line=line,gain=128),'native-basis-v1')
            columns.append(pcm(raw)[:,0]/128);references.append(key)
            if len(columns)%32==0:log('native basis',columns=len(columns),calls=sum(capture.status()['attempts'].values()))
        matrix=np.array(columns).T
        gram=matrix.T@matrix
        inverse=np.linalg.solve(gram,matrix.T)
        model=dict(lines=lines,keys=references,matrix_f64_sha256=digest(matrix.astype('<f8').tobytes()),
            condition_number=float(np.linalg.cond(matrix)),left_inverse_residual=float(np.max(abs(inverse@matrix-np.eye(len(lines))))))
        if model['condition_number']>2 or model['left_inverse_residual']>1e-10:raise RuntimeError('PCM basis is poorly conditioned')
        directory=exp.out/'models';directory.mkdir(exist_ok=True)
        model_path=directory/('basis-'+digest(canonical(model))+'.json')
        if not model_path.exists():atomic(model_path,canonical(dict(created=time.time(),**model)))
        model_sha=digest(model_path.read_bytes());controls=[]
        for seed in (903,904,905,906):
            state=seed;coefficients=[]
            for line in lines:
                state=(1664525*state+1013904223)&0xffffffff
                coefficients.append([line,1 if state&0x80000000 else -1])
            key,raw=probe(f'basis-mixture-{seed}',dict(source='basis-mixture',coefficients=coefficients),model_sha)
            expected=matrix@np.array([128*v for _,v in coefficients])
            value=pcm(raw)[:,0]
            error=float(np.linalg.norm(value-expected)/np.linalg.norm(value))
            controls.append(dict(key=key,seed=seed,relative_pcm_rms=error,passed=error<=1e-6))
        if not all(r['passed'] for r in controls):raise RuntimeError('native basis failed independent mixtures')
        original=json.loads((exp.source_root/'analyses'/LSF_PILOT).read_bytes())
        gain_raw=(exp.source_root/'analyses'/FROZEN_GAIN).read_bytes()
        if digest(gain_raw)!=FROZEN_GAIN_SHA:raise RuntimeError('gain prior differs')
        gain_word=json.loads(gain_raw)['rows'][63]['word'];gain=float(np.array([gain_word],dtype='<u4').view('<f4')[0])
        baseline_key=exp.source_db.execute("SELECT key FROM uses WHERE label='anchor-base-19' LIMIT 1").fetchone()[0]
        baseline_raw,spec,baseline_sha=exp.load_pcm(baseline_key)
        if spec!=dict(source='comb',seed=19,gain=128):raise RuntimeError('baseline input differs')
        baseline=pcm(baseline_raw)[:,0];carrier=np.array(source('comb',19),dtype=float)*128
        rows=[]
        for row in original['estimates']:
            raw,spec,pcm_sha=exp.load_pcm(row['key'])
            value=pcm(raw)[:,0];difference=value-baseline
            coefficients=inverse@difference
            values=carrier.copy();values[lines]=coefficients
            fitted=fit_fixed_gain(values,carrier,row['lsf'],gain,spacing=1.)
            target=envelope(fitted['lsf'])[np.arange(1,512,2)]
            predicted=matrix@(carrier[128+np.arange(1,512,2)%128]*gain/target)
            error=float(np.linalg.norm(predicted-difference)/np.linalg.norm(difference))
            rows.append(dict(pair=row['pair'],key=row['key'],fit=fitted,relative_pcm_rms=error,
                projection_pcm_rms=float(np.linalg.norm(matrix@coefficients-difference)/np.linalg.norm(difference))))
        relative=relative_factorization([dict(pair=r['pair'],lsf=r['fit']['lsf']) for r in rows])
        capture.audit()
        return dict(kind='native-pcm-basis-reanalysis',basis=model,frozen_basis=dict(file=str(model_path.relative_to(exp.out)),sha256=model_sha),
            controls=controls,baseline=dict(key=baseline_key,pcm_sha256=baseline_sha),estimates=rows,relative_model=relative,
            eligible_lsf=False,decoder_status=capture.status())
    finally:capture.close()


def joint_envelope(exp):
    path=max((exp.out/'results').glob('joint-holdout-*.json'),key=lambda p:p.stat().st_mtime_ns)
    old=json.loads(path.read_bytes());model=old['model']
    source_path=exp.source_root/'analyses'/LSF_PILOT
    initial=json.loads(source_path.read_bytes());scale=initial['calibration']['scale']
    gain_raw=(exp.source_root/'analyses'/FROZEN_GAIN).read_bytes()
    if digest(gain_raw)!=FROZEN_GAIN_SHA:raise RuntimeError('gain prior differs')
    gain=float(np.array([json.loads(gain_raw)['rows'][63]['word']],dtype='<u4').view('<f4')[0])
    training=[];heldout=[]
    def observation(pair,raw,seed,amplitude,key):
        values=spectrum(raw,scale);carrier=np.array(source('comb',seed),dtype=float)*2**((amplitude-100)/4)
        bins=np.arange(1,512,2);transfer=values[256+bins]/carrier[128+bins%128]
        if np.any(transfer<=0):raise RuntimeError('nonpositive joint envelope observation')
        return dict(pair=pair,key=key,log_envelope=np.log(gain/transfer).tolist(),carrier_gain=amplitude)
    for row in initial['estimates']:
        raw,spec,_=exp.load_pcm(row['key']);training.append(observation(row['pair'],raw,spec['seed'],spec['gain'],row['key']))
    capture=Capture(exp.out/'decoder',exp.binary,exp.evidence/'decoder',Path(exp.config['volume']['mount']))
    try:
        for pair in old['collected']:
            destination=training if pair['pair'] in old['plan']['train_pairs'] else heldout
            for item in pair['measurements']:
                receipt=json.loads(capture.db.execute('SELECT receipt FROM observations WHERE key=?',(item['key'],)).fetchone()[0])
                raw=capture.read_blob(receipt['artifacts']['native/pcm.f32le'])
                destination.append(observation(pair['pair'],raw,721,item['carrier_gain'],item['key']))
    finally:capture.close()
    fitted=joint_envelope_fit(model,training,gain)
    checks=[]
    for row in heldout:
        a=fitted['first_indices'].index(row['pair'][0]);b=fitted['second_indices'].index(row['pair'][1])
        f=condition(np.array(fitted['first'][a])+fitted['second'][b])
        predicted=np.log(envelope(f)[np.arange(1,512,2)])
        error=float(np.sqrt(np.mean(np.expm1(np.array(row['log_envelope'])-predicted)**2)))
        checks.append(dict(pair=row['pair'],key=row['key'],carrier_gain=row['carrier_gain'],relative_transfer_rms=error,passed=error<=1e-5))
    return dict(kind='joint-envelope-diagnostic',model=fitted,training_keys=[r['key'] for r in training],checks=checks,
        all_passed=all(r['passed'] for r in checks),eligible_lsf=False,
        qualification='exploratory reanalysis on prior held-out inputs; no fresh post-freeze qualification or original offset anchor')


def main():
    bwe2_blackbox.install_discovery_guard()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('refine','encoder','settings','codec-settings','joint-holdout','native-basis','joint-envelope'))
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--observations',type=Path,required=True);p.add_argument('--mount',type=Path);p.add_argument('--evidence',type=Path)
    p.add_argument('--cases',type=int,default=12)
    args=p.parse_args()
    if not 1<=args.cases<=12:p.error('cases must be 1..12')
    exp=Experiment(args)
    try:
        if args.stage=='refine':value=refine(exp)
        elif args.stage=='encoder':value=encoder(exp,args.cases)
        elif args.stage=='joint-holdout':value=joint_holdout(exp)
        elif args.stage=='native-basis':value=native_basis(exp)
        elif args.stage=='joint-envelope':value=joint_envelope(exp)
        else:
            codec=args.stage=='codec-settings'
            path=exp.evidence/('public-codec-settings.json' if codec else 'public-encoder-settings.json')
            invocation=exp.command('codec-settings' if codec else 'public-settings',['-B',str(ROOT/'scripts/bwe2_encoder_settings.py'),'--out',str(path)]+(['--codec'] if codec else []),credits=1,executable=sys.executable)
            value=dict(kind='public-encoder-settings',invocation=invocation,result=json.loads(path.read_bytes()) if path.exists() else None)
        exp.save(args.stage,value)
    finally:exp.close()


if __name__=='__main__':main()
