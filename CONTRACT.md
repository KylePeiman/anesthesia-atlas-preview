# Implementation contract

The app is a local anesthesia scheduling prototype. All data is fictional initially. Do not edit sources/. Multiple agents are working in this directory; preserve each other's changes.

## State returned by GET /api/state

`{revision:number, user:{id,username,role,clinician_id,name}, data:{settings,sites,clinicians,shifts,cases,requests}, issues:[], proposals:[], jobs:[]}`. All IDs are strings. Timestamps are ISO strings with offsets. Default timezone America/New_York.

- settings: `{timezone, min_rest_hours:10, post_call_rest_hours:12, max_weekly_hours:60, supervision_ratio:3, require_scheduler_approval:true, require_swap_acceptance:true, allow_leave_shortage:true, demo:true}`. These are editable DEMONSTRATION rules, not clinical/legal recommendations.
- sites: `[{id,name}]`
- clinicians: `[{id,name,role:'MD'|'CRNA'|'CAA'|'PA',specialties:['OB','Orthopedics','General'],sites:[site_id],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:[],call_eligible:true,supervision_required:false,active:true}]`. Availability is a window for non-call shifts; call eligibility permits call outside it. MD supervision_required false; demo CRNAs true. Role/eligibility always explicit.
- shifts: `[{id,start,end,site_id,specialty,role,kind:'day'|'primary_call'|'backup_call',clinician_id:null|string,locked:false,status:'draft'|'published'}]`. One required clinician per shift. Each shift is one coverage slot. Backup call is reserved work and cannot overlap other assignments in this prototype. Published schedule can have gaps after leave; initially publishing requires no gaps.
- cases: `[{id,title,room,start,end,site_id,specialty,role,clinician_id:null|string,locked:false}]`. Cases must be inside a matching assigned day/primary-call shift at their site. They don't add staffing demand beyond those shifts. No patient identifiers. Cases are fixed times.
- requests: `[{id,kind:'leave'|'swap'|'preferences',clinician_id,start?,end?,note,status:'pending'|'approved'|'rejected'|'cancelled',details:{...},created_by,created_at}]`. Approved leave intervals override all availability. Swap details `{shift_id,other_shift_id,other_clinician_id,accepted_by:[]}`. Preference details are clinician patch. Leave approvals may clear affected shift/case clinician_id and unlock them with audit; all gaps remain explicit.
- issues: `[{code,message,resource:'shifts'|'cases'|'clinicians',id,severity:'error'|'gap',clinician_id?}]`.
- changes: `[{resource:'shifts'|'cases'|'clinicians'|'requests',id,patch:{...}}]`.
- proposal: `{id,summary,kind,changes,base_revision,status:'pending'|'applied'|'rejected',created_at,created_by,details:{...}}`.

## Backend routes

- POST /api/auth/login `{username,password}` -> user; POST /api/auth/logout; GET /api/auth/me.
- GET /api/state (authenticated). Clinicians see team assignments and clinician directory but only own requests/proposals, no others' leave reasons. Admin/scheduler see all.
- POST /api/resources/{clinicians|shifts|cases|sites} `{record,revision}` -> revision. Upsert. Staff/settings editing admin/scheduler only. Single assignment edits use proposals.
- DELETE /api/resources/{kind}/{id}?revision=N (admin/scheduler).
- PUT /api/settings `{settings,revision}`.
- POST /api/proposals `{summary,changes,revision}` -> proposal,issues. Validated preview; not applied. POST /api/proposals/{id}/apply `{revision}`; POST /api/proposals/{id}/reject.
- POST /api/optimize `{stage:'staffing'|'daily',date?:'YYYY-MM-DD',month?:'YYYY-MM',revision}` -> job. GET /api/jobs/{id}. Completed job includes proposal_id if applicable, result.status/metrics/gaps. GET state includes jobs. Staffing edits the selected month, with outside assignments fixed for boundary checks.
- POST /api/publish `{revision}` publishes shifts after validation.
- POST /api/requests `{kind,clinician_id,start?,end?,note?,details?,revision}` -> request/impact. POST /api/requests/{id}/action `{action:'approve'|'reject'|'cancel'|'accept',acknowledge_shortage?:bool,revision}`. Approve requires scheduler except configured valid self-service swaps after both accept. Leave cancellation can be requested by owner via action and reviewed according to implementation.
- POST /api/leave-impact `{clinician_id,start,end}` -> `{affected_shifts,affected_cases,lost_hours,issues}` read-only preview.
- POST /api/import/preview `{kind:'clinicians'|'shifts'|'cases',csv:string}` -> `{token,records,errors}`. POST /api/import/apply `{token,revision}`.
- GET /api/export?kind=shifts -> CSV download. POST /api/backup -> local backup filename; GET /api/audit (scheduler/admin).
- POST /api/chat `{message,conversation_id?:string}` -> `{conversation_id,reply,proposal?:object,action?:object,references?:[],unavailable?:bool}`. `action` may be `{type:'request',payload:{...}}` or `{type:'proposal',payload:{...}}`; UI shows preview and submits through ordinary request/proposal API only on user action. AI never directly commits changes.
- GET /api/health -> application health (no sensitive data); GET /api/ai/status authenticated.

Errors JSON `{detail:string|object}`; conflicts return 409. Mutation requests use current revision. Frontend refreshes state after mutations. APIs same origin, cookie sessions.

## Solver module ownership / interface

`backend/solver.py` exports `validate_schedule(data:dict)->list[dict]`, `solve_schedule(data:dict,stage:str='staffing',date:str|None=None,time_limit:float=30)->dict`, `leave_impact(data,clinician_id,start,end)->dict`. Solver is independent of DB; deepcopy before changes. Result `{status,changes,gaps,metrics,explanations}`. Include explicit uncovered slots as diagnostic gaps, never relax eligibility/rest/approved leave. Return feasible partial proposal if full coverage unavailable, clearly marked gaps; publication blocked. Maintain locked assignments, and only auto-repair eligible scope. daily respects staffing stage.

## Chat module ownership / interface

`backend/chat.py` exports async `chat_reply(message:str,data:dict,user:dict,history:list[dict]|None=None)->dict` and async `ai_status()->dict`. Data already permissions filtered; no DB/network outside loopback Ollama. Return reply + action preview + references and history. Validate structured intent/actions locally. Use installed qwen3.5:4b, localhost 11434, no cloud fallback. Show useful unavailable error. Root handles conversation persistence, action application, access controls.

## Frontend

Own frontend/ only. React+TypeScript+Vite, no external CDNs/fonts. Root installs dependencies/builds, serves dist through FastAPI. Use clear dense working interface branded Anesthesia Atlas with navy/blue/teal, white/slate surfaces, comfortable contrast. Desktop sidebar, calendar first, responsive mobile. Initial state login; no fake successful controls. Poll jobs while running. Show coverage gaps, request statuses, demo badge, timezone, revision. Provide editor dialogs/forms, exact change previews, leave impact, login/account display and chat. User explicitly requested local Python stack; do not invoke Sites skills/tools.
