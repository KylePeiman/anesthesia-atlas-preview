from __future__ import annotations
import copy
import hashlib
import json
from datetime import datetime
from fastapi import HTTPException
from pydantic import ValidationError
from .schemas import RECORD_MODELS, Clinician, Interval
from .db import Proposal, now, uid
from .solver import validate_schedule, leave_impact


def manager(user):
    if user['role'] not in ('admin','scheduler'):
        raise HTTPException(403,'This action requires a scheduler account.')


def approval_policy(data,request):
    """Cross-group swaps must satisfy both participating groups' policies."""
    settings=data['settings']
    people=[request['clinician_id']]
    if request['kind']=='swap':
        people.append(request['details']['other_clinician_id'])
    policies=[next((group for group in settings.get('approval_groups',[]) if cid in group['clinician_ids']),settings) for cid in people]
    return {'require_scheduler_approval':any(p['require_scheduler_approval'] for p in policies),
            'require_swap_acceptance':any(p['require_swap_acceptance'] for p in policies),
            'allow_leave_shortage':all(p['allow_leave_shortage'] for p in policies)}


def find(data,kind,identifier):
    for record in data[kind]:
        if record['id']==identifier:
            return record
    raise HTTPException(404,f'{kind.rstrip("s").title()} not found.')


def validate_record(data,kind,record):
    try:
        record=RECORD_MODELS[kind].model_validate(record).model_dump()
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422,str(exc))
    if kind=='clinicians':
        for site_id in record['sites']:
            find(data,'sites',site_id)
    if kind in ('shifts','cases'):
        find(data,'sites',record['site_id'])
        if record.get('clinician_id'):
            find(data,'clinicians',record['clinician_id'])
    return record


def errors(data):
    return [issue for issue in validate_schedule(data) if issue.get('severity')=='error']


def reject_new_errors(before,after):
    def fingerprint(issue):
        return tuple(issue.get(key) for key in ('code','resource','id','clinician_id','message'))
    existing={fingerprint(i) for i in errors(before)}
    fresh=[i for i in errors(after) if fingerprint(i) not in existing]
    if fresh:
        raise HTTPException(422,{'message':'This change violates scheduling rules.','issues':fresh[:40]})


def preview_changes(data,changes,user):
    revised=copy.deepcopy(data)
    seen=set()
    for change in changes:
        kind=change['resource']; identifier=change['id']; patch=change['patch']
        if (kind,identifier) in seen:
            raise HTTPException(422,'Combine changes to the same record into one patch.')
        seen.add((kind,identifier))
        if kind not in ('clinicians','shifts','cases'):
            raise HTTPException(422,'Unsupported change resource.')
        old=find(revised,kind,identifier)
        if 'id' in patch or 'status' in patch:
            raise HTTPException(422,'Record IDs and publication status cannot be changed here.')
        if user['role']=='clinician':
            if kind!='clinicians' or identifier!=user['clinician_id'] or not set(patch)<= {'preferred_shift_hours','preferred_specialties'}:
                raise HTTPException(403,'Submit a leave or swap request to change assignments. You can directly update your own preferences.')
        updated=validate_record(revised,kind,{**old,**patch})
        revised[kind]=[updated if r['id']==identifier else r for r in revised[kind]]
    reject_new_errors(data,revised)
    changed_safety={(c['resource'],c['id']) for c in changes if c['resource'] in ('shifts','cases') and c['patch']!={'locked':False}}
    remaining=[i for i in errors(revised) if (i.get('resource'),i.get('id')) in changed_safety]
    if remaining:
        raise HTTPException(422,{'message':'Resolve the safety conflicts on these assignments before applying the change.','issues':remaining[:40]})
    return revised


def add_proposal(db,data,user,revision,summary,changes,kind='manual',details=None,original_data=None):
    revised=preview_changes(data,changes,user)
    enriched=[]
    for change in changes:
        old=find(original_data if original_data is not None else data,change['resource'],change['id'])
        enriched.append({**change,'before':{key:old.get(key) for key in change['patch']}})
    proposal=Proposal(id=uid('p'),summary=summary,kind=kind,changes=enriched,base_revision=revision,status='pending',created_at=now(),created_by=user['id'],details=details or {})
    db.add(proposal)
    return proposal,validate_schedule(revised)


def overlaps(a,b):
    return datetime.fromisoformat(a['start'].replace('Z','+00:00'))<datetime.fromisoformat(b['end'].replace('Z','+00:00')) and datetime.fromisoformat(b['start'].replace('Z','+00:00'))<datetime.fromisoformat(a['end'].replace('Z','+00:00'))


def leave_changes(data,request):
    changes=[]
    for kind in ('shifts','cases'):
        for record in data[kind]:
            if record.get('clinician_id')==request['clinician_id'] and overlaps(record,request):
                changes.append({'resource':kind,'id':record['id'],'patch':{'clinician_id':None,'locked':False}})
    return changes


def swap_changes(data,request):
    details=request['details']
    first=find(data,'shifts',details['shift_id'])
    second=find(data,'shifts',details['other_shift_id'])
    signatures=details.get('shift_snapshots')
    if signatures and any(signatures.get(shift['id'])!={key:shift.get(key) for key in ('start','end','site_id','specialty','role','kind','clinician_id')} for shift in (first,second)):
        raise HTTPException(409,'The shift dates or requirements changed after this swap was requested. Submit a new request so both clinicians can review it.')
    if first['id']==second['id']:
        raise HTTPException(422,'Choose two different shifts.')
    if first.get('clinician_id')!=request['clinician_id'] or second.get('clinician_id')!=details['other_clinician_id']:
        raise HTTPException(409,'These shifts have changed since the swap was requested. Submit a new request.')
    if first.get('locked') or second.get('locked'):
        raise HTTPException(422,'A scheduler must unlock these assignments before a swap.')
    changes=[]
    for original,replacement in [(first,second['clinician_id']),(second,first['clinician_id'])]:
        changes.append({'resource':'shifts','id':original['id'],'patch':{'clinician_id':replacement}})
        for case in data['cases']:
            if case.get('clinician_id')==original['clinician_id'] and case['site_id']==original['site_id'] and overlaps(case,original):
                if case.get('locked'):
                    raise HTTPException(422,'A linked case is locked. Ask a scheduler to review it.')
                changes.append({'resource':'cases','id':case['id'],'patch':{'clinician_id':replacement}})
    preview_changes(data,changes,{'role':'scheduler'})
    return changes


def validate_request(data,payload,user):
    find(data,'clinicians',payload['clinician_id'])
    if user['role']=='clinician' and payload['clinician_id']!=user['clinician_id']:
        raise HTTPException(403,'You can only request changes for yourself.')
    kind=payload['kind']
    details=payload.get('details') or {}
    if kind=='leave':
        try:
            Interval.model_validate({'start':payload.get('start'),'end':payload.get('end')})
        except (ValidationError,ValueError) as exc:
            raise HTTPException(422,str(exc))
        previous=details.get('replaces_request_id')
        if previous:
            old=find(data,'requests',previous)
            if old['kind']!='leave' or old['clinician_id']!=payload['clinician_id'] or old['status']!='approved':
                raise HTTPException(422,'Only an approved leave request for this clinician can be replaced.')
        details={'replaces_request_id':previous} if previous else {}
    elif kind=='preferences':
        if not details or not set(details)<= {'preferred_shift_hours','preferred_specialties'}:
            raise HTTPException(422,'Only shift-length and specialty preferences can be updated here.')
        validate_record(data,'clinicians',{**find(data,'clinicians',payload['clinician_id']),**details})
    else:
        if not all(details.get(k) for k in ('shift_id','other_shift_id','other_clinician_id')):
            raise HTTPException(422,'A swap needs both shifts and the other clinician.')
        if details['other_clinician_id']==payload['clinician_id']:
            raise HTTPException(422,'A swap must involve two clinicians.')
        details={k:details[k] for k in ('shift_id','other_shift_id','other_clinician_id')}
        details['accepted_by']=[user['clinician_id']] if user.get('clinician_id')==payload['clinician_id'] else []
        swap_changes(data,{**payload,'details':details})
        details['shift_snapshots']={sid:{key:find(data,'shifts',sid).get(key) for key in ('start','end','site_id','specialty','role','kind','clinician_id')} for sid in (details['shift_id'],details['other_shift_id'])}
    return {**payload,'details':details}


def apply_request(data,request,user,action,acknowledge=False):
    """All request state transitions and consequential assignment changes."""
    if action not in ('cancel','reject','accept','approve'):
        raise HTTPException(422,'This action requires a reviewed repair proposal.')
    revised=copy.deepcopy(data)
    request=find(revised,'requests',request['id'])
    own=user.get('clinician_id')==request['clinician_id']
    is_manager=user['role'] in ('admin','scheduler')
    details=request.setdefault('details',{})
    policy=approval_policy(revised,request)
    state=request['status']
    if action=='cancel':
        if not own and not is_manager:
            raise HTTPException(403,'You cannot cancel this request.')
        if state not in ('pending','approved'):
            raise HTTPException(409,'This request is already closed.')
        if state=='approved' and request['kind']!='leave':
            raise HTTPException(422,'Applied changes need a new request; they cannot be undone by cancelling their record.')
        if state=='approved' and not is_manager:
            details['cancellation_requested']=True
        else:
            request['status']='cancelled'
        return revised,{'status':request['status']}
    if action=='reject':
        manager(user)
        if details.get('cancellation_requested'):
            details.pop('cancellation_requested')
            return revised,{'status':'approved','message':'Cancellation request declined.'}
        if state!='pending':
            raise HTTPException(409,'This request is already closed.')
        request['status']='rejected'
        return revised,{'status':'rejected'}
    if action=='accept':
        if request['kind']!='swap' or state!='pending':
            raise HTTPException(422,'Only pending swaps can be accepted.')
        if user.get('clinician_id') not in (request['clinician_id'],details['other_clinician_id']):
            raise HTTPException(403,'Only the two participating clinicians can accept this swap.')
        swap_changes(revised,request)
        details['accepted_by']=list(set(details.get('accepted_by',[])+[user['clinician_id']]))
        both_required=policy['require_swap_acceptance']
        both_accepted=set([request['clinician_id'],details['other_clinician_id']])<=set(details['accepted_by'])
        if policy['require_scheduler_approval'] or (both_required and not both_accepted):
            return revised,{'status':'pending','message':'Acceptance recorded.'}
    elif action=='approve':
        manager(user)
    if details.get('cancellation_requested') and state=='approved':
        manager(user)
        request['status']='cancelled'
        details.pop('cancellation_requested',None)
        return revised,{'status':'cancelled'}
    if state!='pending':
        raise HTTPException(409,'This request was already processed.')
    impact=None
    changes=[]
    if request['kind']=='leave':
        replacement=details.get('replaces_request_id')
        if replacement:
            old=find(revised,'requests',replacement)
            if old['status']!='approved':
                raise HTTPException(409,'The original leave changed; review the replacement request.')
            old['status']='cancelled'
        impact=leave_impact(revised,request['clinician_id'],request['start'],request['end'])
        changes=leave_changes(revised,request)
        shortage=bool(changes or impact.get('issues'))
        if shortage and not policy['allow_leave_shortage']:
            raise HTTPException(422,'This group requires coverage to be resolved before approving leave.')
        if shortage and not acknowledge:
            raise HTTPException(409,{'message':'Review and acknowledge the coverage impact before approving this leave.','impact':impact})
        details['impact']=impact
        details['shortage_acknowledged']=acknowledge
        for change in changes:
            find(revised,change['resource'],change['id']).update(change['patch'])
    elif request['kind']=='swap':
        if policy['require_swap_acceptance'] and not set([request['clinician_id'],details['other_clinician_id']])<=set(details.get('accepted_by',[])):
            raise HTTPException(422,'Both clinicians must accept the swap first.')
        changes=swap_changes(revised,request)
        revised=preview_changes(revised,changes,{'role':'scheduler'})
        request=find(revised,'requests',request['id'])
    else:
        clinician=find(revised,'clinicians',request['clinician_id'])
        clinician.update(details)
    request['status']='approved'
    request['approved_by']=user['id']
    request['approved_at']=now()
    return revised,{'status':'approved','impact':impact,'changes':changes}


def visible_data(data,user):
    result=copy.deepcopy(data)
    if user['role']=='clinician':
        cid=user['clinician_id']
        result['requests']=[r for r in result['requests'] if r['clinician_id']==cid or (r['kind']=='swap' and r.get('details',{}).get('other_clinician_id')==cid)]
    return result


def request_fingerprint(request):
    relevant={key:request.get(key) for key in ('id','kind','clinician_id','start','end','status')}
    relevant['replaces_request_id']=request.get('details',{}).get('replaces_request_id')
    relevant['cancellation_requested']=request.get('details',{}).get('cancellation_requested',False)
    return hashlib.sha256(json.dumps(relevant,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def apply_leave_repair(data,request,proposal,user,acknowledge=False):
    """Validate the exact reviewed leave plus assignments as one state change."""
    from .repair import simulate_leave
    manager(user)
    metadata=proposal.details
    if proposal.kind!='leave_repair' or metadata.get('request_id')!=request['id']:
        raise HTTPException(422,'This repair proposal belongs to a different request.')
    if request['kind']!='leave' or request['status'] not in ('pending','approved') or request.get('details',{}).get('cancellation_requested'):
        raise HTTPException(409,'The leave request changed. Generate a new preview.')
    if metadata.get('request_fingerprint')!=request_fingerprint(request):
        raise HTTPException(409,'The leave details changed. Generate a new preview.')
    before_impact=leave_impact(data,request['clinician_id'],request['start'],request['end'])
    hypothetical=simulate_leave(data,request)
    revised=preview_changes(hypothetical,proposal.changes,{'role':'scheduler'})
    reject_new_errors(data,revised)
    final_issues=validate_schedule(revised)
    baseline={(i['code'],i['resource'],i['id'],i.get('clinician_id'),i['message']) for i in validate_schedule(data)}
    scope=metadata.get('scope',{})
    relevant={('shifts',sid) for sid in scope.get('shift_ids',[])} | {('cases',cid) for cid in scope.get('case_ids',[])}
    remaining=[i for i in final_issues if (i['resource'],i['id']) in relevant or (i['code'],i['resource'],i['id'],i.get('clinician_id'),i['message']) not in baseline]
    if remaining and not approval_policy(data,request)['allow_leave_shortage']:
        raise HTTPException(422,{'message':'This group requires coverage to be resolved before approving leave.','issues':remaining})
    if remaining and not acknowledge:
        raise HTTPException(409,{'message':'Review and acknowledge the remaining coverage gaps before applying this repair.','issues':remaining})
    updated=find(revised,'requests',request['id'])
    timestamp=now()
    updated['details']={**updated.get('details',{}),'impact':before_impact,'shortage_acknowledged':bool(remaining and acknowledge),
                        'repair_proposal_id':proposal.id,'repair_applied_by':user['id'],'repair_applied_at':timestamp,
                        'remaining_gaps':remaining}
    if request['status']=='pending':
        updated.update(approved_by=user['id'],approved_at=timestamp)
    return revised,{'status':'approved','proposal_id':proposal.id,'impact':before_impact,'remaining_gaps':remaining,'changes':proposal.changes}
