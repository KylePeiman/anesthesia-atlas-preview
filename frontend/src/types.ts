export type User = {id:string;username:string;role:string;clinician_id:string|null;name:string};
export type Site = {id:string;name:string};
export type Clinician = {id:string;name:string;role:string;specialties:string[];sites:string[];availability:{weekdays:number[];start:string;end:string};fte:number;target_hours:number;preferred_shift_hours:number[];preferred_specialties:string[];call_eligible:boolean;supervision_required:boolean;active:boolean};
export type Shift = {id:string;start:string;end:string;site_id:string;specialty:string;role:string;kind:string;clinician_id:string|null;locked:boolean;status:string};
export type Case = {id:string;title:string;room:string;start:string;end:string;site_id:string;specialty:string;role:string;clinician_id:string|null;locked:boolean};
export type Change = {resource:string;id:string;patch:Record<string,unknown>};
export type Request = {id:string;kind:string;clinician_id:string;start?:string;end?:string;note:string;status:string;details:Record<string,any>;created_by:string;created_at:string};
export type Issue = {code:string;message:string;resource:string;id:string;severity:string;clinician_id?:string};
export type Proposal = {id:string;summary:string;kind:string;changes:Change[];base_revision:number;status:string;created_at:string;created_by:string;details:Record<string,any>};
export type RecordResource = 'shifts'|'cases'|'clinicians'|'requests';
export type RecordLink = {resource:RecordResource;id:string;label:string;reasons?:string[]};
export type RepairContext = {kind:'leave_repair'|'coverage_repair';request_id?:string;target?:{resource:'shifts'|'cases';id:string}};
export type RepairResult = {status?:string;message?:string;changes?:Change[];preview?:{shifts:Shift[];cases:Case[]};scope?:{shift_ids:string[];case_ids:string[]};metrics?:Record<string,unknown>;gaps?:Issue[];new_issues?:Issue[];existing_issues?:Issue[];released_locks?:{resource:string;id:string}[];explanations?:unknown[]};
export type Job = {id:string;status:string;stage?:string;context?:RepairContext;proposal_id?:string;result?:RepairResult;base_revision?:number;error?:string;created_at?:string};
export type CoverageCandidate = {clinician_id:string;name:string;role:string;reason:string;changes:Change[]};
export type CoverageRecommendations = {resource:string;id:string;base_revision:number;candidates:CoverageCandidate[];reason?:string;requires_staffing_repair?:boolean;missing_coverage?:boolean;issues?:Issue[];existing_issues?:Issue[]};
export type ApprovalGroup = {id:string;name:string;clinician_ids:string[];require_scheduler_approval:boolean;require_swap_acceptance:boolean;allow_leave_shortage:boolean};
export type Settings = {approval_groups?:ApprovalGroup[];timezone:string;min_rest_hours:number;post_call_rest_hours:number;max_weekly_hours:number;supervision_ratio:number;require_scheduler_approval:boolean;require_swap_acceptance:boolean;allow_leave_shortage:boolean;demo:boolean;[key:string]:unknown};
export type State = {revision:number;user:User;data:{settings:Settings;sites:Site[];clinicians:Clinician[];shifts:Shift[];cases:Case[];requests:Request[]};issues:Issue[];proposals:Proposal[];jobs:Job[]};
export type Impact = {affected_shifts:any[];affected_cases:any[];lost_hours:number;issues:Issue[]};
export type ChatResult = {base_revision?:number;conversation_id?:string;reply:string;proposal?:Proposal;action?:{type:string;payload:Record<string,any>};references?:RecordLink[];query?:{resource:string;coverage:string;date_from:string;date_to:string;total:number};clarification?:{id:string;prompt:string;choices:{id:string;label:string;resource?:string;record_id?:string}[]};unavailable?:boolean};
export class ApiError extends Error { constructor(public status:number,message:string) {super(message)} }
export async function api<T=any>(path:string,body?:unknown,method?:string):Promise<T> {
  if(STATIC_PREVIEW)return staticRequest<T>(path,body);
  const response = await fetch(`/api${path}`, {method:method || (body===undefined?'GET':'POST'),credentials:'same-origin',headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const raw = await response.text(); let data:any; try {data=raw?JSON.parse(raw):{}} catch {data={detail:raw || response.statusText}}
  if (!response.ok) { const d=data.detail || data; throw new ApiError(response.status,typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg || x.message || JSON.stringify(x)).join('; '):d.message ? `${d.message}${d.issues?.length?' '+d.issues.slice(0,5).map((i:any)=>i.message||JSON.stringify(i)).join(' '):''}` : JSON.stringify(d)); }
  return data as T;
}
export function dateKey(iso:string,zone='America/New_York') {if(!iso)return ''; const p=new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(iso));return `${p.find(x=>x.type==='year')?.value}-${p.find(x=>x.type==='month')?.value}-${p.find(x=>x.type==='day')?.value}`}
export function formatDate(iso:string,zone='America/New_York',options:Intl.DateTimeFormatOptions={month:'short',day:'numeric'}) {if(!iso)return '—';return new Intl.DateTimeFormat('en-US',{timeZone:zone,...options}).format(new Date(iso))}
export function time(iso:string,zone='America/New_York') {return formatDate(iso,zone,{hour:'numeric',minute:'2-digit'})}
export function endTimeLabel(start:string,end:string,zone='America/New_York') {return `${dateKey(start,zone)!==dateKey(end,zone)?`${formatDate(end,zone)} · `:''}${time(end,zone)}`}
export function dateLabel(day:string) {return new Intl.DateTimeFormat('en-US',{weekday:'long',month:'long',day:'numeric',timeZone:'UTC'}).format(new Date(`${day}T12:00:00Z`))}
export function localInput(iso:string,zone:string) {if(!iso)return ''; const d=new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso));const v=(t:string)=>d.find(x=>x.type===t)?.value;return `${v('year')}-${v('month')}-${v('day')}T${v('hour')}:${v('minute')}`}
export function zonedISO(value:string,zone:string) {const [date,clock]=value.split('T');if(!date||!clock)throw new Error('Enter a complete date and time.');const wall=Date.parse(`${date}T${clock}:00Z`);let stamp=wall; for(let i=0;i<4;i++){const formatted=localInput(new Date(stamp).toISOString(),zone);const delta=wall-Date.parse(`${formatted}:00Z`);if(delta===0){for(const offset of [-120,-90,-60,-30,30,60,90,120]){if(localInput(new Date(stamp+offset*60000).toISOString(),zone)===value)throw new Error('This local time occurs twice when daylight saving time ends. Choose a time outside the repeated hour, or import an exact timestamp with an explicit UTC offset.');}return new Date(stamp).toISOString();}stamp+=delta;}throw new Error('This local time does not exist because the clocks change. Choose another time.');}
export const kindLabel=(kind:string)=>({day:'Day shift',primary_call:'Primary call',backup_call:'Backup call',leave:'Time off',swap:'Shift swap',preferences:'Preferences',staffing:'Staffing',daily:'OR assignments'}[kind] || kind.replaceAll('_',' '));
export const initials=(name:string)=>name.split(' ').filter(Boolean).map(x=>x[0]).slice(0,2).join('');
export const hours=(start:string,end:string)=>Math.round((Date.parse(end)-Date.parse(start))/3600000*10)/10;

export function effectivePolicy(settings:Settings,clinicianId:string){return {...settings,...settings.approval_groups?.find(group=>group.clinician_ids.includes(clinicianId))}}

export function newId(){
 const bytes=new Uint8Array(16);
 if(globalThis.crypto?.getRandomValues)globalThis.crypto.getRandomValues(bytes);else for(let i=0;i<bytes.length;i++)bytes[i]=Math.floor(Math.random()*256);
 bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
 return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
import {STATIC_PREVIEW,staticRequest} from './static-demo';
