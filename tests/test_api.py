"""Integration checks against an isolated database, never the user's demo state."""
import copy
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ['ATLAS_DATA_DIR']=tempfile.mkdtemp(prefix='atlas-api-test-')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from backend.main import app
from backend import db
from backend.seed import demo_data
from backend.solver import solve_schedule


def small_state():
    data=demo_data()
    data['requests']=[]
    data['clinicians']=[copy.deepcopy(data['clinicians'][i]) for i in (0,1,8,9)]
    data['sites']=data['sites'][:1]
    for clinician in data['clinicians']:
        clinician['sites']=['main']
        clinician['availability']['weekdays']=list(range(7))
    data['shifts']=[{'id':'md','start':'2026-09-14T07:00:00-04:00','end':'2026-09-14T19:00:00-04:00','site_id':'main','specialty':'OB','role':'MD','kind':'day','clinician_id':'c01','locked':False,'status':'draft'}, {'id':'crna','start':'2026-09-14T07:00:00-04:00','end':'2026-09-14T19:00:00-04:00','site_id':'main','specialty':'OB','role':'CRNA','kind':'day','clinician_id':'c09','locked':False,'status':'draft'}]
    data['cases']=[{'id':'case','title':'Demo procedure','room':'OR 1','start':'2026-09-14T09:00:00-04:00','end':'2026-09-14T12:00:00-04:00','site_id':'main','specialty':'OB','role':'CRNA','clinician_id':'c09','locked':False}]
    return data


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def reset(client):
    with Session(db.engine) as session:
        for table in [db.LoginSession,db.Proposal,db.Job,db.Audit,db.ImportPreview,db.Conversation]:
            session.execute(delete(table))
        row=session.get(db.State,1); row.data=small_state(); row.revision=1
        session.execute(delete(db.User).where(db.User.id.not_in(['admin','scheduler','clinician'])))
        session.get(db.User,'clinician').clinician_id='c09'
        for user in session.scalars(select(db.User)):
            user.password_hash=db.password_hash('TestingAtlas!')
        session.commit()
    client.cookies.clear()


def sign_in(client,username='admin'):
    response=client.post('/api/auth/login',json={'username':username,'password':'TestingAtlas!'})
    assert response.status_code==200,response.text


def revision(client):
    return client.get('/api/state').json()['revision']


def leave_request(client):
    response=client.post('/api/requests',json={'kind':'leave','clinician_id':'c09','start':'2026-09-14T00:00:00-04:00','end':'2026-09-28T00:00:00-04:00','note':'Private vacation reason','revision':revision(client)})
    assert response.status_code==200,response.text
    return response.json()['request']['id']


def test_auth_and_permissions(client):
    assert client.get('/api/state').status_code==401
    sign_in(client,'clinician')
    assert client.post('/api/optimize',json={'stage':'staffing','revision':1}).status_code==403
    assert client.get('/api/users').status_code==403
    assert client.post('/api/backup').status_code==403


def test_simple_demo_logins_provide_admin_and_clinician_views(client):
    with Session(db.engine) as session:
        accounts=db.provision_simple_demo_logins(session)
        session.commit()
    assert accounts['admin']['role']=='admin'
    assert accounts['user']['role']=='clinician'
    assert accounts['user']['clinician_id']=='c10'

    client.cookies.clear()
    response=client.post('/api/auth/login',json={'username':'admin','password':'admin'})
    assert response.status_code==200,response.text
    assert response.json()['role']=='admin'
    assert client.get('/api/users').status_code==200

    client.cookies.clear()
    response=client.post('/api/auth/login',json={'username':'user','password':'user'})
    assert response.status_code==200,response.text
    assert response.json()['role']=='clinician'
    assert response.json()['clinician_id']=='c10'
    assert client.get('/api/users').status_code==403


def test_cross_origin_changes_blocked(client):
    sign_in(client)
    response=client.post('/api/publish',json={'revision':1},headers={'origin':'http://evil.example'})
    assert response.status_code==403


def test_publish_then_approved_leave_exposes_gap(client):
    sign_in(client)
    assert client.post('/api/publish',json={'revision':1}).status_code==200
    identifier=leave_request(client)
    before=client.get('/api/state').json()
    assert next(s for s in before['data']['shifts'] if s['id']=='crna')['clinician_id']=='c09'
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','revision':before['revision']})
    assert response.status_code==409
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':before['revision']})
    assert response.status_code==200,response.text
    state=client.get('/api/state').json()
    assert next(s for s in state['data']['shifts'] if s['id']=='crna')['clinician_id'] is None
    assert state['data']['cases'][0]['clinician_id'] is None
    assert any(i['id']=='crna' and i['severity']=='gap' for i in state['issues'])
    assert client.post('/api/publish',json={'revision':state['revision']}).status_code==422
    assert client.get('/api/audit').json()[0]['action']=='request_approve'


def test_unavailable_assignment_rejected(client):
    sign_in(client)
    identifier=leave_request(client)
    client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)})
    response=client.post('/api/proposals',json={'summary':'Assign during leave','changes':[{'resource':'shifts','id':'crna','patch':{'clinician_id':'c09'}}],'revision':revision(client)})
    assert response.status_code==422


def test_clinician_cannot_approve_own_leave(client):
    sign_in(client,'clinician')
    identifier=leave_request(client)
    assert client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)}).status_code==403


def test_stale_request_and_duplicate_approval(client):
    sign_in(client)
    identifier=leave_request(client)
    assert client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':1}).status_code==409
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)})
    assert response.status_code==200
    assert client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)}).status_code==409


def test_proposal_stale_after_other_change(client):
    sign_in(client)
    proposal=client.post('/api/proposals',json={'summary':'Preference change','changes':[{'resource':'clinicians','id':'c09','patch':{'preferred_shift_hours':[24]}}],'revision':1}).json()
    leave_request(client)
    response=client.post(f'/api/proposals/{proposal["id"]}/apply',json={'revision':revision(client)})
    assert response.status_code==409


def test_preference_can_be_applied_by_owner(client):
    sign_in(client,'clinician')
    response=client.post('/api/proposals',json={'summary':'Prefer 24-hour shifts','changes':[{'resource':'clinicians','id':'c09','patch':{'preferred_shift_hours':[24]}}],'revision':1})
    assert response.status_code==200,response.text
    identifier=response.json()['id']
    assert client.post(f'/api/proposals/{identifier}/apply',json={'revision':1}).status_code==200
    assert client.post(f'/api/proposals/{identifier}/apply',json={'revision':1}).status_code==200
    data=client.get('/api/state').json()['data']
    assert next(c for c in data['clinicians'] if c['id']=='c09')['preferred_shift_hours']==[24]


def test_clinician_cannot_escalate_qualification(client):
    sign_in(client,'clinician')
    response=client.post('/api/proposals',json={'summary':'Change role','changes':[{'resource':'clinicians','id':'c09','patch':{'role':'MD'}}],'revision':1})
    assert response.status_code==403


def test_assignment_cannot_be_cleared_without_proposal(client):
    sign_in(client)
    for kind in ('shifts','cases'):
        record=copy.deepcopy(small_state()[kind][0]); record['clinician_id']=None
        assert client.post(f'/api/resources/{kind}',json={'record':record,'revision':1}).status_code==422


def test_reassigning_unsupervised_shift_still_rejected(client):
    with Session(db.engine) as session:
        row=session.get(db.State,1); data=small_state(); data['shifts'][0]['clinician_id']=None; data['cases']=[]; row.data=data; session.commit()
    sign_in(client)
    response=client.post('/api/proposals',json={'summary':'Replace unsupervised clinician','changes':[{'resource':'shifts','id':'crna','patch':{'clinician_id':'c10'}}],'revision':1})
    assert response.status_code==422


def test_leave_cancel_requires_review_and_restores_availability(client):
    sign_in(client)
    identifier=leave_request(client)
    client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)})
    sign_in(client,'clinician')
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'cancel','revision':revision(client)})
    assert response.status_code==200
    state=client.get('/api/state').json()
    assert state['data']['requests'][0]['status']=='approved'
    assert state['data']['requests'][0]['details']['cancellation_requested']
    sign_in(client)
    assert client.post(f'/api/requests/{identifier}/action',json={'action':'approve','revision':revision(client)}).status_code==200
    state=client.get('/api/state').json()
    assert state['data']['requests'][0]['status']=='cancelled'
    assert client.post('/api/proposals',json={'summary':'Restore availability assignment','changes':[{'resource':'shifts','id':'crna','patch':{'clinician_id':'c09'}}],'revision':state['revision']}).status_code==200


def test_private_requests_not_visible_to_others(client):
    sign_in(client)
    identifier=leave_request(client)
    with Session(db.engine) as session:
        session.get(db.User,'clinician').clinician_id='c10'; session.commit()
    sign_in(client,'clinician')
    state=client.get('/api/state').json()
    assert not state['data']['requests']
    with Session(db.engine) as session:
        session.get(db.User,'clinician').clinician_id='c09'; session.commit()


def test_csv_preview_invalid_and_stale(client):
    sign_in(client)
    invalid=client.post('/api/import/preview',json={'kind':'shifts','csv':'id,start,end,site_id,specialty,role\nx,2026-09-14T07:00:00,2026-09-14T19:00:00,main,OB,CRNA\n'}).json()
    assert invalid['errors'] and invalid['token'] is None
    exported=client.get('/api/export?kind=clinicians').text
    preview=client.post('/api/import/preview',json={'kind':'clinicians','csv':exported}).json()
    assert not preview['errors'],preview
    leave_request(client)
    assert client.post('/api/import/apply',json={'token':preview['token'],'revision':revision(client)}).status_code==409


def test_backup_contains_committed_data(client):
    import sqlite3,json
    sign_in(client)
    identifier=leave_request(client)
    response=client.post('/api/backup')
    assert response.status_code==200,response.text
    with sqlite3.connect(response.json()['path']) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        state=json.loads(backup.execute('SELECT data FROM application_state').fetchone()[0])
        assert state['requests'][0]['id']==identifier


def test_chat_unavailable_does_not_break_schedule(client,monkeypatch):
    import backend.chat
    async def unavailable(*args): return {'reply':'Local AI is unavailable.','unavailable':True}
    monkeypatch.setattr(backend.chat,'chat_reply',unavailable)
    sign_in(client)
    assert client.post('/api/chat',json={'message':'When am I working?'}).json()['unavailable']
    assert client.get('/api/state').status_code==200


def test_chat_gets_only_redacted_other_leave(client,monkeypatch):
    import backend.chat
    received={}
    async def capture(message,data,user,history):
        received.update(data)
        return {'reply':'Captured'}
    monkeypatch.setattr(backend.chat,'chat_reply',capture)
    sign_in(client)
    identifier=leave_request(client)
    client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)})
    with Session(db.engine) as session:
        session.get(db.User,'clinician').clinician_id='c10'; session.commit()
    sign_in(client,'clinician')
    response=client.post('/api/chat',json={'message':'Who can cover OB?'})
    assert response.status_code==200,response.text
    assert received['requests'][0]['status']=='approved'
    assert 'note' not in received['requests'][0]
    assert 'details' not in received['requests'][0]
    with Session(db.engine) as session:
        session.get(db.User,'clinician').clinician_id='c09'; session.commit()


def setup_swap(client,unrestricted=False):
    with Session(db.engine) as session:
        row=session.get(db.State,1); data=small_state()
        for base,identifier,cid in [(data['shifts'][0],'md2','c02'),(data['shifts'][1],'crna2','c10')]:
            data['shifts'].append({**base,'id':identifier,'clinician_id':cid,'start':'2026-09-16T07:00:00-04:00','end':'2026-09-16T19:00:00-04:00'})
        if unrestricted:
            data['settings'].update(require_scheduler_approval=False,require_swap_acceptance=False)
        row.data=data; session.commit()
    sign_in(client)
    assert client.post('/api/users',json={'username':'colleague','name':'Colleague','role':'clinician','clinician_id':'c10','password':'TestingAtlas!'}).status_code==200
    sign_in(client,'clinician')
    response=client.post('/api/requests',json={'kind':'swap','clinician_id':'c09','details':{'shift_id':'crna','other_shift_id':'crna2','other_clinician_id':'c10'},'revision':1})
    assert response.status_code==200,response.text
    return response.json()['request']


def test_swap_requires_both_consent_and_scheduler(client):
    request=setup_swap(client)
    sign_in(client)
    assert client.post(f'/api/requests/{request["id"]}/action',json={'action':'approve','revision':revision(client)}).status_code==422
    sign_in(client,'colleague')
    accepted=client.post(f'/api/requests/{request["id"]}/action',json={'action':'accept','revision':revision(client)})
    assert accepted.status_code==200 and accepted.json()['status']=='pending'
    sign_in(client)
    applied=client.post(f'/api/requests/{request["id"]}/action',json={'action':'approve','revision':revision(client)})
    assert applied.status_code==200,applied.text
    state=client.get('/api/state').json()
    assert state['data']['cases'][0]['clinician_id']=='c10'
    assert next(s for s in state['data']['shifts'] if s['id']=='crna2')['clinician_id']=='c09'


def test_configurable_swap_self_service(client):
    request=setup_swap(client,True)
    assert request['status']=='approved'
    state=client.get('/api/state').json()
    assert state['data']['cases'][0]['clinician_id']=='c10'


def test_invalid_role_swap_rejected(client):
    sign_in(client,'clinician')
    response=client.post('/api/requests',json={'kind':'swap','clinician_id':'c09','details':{'shift_id':'crna','other_shift_id':'md','other_clinician_id':'c01'},'revision':1})
    assert response.status_code==422,response.text


def test_restart_marks_unfinished_jobs_without_losing_schedule(client):
    with Session(db.engine) as session:
        session.add(db.Job(id='interrupted-test',status='running',stage='staffing',base_revision=1,created_at=db.now(),created_by='admin',snapshot=small_state()))
        session.commit()
    db.initialize()
    with Session(db.engine) as session:
        assert session.get(db.Job,'interrupted-test').status=='interrupted'
        assert session.get(db.State,1).data['shifts'][0]['clinician_id']=='c01'


def test_swap_acceptance_expires_when_shift_times_change(client):
    request=setup_swap(client)
    sign_in(client,'colleague')
    assert client.post(f'/api/requests/{request["id"]}/action',json={'action':'accept','revision':revision(client)}).status_code==200
    sign_in(client)
    state=client.get('/api/state').json()
    changed=next(s for s in state['data']['shifts'] if s['id']=='crna2')
    changed.update(start='2026-09-16T08:00:00-04:00',end='2026-09-16T18:00:00-04:00')
    assert client.post('/api/resources/shifts',json={'record':changed,'revision':state['revision']}).status_code==200
    result=client.post(f'/api/requests/{request["id"]}/action',json={'action':'approve','revision':revision(client)})
    assert result.status_code==409 and 'changed' in result.text
    assert next(s for s in client.get('/api/state').json()['data']['shifts'] if s['id']=='crna')['clinician_id']=='c09'


def test_group_policies_combine_strictest_swap_requirements(client):
    request=setup_swap(client)
    sign_in(client)
    state=client.get('/api/state').json()
    settings={**state['data']['settings'],'require_scheduler_approval':False,'require_swap_acceptance':False,
              'approval_groups':[{'id':'group1','name':'OB team','clinician_ids':['c09'],'require_scheduler_approval':True,'require_swap_acceptance':True,'allow_leave_shortage':False}]}
    assert client.put('/api/settings',json={'settings':settings,'revision':state['revision']}).status_code==200
    assert client.post(f'/api/requests/{request["id"]}/action',json={'action':'approve','revision':revision(client)}).status_code==422
    sign_in(client,'colleague')
    accepted=client.post(f'/api/requests/{request["id"]}/action',json={'action':'accept','revision':revision(client)})
    assert accepted.status_code==200 and accepted.json()['status']=='pending'
    sign_in(client)
    assert client.post(f'/api/requests/{request["id"]}/action',json={'action':'approve','revision':revision(client)}).status_code==200
    identifier=leave_request(client)
    result=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','acknowledge_shortage':True,'revision':revision(client)})
    assert result.status_code==422 and 'requires coverage' in result.text


def test_group_membership_validation(client):
    sign_in(client)
    settings=client.get('/api/state').json()['data']['settings']
    group={'id':'one','name':'One','clinician_ids':['c09'],'require_scheduler_approval':True,'require_swap_acceptance':True,'allow_leave_shortage':True}
    settings['approval_groups']=[group,{**group,'id':'two'}]
    assert client.put('/api/settings',json={'settings':settings,'revision':1}).status_code==422
    settings['approval_groups']=[{**group,'clinician_ids':['nonexistent']}]
    assert client.put('/api/settings',json={'settings':settings,'revision':1}).status_code==404


def test_chat_actions_bind_to_snapshot_revision(client,monkeypatch):
    async def answer(message,data,user,history):
        with Session(db.engine) as session:
            row=session.get(db.State,1); row.revision=2; session.commit()
        return {'reply':'Change preference preview','action':{'type':'proposal','payload':{'summary':'Prefer 24 hours','changes':[{'resource':'clinicians','id':'c09','patch':{'preferred_shift_hours':[24]}}]}}}
    monkeypatch.setattr('backend.chat.chat_reply',answer)
    sign_in(client,'clinician')
    response=client.post('/api/chat',json={'message':'Prefer 24 hour shifts'}).json()
    assert response['base_revision']==1
    payload={**response['action']['payload'],'revision':response['base_revision']}
    assert client.post('/api/proposals',json=payload).status_code==409
    assert client.get('/api/state').json()['revision']==2


def prepare_repair(client,monkeypatch,*,allow_shortage=True,spare=True,as_clinician=False):
    """Capture the worker Future while exercising the real persisted job callback."""
    from concurrent.futures import Future
    from types import SimpleNamespace
    from backend.repair import solve_repair
    with Session(db.engine) as session:
        row=session.get(db.State,1); data=copy.deepcopy(row.data)
        data['settings']['allow_leave_shortage']=allow_shortage
        if not spare:
            next(c for c in data['clinicians'] if c['id']=='c10')['specialties']=['General']
        row.data=data; session.commit()
    sign_in(client,'clinician' if as_clinician else 'admin')
    identifier=leave_request(client)
    state_before=client.get('/api/state').json()
    captured=[]
    def submit(fn,*args):
        future=Future(); captured.append((future,fn,args)); return future
    monkeypatch.setattr('backend.main.executor',SimpleNamespace(submit=submit))
    response=client.post(f'/api/requests/{identifier}/repair-preview',json={'revision':state_before['revision']})
    assert response.status_code==200,response.text
    running=response.json()
    during=client.get('/api/state').json()
    assert during['revision']==state_before['revision'] and during['data']==state_before['data']
    assert 'snapshot' not in running
    future,fn,args=captured[0]
    result=solve_repair(args[0],request=args[1],target=args[2],time_limit=3)
    assert result['status'] in ('OPTIMAL','FEASIBLE'),result
    future.set_result(result)
    job=client.get('/api/jobs/'+running['id']).json()
    assert job['status']=='completed',job
    assert job['proposal_id']
    return identifier,job,state_before


def test_leave_preview_and_atomic_approval_fix_coverage_under_strict_policy(client,monkeypatch):
    identifier,job,before=prepare_repair(client,monkeypatch,allow_shortage=False)
    proposal=client.get('/api/proposals/'+job['proposal_id']).json()
    assert proposal['kind']=='leave_repair'
    change=next(c for c in proposal['changes'] if c['resource']=='shifts' and c['id']=='crna')
    assert change['before']['clinician_id']=='c09' and change['patch']['clinician_id']=='c10'
    assert client.post('/api/proposals/'+proposal['id']+'/apply',json={'revision':before['revision']}).status_code==422
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','repair_proposal_id':proposal['id'],'revision':before['revision']})
    assert response.status_code==200,response.text
    after=client.get('/api/state').json()
    assert after['revision']==before['revision']+1
    assert next(r for r in after['data']['requests'] if r['id']==identifier)['status']=='approved'
    assert next(s for s in after['data']['shifts'] if s['id']=='crna')['clinician_id']=='c10'
    assert after['data']['cases'][0]['clinician_id']=='c10'
    assert not after['issues']
    retry=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','repair_proposal_id':proposal['id'],'revision':before['revision']})
    assert retry.status_code==200 and retry.json()['already_applied']
    assert client.get('/api/state').json()['revision']==after['revision']


def test_owner_can_view_leave_preview_but_cannot_apply(client,monkeypatch):
    identifier,job,before=prepare_repair(client,monkeypatch,as_clinician=True)
    assert client.get(f'/api/requests/{identifier}/repair-preview').json()['id']==job['id']
    assert any(j['id']==job['id'] for j in client.get('/api/state').json()['jobs'])
    assert client.get('/api/proposals/'+job['proposal_id']).status_code==200
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','repair_proposal_id':job['proposal_id'],'revision':before['revision']})
    assert response.status_code==403
    with Session(db.engine) as session:
        session.get(db.User,'clinician').clinician_id='c10'; session.commit()
    assert client.get('/api/jobs/'+job['id']).status_code==403
    assert client.get(f'/api/requests/{identifier}/repair-preview').status_code==403


@pytest.mark.parametrize('allow_shortage',[False,True])
def test_partial_leave_repair_requires_policy_and_acknowledgement(client,monkeypatch,allow_shortage):
    identifier,job,before=prepare_repair(client,monkeypatch,spare=False,allow_shortage=allow_shortage)
    payload={'action':'approve','repair_proposal_id':job['proposal_id'],'revision':before['revision']}
    response=client.post(f'/api/requests/{identifier}/action',json=payload)
    assert response.status_code==(409 if allow_shortage else 422),response.text
    assert client.get('/api/state').json()['data']==before['data']
    response=client.post(f'/api/requests/{identifier}/action',json={**payload,'acknowledge_shortage':True})
    assert response.status_code==(200 if allow_shortage else 422),response.text
    if allow_shortage:
        assert response.json()['remaining_gaps']
        assert any(i['id']=='crna' for i in client.get('/api/state').json()['issues'])


def test_stale_leave_repair_cannot_partially_approve(client,monkeypatch):
    identifier,job,before=prepare_repair(client,monkeypatch)
    record=before['data']['clinicians'][0]
    record={**record,'name':'Updated name'}
    assert client.post('/api/resources/clinicians',json={'record':record,'revision':before['revision']}).status_code==200
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','repair_proposal_id':job['proposal_id'],'revision':revision(client)})
    assert response.status_code==409
    current=client.get('/api/state').json()
    assert next(r for r in current['data']['requests'] if r['id']==identifier)['status']=='pending'
    assert next(s for s in current['data']['shifts'] if s['id']=='crna')['clinician_id']=='c09'
    with Session(db.engine) as session:
        assert session.get(db.Proposal,job['proposal_id']).status=='pending'


def test_preview_candidates_exclude_hypothetical_leave(client,monkeypatch):
    identifier,job,before=prepare_repair(client,monkeypatch)
    response=client.get('/api/coverage/candidates',params={'resource':'cases','id':'case','revision':before['revision'],'preview_job_id':job['id']})
    assert response.status_code==200,response.text
    assert 'c09' not in [c['clinician_id'] for c in response.json()['candidates']]
    assert client.get('/api/state').json()['data']==before['data']


def test_apply_repair_requires_explicit_proposal(client):
    sign_in(client,'clinician')
    identifier=leave_request(client)
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'apply_repair','revision':revision(client)})
    assert response.status_code==422


def test_atomic_repair_claim_rolls_back_on_competing_schedule_edit(client,monkeypatch):
    identifier,job,before=prepare_repair(client,monkeypatch)
    def conflict(*args,**kwargs):
        raise db.Conflict('Competing schedule update')
    monkeypatch.setattr('backend.main.save_state',conflict)
    response=client.post(f'/api/requests/{identifier}/action',json={'action':'approve','repair_proposal_id':job['proposal_id'],'revision':before['revision']})
    assert response.status_code==409
    with Session(db.engine) as session:
        assert session.get(db.Proposal,job['proposal_id']).status=='pending'
        assert session.get(db.State,1).data==before['data']


def test_repair_proposal_cannot_approve_another_request(client,monkeypatch):
    _,job,before=prepare_repair(client,monkeypatch)
    second=leave_request(client)
    response=client.post(f'/api/requests/{second}/action',json={'action':'approve','repair_proposal_id':job['proposal_id'],'revision':revision(client)})
    assert response.status_code==404
    assert all(r['status']=='pending' for r in client.get('/api/state').json()['data']['requests'])


@pytest.mark.parametrize('status',['UNKNOWN','INFEASIBLE','MODEL_INVALID'])
def test_unsuccessful_repair_never_produces_applicable_changes(client,monkeypatch,status):
    from concurrent.futures import Future
    from types import SimpleNamespace
    sign_in(client)
    identifier=leave_request(client)
    before=client.get('/api/state').json()
    future=Future()
    monkeypatch.setattr('backend.main.executor',SimpleNamespace(submit=lambda *args:future))
    running=client.post(f'/api/requests/{identifier}/repair-preview',json={'revision':before['revision']}).json()
    future.set_result({'status':status,'changes':[],'explanations':['No valid repair result.']})
    job=client.get('/api/jobs/'+running['id']).json()
    assert job['status']=='completed' and not job['proposal_id']
    assert client.get('/api/state').json()['data']==before['data']
    assert client.get('/api/coverage/candidates',params={'resource':'cases','id':'case','revision':before['revision'],'preview_job_id':job['id']}).status_code==409


def test_late_worker_result_cannot_revive_interrupted_job(client,monkeypatch):
    from concurrent.futures import Future
    from types import SimpleNamespace
    sign_in(client)
    identifier=leave_request(client)
    future=Future()
    monkeypatch.setattr('backend.main.executor',SimpleNamespace(submit=lambda *args:future))
    running=client.post(f'/api/requests/{identifier}/repair-preview',json={'revision':revision(client)}).json()
    db.initialize()
    future.set_result({'status':'FEASIBLE','changes':[]})
    job=client.get('/api/jobs/'+running['id']).json()
    assert job['status']=='interrupted' and not job['proposal_id']


def test_chat_clarification_persists_choices_and_exact_revision(client):
    with Session(db.engine) as session:
        row=session.get(db.State,1); data=copy.deepcopy(row.data)
        data['clinicians'][0]['name']='Alex Smith'; data['clinicians'][1]['name']='Chris Smith'
        row.data=data; session.commit()
    sign_in(client)
    response=client.post('/api/chat',json={'message':"Show Smith's schedule on September 14, 2026"})
    assert response.status_code==200,response.text
    result=response.json(); clarification=result['clarification']
    assert len(clarification['choices'])==2 and 'context' not in result
    with Session(db.engine) as session:
        context=session.get(db.Conversation,result['conversation_id']).context
        assert context['base_revision']==1
    selected=client.post('/api/chat',json={'conversation_id':result['conversation_id'],
        'selection':{'clarification_id':clarification['id'],'choice_id':clarification['choices'][0]['id']}})
    assert selected.status_code==200,selected.text
    assert not selected.json().get('action')
    assert 'Alex Smith' in selected.json()['reply']


def test_concurrent_chat_response_cannot_overwrite_newer_context(client,monkeypatch):
    with Session(db.engine) as session:
        session.add(db.Conversation(id='shared-chat',user_id='admin',history=[],context={},revision=0));session.commit()
    async def slow_response(message,data,user,history):
        with Session(db.engine) as session:
            row=session.get(db.Conversation,'shared-chat')
            row.revision=1;row.context={'newer':True};row.history=[{'role':'assistant','content':'Newer answer'}];session.commit()
        return {'reply':'Stale answer','context':{'older':True}}
    monkeypatch.setattr('backend.chat.chat_reply',slow_response)
    sign_in(client)
    response=client.post('/api/chat',json={'conversation_id':'shared-chat','message':'Question'})
    assert response.status_code==409,response.text
    with Session(db.engine) as session:
        row=session.get(db.Conversation,'shared-chat')
        assert row.context=={'newer':True} and row.history[0]['content']=='Newer answer'
