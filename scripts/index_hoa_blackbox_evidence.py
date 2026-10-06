#!/usr/bin/env python3
"""Add optional SQLite covering indexes while a campaign is stopped.

Only database indexes change. Observations, frozen results, analysis identities,
attempt counters and limits are preserved. The campaign writer lock is required.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from hoa_blackbox_lib.common import canonical, require
from hoa_blackbox_lib.store import Store, atomic_file, writer_lock


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True,help='existing campaign directory')
    args=p.parse_args();root=args.out.resolve()
    require((root/'campaign.json').is_file(),'not a campaign')
    with writer_lock(root):
        directories=sorted((root/'batches').glob('order*-q*'),
                           key=lambda path: json.loads((path/'manifest.json').read_text())['created_utc'])
        require(directories,'no campaign batches')
        db=sqlite3.connect(root/'evidence.sqlite3')
        count=db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        newest=Store(directories[-1])
        try:
            # Two compact pool indexes plus local state indexes, conservatively
            # reserved against the current shard and the campaign disk budget.
            estimate=count*256+16*1024**2
            newest.reserve(estimate)
        finally:
            newest.close()
        with db:
            db.execute('CREATE INDEX IF NOT EXISTS pool_stored_bytes ON objects(stored_bytes)')
            db.execute('CREATE INDEX IF NOT EXISTS pool_owner_shard_bytes ON objects(owner,shard,stored_bytes)')
        require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','pool integrity failed')
        db.close()
        for path in directories:
            db=sqlite3.connect(path/'state.sqlite3')
            with db:
                db.execute('CREATE INDEX IF NOT EXISTS query_states ON queries(state)')
                db.execute('CREATE INDEX IF NOT EXISTS shard_attempt_counts ON shard_attempts(shard)')
            require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','batch integrity failed')
            db.close()
        report=dict(status='passed',pool_objects=count,batches=len(directories),reserved_bytes=estimate,
                    observations_changed=False,candidates_changed=False)
        atomic_file(root/'index-check.json',canonical(report))
        print(json.dumps(report))


if __name__=='__main__':main()
