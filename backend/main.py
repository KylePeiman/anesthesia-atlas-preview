from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import multiprocessing
import os
import secrets
import threading
import time
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, update, or_
from sqlalchemy.orm import Session

from . import db as storage
from .db import State, User, LoginSession, Audit, Proposal, Job, Conversation, ImportPreview, engine, snapshot, save_state, row_dict, user_dict, uid, now
from .schemas import *
from .services import manager, find, validate_record, reject_new_errors, preview_changes, add_proposal, validate_request, apply_request, visible_data, leave_impact, validate_schedule, approval_policy, apply_leave_repair, request_fingerprint
from .solver import solve_schedule

executor=None
login_attempts={}
job_submission_lock=threading.Lock()


@asynccontextmanager
async def lifespan(app):
    global executor
    storage.initialize()
    executor=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'))
    yield
    await asyncio.to_thread(executor.shutdown,wait=True,cancel_futures=True)


app=FastAPI(title='Anesthesia Atlas',version='1.1.0',lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json')


@app.exception_handler(storage.Conflict)
async def conflict_handler(request,exc):
    return JSONResponse(status_code=409,content={'detail':str(exc)})


@app.middleware('http')
async def browser_protection(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc!=request.headers.get('host'):
            return JSONResponse(status_code=403,content={'detail':'Cross-origin changes are not allowed.'})
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse(status_code=403,content={'detail':'Cross-site changes are not allowed.'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    return response


def database():
    with Session(engine) as db:
        yield db


def current_user(request:Request,db:Session=Depends(database)):
    token=request.cookies.get('atlas_session','')
    login=db.get(LoginSession,hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not login or datetime.fromisoformat(login.expires_at)<datetime.now(timezone.utc):
        raise HTTPException(401,'Please sign in.')
    user=db.get(User,login.user_id)
    if not user:
        raise HTTPException(401,'Please sign in.')
    return user_dict(user)


def revision_check(actual,expected):
    if actual!=expected:
        raise storage.Conflict('The schedule changed. Refresh and review your change again.')


@app.get('/api/health')
def health():
    return {'status':'ok','application':'Anesthesia Atlas','local_ai':True}


@app.post('/api/auth/login')
def login(payload:Login,request:Request,response:Response,db:Session=Depends(database)):
    ip=request.client.host if request.client else 'local'
    recent=[stamp for stamp in login_attempts.get(ip,[]) if stamp>time.monotonic()-600]
    if len(recent)>=12:
        raise HTTPException(429,'Too many attempts. Try again in 10 minutes.')
    user=db.scalar(select(User).where(User.username==payload.username))
    if not user or not storage.verify_password(payload.password,user.password_hash):
        login_attempts[ip]=recent+[time.monotonic()]
        raise HTTPException(401,'Incorrect username or password.')
    login_attempts.pop(ip,None)
    token=secrets.token_urlsafe(40)
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires_at=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()))
    db.commit()
    response.set_cookie('atlas_session',token,max_age=43200,httponly=True,samesite='strict',secure=os.environ.get('ATLAS_HTTPS')=='1',path='/')
    return user_dict(user)


@app.post('/api/auth/logout')
def logout(request:Request,response:Response,db:Session=Depends(database)):
    token=request.cookies.get('atlas_session','')
    db.execute(delete(LoginSession).where(LoginSession.token_hash==hashlib.sha256(token.encode()).hexdigest()))
    db.commit()
    response.delete_cookie('atlas_session')
    return {'ok':True}


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return user


@app.post('/api/auth/password')
def change_password(payload:PasswordInput,user=Depends(current_user),db:Session=Depends(database)):
    row=db.get(User,user['id'])
    if not storage.verify_password(payload.current_password,row.password_hash):
        raise HTTPException(403,'Current password is incorrect.')
    row.password_hash=storage.password_hash(payload.new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id==user['id']))
    db.commit()
    return {'ok':True,'message':'Password changed. Please sign in again.'}


@app.get('/api/users')
def users(user=Depends(current_user),db:Session=Depends(database)):
    if user['role']!='admin':
        raise HTTPException(403,'Administrator access required.')
    return [user_dict(row) for row in db.scalars(select(User))]


@app.post('/api/users')
def create_user(payload:UserInput,user=Depends(current_user),db:Session=Depends(database)):
    if user['role']!='admin':
        raise HTTPException(403,'Administrator access required.')
    if db.scalar(select(User).where(User.username==payload.username)):
        raise HTTPException(409,'Username already exists.')
    _,data=snapshot(db)
    if payload.role=='clinician' and not payload.clinician_id:
        raise HTTPException(422,'Clinician accounts must be linked to a clinician.')
    if payload.clinician_id:
        find(data,'clinicians',payload.clinician_id)
        if db.scalar(select(User).where(User.clinician_id==payload.clinician_id)):
            raise HTTPException(409,'This clinician already has an account.')
    row=User(id=uid('u'),**payload.model_dump(exclude={'password'}),password_hash=storage.password_hash(payload.password))
    db.add(row); db.commit()
    return user_dict(row)


@app.get('/api/state')
def state(user=Depends(current_user),db:Session=Depends(database)):
    revision,data=snapshot(db)
    proposal_query=select(Proposal)
    job_query=select(Job)
    if user['role']=='clinician':
        own_requests=[r['id'] for r in data['requests'] if r['kind']=='leave' and r['clinician_id']==user['clinician_id']]
        proposal_query=proposal_query.where(or_(Proposal.created_by==user['id'], (Proposal.kind=='leave_repair') & Proposal.details['request_id'].as_string().in_(own_requests)))
        job_query=job_query.where(Job.context['kind'].as_string()=='leave_repair',Job.context['request_id'].as_string().in_(own_requests))
    proposals=list(db.scalars(proposal_query.order_by(Proposal.created_at.desc()).limit(40)))
    jobs=list(db.scalars(job_query.order_by(Job.created_at.desc()).limit(10)))
    return {'revision':revision,'user':user,'data':visible_data(data,user),'issues':validate_schedule(data),'proposals':[row_dict(p) for p in proposals],'jobs':[row_dict(j,('snapshot',)) for j in jobs]}


@app.post('/api/resources/{kind}')
def upsert(kind:str,payload:Upsert,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    if kind not in RECORD_MODELS:
        raise HTTPException(404,'Unknown resource.')
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    record=validate_record(data,kind,payload.record)
    before=copy.deepcopy(data)
    existing=next((r for r in data[kind] if r['id']==record['id']),None)
    if kind=='shifts':
        if record['status']=='published' and (not existing or existing['status']!='published'):
            raise HTTPException(422,'Use Publish to publish a schedule.')
        if existing and existing.get('status')=='published' and record!=existing:
            raise HTTPException(422,'Use a change proposal to edit a published shift.')
    if kind in ('cases','shifts') and ((existing and existing.get('clinician_id')!=record.get('clinician_id')) or (not existing and record.get('clinician_id'))):
        raise HTTPException(422,'Create coverage first, then use Assign to review an assignment proposal.')
    if existing:
        data[kind]=[record if r['id']==record['id'] else r for r in data[kind]]
    else:
        if kind=='sites' and len(data['sites'])>=3:
            raise HTTPException(422,'This installation supports up to three sites.')
        data[kind].append(record)
    reject_new_errors(before,data)
    new_revision=save_state(db,data,revision,user,f'upsert_{kind}',{'id':record['id'],'before':existing,'after':record})
    db.commit(); return {'revision':new_revision,'record':record}


@app.delete('/api/resources/{kind}/{identifier}')
def delete_resource(kind:str,identifier:str,revision:int,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    if kind not in RECORD_MODELS:
        raise HTTPException(404,'Unknown resource.')
    actual,data=snapshot(db); revision_check(actual,revision)
    record=find(data,kind,identifier)
    if kind=='shifts' and record['status']=='published':
        raise HTTPException(422,'Published coverage cannot be deleted. Use a reviewed change.')
    if kind=='clinicians' and (any(r.get('clinician_id')==identifier for k in ('shifts','cases','requests') for r in data[k]) or db.scalar(select(User).where(User.clinician_id==identifier))):
        raise HTTPException(422,'This clinician has linked records. Mark inactive after reviewing their assignments instead.')
    if kind=='sites' and (any(r.get('site_id')==identifier for k in ('shifts','cases') for r in data[k]) or any(identifier in c['sites'] for c in data['clinicians'])):
        raise HTTPException(422,'This site is still referenced by staff or coverage.')
    before=copy.deepcopy(data)
    data[kind]=[r for r in data[kind] if r['id']!=identifier]
    reject_new_errors(before,data)
    result=save_state(db,data,revision,user,f'delete_{kind}',record)
    db.commit(); return {'revision':result}


@app.put('/api/settings')
def settings(payload:SettingsUpdate,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    before=copy.deepcopy(data); data['settings']=payload.settings.model_dump()
    for group in data['settings']['approval_groups']:
        for cid in group['clinician_ids']:
            find(data,'clinicians',cid)
    reject_new_errors(before,data)
    result=save_state(db,data,revision,user,'update_settings',{'before':before['settings'],'after':data['settings']})
    db.commit(); return {'revision':result}


@app.post('/api/proposals')
def propose(payload:ProposalInput,user=Depends(current_user),db:Session=Depends(database)):
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    proposal,issues=add_proposal(db,data,user,revision,payload.summary,[c.model_dump() for c in payload.changes])
    db.commit(); return {**row_dict(proposal),'issues':issues}


@app.post('/api/proposals/{identifier}/apply')
def apply_proposal(identifier:str,payload:Revision,user=Depends(current_user),db:Session=Depends(database)):
    proposal=db.get(Proposal,identifier)
    if not proposal:
        raise HTTPException(404,'Proposal not found.')
    if proposal.kind=='leave_repair':
        raise HTTPException(422,'Apply this repair through its time-off request so leave and assignments are saved together.')
    if user['role']=='clinician' and proposal.created_by!=user['id']:
        raise HTTPException(403,'You cannot apply this proposal.')
    if proposal.status=='applied':
        return {'revision':snapshot(db)[0],'status':'applied'}
    if proposal.status!='pending':
        raise HTTPException(409,'This proposal is closed.')
    revision,data=snapshot(db)
    revision_check(revision,payload.revision); revision_check(revision,proposal.base_revision)
    revised=preview_changes(data,proposal.changes,user)
    claimed=db.execute(update(Proposal).where(Proposal.id==identifier,Proposal.status=='pending').values(status='applied').execution_options(synchronize_session=False))
    if claimed.rowcount!=1:
        raise HTTPException(409,'This proposal was already processed.')
    result=save_state(db,revised,revision,user,'apply_proposal',{'proposal_id':identifier,'changes':proposal.changes,'requested_by':proposal.created_by})
    proposal.status='applied'; proposal.details={**proposal.details,'applied_by':user['id'],'applied_at':now()}
    db.commit(); return {'revision':result,'status':'applied'}


@app.post('/api/proposals/{identifier}/reject')
def reject_proposal(identifier:str,user=Depends(current_user),db:Session=Depends(database)):
    proposal=db.get(Proposal,identifier)
    if not proposal:
        raise HTTPException(404,'Proposal not found.')
    if user['role']=='clinician' and proposal.created_by!=user['id']:
        raise HTTPException(403,'You cannot dismiss this proposal.')
    if proposal.status!='pending':
        raise HTTPException(409,'This proposal is closed.')
    claimed=db.execute(update(Proposal).where(Proposal.id==identifier,Proposal.status=='pending').values(status='rejected').execution_options(synchronize_session=False))
    if claimed.rowcount!=1:
        raise HTTPException(409,'This proposal was already processed.')
    db.commit(); return {'status':'rejected'}


def finish_job(identifier,future):
    try:
        result=future.result()
        with Session(engine) as db:
            job=db.get(Job,identifier)
            if not job or job.status not in ('running','queued'):
                return
            revision,data=snapshot(db)
            job.result=result
            if revision!=job.base_revision:
                job.status='stale'
                job.result={**result,'message':'The schedule changed during optimization. Run again against the latest schedule.'}
            elif job.stage=='repair' and str(result.get('status','')).upper() in ('OPTIMAL','FEASIBLE'):
                from .repair import simulate_leave
                actor=user_dict(db.get(User,job.created_by))
                context=job.context or {}
                kind=context.get('kind','coverage_repair')
                hypothetical=data
                if kind=='leave_repair':
                    request=find(data,'requests',context['request_id'])
                    if context.get('request_fingerprint')!=request_fingerprint(request):
                        job.status='stale'; db.commit(); return
                    hypothetical=simulate_leave(data,request)
                    summary='Replacement schedule for '+find(data,'clinicians',request['clinician_id'])['name']+'’s time off'
                else:
                    summary='Repair coverage and affected OR assignments'
                changes=result.get('changes',[])
                projected=preview_changes(hypothetical,changes,{'role':'scheduler'})
                reject_new_errors(data,projected)
                metadata={**context,'job_id':identifier,'scope':result.get('scope',{}),'metrics':result.get('metrics',{}),
                          'gaps':result.get('gaps',[]),'new_issues':result.get('new_issues',[]),
                          'existing_issues':result.get('existing_issues',[]),'released_locks':result.get('released_locks',[])}
                proposal,_=add_proposal(db,hypothetical,{**actor,'role':'scheduler'},revision,summary,changes,
                                       kind=kind,details=metadata,original_data=data)
                job.proposal_id=proposal.id
                job.result={**result,'preview':{key:projected[key] for key in ('shifts','cases')}}
                job.status='completed'
            elif result.get('changes') and str(result.get('status','')).upper() in ('OPTIMAL','FEASIBLE'):
                user=user_dict(db.get(User,job.created_by))
                proposal,_=add_proposal(db,data,user,revision,'Optimized '+('staffing schedule' if job.stage=='staffing' else f'OR board for {job.date}'),result['changes'],kind='optimization',details={'job_id':identifier,'metrics':result.get('metrics',{}),'gaps':result.get('gaps',[])})
                job.proposal_id=proposal.id; job.status='completed'
            else:
                job.status='completed'
            db.commit()
    except Exception as exc:
        with Session(engine) as db:
            job=db.get(Job,identifier)
            if job:
                job.status='failed'; job.result={'status':'ERROR','message':str(getattr(exc,'detail',exc))[:2000]}; db.commit()


@app.post('/api/optimize')
def optimize(payload:Optimize,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    if payload.stage=='daily':
        if not payload.date:
            raise HTTPException(422,'Choose a date for daily assignments.')
        try:
            datetime.strptime(payload.date,'%Y-%m-%d')
        except ValueError:
            raise HTTPException(422,'Use YYYY-MM-DD for the board date.')
    with job_submission_lock:
        if db.scalar(select(Job).where(Job.status.in_(['queued','running']))):
            raise HTTPException(409,'An optimization is already running. Wait for its result.')
        job=Job(id=uid('j'),status='running',stage=payload.stage,date=payload.date,base_revision=revision,created_at=now(),created_by=user['id'],snapshot=data)
        db.add(job); db.commit()
        identifier=job.id
        month=payload.month or (datetime.now(ZoneInfo(data['settings']['timezone'])).strftime('%Y-%m') if payload.stage=='staffing' else None)
        try:
            future=executor.submit(solve_schedule,data,payload.stage,payload.date,30 if payload.stage=='staffing' else 15,month)
            future.add_done_callback(lambda f:finish_job(identifier,f))
        except Exception:
            job.status='failed'; job.result={'status':'ERROR','message':'The background worker could not start. Please retry.'}
            db.commit()
            raise HTTPException(503,'The background worker could not start. Please retry.')
    return row_dict(job,('snapshot',))


@app.get('/api/jobs/{identifier}')
def job_status(identifier:str,user=Depends(current_user),db:Session=Depends(database)):
    job=db.get(Job,identifier)
    if not job:
        raise HTTPException(404,'Job not found.')
    _,data=snapshot(db)
    authorize_job(job,data,user)
    return row_dict(job,('snapshot',))


def authorize_leave(request,user):
    if user['role']=='clinician' and request['clinician_id']!=user['clinician_id']:
        raise HTTPException(403,'You can preview only your own leave.')
    if request['kind']!='leave':
        raise HTTPException(422,'A schedule preview requires a leave request.')


def authorize_job(job,data,user):
    if user['role'] in ('admin','scheduler'):
        return
    context=job.context or {}
    if context.get('kind')!='leave_repair':
        raise HTTPException(403,'This job requires a scheduler account.')
    authorize_leave(find(data,'requests',context.get('request_id')),user)


@app.get('/api/proposals/{identifier}')
def get_proposal(identifier:str,user=Depends(current_user),db:Session=Depends(database)):
    proposal=db.get(Proposal,identifier)
    if not proposal:
        raise HTTPException(404,'Proposal not found.')
    if user['role']=='clinician' and proposal.created_by!=user['id']:
        if proposal.kind!='leave_repair':
            raise HTTPException(403,'You cannot view this proposal.')
        _,data=snapshot(db)
        authorize_leave(find(data,'requests',proposal.details.get('request_id')),user)
    return row_dict(proposal)


def start_repair(db,data,revision,user,context,request=None,target=None):
    from .repair import solve_repair
    with job_submission_lock:
        if db.scalar(select(Job).where(Job.status.in_(['queued','running']))):
            raise HTTPException(409,'An optimization is already running. Wait for its result.')
        job=Job(id=uid('j'),status='running',stage='repair',base_revision=revision,created_at=now(),
                created_by=user['id'],snapshot=data,context=context)
        db.add(job); db.commit()
        identifier=job.id
        try:
            future=executor.submit(solve_repair,data,request,target,60)
            future.add_done_callback(lambda f:finish_job(identifier,f))
        except Exception:
            job.status='failed'; job.result={'status':'ERROR','message':'The background worker could not start. Please retry.'}
            db.commit()
            raise HTTPException(503,'The background worker could not start. Please retry.')
    db.refresh(job)
    return row_dict(job,('snapshot',))


@app.post('/api/requests/{identifier}/repair-preview')
def preview_request_repair(identifier:str,payload:Revision,user=Depends(current_user),db:Session=Depends(database)):
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    request=find(data,'requests',identifier)
    authorize_leave(request,user)
    if request['status'] not in ('pending','approved') or request.get('details',{}).get('cancellation_requested'):
        raise HTTPException(409,'Only active pending or approved leave can be previewed.')
    return start_repair(db,data,revision,user,{'kind':'leave_repair','request_id':identifier,
                       'request_fingerprint':request_fingerprint(request)},request=request)


@app.get('/api/requests/{identifier}/repair-preview')
def latest_request_repair(identifier:str,user=Depends(current_user),db:Session=Depends(database)):
    _,data=snapshot(db)
    authorize_leave(find(data,'requests',identifier),user)
    job=db.scalar(select(Job).where(Job.context['kind'].as_string()=='leave_repair',Job.context['request_id'].as_string()==identifier).order_by(Job.created_at.desc()).limit(1))
    return row_dict(job,('snapshot',)) if job else None


@app.post('/api/coverage/repair-preview')
def preview_coverage_repair(payload:CoverageRepair,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    find(data,payload.resource,payload.id)
    target={'resource':payload.resource,'id':payload.id}
    return start_repair(db,data,revision,user,{'kind':'coverage_repair','target':target},target=target)


@app.get('/api/coverage/candidates')
def coverage_candidates(resource:Literal['shifts','cases'],id:str,revision:int,preview_job_id:str|None=None,user=Depends(current_user),db:Session=Depends(database)):
    from .repair import coverage_recommendations,simulate_leave
    current,data=snapshot(db); revision_check(current,revision)
    if preview_job_id:
        job=db.get(Job,preview_job_id)
        if not job:
            raise HTTPException(404,'Preview not found.')
        authorize_job(job,data,user)
        revision_check(current,job.base_revision)
        if job.status!='completed' or not job.result or job.result.get('status') not in ('OPTIMAL','FEASIBLE') or not job.result.get('preview'):
            raise HTTPException(409,'This preview is not ready. Generate a new completed preview.')
        if (job.context or {}).get('kind')=='leave_repair':
            leave=find(data,'requests',job.context['request_id'])
            if request_fingerprint(leave)!=job.context.get('request_fingerprint'):
                raise HTTPException(409,'Leave details changed. Generate a new preview.')
            data=simulate_leave(data,leave)
        for key in ('shifts','cases'):
            data[key]=copy.deepcopy(job.result['preview'][key])
    find(data,resource,id)
    result=coverage_recommendations(data,resource,id)
    return {**result,'base_revision':current,'preview_job_id':preview_job_id}


@app.post('/api/publish')
def publish(payload:Revision,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    issues=[i for i in validate_schedule(data) if i.get('resource')!='cases' or i.get('severity')=='error']
    if issues:
        raise HTTPException(422,{'message':'Resolve staffing gaps and rule violations before publishing. Daily case assignments can be completed afterward.','issues':issues[:50]})
    for shift in data['shifts']:
        shift['status']='published'
    result=save_state(db,data,revision,user,'publish_schedule')
    db.commit(); return {'revision':result,'status':'published'}


@app.post('/api/leave-impact')
def impact(payload:LeaveImpact,user=Depends(current_user),db:Session=Depends(database)):
    if user['role']=='clinician' and user['clinician_id']!=payload.clinician_id:
        raise HTTPException(403,'You can preview only your own leave.')
    _,data=snapshot(db); find(data,'clinicians',payload.clinician_id)
    return leave_impact(data,payload.clinician_id,payload.start,payload.end)


@app.post('/api/requests')
def create_request(payload:RequestInput,user=Depends(current_user),db:Session=Depends(database)):
    revision,data=snapshot(db); revision_check(revision,payload.revision)
    record=validate_request(data,payload.model_dump(exclude={'revision'}),user)
    record.update(id=uid('r'),status='pending',created_by=user['id'],created_at=now())
    impact=leave_impact(data,record['clinician_id'],record['start'],record['end']) if record['kind']=='leave' else None
    data['requests'].append(record)
    policy=approval_policy(data,record)
    if record['kind']=='swap' and not policy['require_scheduler_approval'] and not policy['require_swap_acceptance'] and user.get('clinician_id')==record['clinician_id']:
        data,_=apply_request(data,record,user,'accept')
        record=find(data,'requests',record['id'])
    result=save_state(db,data,revision,user,'submit_request',record)
    db.commit(); return {'revision':result,'request':record,'impact':impact}


@app.post('/api/requests/{identifier}/action')
def request_action(identifier:str,payload:RequestAction,user=Depends(current_user),db:Session=Depends(database)):
    revision,data=snapshot(db)
    request=find(data,'requests',identifier)
    proposal=None
    if payload.repair_proposal_id:
        manager(user)
        proposal=db.get(Proposal,payload.repair_proposal_id)
        if not proposal or proposal.kind!='leave_repair' or proposal.details.get('request_id')!=identifier:
            raise HTTPException(404,'Repair proposal not found for this leave request.')
        if proposal.status=='applied':
            return {'revision':revision,'status':request['status'],'proposal_id':proposal.id,'already_applied':True}
        if proposal.status!='pending':
            raise HTTPException(409,'This repair proposal is already closed.')
        if (payload.action=='approve' and request['status']!='pending') or (payload.action=='apply_repair' and request['status']!='approved'):
            raise HTTPException(409,'The leave status changed. Reopen the request and generate a new preview.')
        revision_check(revision,payload.revision); revision_check(revision,proposal.base_revision)
        revised,details=apply_leave_repair(data,request,proposal,user,payload.acknowledge_shortage)
        claimed=db.execute(update(Proposal).where(Proposal.id==proposal.id,Proposal.status=='pending').values(status='applied').execution_options(synchronize_session=False))
        if claimed.rowcount!=1:
            raise HTTPException(409,'This repair proposal was already processed.')
        proposal.status='applied'
        proposal.details={**proposal.details,'applied_by':user['id'],'applied_at':now()}
    else:
        revision_check(revision,payload.revision)
        revised,details=apply_request(data,request,user,payload.action,payload.acknowledge_shortage)
    result=save_state(db,revised,revision,user,f'request_{payload.action}',{'request_id':identifier,'before':request,'after':find(revised,'requests',identifier),'result':details})
    db.commit(); return {'revision':result,**details}


def parse_csv(kind,source,data):
    reader=csv.DictReader(io.StringIO(source))
    records=[]; errors=[]; seen=set()
    lists={'specialties','sites','preferred_shift_hours','preferred_specialties'}
    booleans={'active','call_eligible','supervision_required','locked'}
    numbers={'fte','target_hours'}
    for index,row in enumerate(reader,start=2):
        if index>2001:
            errors.append({'row':index,'message':'Maximum 2,000 records per import.'}); break
        try:
            if None in row:
                raise ValueError('Some cells have no corresponding column header.')
            record={key.strip():value.strip() for key,value in row.items() if key and value is not None and value.strip()!=''}
            for key in lists & record.keys():
                record[key]=json.loads(record[key]) if record[key].startswith('[') else [value.strip() for value in record[key].split(';') if value.strip()]
                if key=='preferred_shift_hours': record[key]=[float(v) for v in record[key]]
            for key in booleans & record.keys():
                if record[key].lower() not in ('true','false','1','0','yes','no'):
                    raise ValueError(f'{key} must be true or false.')
                record[key]=record[key].lower() in ('true','1','yes')
            for key in numbers & record.keys(): record[key]=float(record[key])
            if 'availability' in record: record['availability']=json.loads(record['availability'])
            if record.get('clinician_id') in ('null','None'): record['clinician_id']=None
            record=validate_record(data,kind,record)
            if record['id'] in seen: raise ValueError('Duplicate ID in import.')
            seen.add(record['id']); records.append(record)
        except (ValueError,HTTPException,TypeError) as exc:
            errors.append({'row':index,'message':str(getattr(exc,'detail',exc))})
    if not records and not errors: errors.append({'row':1,'message':'The CSV contains no records.'})
    return records,errors


@app.post('/api/import/preview')
def import_preview(payload:ImportInput,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    revision,data=snapshot(db)
    records,errors=parse_csv(payload.kind,payload.csv,data)
    token=None
    if not errors:
        preview=ImportPreview(id=uid('i'),user_id=user['id'],kind=payload.kind,records=records,revision=revision,created_at=now(),applied=0)
        db.add(preview); db.commit(); token=preview.id
    return {'token':token,'records':records,'errors':errors}


@app.post('/api/import/apply')
def apply_import(payload:ImportApply,user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    preview=db.get(ImportPreview,payload.token)
    if not preview or preview.user_id!=user['id']:
        raise HTTPException(404,'Import preview not found.')
    if preview.applied:
        raise HTTPException(409,'This import has already been applied.')
    revision,data=snapshot(db); revision_check(revision,payload.revision); revision_check(revision,preview.revision)
    before=copy.deepcopy(data)
    kind=preview.kind
    for record in preview.records:
        previous=next((r for r in data[kind] if r['id']==record['id']),None)
        if kind=='shifts' and (record.get('status')=='published' or (previous and previous.get('status')=='published')):
            raise HTTPException(422,'Import draft coverage only. Published shifts require change proposals.')
        data[kind]=[r for r in data[kind] if r['id']!=record['id']]+[record]
    reject_new_errors(before,data)
    result=save_state(db,data,revision,user,'import_records',{'kind':kind,'count':len(preview.records),'records':preview.records})
    preview.applied=1; db.commit(); return {'revision':result,'count':len(preview.records)}


@app.get('/api/export')
def export(kind:str='shifts',user=Depends(current_user),db:Session=Depends(database)):
    if kind not in ('shifts','cases','clinicians'):
        raise HTTPException(422,'Choose shifts, cases or clinicians.')
    _,data=snapshot(db)
    records=data[kind]
    if user['role']=='clinician' and kind=='clinicians':
        records=[c for c in records if c['id']==user['clinician_id']]
    output=io.StringIO()
    if records:
        writer=csv.DictWriter(output,fieldnames=list(RECORD_MODELS[kind].model_fields))
        writer.writeheader()
        for record in records:
            formatted={}
            for k in writer.fieldnames:
                value=record.get(k)
                if isinstance(value,(list,dict)): value=json.dumps(value)
                if isinstance(value,str) and value.startswith(('=','+','-','@')): value="'"+value
                formatted[k]=value
            writer.writerow(formatted)
    return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="atlas-{kind}.csv"'})


@app.post('/api/backup')
def backup(user=Depends(current_user)):
    if user['role']!='admin': raise HTTPException(403,'Administrator access required.')
    path=storage.backup_database()
    return {'filename':path.name,'path':str(path)}


@app.get('/api/audit')
def audit(user=Depends(current_user),db:Session=Depends(database)):
    manager(user)
    return [row_dict(row) for row in db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(100))]


@app.get('/api/ai/status')
async def ai_health(user=Depends(current_user)):
    from .chat import ai_status
    return await ai_status()


@app.post('/api/chat')
async def chat(payload:ChatInput,user=Depends(current_user),db:Session=Depends(database)):
    from .chat import chat_reply
    conversation=db.get(Conversation,payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id and (not conversation or conversation.user_id!=user['id']):
        raise HTTPException(404,'Conversation not found.')
    base_revision,data=snapshot(db)
    if not conversation:
        conversation=Conversation(id=uid('chat'),user_id=user['id'],history=[])
        db.add(conversation); db.commit()
    identifier=conversation.id
    conversation_revision=conversation.revision
    history=copy.deepcopy(conversation.history)
    context=copy.deepcopy(conversation.context or {})
    if payload.selection and not context.get('clarification'):
        raise HTTPException(409,'This clarification is no longer active. Ask your question again.')
    if context.get('clarification') and context.get('base_revision')!=base_revision:
        if payload.selection:
            raise HTTPException(409,'The schedule changed since that question. Ask it again to review current choices.')
        context={**context,'stale':True}
    # Release read transaction while the local model is working.
    db.rollback()
    chat_data=visible_data(data,user)
    # Availability may be shared; private leave notes and pending requests may not.
    if user['role']=='clinician':
        visible_ids={r['id'] for r in chat_data['requests']}
        chat_data['requests'] += [{key:r[key] for key in ('id','kind','clinician_id','start','end','status')} for r in data['requests'] if r['id'] not in visible_ids and r['kind']=='leave' and r['status']=='approved']
    kwargs={}
    if context or payload.selection:
        kwargs={'context':context,'selection':payload.selection.model_dump() if payload.selection else None}
    result=await chat_reply(payload.message,chat_data,user,history,**kwargs)
    returned_history=result.pop('history',None)
    returned_context=result.pop('context',{}) or {}
    updated_context={**returned_context,'base_revision':base_revision} if returned_context else {}
    updated_history=(returned_history if isinstance(returned_history,list) else history+[{'role':'user','content':payload.message},{'role':'assistant','content':result.get('reply','')}])[-20:]
    claimed=db.execute(update(Conversation).where(Conversation.id==identifier,Conversation.revision==conversation_revision).values(
        context=updated_context,history=updated_history,revision=conversation_revision+1).execution_options(synchronize_session=False))
    if claimed.rowcount!=1:
        raise HTTPException(409,'Another message completed in this conversation. Ask again to use its latest context, or start a new conversation.')
    db.commit()
    return {**result,'conversation_id':identifier,'base_revision':base_revision}


@app.get('/api/{path:path}')
def unknown_api(path:str):
    raise HTTPException(404,'API endpoint not found.')


dist=storage.ROOT/'frontend'/'dist'
if (dist/'assets').exists():
    app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')


@app.get('/{path:path}')
def frontend(path:str):
    if path=='favicon.svg' and (dist/path).is_file():
        return FileResponse(dist/path)
    if (dist/'index.html').exists():
        return FileResponse(dist/'index.html')
    return JSONResponse(status_code=503,content={'detail':'The interface has not been built. Run the setup launcher.'})
