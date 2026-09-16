import type {Case, ChatResult, Clinician, Issue, Shift, State, User} from './types';

const environment = (import.meta as ImportMeta & {env?: Record<string, string | undefined>}).env;
export const STATIC_PREVIEW = environment?.VITE_STATIC_DEMO === 'true';

const sites = [{id: 'main', name: 'Memorial Hospital'}, {id: 'riverside', name: 'Riverside Surgery Center'}];
const clinicians: Clinician[] = [
 {id:'c01',name:'Amelia Chen',role:'MD',specialties:['General','OB'],sites:['main','riverside'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12,24],preferred_specialties:['OB'],call_eligible:true,supervision_required:false,active:true},
 {id:'c02',name:'Ethan Brooks',role:'MD',specialties:['General','OB'],sites:['main','riverside'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['General'],call_eligible:true,supervision_required:false,active:true},
 {id:'c03',name:'Sofia Patel',role:'MD',specialties:['OB'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[24],preferred_specialties:['OB'],call_eligible:true,supervision_required:false,active:true},
 {id:'c04',name:'Daniel Kim',role:'MD',specialties:['General'],sites:['main','riverside'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['General'],call_eligible:true,supervision_required:false,active:true},
 {id:'c05',name:'Priya Shah',role:'CRNA',specialties:['OB'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['OB'],call_eligible:true,supervision_required:true,active:true},
 {id:'c06',name:'Lucas Rivera',role:'CRNA',specialties:['OB'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[24,12],preferred_specialties:['OB'],call_eligible:true,supervision_required:true,active:true},
 {id:'c07',name:'James Wilson',role:'CRNA',specialties:['OB'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['OB'],call_eligible:true,supervision_required:true,active:true},
 {id:'c08',name:'Ava Martinez',role:'CRNA',specialties:['Orthopedics'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['Orthopedics'],call_eligible:true,supervision_required:true,active:true},
 {id:'c09',name:'Liam Davis',role:'CRNA',specialties:['Orthopedics'],sites:['main','riverside'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['Orthopedics'],call_eligible:true,supervision_required:true,active:true},
 {id:'c10',name:'Charlotte Lewis',role:'CRNA',specialties:['Orthopedics'],sites:['main','riverside'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['Orthopedics'],call_eligible:true,supervision_required:true,active:true},
];

// September 2026 is in EDT. Building from UTC also carries 24-hour call
// shifts correctly into October instead of constructing an invalid Sep 31.
const stamp = (day: number, hour: number) => new Date(Date.UTC(2026, 8, day, hour + 4)).toISOString();
const shifts: Shift[] = [], cases: Case[] = [];
for (let day = 1; day <= 30; day++) {
 const weekday = new Date(`2026-09-${String(day).padStart(2,'0')}T12:00:00Z`).getUTCDay();
 const call = (site_id:string,specialty:string,role:'MD'|'CRNA',clinician_id:string|null) => shifts.push({id:`s-${day}-${site_id}-${specialty}-${role}-call`,start:stamp(day,7),end:stamp(day + 1,7),site_id,specialty,role,kind:'primary_call',clinician_id,locked:false,status:'draft'});
 call('main','OB','MD',day % 3 === 0 ? 'c01' : 'c03');
 call('main','OB','CRNA',day === 15 ? null : (day % 2 ? 'c06' : 'c07'));
 if ([1,2,3,4,5].includes(weekday)) {
  const dayShift = (site_id:string,specialty:string,role:'MD'|'CRNA',clinician_id:string|null) => shifts.push({id:`s-${day}-${site_id}-${specialty}-${role}-day`,start:stamp(day,7),end:stamp(day,19),site_id,specialty,role,kind:'day',clinician_id,locked:false,status:'draft'});
  dayShift('main','General','MD','c04'); dayShift('riverside','General','MD','c02'); dayShift('main','OB','CRNA',day === 15 ? null : 'c05'); dayShift('main','Orthopedics','CRNA','c08'); dayShift('riverside','Orthopedics','CRNA','c09');
  const roomCase = (site_id:string,room:string,specialty:string,clinician_id:string|null,title:string) => [8,12].forEach((hour,index) => cases.push({id:`case-${day}-${site_id}-${room.replaceAll(' ','-')}-${index}`,title,room,start:stamp(day,hour),end:stamp(day,hour+3),site_id,specialty,role:'CRNA',clinician_id,locked:false}));
  roomCase('main','OR 1','OB',day === 15 ? null : 'c05','Scheduled OB procedure'); roomCase('main','OR 2','Orthopedics','c08','Joint replacement'); roomCase('riverside','OR 1','Orthopedics','c09','Arthroscopy');
 }
}

const issues: Issue[] = [
 {code:'unfilled',message:'Required shift has no assigned clinician.',resource:'shifts',id:'s-15-main-OB-CRNA-call',severity:'gap'},
 {code:'unfilled',message:'Case has no assigned clinician.',resource:'cases',id:'case-15-main-OR-1-0',severity:'gap'},
 {code:'unfilled',message:'Case has no assigned clinician.',resource:'cases',id:'case-15-main-OR-1-1',severity:'gap'},
];

const admin: User = {id:'static-admin',username:'admin',name:'Alex Morgan',role:'admin',clinician_id:null};
const user: User = {id:'static-user',username:'user',name:'Lucas Rivera',role:'clinician',clinician_id:'c06'};
let currentUser = admin;

function currentState(): State {
 return {revision:1,user:currentUser,data:{settings:{timezone:'America/New_York',min_rest_hours:10,post_call_rest_hours:12,max_weekly_hours:60,supervision_ratio:3,require_scheduler_approval:true,require_swap_acceptance:true,allow_leave_shortage:true,demo:true},sites,clinicians,shifts,cases,requests:[{id:'vacation-demo',kind:'leave',clinician_id:'c05',start:stamp(8,0),end:stamp(22,0),note:'Two-week vacation — preview the staffing effect before approval.',status:'pending',details:{},created_by:'static-user',created_at:stamp(2,10)}]},issues,proposals:[],jobs:[]};
}

export function staticDemoState() { return currentState(); }

export async function staticRequest<T>(path:string, body?:unknown): Promise<T> {
 if (path === '/state') return currentState() as T;
 if (path === '/auth/me') return currentUser as T;
 if (path === '/auth/logout') return {ok:true} as T;
 if (path === '/auth/login') {
  const login = body as {username?:string;password?:string};
  const account = login.username === 'admin' && login.password === 'admin' ? admin : login.username === 'user' && login.password === 'user' ? user : null;
  if (!account) throw new Error('This public preview accepts admin / admin or user / user.');
  currentUser = account;
  return account as T;
 }
 if (path === '/chat') {
  const message = String((body as {message?:string})?.message || '').toLowerCase();
  const records = message.includes('gap') ? issues.map(issue => ({resource:issue.resource,id:issue.id,label:issue.resource === 'shifts' ? 'Sep 15 · OB CRNA primary call · Memorial Hospital — UNFILLED' : 'Sep 15 · OR 1 · OB CRNA · Memorial Hospital — UNFILLED',reasons:[issue.message]})) : [];
  const response: ChatResult = {conversation_id:'static-demo',base_revision:1,reply:records.length ? 'This visual preview includes three sample coverage findings for September 15.' : 'Atlas is shown here as a visual preview. Run Atlas locally to ask live schedule questions or prepare changes.',references:records as ChatResult['references']};
  return response as T;
 }
 if (path === '/ai/status') return {available:false,installed:false,local:false,detail:'The public preview does not connect to local AI.'} as T;
 throw new Error('This public GitHub Pages preview is read-only. Run Atlas locally to use this feature.');
}
