"""Copy a consistent Atlas database to a new, empty data destination.

Uses a read-only source connection so the original installation is retained.
No runtime process IDs, logs, environments, or build output are copied.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3


def transfer(source_root, destination):
    source_root=Path(source_root).resolve()
    destination=Path(destination).resolve()
    source=source_root/'data'/'atlas.db'
    target=destination/'atlas.db'
    if not source.is_file():
        raise SystemExit('The source Atlas database was not found.')
    if target.exists():
        raise SystemExit('The destination already contains a database; it will not be overwritten.')
    destination.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
        with sqlite3.connect(target) as copy:
            original.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise SystemExit('Transferred database failed its integrity check.')
            revision,raw=copy.execute('SELECT revision,data FROM application_state WHERE id=1').fetchone()
            data=json.loads(raw)
            counts={key:len(data[key]) for key in ('clinicians','shifts','cases','requests')}
            counts.update({table:copy.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                           for table in ('users','proposals','jobs','conversations','audit')})
    target.chmod(0o600)
    credentials=source_root/'.runtime'/'credentials.txt'
    if credentials.exists():
        shutil.copyfile(credentials,destination/'credentials.txt')
        (destination/'credentials.txt').chmod(0o600)
    manifest={'source':str(source),'destination':str(target),'copied_at':datetime.now(timezone.utc).isoformat(),
              'revision':revision,'state_sha256':hashlib.sha256(raw.encode()).hexdigest(),'counts':counts}
    (destination/'transfer-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    transfer(args.source,args.destination)
