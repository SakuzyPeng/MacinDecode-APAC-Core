#!/usr/bin/env python3
"""Verify a remounted campaign volume before explicitly rebinding its device ID.

The trusted volume pin is kept outside the removable disk. No decoder identity,
candidate, evidence object, budget, or probe counter is changed.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess

from hoa_blackbox_lib.common import EvidenceError, IdentityError, canonical, digest, require
from hoa_blackbox_lib.store import Store, atomic_file, writer_lock


def manifest_path(root):
    choices=[p for p in (root/'campaign.json',root/'coordinate.json',root/'validation.json') if p.is_file()]
    require(len(choices)==1,'missing or ambiguous measurement manifest',EvidenceError)
    return choices[0]


def volume_identity(root):
    mount=root
    while not os.path.ismount(mount) and mount.parent != mount:
        mount=mount.parent
    result=subprocess.run(['diskutil','info','-plist',str(mount)],capture_output=True,timeout=30)
    require(result.returncode==0,'cannot identify the mounted evidence volume',IdentityError)
    value=plistlib.loads(result.stdout)
    require(value.get('Mounted',True) and value.get('VolumeUUID') and value.get('Writable'),
            'evidence volume is not mounted and writable',IdentityError)
    return dict(volume_uuid=value['VolumeUUID'],device=root.stat().st_dev,
                mount_point=value['MountPoint'],filesystem=value.get('FilesystemType'))


def pin_volume(root,path):
    root=Path(root).resolve();path=Path(path).resolve()
    require(not path.exists(),'volume pin already exists')
    require(not path.is_relative_to(root),'keep the volume pin outside the campaign')
    config=json.loads(manifest_path(root).read_text())
    current=volume_identity(root)
    require(current['device']==config['evidence_device'],'cannot establish a pin after the device changed',IdentityError)
    result=dict(current,campaign=str(root),native_identity=config['native_identity'])
    atomic_file(path,canonical(result))
    return result


def verify(root,pin,write=False):
    root=Path(root).resolve()
    require(str(root)==pin['campaign'],'volume pin belongs to another campaign',IdentityError)
    current=volume_identity(root)
    require(current['volume_uuid']==pin['volume_uuid'],'a different volume is mounted at the evidence path',IdentityError)
    checked=[]
    with writer_lock(root):
        config_path=manifest_path(root)
        config=json.loads(config_path.read_text())
        require(config['native_identity']==pin['native_identity'],'campaign identity differs from volume pin',IdentityError)
        db=sqlite3.connect((root/'evidence.sqlite3').as_uri()+'?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        try:
            require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','shared index is damaged',EvidenceError)
            for row in db.execute('SELECT * FROM objects'):
                identity,size=row['sha'],row['size']
                path=Path(row['path']) if 'path' in row.keys() and row['path'] else root/'objects'/(identity+'.gz')
                kind=row['kind'] if 'kind' in row.keys() else 'gzip'
                try:
                    if kind in ('gzip','external-gzip'):
                        with gzip.open(path,'rb') as stream:raw=stream.read(size+1)
                    else:
                        with path.open('rb') as stream:
                            stream.seek(row['offset']);raw=stream.read(size)
                except (OSError,EOFError) as error:
                    raise EvidenceError('shared evidence object is unreadable: '+identity) from error
                require(len(raw)==size and digest(raw)==identity,'shared evidence object is corrupt: '+identity,EvidenceError)
        finally:db.close()
        for path in sorted((root/'batches').glob('order*-q*')):
            store=Store(path,readonly=True)
            try:
                require(store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','batch journal is damaged',EvidenceError)
                require(store.config['native_identity']==config['native_identity'],'batch native identity differs',IdentityError)
                objects=store.audit_evidence()
                for identity in (store.meta('source_snapshot') or {}).values():store.read_blob(identity)
                stages=[tuple(r) for r in store.db.execute('SELECT target,name FROM stages')]
                for target,name in stages:store.stage(target,name)
                checked.append(dict(batch=path.name,objects=objects,stages=len(stages),
                                    attempts=store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]))
            finally:store.close()
        if write:
            # Write only the volatile mount device number, after all evidence
            # and frozen-stage checks succeeded. A retry is idempotent.
            require(volume_identity(root)==current,'evidence volume changed during verification',IdentityError)
            for item in checked:
                path=root/'batches'/item['batch']
                db=sqlite3.connect(path/'state.sqlite3')
                try:
                    batch=json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
                    batch['evidence_device']=current['device']
                    with db:db.execute("UPDATE meta SET value=? WHERE key='config'",(canonical(batch).decode(),))
                    atomic_file(path/'manifest.json',canonical(batch))
                finally:db.close()
            config['evidence_device']=current['device']
            atomic_file(config_path,canonical(config))
    return dict(status='passed',campaign=str(root),volume=current,checked_batches=checked,
                device_rebound=write,recorded_decoder_identity_unchanged=True,budgets_unchanged=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True,help='existing campaign directory')
    p.add_argument('--identity',type=Path,required=True,help='trusted volume pin kept on the internal disk')
    p.add_argument('--write-rebind',action='store_true',help='update mount device IDs after successful verification')
    p.add_argument('--pin-current',action='store_true',help='save the original volume identity before a disconnection')
    args=p.parse_args()
    require(not (args.write_rebind and args.pin_current),'choose pinning or rebinding, not both')
    if args.pin_current:
        print(json.dumps(pin_volume(args.out,args.identity)))
        return
    print(json.dumps(verify(args.out,json.loads(args.identity.read_text()),args.write_rebind)))


if __name__=='__main__':main()
