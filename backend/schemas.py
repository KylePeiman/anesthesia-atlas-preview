from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Availability(Strict):
    weekdays:list[int]=Field(default_factory=lambda:[0,1,2,3,4])
    start:str='07:00'
    end:str='19:00'

    @field_validator('weekdays')
    @classmethod
    def days(cls,value):
        if len(value)!=len(set(value)) or any(day<0 or day>6 for day in value):
            raise ValueError('Weekdays must be unique values from 0 to 6.')
        return value

    @field_validator('start','end')
    @classmethod
    def clock(cls,value):
        datetime.strptime(value,'%H:%M')
        return value


class Clinician(Strict):
    id:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=100)
    role:Literal['MD','CRNA','CAA','PA']
    specialties:list[str]=Field(min_length=1,max_length=30)
    sites:list[str]=Field(min_length=1,max_length=3)
    availability:Availability=Field(default_factory=Availability)
    fte:float=Field(default=1,gt=0,le=2)
    target_hours:float=Field(default=144,ge=0,le=744)
    preferred_shift_hours:list[float]=Field(default_factory=lambda:[12])
    preferred_specialties:list[str]=Field(default_factory=list)
    call_eligible:bool=False
    supervision_required:bool=False
    active:bool=True


def aware(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Include a timezone offset in dates and times.')
    return value


class Interval(Strict):
    start:str
    end:str
    _aware=field_validator('start','end')(aware)

    @model_validator(mode='after')
    def ordered(self):
        if datetime.fromisoformat(self.end.replace('Z','+00:00'))<=datetime.fromisoformat(self.start.replace('Z','+00:00')):
            raise ValueError('End must be after start.')
        return self


class Shift(Interval):
    id:str=Field(min_length=1,max_length=150)
    site_id:str
    specialty:str=Field(min_length=1)
    role:Literal['MD','CRNA','CAA','PA']
    kind:Literal['day','primary_call','backup_call']='day'
    clinician_id:str|None=None
    locked:bool=False
    status:Literal['draft','published']='draft'


class Case(Interval):
    id:str=Field(min_length=1,max_length=150)
    title:str=Field(min_length=1,max_length=150)
    room:str=Field(min_length=1,max_length=50)
    site_id:str
    specialty:str=Field(min_length=1)
    role:Literal['MD','CRNA','CAA','PA']='CRNA'
    clinician_id:str|None=None
    locked:bool=False


class Site(Strict):
    id:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=100)


class ApprovalGroup(Strict):
    id:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=100)
    clinician_ids:list[str]=Field(default_factory=list)
    require_scheduler_approval:bool=True
    require_swap_acceptance:bool=True
    allow_leave_shortage:bool=True


class Settings(Strict):
    timezone:str='America/New_York'
    min_rest_hours:float=Field(default=10,ge=0,le=48)
    post_call_rest_hours:float=Field(default=12,ge=0,le=72)
    max_weekly_hours:float=Field(default=60,gt=0,le=168)
    supervision_ratio:int=Field(default=3,ge=1,le=8)
    require_scheduler_approval:bool=True
    require_swap_acceptance:bool=True
    allow_leave_shortage:bool=True
    approval_groups:list[ApprovalGroup]=Field(default_factory=list,max_length=50)
    demo:bool=True

    @model_validator(mode='after')
    def unique_groups(self):
        ids=[group.id for group in self.approval_groups]
        members=[cid for group in self.approval_groups for cid in group.clinician_ids]
        if len(ids)!=len(set(ids)) or len(members)!=len(set(members)):
            raise ValueError('Groups must have unique IDs and each clinician can belong to only one approval group.')
        return self

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,value):
        ZoneInfo(value)
        return value


RECORD_MODELS={'clinicians':Clinician,'shifts':Shift,'cases':Case,'sites':Site}


class Login(Strict):
    username:str=Field(min_length=1,max_length=100)
    password:str=Field(min_length=1,max_length=256)


class Upsert(Strict):
    record:dict
    revision:int


class SettingsUpdate(Strict):
    settings:Settings
    revision:int


class Change(Strict):
    resource:Literal['shifts','cases','clinicians']
    id:str
    patch:dict


class ProposalInput(Strict):
    summary:str=Field(min_length=1,max_length=500)
    changes:list[Change]=Field(min_length=1,max_length=2000)
    revision:int


class Revision(Strict):
    revision:int


class Optimize(Revision):
    stage:Literal['staffing','daily']='staffing'
    date:str|None=None
    month:str|None=Field(default=None,pattern=r'^\d{4}-(0[1-9]|1[0-2])$')


class RequestInput(Strict):
    kind:Literal['leave','swap','preferences']
    clinician_id:str
    start:str|None=None
    end:str|None=None
    note:str=Field(default='',max_length=1000)
    details:dict=Field(default_factory=dict)
    revision:int


class RequestAction(Revision):
    action:Literal['approve','reject','cancel','accept','apply_repair']
    acknowledge_shortage:bool=False
    repair_proposal_id:str|None=None

    @model_validator(mode='after')
    def repair_action(self):
        if self.action=='apply_repair' and not self.repair_proposal_id:
            raise ValueError('Choose a reviewed repair proposal.')
        if self.repair_proposal_id and self.action not in ('approve','apply_repair'):
            raise ValueError('A repair proposal can only approve leave or apply a repair.')
        return self


class CoverageRepair(Revision):
    resource:Literal['shifts','cases']
    id:str=Field(min_length=1,max_length=150)


class LeaveImpact(Interval):
    clinician_id:str


class ImportInput(Strict):
    kind:Literal['clinicians','shifts','cases']
    csv:str=Field(max_length=2_000_000)


class ImportApply(Revision):
    token:str


class ChatSelection(Strict):
    clarification_id:str=Field(min_length=1,max_length=150)
    choice_id:str=Field(min_length=1,max_length=200)


class ChatInput(Strict):
    message:str=Field(default='',max_length=4000)
    conversation_id:str|None=None
    selection:ChatSelection|None=None

    @model_validator(mode='after')
    def message_or_selection(self):
        if not self.message.strip() and self.selection is None:
            raise ValueError('Enter a message or select a clarification option.')
        return self


class UserInput(Strict):
    username:str=Field(min_length=3,max_length=50,pattern=r'^[a-zA-Z0-9_.-]+$')
    name:str=Field(min_length=1,max_length=100)
    role:Literal['admin','scheduler','clinician']
    clinician_id:str|None=None
    password:str=Field(min_length=10,max_length=256)


class PasswordInput(Strict):
    current_password:str
    new_password:str=Field(min_length=10,max_length=256)
