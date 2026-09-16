#!/usr/bin/env python3
"""Own only this application's server process; leave shared Ollama untouched."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/'.runtime'
RUNTIME.mkdir(exist_ok=True)
PID=RUNTIME/'server.json'


def owned_server():
    if not PID.exists(): return None
    try:
        record=json.loads(PID.read_text())
        args=subprocess.check_output(['ps','-p',str(record['pid']),'-o','args='],text=True,stderr=subprocess.DEVNULL)
        if 'backend.main:app' in args and str(ROOT) in args:
            return record
    except (ValueError,KeyError,subprocess.SubprocessError):
        pass
    return None


def addresses(port):
    print(f'\nOn this Mac: http://localhost:{port}')
    seen=set()
    for interface in ('en0','en1'):
        try:
            address=subprocess.check_output(['/usr/sbin/ipconfig','getifaddr',interface],text=True,stderr=subprocess.DEVNULL).strip()
            if address and address not in seen:
                print(f'On this network: http://{address}:{port}')
                seen.add(address)
        except (OSError,subprocess.SubprocessError): pass
    if not seen: print('Network address unavailable; use the Mac’s Wi-Fi address shown in System Settings.')


def start(port):
    existing=owned_server()
    if existing:
        print('Anesthesia Atlas is already running.'); addresses(existing['port']); return
    if not (ROOT/'frontend/dist/index.html').exists():
        raise SystemExit('Run Setup.command first to build the interface.')
    with socket.socket() as probe:
        # Match Uvicorn's reusable listener so recent connections in TIME_WAIT
        # do not look like another application still owns the port on restart.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: probe.bind(('0.0.0.0',port))
        except OSError: raise SystemExit(f'Port {port} is occupied. Choose another with: .venv/bin/python run.py start --port 8001')
    log=open(RUNTIME/'server.log','a')
    process=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','uvicorn','backend.main:app','--app-dir',str(ROOT),'--host','0.0.0.0','--port',str(port)],cwd=ROOT,stdout=log,stderr=log,start_new_session=True)
    PID.write_text(json.dumps({'pid':process.pid,'port':port}))
    PID.chmod(0o600)
    for _ in range(40):
        if process.poll() is not None:
            raise SystemExit('Startup failed. See .runtime/server.log.')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
                if response.status==200: break
        except Exception: time.sleep(.5)
    else: raise SystemExit('Server is starting slowly. See .runtime/server.log.')
    print('Anesthesia Atlas is running. Keep this Mac awake for network access.')
    addresses(port)
    print(f'\nDemo account credentials: {RUNTIME / "credentials.txt"}')
    print('Local AI uses the existing Ollama service; start Ollama separately if it is stopped.')


def stop():
    record=owned_server()
    if not record:
        print('No Atlas-owned server is running.'); return
    os.kill(record['pid'],signal.SIGTERM)
    for _ in range(40):
        if not owned_server(): break
        time.sleep(.25)
    if owned_server():
        print('Server is finishing a job. Try Stop again shortly.'); return
    PID.unlink(missing_ok=True)
    print('Anesthesia Atlas stopped. Ollama was left running.')


def backup():
    from backend.db import backup_database
    print(backup_database())


def restore(path):
    if owned_server(): raise SystemExit('Stop Atlas before restoring a backup.')
    source=Path(path).resolve()
    if not source.is_file(): raise SystemExit('Backup file does not exist.')
    from backend.db import DATA_DIR, engine, backup_database
    with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise SystemExit('Backup integrity check failed.')
        row=db.execute('SELECT data FROM application_state WHERE id=1').fetchone()
        if not row or not all(key in json.loads(row[0]) for key in ('settings','clinicians','shifts','cases','requests')):
            raise SystemExit('This file is not an Atlas backup.')
        target=engine.url.database
        if Path(target).exists(): print('Previous state saved to',backup_database())
        engine.dispose()
        with sqlite3.connect(target) as output:
            db.backup(output)
            output.execute('DELETE FROM sessions')
            output.commit()
    print('Backup restored. Start Atlas and sign in again.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop','status','backup','restore'])
    parser.add_argument('file',nargs='?')
    parser.add_argument('--port',type=int,default=int(os.environ.get('ATLAS_PORT','8000')))
    args=parser.parse_args()
    if args.action=='start': start(args.port)
    elif args.action=='stop': stop()
    elif args.action=='backup': backup()
    elif args.action=='restore':
        if not args.file: parser.error('restore requires a backup path')
        restore(args.file)
    else:
        record=owned_server()
        print('Running' if record else 'Stopped')
        if record: addresses(record['port'])
