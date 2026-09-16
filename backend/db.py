from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, Integer, String, Text, create_engine, delete, event, select, update
from sqlalchemy.orm import DeclarativeBase, Session

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('ATLAS_DATA_DIR', ROOT / 'data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.environ.get('DATABASE_URL', f'sqlite:///{DATA_DIR / "atlas.db"}')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False, 'timeout': 15} if DATABASE_URL.startswith('sqlite:') else {})


@event.listens_for(engine, 'connect')
def sqlite_pragmas(connection, record):
    if DATABASE_URL.startswith('sqlite:'):
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')


class Base(DeclarativeBase):
    pass


class State(Base):
    __tablename__ = 'application_state'
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    data = Column(JSON, nullable=False)


class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    clinician_id = Column(String, nullable=True)
    password_hash = Column(Text, nullable=False)


class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)


class Audit(Base):
    __tablename__ = 'audit'
    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    revision = Column(Integer, nullable=False)
    details = Column(JSON, nullable=False)


class Proposal(Base):
    __tablename__ = 'proposals'
    id = Column(String, primary_key=True)
    summary = Column(Text, nullable=False)
    kind = Column(String, nullable=False)
    changes = Column(JSON, nullable=False)
    base_revision = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='pending')
    created_at = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    details = Column(JSON, nullable=False, default=dict)


class Job(Base):
    __tablename__ = 'jobs'
    id = Column(String, primary_key=True)
    status = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    date = Column(String)
    base_revision = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    snapshot = Column(JSON, nullable=False)
    result = Column(JSON)
    proposal_id = Column(String)
    context = Column(JSON, nullable=False, default=dict)


class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    history = Column(JSON, nullable=False, default=list)
    context = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=0)


class ImportPreview(Base):
    __tablename__ = 'import_previews'
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    records = Column(JSON, nullable=False)
    revision = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    applied = Column(Integer, default=0, nullable=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=''):
    return prefix + secrets.token_hex(8)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return salt + ':' + result


def verify_password(password, stored):
    return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)


def user_dict(user):
    return {key: getattr(user, key) for key in ('id', 'username', 'name', 'role', 'clinician_id')}


def provision_simple_demo_logins(session):
    """Create the documented local demo accounts without touching schedules.

    The clinician-facing account is linked to Lucas Rivera in the fictional
    roster.  Refuse to reuse a real account or clinician mapping silently.
    """
    _, data = snapshot(session)
    if not any(clinician['id'] == 'c10' for clinician in data['clinicians']):
        raise ValueError('The fictional clinician c10 is required for the user demo account.')

    admin = session.scalar(select(User).where(User.username == 'admin'))
    if admin and admin.role != 'admin':
        raise ValueError('The existing admin username is not an administrator account.')
    if not admin:
        admin = User(id='admin', username='admin', name='Alex Morgan', role='admin', clinician_id=None, password_hash='')
        session.add(admin)
    admin.password_hash = password_hash('admin')

    user = session.scalar(select(User).where(User.username == 'user'))
    if user and (user.role != 'clinician' or user.clinician_id != 'c10'):
        raise ValueError('The existing user username is already assigned to a different account.')
    mapped = session.scalar(select(User).where(User.clinician_id == 'c10'))
    if mapped and mapped is not user:
        raise ValueError('Lucas Rivera already has a different sign-in account.')
    if not user:
        user = User(id='user', username='user', name='Lucas Rivera', role='clinician', clinician_id='c10', password_hash='')
        session.add(user)
    user.password_hash = password_hash('user')

    # A changed password should take effect immediately for these two logins.
    session.execute(delete(LoginSession).where(LoginSession.user_id.in_([admin.id, user.id])))
    return {'admin': user_dict(admin), 'user': user_dict(user)}


def snapshot(session):
    state = session.get(State, 1)
    return state.revision, copy.deepcopy(state.data)


class Conflict(Exception):
    pass


def save_state(session, data, revision, user, action, details=None):
    result = session.execute(update(State).where(State.id == 1, State.revision == revision).values(data=data, revision=revision + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise Conflict('The schedule changed. Refresh and review your change again.')
    session.add(Audit(id=uid('a'), created_at=now(), user_id=user['id'], action=action, revision=revision + 1, details=details or {}))
    return revision + 1


def row_dict(row, exclude=()):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in exclude}


def initialize():
    from alembic.config import Config
    from alembic import command
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'migrations'))
    config.set_main_option('sqlalchemy.url', DATABASE_URL.replace('%', '%%'))
    command.upgrade(config, 'head')
    with Session(engine) as session:
        if not session.get(State, 1):
            from .seed import demo_data
            session.add(State(id=1, revision=1, data=demo_data()))
        if not session.scalar(select(User).limit(1)):
            staff_password = secrets.token_urlsafe(12)
            for username, role, name, clinician_id, password in [
                ('admin', 'admin', 'Alex Morgan', None, 'admin'),
                ('user', 'clinician', 'Lucas Rivera', 'c10', 'user'),
                ('scheduler', 'scheduler', 'Jordan Ellis', None, staff_password),
                ('clinician', 'clinician', 'Priya Shah', 'c09', staff_password),
            ]:
                session.add(User(id=username, username=username, role=role, name=name, clinician_id=clinician_id, password_hash=password_hash(password)))
            runtime = DATA_DIR if os.environ.get('ATLAS_DATA_DIR') else ROOT / '.runtime'
            runtime.mkdir(exist_ok=True)
            credentials = runtime / 'credentials.txt'
            credentials.write_text(
                'Anesthesia Atlas — fictional demo accounts\n\n'
                'Admin view: admin / admin\n'
                'User view: user / user (Lucas Rivera)\n\n'
                'Scheduler and clinician accounts: scheduler, clinician\n'
                'Password (both): ' + staff_password + '\n\n'
                'The clinician account is Priya Shah.\n'
            )
            credentials.chmod(0o600)
        for job in session.scalars(select(Job).where(Job.status.in_(['queued','running']))):
            job.status = 'interrupted'
            job.result = {'status':'INTERRUPTED','message':'Application restarted during optimization. Run again.'}
        session.commit()


def backup_database():
    if not DATABASE_URL.startswith('sqlite:'):
        raise ValueError('Use your database provider backup tools for this database.')
    backups = DATA_DIR / 'backups'
    backups.mkdir(exist_ok=True)
    destination = backups / (datetime.now().strftime('atlas-%Y%m%d-%H%M%S-') + secrets.token_hex(2) + '.db')
    source_path = engine.url.database
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    destination.chmod(0o600)
    return destination
