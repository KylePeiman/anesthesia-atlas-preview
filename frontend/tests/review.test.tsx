import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderToStaticMarkup} from 'react-dom/server';
import {AppContext,type AppContextType} from '../src/context';
import {ChatReferences} from '../src/chat';
import {ChangesView,CoverageFindings,RecordDetail,RepairPreview} from '../src/review';
import {BoardScreen,CalendarScreen} from '../src/screens';
import {staticDemoState} from '../src/static-demo';
import type {Job,State} from '../src/types';

const clinician={id:'person-id-hidden',name:'Priya Shah',role:'CRNA',specialties:['OB'],sites:['main'],availability:{weekdays:[0,1,2,3,4],start:'07:00',end:'19:00'},fte:1,target_hours:144,preferred_shift_hours:[12],preferred_specialties:['OB'],call_eligible:true,supervision_required:true,active:true};
const shift={id:'internal-shift-id',start:'2026-09-14T07:00:00-04:00',end:'2026-09-14T19:00:00-04:00',site_id:'main',specialty:'OB',role:'CRNA',kind:'day',clinician_id:clinician.id,locked:false,status:'draft'};
const orCase={...shift,id:'internal-case-id',title:'Obstetric procedure',room:'OR 1'};
const request={id:'leave-id',kind:'leave',clinician_id:clinician.id,start:'2026-09-14T00:00:00-04:00',end:'2026-09-28T00:00:00-04:00',note:'Vacation',status:'pending',details:{},created_by:'admin',created_at:'2026-09-13T00:00:00Z'};
const issue={code:'uncovered',resource:'cases',id:orCase.id,severity:'gap',message:'This case needs a clinician.'};
const state:State={revision:4,user:{id:'admin',username:'admin',name:'Administrator',role:'admin',clinician_id:null},data:{settings:{timezone:'America/New_York',min_rest_hours:10,post_call_rest_hours:12,max_weekly_hours:60,supervision_ratio:3,require_scheduler_approval:true,require_swap_acceptance:true,allow_leave_shortage:true,demo:true},sites:[{id:'main',name:'Memorial Hospital'}],clinicians:[clinician,{...clinician,id:'replacement',name:'Charlotte Lewis'}],shifts:[shift],cases:[orCase],requests:[request]},issues:[],jobs:[],proposals:[]};
const noop=()=>{};
const context=(overrides:Partial<AppContextType>={}):AppContextType=>({state:structuredClone(state),manager:true,admin:true,busy:false,refresh:async()=>{},notify:noop,mutate:async()=>{throw Error('Unexpected mutation during rendering')},open:noop,close:noop,selectedDay:'2026-09-14',setSelectedDay:noop,setView:noop,openChat:noop,selectedSite:'all',setSelectedSite:noop,focusedRecord:null,previewJob:null,clearPreview:noop,scheduleData:state.data,scheduleIssues:[],navigateRecord:noop,...overrides});
const render=(node:React.ReactNode,overrides:Partial<AppContextType>={})=>renderToStaticMarkup(<AppContext.Provider value={context(overrides)}>{node}</AppContext.Provider>);
const content=(html:string)=>html.replace(/<[^>]*>/g,' ');

test('chat references render each record once with readable labels and no identifiers',()=>{
 const reference={resource:'cases' as const,id:orCase.id,label:`${orCase.id}: OR 1`};
 const html=render(<ChatReferences references={[reference,reference,{resource:'clinicians',id:clinician.id,label:clinician.name}]}/>);
 assert.equal((html.match(/class="chat-record-link"/g)||[]).length,2);
 assert.match(content(html),/OR 1/);assert.match(content(html),/Memorial Hospital/);assert.match(content(html),/Priya Shah/);
 assert.doesNotMatch(content(html),/internal-case-id|person-id-hidden/);
});
test('chat results are paged without silently losing remaining records',()=>{
 const references=Array.from({length:12},(_,i)=>({resource:'cases' as const,id:`case-${i}`,label:`Readable case ${i}`}));
 const html=render(<ChatReferences references={references}/>);
 assert.equal((html.match(/class="chat-record-link"/g)||[]).length,8);
 assert.match(content(html),/Show 4 more results \(4 remaining\)/);
});
test('coverage findings group multiple violations into a single case link',()=>{
 const html=render(<CoverageFindings issues={[issue,{...issue,code:'supervision',message:'Supervision is missing.'},issue]} recommendations={false}/>);
 assert.equal((html.match(/class="coverage-finding"/g)||[]).length,1);
 assert.equal((content(html).match(/This case needs a clinician/g)||[]).length,1);
 assert.match(content(html),/Supervision is missing/);assert.match(content(html),/OR 1/);
});
test('every OR case calls out its room and current versus proposed preview names',()=>{
 const board=render(<BoardScreen/>);assert.match(board,/class="room-callout"/);assert.match(content(board),/OR 1/);
 const html=render(<ChangesView changes={[{resource:'cases',id:orCase.id,patch:{clinician_id:'replacement'}}]}/>);
 assert.match(content(html),/OR 1/);assert.match(content(html),/Memorial Hospital/);assert.match(content(html),/Current/);assert.match(content(html),/Proposed/);assert.match(content(html),/Priya Shah/);assert.match(content(html),/Charlotte Lewis/);
});
test('new users see a clickable four-step demo guide on the staffing calendar',()=>{
 const html=render(<CalendarScreen/>);
 assert.match(content(html),/NEW TO ATLAS\?/);assert.match(content(html),/Take a quick demo tour/);
 for(const label of ['Check staffing coverage','Review the OR board','Try a time-off preview','Ask Atlas a question'])assert.match(content(html),new RegExp(label));
 assert.equal((html.match(/class="guide-step"/g)||[]).length,4);
});
test('the static visual preview has fictional staffing, rooms, and a visible coverage gap',()=>{
 const demo=staticDemoState();
 assert.equal(demo.data.settings.demo,true);assert.ok(demo.data.shifts.length>100);assert.ok(demo.data.cases.length>100);
 assert.ok(demo.data.cases.some(item=>item.room==='OR 1'));assert.ok(demo.issues.some(issue=>issue.resource==='cases'));
});
test('hypothetical record details cannot offer a direct assignment change',()=>{
 const job:Job={id:'preview',status:'completed',base_revision:4,result:{status:'FEASIBLE',preview:{shifts:[shift],cases:[orCase]}}};
 const html=render(<RecordDetail resource="cases" record={orCase} previewJob={job}/>);
 assert.match(content(html),/Hypothetical/);assert.match(content(html),/Back to repair preview/);assert.doesNotMatch(content(html),/Change assignment/);
});
test('repair comparison preserves unassigned originals and shows overnight end dates',()=>{
 const current=structuredClone(state);current.data.cases[0].clinician_id=null as any;current.data.shifts[0].end='2026-09-15T07:00:00-04:00';
 const html=render(<ChangesView changes={[{resource:'cases',id:orCase.id,patch:{clinician_id:'replacement'}},{resource:'shifts',id:shift.id,patch:{clinician_id:'replacement'}}]}/>,{state:current});
 assert.match(content(html),/Unassigned/);assert.doesNotMatch(content(html),/Clinician unavailable/);assert.match(content(html),/Sep 15/);
});
test('a valid leave preview enables atomic approval despite unrelated old gaps',()=>{
 const job:Job={id:'preview',status:'completed',base_revision:4,proposal_id:'proposal',context:{kind:'leave_repair',request_id:request.id},result:{status:'FEASIBLE',scope:{shift_ids:[shift.id],case_ids:[orCase.id]},changes:[],preview:{shifts:[shift],cases:[orCase]},new_issues:[],existing_issues:[{...issue,id:'unrelated-case'}],gaps:[{...issue,id:'unrelated-case'}]}};
 const current=structuredClone(state);current.data.settings.allow_leave_shortage=false;current.jobs=[job];current.proposals=[{id:'proposal',summary:'Leave repair',kind:'leave_repair',changes:[],base_revision:4,status:'pending',created_at:'',created_by:'admin',details:{}}];
 const html=render(<RepairPreview request={request} initialJob={job}/>,{state:current});
 const applyButton=html.match(/<button[^>]*>[^]*?Approve leave and apply this schedule<\/button>/g)?.at(-1)||'';
 assert.ok(applyButton);assert.doesNotMatch(applyButton.split('<button').at(-1)||'',/disabled/);
});
test('clinicians can inspect leave previews but cannot approve them',()=>{
 const html=render(<RepairPreview request={request}/>,{manager:false,admin:false});
 assert.match(content(html),/Preview optimized schedule/);assert.doesNotMatch(content(html),/Approve leave and apply this schedule/);
});
