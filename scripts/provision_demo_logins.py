#!/usr/bin/env python3
"""Add the documented admin/admin and user/user demo sign-ins to Atlas.

This intentionally changes only the two account records and their sessions.
It never changes clinicians, assignments, requests, or scheduling history.
"""
from pathlib import Path
import sys

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import DATA_DIR, engine, backup_database, provision_simple_demo_logins


NOTICE = '\nSimple local demo sign-ins\n\nAdmin view: admin / admin\nUser view: user / user (Lucas Rivera)\n'


def update_credentials_notice():
    runtime = DATA_DIR.parent / '.runtime'
    runtime.mkdir(exist_ok=True)
    credentials = runtime / 'credentials.txt'
    existing = credentials.read_text() if credentials.exists() else 'Anesthesia Atlas — fictional demo accounts\n'
    if 'Simple local demo sign-ins' in existing:
        existing = existing.split('Simple local demo sign-ins', 1)[0].rstrip() + '\n'
    credentials.write_text(existing.rstrip() + NOTICE)
    credentials.chmod(0o600)


def main():
    backup = backup_database()
    with Session(engine) as session:
        accounts = provision_simple_demo_logins(session)
        session.commit()
    update_credentials_notice()
    print(f'Updated {accounts["admin"]["username"]} and {accounts["user"]["username"]}.')
    print(f'Backup created: {backup}')


if __name__ == '__main__':
    main()
