import {useEffect,useState} from 'react';
import {AlertCircle,ArrowRight,CalendarDays,Check,ChevronRight,LockKeyhole,MapPin,ShieldCheck,WandSparkles} from 'lucide-react';
import {useApp} from './context';
import {Badge,Empty,ErrorMessage,Modal,Spinner} from './components';
import {api,effectivePolicy,endTimeLabel,formatDate,kindLabel,time,type Change,type CoverageRecommendations,type Issue,type Job,type Proposal,type RecordResource,type Request,type State} from './types';

export function recordLabel(data:State['data'],resource:string,id:string) {
 const record=(data[resource as RecordResource] as any[]||[]).find(r=>r.id===id);
 if(!record)return resource==='cases'?'Case no longer available':resource==='shifts'?'Shift no longer available':'Record no longer available';
 if(resource==='clinicians')return `${record.name} · ${record.role}`;
 if(resource==='requests')return `${kindLabel(record.kind)} · ${data.clinicians.find(c=>c.id===record.clinician_id)?.name||'Clinician'}`;
 const site=data.sites.find(s=>s.id===record.site_id)?.name||'Location';
 return `${resource==='cases'?`${record.room} · ${record.title}`:`${record.specialty} ${record.role} · ${kindLabel(record.kind)}`} · ${formatDate(record.start,data.settings.timezone)} ${time(record.start,data.settings.timezone)} · ${site}`;
}

export function RoomLabel({room,site}:{room:string;site?:string}) {
 return <div className="room-callout"><span><MapPin size={13}/>{room}</span>{site&&<small>{site}</small>}</div>;
}

export function PreviewBanner() {
 const {previewJob,clearPreview,open,state}=useApp();
 if(!previewJob)return null;
 return <div className="preview-banner"><ShieldCheck size={19}/><div><strong>Hypothetical schedule</strong><span>These assignments have not been applied.{previewJob.base_revision!==state.revision?' This preview is now out of date.':''}</span></div><button className="button compact" onClick={()=>open({type:'job',job:previewJob})}>Back to preview</button><button className="text-button" onClick={clearPreview}>Show current schedule</button></div>;
}

export function CoverageFindings({issues,max=8,previewJob,recommendations=true}:{issues:Issue[];max?:number;previewJob?:Job;recommendations?:boolean}) {
 const {state,navigateRecord}=useApp();
 const [limit,setLimit]=useState(max);
 const data=previewJob?.result?.preview?{...state.data,...previewJob.result.preview}:state.data;
 const grouped=new Map<string,Issue[]>();
 for(const issue of issues){const key=`${issue.resource}:${issue.id}`;(grouped.get(key)||grouped.set(key,[]).get(key)!).push(issue)}
 const records=[...grouped.values()];
 if(!records.length)return null;
 return <section className="coverage-findings"><div className="coverage-heading"><AlertCircle size={17}/><strong>{records.length} {records.length===1?'record needs':'records need'} attention</strong></div>{records.slice(0,limit).map((group,index)=>{const issue=group[0],isSchedule=issue.resource==='cases'||issue.resource==='shifts',exists=isSchedule&&(data[issue.resource as 'cases'|'shifts']||[]).some(r=>r.id===issue.id);return <article className="coverage-finding" key={`${issue.resource}:${issue.id}:${index}`}>
 {exists?<button className="record-link" onClick={()=>navigateRecord(issue.resource as RecordResource,issue.id,previewJob)}><CalendarDays size={16}/><strong>{recordLabel(data,issue.resource,issue.id)}</strong><ChevronRight size={16}/></button>:<strong>Scheduling rule needs attention</strong>}
 <ul className="coverage-reasons">{Array.from(new Set(group.map(i=>i.message))).map(message=><li key={message}>{message}</li>)}</ul>
 {exists&&recommendations&&<CoverageCandidates resource={issue.resource as 'cases'|'shifts'} id={issue.id} previewJob={previewJob}/>}</article>})}
 {limit<records.length&&<button className="button compact" onClick={()=>setLimit(n=>n+max)}>Show {Math.min(max,records.length-limit)} more ({records.length-limit} remaining)</button>}</section>;
}

export function CoverageCandidates({resource,id,previewJob}:{resource:'cases'|'shifts';id:string;previewJob?:Job}) {
 const {state,manager,mutate,open,busy}=useApp();
 const [result,setResult]=useState<CoverageRecommendations|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[expanded,setExpanded]=useState(false);
 const revision=previewJob?.base_revision??state.revision,stale=revision!==state.revision;
 useEffect(()=>{let active=true;setResult(null);setError('');setLoading(true);if(stale){setLoading(false);return;}
 const params=new URLSearchParams({resource,id,revision:String(revision)});if(previewJob)params.set('preview_job_id',previewJob.id);
 api<CoverageRecommendations>(`/coverage/candidates?${params}`).then(r=>{if(active)setResult(r)}).catch(e=>{if(active)setError(e.message)}).finally(()=>{if(active)setLoading(false)});return()=>{active=false};
 },[resource,id,revision,previewJob?.id,stale]);
 const repair=async()=>{setError('');try{const response=await mutate<Job|{job:Job}>('/coverage/repair-preview',{resource,id,revision:state.revision});open({type:'job',job:'job'in response?response.job:response})}catch(e){setError((e as Error).message)}};
 return <div className="candidate-panel">{stale?<p className="form-hint">Regenerate this preview to see current recommendations.</p>:loading?<Spinner label="Checking eligible replacements"/>:null}<ErrorMessage message={error}/>{result&&<>
 {(expanded?result.candidates:result.candidates.slice(0,1)).map((candidate,index)=><div className="candidate" key={candidate.clinician_id}><div><span className="candidate-eyebrow">{index===0?'Recommended clinician':'Eligible alternative'}</span><strong>{candidate.name} <Badge tone="teal">{candidate.role}</Badge></strong><p>{candidate.reason}</p></div>{manager&&!previewJob&&candidate.changes.length>0&&<button className="button compact" disabled={busy||result.base_revision!==state.revision} onClick={async()=>{setError('');try{const response=await mutate('/proposals',{summary:`Assign ${candidate.name} to ${resource==='cases'?'case':'shift'}`,changes:candidate.changes,revision:result.base_revision});open({type:'proposal',proposal:response.proposal||response})}catch(e){setError((e as Error).message)}}}>Preview assignment<ChevronRight size={14}/></button>}</div>)}
 {!result.candidates.length&&<p className="candidate-empty">{result.reason||(resource==='cases'?'No eligible clinician is already staffed for this case.':'No eligible direct replacement is available.')}</p>}
 {result.candidates.length>1&&<button className="text-button" onClick={()=>setExpanded(!expanded)}>{expanded?'Show recommendation only':`Show ${result.candidates.length-1} eligible alternatives`}</button>}
 {previewJob&&<small className="form-hint">Recommendations use this hypothetical schedule. Apply changes together through the repair preview.</small>}
 {result.missing_coverage?<p className="form-hint"><strong>Coverage must be added first.</strong> Add a matching staffing slot for the required location, time, role, and specialty.</p>:result.requires_staffing_repair&&manager&&!previewJob?<button className="button compact" disabled={busy} onClick={repair}><WandSparkles size={14}/>Preview staffing repair</button>:null}
 </>}</div>;
}

export function ChangesView({changes,previewJob}:{changes:Change[];previewJob?:Job}) {
 const {state,navigateRecord}=useApp();
 const [limit,setLimit]=useState(12);
 const zone=state.data.settings.timezone;
 const display=(key:string,value:any)=>{if(value===null||value==='')return 'Unassigned';if(key==='clinician_id')return state.data.clinicians.find(c=>c.id===value)?.name||'Clinician unavailable';if(key==='site_id')return state.data.sites.find(s=>s.id===value)?.name||'Location unavailable';if((key==='start'||key==='end')&&typeof value==='string')return formatDate(value,zone,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});if(typeof value==='boolean')return value?'Yes':'No';return Array.isArray(value)?value.join(', '):typeof value==='object'?JSON.stringify(value):String(value)};
 return <div className="change-list">{changes.slice(0,limit).map((change,index)=>{const source=(state.data[change.resource as RecordResource] as any[]||[]).find(r=>r.id===change.id);const schedule=change.resource==='cases'||change.resource==='shifts';return <article className="change-card" key={`${change.id}:${index}`}>
 <div className="change-title">{schedule?<button className="record-link" onClick={()=>navigateRecord(change.resource as RecordResource,change.id,previewJob)}><strong>{source?.title||`${source?.specialty||''} ${source?.role||''} ${kindLabel(source?.kind||'shift')}`}</strong><ChevronRight size={15}/></button>:<strong>{source?.name||'Record update'}</strong>}<span>{source?.start?`${formatDate(source.start,zone)} · ${time(source.start,zone)} – ${endTimeLabel(source.start,source.end,zone)}`:''}</span></div>
 {source?.room?<RoomLabel room={source.room} site={state.data.sites.find(s=>s.id===source.site_id)?.name}/>:source?.site_id?<p className="change-location"><MapPin size={13}/>{state.data.sites.find(s=>s.id===source.site_id)?.name}</p>:null}
 {Object.entries(change.patch).map(([key,value])=><div className="change-field" key={key}><span>{key==='clinician_id'?'Assigned clinician':key==='locked'?'Assignment locked':key.replaceAll('_',' ')}</span><div><span className="before-value"><small>Current</small><del>{display(key,source?.[key]===undefined?'—':source[key])}</del></span><ArrowRight size={14}/><span className="after-value"><small>Proposed</small><strong>{display(key,value)}</strong></span></div></div>)}</article>})}
 {limit<changes.length&&<button className="button" onClick={()=>setLimit(n=>n+12)}>Show {Math.min(12,changes.length-limit)} more changes ({changes.length-limit} remaining)</button>}</div>;
}

function useRepairJob(initial?:Job) {
 const {state}=useApp();const [fetched,setFetched]=useState<Job|undefined>(initial);
 const stateJob=state.jobs.find(j=>j.id===initial?.id);
 const current:Job|undefined=stateJob?{...fetched,...stateJob,result:stateJob.result||fetched?.result}:fetched?.id===initial?.id?fetched:initial;
 useEffect(()=>{if(!initial?.id)return;let active=true;const fetchJob=()=>api<Job>(`/jobs/${initial.id}`).then(job=>{if(active)setFetched(job);if(!['pending','queued','running'].includes(job.status))clearInterval(timer)}).catch(()=>{});const timer=setInterval(fetchJob,2500);fetchJob();return()=>{active=false;clearInterval(timer)}},[initial?.id]);
 return current;
}

export function RepairPreview({request,initialJob}:{request?:Request;initialJob?:Job}) {
 const {state,manager,busy,mutate,notify,close,navigateRecord,clearPreview}=useApp();
 const savedJobs=request?state.jobs.filter(j=>j.context?.kind==='leave_repair'&&j.context.request_id===request.id).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||'')):[];
 const [created,setCreated]=useState<Job|undefined>(initialJob),[remoteJob,setRemoteJob]=useState<Job|undefined>(),[fetchedProposal,setFetchedProposal]=useState<Proposal|undefined>(),[error,setError]=useState(''),[acknowledge,setAcknowledge]=useState(false);
 useEffect(()=>{if(!request)return;let active=true;api<Job|null>(`/requests/${request.id}/repair-preview`).then(job=>{if(active)setRemoteJob(job||undefined)}).catch(()=>{});return()=>{active=false}},[request?.id,state.revision]);
 const job=useRepairJob(created||savedJobs[0]||remoteJob);
 const running=!!job&&['pending','queued','running'].includes(job.status),stale=!!job&&job.base_revision!==state.revision;
 const proposal=state.proposals.find(p=>p.id===job?.proposal_id)||(fetchedProposal?.id===job?.proposal_id?fetchedProposal:undefined);
 useEffect(()=>{if(!job?.proposal_id)return;let active=true;api<Proposal>(`/proposals/${job.proposal_id}`).then(p=>{if(active)setFetchedProposal(p)}).catch(()=>{});return()=>{active=false}},[job?.proposal_id,state.revision]);
 const result=job?.result,changes=result?.changes||proposal?.changes||[],valid=job?.status==='completed'&&['OPTIMAL','FEASIBLE'].includes(result?.status||'')&&!stale;
 const gaps=result?.gaps||[],newIssues=result?.new_issues||[],existingIssues=result?.existing_issues||[];
 const scope=result?.scope||proposal?.details?.scope;
 const relevantExisting=existingIssues.filter(issue=>!scope||(issue.resource==='shifts'?scope.shift_ids:issue.resource==='cases'?scope.case_ids:[])?.includes(issue.id));
 const shortage=newIssues.length>0||relevantExisting.length>0||(!scope&&gaps.length>0);
 const policy=request?effectivePolicy(state.data.settings,request.clinician_id):state.data.settings;
 const canApprove=manager&&!busy&&valid&&!!job?.proposal_id&&proposal?.status==='pending'&&(!request||['pending','approved'].includes(request.status))&&(!shortage||policy.allow_leave_shortage&&acknowledge);
 const generate=async()=>{setError('');setAcknowledge(false);try{const response=await mutate<Job|{job:Job}>(request?`/requests/${request.id}/repair-preview`:'/coverage/repair-preview',request?{revision:state.revision}:{...job?.context?.target,revision:state.revision});setCreated('job'in response?response.job:response)}catch(e){setError((e as Error).message)}};
 const apply=async()=>{if(!job?.proposal_id)return;setError('');try{if(request){await mutate(`/requests/${request.id}/action`,{action:request.status==='approved'?'apply_repair':'approve',repair_proposal_id:job.proposal_id,revision:job.base_revision,acknowledge_shortage:acknowledge});notify(request.status==='approved'?'Reviewed repair applied.':'Leave approved and reviewed schedule applied.')}else{await mutate(`/proposals/${job.proposal_id}/apply`,{revision:job.base_revision});notify('Reviewed staffing repair applied.')}clearPreview();close()}catch(e){setError((e as Error).message)}};
 return <section className="repair-preview"><div className="repair-heading"><div><h3>{request?'Schedule with this time off':'Coverage repair'}</h3><p>{request?.status==='approved'?'Preview a repair while keeping this leave approved.':'Review the optimized assignments before saving changes.'}</p></div>{(!request||['pending','approved'].includes(request.status))&&!request?.details?.cancellation_requested&&<button className="button primary" disabled={busy||running} onClick={generate}><WandSparkles size={16}/>{running?'Preparing preview':job?'Regenerate preview':'Preview optimized schedule'}</button>}</div>
 {job&&<><div className="repair-status"><Badge tone={stale?'amber':valid?'teal':running?'blue':'amber'}>{stale?'Outdated preview':running?'Optimization in progress':result?.status==='UNKNOWN'?'Timed out':result?.status==='INFEASIBLE'?'No valid repair found':result?.status==='MODEL_INVALID'?'Rules need attention':job.status==='failed'?'Preview failed':valid?shortage?'Valid proposal · gaps remain':'Valid proposal · coverage resolved':job.status}</Badge><span>Based on revision {job.base_revision}</span></div>
 {stale&&<p className="form-hint">The schedule has changed. Regenerate this preview before applying it or checking replacements.</p>}
 {running?<div className="repair-running"><Spinner label="Repairing staffing, then affected OR assignments"/><p>You can close this panel. This request’s preview is saved in the background.</p></div>:<>
 {(job.error||result?.message)&&<ErrorMessage message={job.error||result?.message||''}/>}
 {!valid&&result?.status==='UNKNOWN'&&<p className="form-hint">The time limit was reached before a validated proposal was ready. Regenerate the preview or review leave-only approval separately.</p>}
 {result?.preview&&<><div className="info-strip"><ShieldCheck size={17}/><span><strong>Hypothetical schedule.</strong> This preview has not changed leave approval or saved assignments.</span></div><div className="impact-metrics"><div><strong>{changes.length}</strong><span>assignment changes</span></div><div><strong>{newIssues.length}</strong><span>new coverage issues</span></div><div><strong>{existingIssues.length}</strong><span>existing issues remain</span></div></div>
 {!!result.released_locks?.length&&<div className="released-locks"><h4><LockKeyhole size={15}/>{result.released_locks.length} leave-affected locks will be released</h4>{result.released_locks.map(lock=><button className="record-link" key={`${lock.resource}:${lock.id}`} onClick={()=>navigateRecord(lock.resource as RecordResource,lock.id,job)}>{recordLabel(state.data,lock.resource,lock.id)}<ChevronRight size={14}/></button>)}</div>}
 <h4>Current and proposed assignments</h4>{changes.length?<ChangesView changes={changes} previewJob={job}/>:<Empty title="No assignment changes needed"/>}
 {newIssues.length>0&&<><h4>New issues after this change</h4><CoverageFindings issues={newIssues} max={4} previewJob={job}/></>}
 {existingIssues.length>0&&<><h4>Pre-existing issues that remain</h4><CoverageFindings issues={existingIssues} max={4} previewJob={job}/></>}
 {gaps.length>0&&!newIssues.length&&!existingIssues.length&&<><h4>Remaining gaps</h4><CoverageFindings issues={gaps} max={4} previewJob={job}/></>}
 {(result.explanations||[]).map((explanation,index)=><p className="form-hint" key={index}>{typeof explanation==='string'?explanation:JSON.stringify(explanation)}</p>)}
 </>}
 {valid&&manager&&request&&shortage&&(policy.allow_leave_shortage?<label className="acknowledgement"><input type="checkbox" checked={acknowledge} onChange={e=>setAcknowledge(e.target.checked)}/><span>I reviewed the remaining gaps in this proposed schedule and acknowledge that they still need coverage.</span></label>:<p className="error-message">This group’s policy requires coverage to be resolved before leave approval.</p>)}
 {job.proposal_id&&(proposal?.status==='applied'?<Badge tone="teal">Applied</Badge>:manager?<button className="button primary" disabled={request?!canApprove:!valid||busy||proposal?.status==='rejected'} onClick={apply}><Check size={16}/>{request?request.status==='approved'?'Apply this repair':'Approve leave and apply this schedule':'Apply this staffing repair'}</button>:<p className="form-hint">A scheduler can approve leave and apply this reviewed schedule.</p>)}
 </>}</>}
 <ErrorMessage message={error}/></section>;
}

export function RepairDialog({job}:{job:Job}) {
 const {state,close}=useApp();const request=state.data.requests.find(r=>r.id===job.context?.request_id);
 return <Modal title={request?'Review time-off schedule':'Review staffing repair'} subtitle={request?`${state.data.clinicians.find(c=>c.id===request.clinician_id)?.name} · Hypothetical assignments`:'Current and proposed coverage'} onClose={close} wide><div className="form-body"><RepairPreview request={request} initialJob={job}/></div><div className="modal-actions"><button className="button" onClick={close}>Close</button></div></Modal>;
}

export function RecordDetail({resource,record,previewJob}:{resource:string;record:any;previewJob?:Job}) {
 const {state,manager,close,open,navigateRecord}=useApp();const zone=state.data.settings.timezone,schedule=resource==='shifts'||resource==='cases';
 const data=previewJob?.result?.preview?{...state.data,...previewJob.result.preview}:state.data;
 const current=(data[resource as RecordResource] as any[]||[]).find(r=>r.id===record.id)||record;
 const site=state.data.sites.find(s=>s.id===current.site_id),clinician=state.data.clinicians.find(c=>c.id===current.clinician_id);
 return <Modal title={resource==='clinicians'?current.name:resource==='cases'?current.title:`${current.specialty} ${current.role} · ${kindLabel(current.kind)}`} subtitle={previewJob?'Hypothetical assignment · Not applied':schedule?'Current assignment details':'Clinician profile'} onClose={close}><div className="form-body">
 {previewJob&&<Badge tone="amber">Hypothetical schedule</Badge>}
 {schedule?<>{current.room?<RoomLabel room={current.room} site={site?.name}/>:<p className="change-location"><MapPin size={16}/>{site?.name}</p>}<div className="detail-pairs"><div><span>Starts</span><strong>{formatDate(current.start,zone,{weekday:'short',month:'long',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'})}</strong></div><div><span>Ends</span><strong>{formatDate(current.end,zone,{weekday:'short',month:'long',day:'numeric',hour:'numeric',minute:'2-digit'})}</strong></div><div><span>Required qualification</span><strong>{current.specialty} · {current.role}</strong></div><div><span>Assigned clinician</span>{clinician?<button className="text-button" onClick={()=>navigateRecord('clinicians',clinician.id,previewJob)}>{clinician.name}<ChevronRight size={14}/></button>:<Badge tone="amber">Needs coverage</Badge>}</div><div><span>Assignment lock</span><strong>{current.locked?'Locked':'Unlocked'}</strong></div></div><CoverageCandidates resource={resource as 'cases'|'shifts'} id={current.id} previewJob={previewJob}/></>:<div className="detail-pairs"><div><span>Qualification</span><strong>{current.role}</strong></div><div><span>Specialty competencies</span><strong>{current.specialties?.join(', ')}</strong></div><div><span>Locations</span><strong>{current.sites?.map((id:string)=>state.data.sites.find(s=>s.id===id)?.name).join(', ')}</strong></div><div><span>Normal availability</span><strong>{current.availability?.start} – {current.availability?.end}</strong></div><div><span>Preferred shifts</span><strong>{current.preferred_shift_hours?.join(' / ')} hours</strong></div></div>}
 </div><div className="modal-actions">{previewJob&&<button className="button" onClick={()=>open({type:'job',job:previewJob})}>Back to repair preview</button>}<button className="button" onClick={close}>Close</button>{manager&&!previewJob&&<button className="button primary" onClick={()=>open({type:schedule?'assignment':'resource',resource,record:current})}>{schedule?'Change assignment':'Edit clinician'}</button>}</div></Modal>;
}
