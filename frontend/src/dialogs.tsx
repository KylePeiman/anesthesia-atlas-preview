import { useEffect, useState, type FormEvent } from 'react';
import { Check, ChevronRight, Download, FileUp, LockKeyhole, Plus, ShieldCheck, Trash2, WandSparkles } from 'lucide-react';
import { useApp, type ModalSpec } from './context';
import {ChangesView,CoverageFindings,RecordDetail,RepairDialog,RepairPreview,RoomLabel} from './review';
import { Badge, Empty, ErrorMessage, Field, Issues, Modal, Spinner, Toggle } from './components';
import { api, newId, effectivePolicy, endTimeLabel, formatDate, hours, kindLabel, localInput, time, zonedISO, type Change, type Impact, type Job, type Proposal, type Request } from './types';
export function AppModal({ spec }: {
    spec: ModalSpec;
}) { switch (spec.type) {
    case 'record-detail': return <RecordDetail resource={spec.resource!} record={spec.record} previewJob={spec.previewJob}/>;
    case 'resource': return <ResourceDialog resource={spec.resource!} record={spec.record}/>;
    case 'assignment': return <AssignmentDialog resource={spec.resource!} record={spec.record}/>;
    case 'request': return <RequestDialog record={spec.record}/>;
    case 'request-detail': return <RequestDetail request={spec.record}/>;
    case 'proposal': return <ProposalDialog proposal={spec.proposal}/>;
    case 'optimize': return <OptimizeDialog options={spec.record}/>;
    case 'job': return spec.job?.context ? <RepairDialog job={spec.job}/> : <JobDialog initial={spec.job}/>;
    case 'import': return <ImportDialog />;
    case 'users': return <UsersDialog />;
    case 'account': return <AccountDialog />;
    case 'audit': return <ReadOnlyDialog kind="audit"/>;
    case 'ai-status': return <ReadOnlyDialog kind="ai"/>;
    default: return null;
} }
function ResourceDialog({ resource, record }: {
    resource: string;
    record?: any;
}) {
    const { state, selectedDay, close, mutate, notify, busy, open } = useApp();
    const [editRevision] = useState(state.revision);
    const zone = state.data.settings.timezone;
    const isClinician = resource === 'clinicians', isSite = resource === 'sites', isCase = resource === 'cases';
    const defaults: any = isClinician ? { id: `c-${newId().slice(0, 8)}`, name: '', role: 'MD', specialties: ['General'], sites: state.data.sites.map(s => s.id), availability: { weekdays: [0, 1, 2, 3, 4], start: '07:00', end: '19:00' }, fte: 1, target_hours: 144, preferred_shift_hours: [12], preferred_specialties: [], call_eligible: true, supervision_required: false, active: true } : isSite ? { id: `site-${newId().slice(0, 8)}`, name: '' } : { id: `${isCase ? 'case' : 'shift'}-${newId().slice(0, 8)}`, start: zonedISO(`${selectedDay}T07:00`, zone), end: zonedISO(`${selectedDay}T${isCase ? '10' : '19'}:00`, zone), site_id: state.data.sites[0]?.id || '', specialty: 'General', role: 'MD', clinician_id: null, locked: false, ...(isCase ? { title: '', room: 'OR 1' } : { kind: 'day', status: 'draft' }) };
    const [form, setForm] = useState<any>(record ? structuredClone(record) : defaults), [error, setError] = useState(''), [deleting, setDeleting] = useState(false), [start, setStart] = useState(record?.start ? localInput(record.start, zone) : `${selectedDay}T07:00`), [end, setEnd] = useState(record?.end ? localInput(record.end, zone) : `${selectedDay}T${isCase ? '10' : '19'}:00`);
    const [specialtiesText, setSpecialtiesText] = useState<string>((form.specialties || []).join(', ')), [prefSpecialtiesText, setPrefSpecialtiesText] = useState<string>((form.preferred_specialties || []).join(', ')), [prefHoursText, setPrefHoursText] = useState<string>((form.preferred_shift_hours || []).join(', '));
    const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
    const title = isClinician ? 'clinician' : isSite ? 'location' : isCase ? 'case' : 'coverage slot';
    const submit = async (e: FormEvent) => { e.preventDefault(); setError(''); try {
        const next = { ...form };
        if (isClinician) {
            next.specialties = specialtiesText.split(',').map(v => v.trim()).filter(Boolean);
            next.preferred_specialties = prefSpecialtiesText.split(',').map(v => v.trim()).filter(Boolean);
            next.preferred_shift_hours = prefHoursText.split(',').map(v => Number(v.trim()));
            if (next.preferred_shift_hours.some((n: number) => !Number.isFinite(n) || n <= 0 || n > 48))
                throw new Error('Preferred durations must be between 1 and 48 hours.');
        }
        if (!isClinician && !isSite) {
            next.start = record?.start && start === localInput(record.start, zone) ? record.start : zonedISO(start, zone);
            next.end = record?.end && end === localInput(record.end, zone) ? record.end : zonedISO(end, zone);
            if (Date.parse(next.end) <= Date.parse(next.start))
                throw new Error('End time must be after start time.');
        }
        if (resource === 'shifts' && record?.status === 'published') {
            const patch = Object.fromEntries(Object.entries(next).filter(([key, value]) => !['id', 'status'].includes(key) && JSON.stringify(value) !== JSON.stringify(record[key])));
            if (!Object.keys(patch).length) {
                close();
                return;
            }
            const proposal = await mutate('/proposals', { summary: `Edit published coverage on ${formatDate(record.start, zone)}`, changes: [{ resource, id: record.id, patch }], revision: editRevision });
            open({ type: 'proposal', proposal });
            return;
        }
        await mutate(`/resources/${resource}`, { record: next, revision: editRevision });
        notify(`${title[0].toUpperCase() + title.slice(1)} saved.`);
        close();
    }
    catch (e) {
        setError((e as Error).message);
    } };
    return <Modal title={`${record ? 'Edit' : 'Add'} ${title}`} subtitle={isClinician ? 'Keep credentials, eligibility, and preferences distinct.' : isSite ? 'A location where the team provides care.' : `Enter times in ${zone}. Assignment changes are reviewed separately.`} onClose={close} wide={isClinician}><form onSubmit={submit}><div className="form-body">{(isClinician || isSite) && <Field label={isClinician ? 'Full name' : 'Location name'}><input required value={form.name} onChange={e => set('name', e.target.value)}/></Field>}{isClinician ? <><div className="form-grid"><Field label="Professional role"><select value={form.role} onChange={e => set('role', e.target.value)}>{['MD', 'CRNA', 'CAA', 'PA'].map(r => <option key={r}>{r}</option>)}</select></Field><Field label="FTE"><input required type="number" min="0.1" max="2" step="0.1" value={form.fte} onChange={e => set('fte', Number(e.target.value))}/></Field><Field label="Qualified specialties" hint="Comma-separated competencies."><input required value={specialtiesText} onChange={e => setSpecialtiesText(e.target.value)}/></Field><Field label="Monthly target hours"><input required type="number" min="0" max="400" value={form.target_hours} onChange={e => set('target_hours', Number(e.target.value))}/></Field></div><fieldset className="checkbox-group"><legend>Facility privileges</legend>{state.data.sites.map(site => <label key={site.id}><input type="checkbox" checked={form.sites.includes(site.id)} onChange={e => set('sites', e.target.checked ? [...form.sites, site.id] : form.sites.filter((id: string) => id !== site.id))}/>{site.name}</label>)}</fieldset><div className="form-divider"/><h3>Normal availability</h3><div className="day-picker">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => <label key={d} className={form.availability.weekdays.includes(i) ? 'selected' : ''}><input type="checkbox" checked={form.availability.weekdays.includes(i)} onChange={e => set('availability', { ...form.availability, weekdays: e.target.checked ? [...form.availability.weekdays, i].sort() : form.availability.weekdays.filter((n: number) => n !== i) })}/>{d}</label>)}</div><div className="form-grid"><Field label="Available from"><input required type="time" value={form.availability.start} onChange={e => set('availability', { ...form.availability, start: e.target.value })}/></Field><Field label="Available until" hint="An end before start is an overnight window."><input required type="time" value={form.availability.end} onChange={e => set('availability', { ...form.availability, end: e.target.value })}/></Field><Field label="Preferred shift hours" hint="Comma-separated durations, e.g. 12, 24."><input value={prefHoursText} onChange={e => setPrefHoursText(e.target.value)}/></Field><Field label="Preferred specialties" hint="Preferences do not confer eligibility."><input value={prefSpecialtiesText} onChange={e => setPrefSpecialtiesText(e.target.value)}/></Field></div><Toggle label="Eligible for call" detail="Allows call outside the normal availability window." checked={form.call_eligible} onChange={v => set('call_eligible', v)}/><Toggle label="Requires supervision" detail="Scheduling must preserve the configured MD supervision capacity." checked={form.supervision_required} onChange={v => set('supervision_required', v)}/><Toggle label="Active clinician" checked={form.active} onChange={v => set('active', v)}/></> : !isSite ? <>{isCase && <div className="form-grid"><Field label="Case title" hint="Use a description without patient identifiers."><input required value={form.title} onChange={e => set('title', e.target.value)} placeholder="Orthopedic procedure"/></Field><Field label="Room"><input required value={form.room} onChange={e => set('room', e.target.value)}/></Field></div>}<div className="form-grid"><Field label="Start"><input required type="datetime-local" value={start} onChange={e => setStart(e.target.value)}/></Field><Field label="End"><input required type="datetime-local" value={end} onChange={e => setEnd(e.target.value)}/></Field><Field label="Location"><select required value={form.site_id} onChange={e => set('site_id', e.target.value)}>{state.data.sites.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field><Field label="Required specialty"><input required value={form.specialty} onChange={e => set('specialty', e.target.value)} list="specialties"/><datalist id="specialties"><option>General</option><option>OB</option><option>Orthopedics</option></datalist></Field><Field label="Required role"><select value={form.role} onChange={e => set('role', e.target.value)}>{['MD', 'CRNA', 'CAA', 'PA'].map(r => <option key={r}>{r}</option>)}</select></Field>{!isCase && <Field label="Shift type"><select value={form.kind} onChange={e => set('kind', e.target.value)}>{['day', 'primary_call', 'backup_call'].map(k => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></Field>}</div>{record?.clinician_id && <div className="info-strip"><LockKeyhole size={16}/><span>Assigned to {state.data.clinicians.find(c => c.id === record.clinician_id)?.name}. Use “Change” in the calendar to propose a different clinician.</span></div>}</> : null}<ErrorMessage message={error}/>{deleting && <div className="issues"><strong>Delete this {title}?</strong><p>This removes this record. Related records may prevent deletion.</p><div className="head-actions"><button type="button" className="button danger" disabled={busy} onClick={async () => { try {
        await mutate(`/resources/${resource}/${record.id}?revision=${editRevision}`, undefined, 'DELETE');
        notify(`${title} deleted.`);
        close();
    }
    catch (e) {
        setError((e as Error).message);
        setDeleting(false);
    } }}>Delete {title}</button><button type="button" className="button" onClick={() => setDeleting(false)}>Keep it</button></div></div>}</div><div className="modal-actions">{record && !(resource === 'shifts' && record.status === 'published') && <button className="icon-button danger-text" title={`Delete ${title}`} aria-label={`Delete ${title}`} type="button" onClick={() => setDeleting(true)}><Trash2 size={17}/></button>}<span className="spacer"/><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy}>{busy ? <Spinner label="Saving"/> : resource === 'shifts' && record?.status === 'published' ? 'Preview changes' : `Save ${title}`} </button></div></form></Modal>;
}
function AssignmentDialog({ resource, record }: {
    resource: string;
    record: any;
}) { const { state, close, mutate, open, busy } = useApp(); const [editRevision] = useState(state.revision); const [clinician, setClinician] = useState(record.clinician_id || ''), [locked, setLocked] = useState(record.locked || false), [error, setError] = useState(''); const zone = state.data.settings.timezone; return <Modal title="Propose an assignment" subtitle="Your changes will be validated and shown for review." onClose={close}><form onSubmit={async (e) => { e.preventDefault(); setError(''); try {
    const r = await mutate('/proposals', { summary: `Update ${resource === 'cases' ? record.title : kindLabel(record.kind)} on ${formatDate(record.start, zone)}`, changes: [{ resource, id: record.id, patch: { clinician_id: clinician || null, locked } }], revision: editRevision });
    open({ type: 'proposal', proposal: r.proposal || r });
}
catch (e) {
    setError((e as Error).message);
} }}><div className="form-body"><div className="assignment-summary"><Badge tone="blue">{record.specialty} · {record.role}</Badge>{record.room?<RoomLabel room={record.room} site={state.data.sites.find(s=>s.id===record.site_id)?.name}/>:<h3>{state.data.sites.find(s=>s.id===record.site_id)?.name}</h3>}<p>{formatDate(record.start, zone, { weekday: 'short', month: 'short', day: 'numeric' })} · {time(record.start, zone)} – {endTimeLabel(record.start,record.end,zone)} · {hours(record.start, record.end)}h</p></div><Field label="Assign clinician" hint="Final validation also checks leave, availability, overlap, rest, and supervision."><select value={clinician} onChange={e => setClinician(e.target.value)}><option value="">Leave unassigned</option>{state.data.clinicians.filter(c => c.active && c.role === record.role && c.specialties.includes(record.specialty) && c.sites.includes(record.site_id)).map(c => <option key={c.id} value={c.id}>{c.name} · {c.role}</option>)}</select></Field><Toggle label="Lock this assignment" detail="Optimization will preserve it; rule conflicts remain visible." checked={locked} onChange={setLocked}/><ErrorMessage message={error}/></div><div className="modal-actions"><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy}>Preview proposal<ChevronRight size={16}/></button></div></form></Modal>; }
export function ChangePreview({changes}:{changes:Change[]}) {return <ChangesView changes={changes}/>;}

function ProposalDialog({ proposal: initial }: {
    proposal: Proposal;
}) { const { state, manager, close, mutate, notify, busy, open } = useApp(); const proposal = { ...initial, ...state.proposals.find(p => p.id === initial.id) }; const [error, setError] = useState(''); const changes = proposal.changes || []; const [fetchedJob,setFetchedJob]=useState<Job|null>(null); useEffect(()=>{const id=proposal.details?.job_id;if(!id||!['leave_repair','coverage_repair'].includes(proposal.kind))return;let active=true;api<Job>(`/jobs/${id}`).then(job=>{if(active)setFetchedJob(job)}).catch(()=>{});return()=>{active=false}},[proposal.details?.job_id,proposal.kind]); const linkedJob=state.jobs.find(j=>j.proposal_id===proposal.id&&j.context)||(fetchedJob?.proposal_id===proposal.id?fetchedJob:null); if(linkedJob)return <RepairDialog job={linkedJob}/>; if(proposal.kind==='leave_repair'||proposal.details?.context?.kind==='leave_repair')return <Modal title="Review time-off schedule" onClose={close}><div className="form-body"><p>Open this leave request to regenerate its schedule preview before approval.</p>{state.data.requests.find(r=>r.id===proposal.details?.request_id)&&<button className="button" onClick={()=>open({type:'request-detail',record:state.data.requests.find(r=>r.id===proposal.details.request_id)})}>Open leave request</button>}</div><div className="modal-actions"><button className="button" onClick={close}>Close</button></div></Modal>; return <Modal title="Review proposed changes" subtitle={proposal.summary || 'A proposed schedule update'} onClose={close} wide><div className="form-body"><div className="proposal-meta"><Badge tone={proposal.status === 'applied' ? 'teal' : 'amber'}>{proposal.status || 'pending'}</Badge><span>{changes.length} changes · Based on revision {proposal.base_revision}</span></div>{proposal.base_revision !== state.revision && proposal.status === 'pending' && <div className="info-strip"><ShieldCheck size={16}/><span>The schedule has changed since this proposal was created. Create an updated preview before applying it.</span></div>}<Issues issues={proposal.details?.issues || (proposal as any).issues || []} max={5}/>{proposal.details?.gaps?.length > 0 && <div className="issues"><strong>{proposal.details.gaps.length} gaps remain in this proposal.</strong><p>Uncovered slots stay visible. Publication requires full coverage.</p></div>}<ChangePreview changes={changes}/>{!changes.length && <Empty title="No changes in this proposal"/>}<ErrorMessage message={error}/></div><div className="modal-actions"><button className="button" onClick={close}>Close</button>{proposal.status === 'pending' && <><button className="button" disabled={busy} onClick={async () => { try {
    await mutate(`/proposals/${proposal.id}/reject`, {});
    notify('Proposal rejected.');
    close();
}
catch (e) {
    setError((e as Error).message);
} }}>Reject proposal</button>{proposal.base_revision !== state.revision && <button className="button primary" disabled={busy} onClick={async () => { try {
    const updated = await mutate('/proposals', { summary: proposal.summary, changes: changes.map(({ resource, id, patch }) => ({ resource, id, patch })), revision: state.revision });
    open({ type: 'proposal', proposal: updated.proposal || updated });
}
catch (e) {
    setError((e as Error).message);
} }}>Create updated preview</button>}{(manager || proposal.created_by === state.user.id) && proposal.base_revision === state.revision && <button className="button primary" disabled={busy || !changes.length} onClick={async () => { setError(''); try {
    await mutate(`/proposals/${proposal.id}/apply`, { revision: state.revision });
    notify('Proposed changes applied.');
    close();
}
catch (e) {
    setError((e as Error).message);
} }}><Check size={16}/>Apply changes</button>}</>}</div></Modal>; }
function OptimizeDialog({ options }: {
    options: {
        stage: string;
        date?: string;
        month?: string;
    };
}) { const { state, selectedDay, close, mutate, open, busy } = useApp(); const [stage, setStage] = useState(options.stage), [day, setDay] = useState(options.date || selectedDay), [month, setMonth] = useState(options.month || selectedDay.slice(0, 7)), [error, setError] = useState(''); return <Modal title="Build a thoughtful schedule" subtitle="OR-Tools balances coverage, eligibility, preferences, and disruption." onClose={close}><form onSubmit={async (e) => { e.preventDefault(); setError(''); try {
    const result = await mutate('/optimize', { stage, ...(stage === 'daily' ? { date: day } : { month }), revision: state.revision });
    open({ type: 'job', job: result.job || result });
}
catch (e) {
    setError((e as Error).message);
} }}><div className="form-body"><Field label="Scheduling stage"><select value={stage} onChange={e => setStage(e.target.value)}><option value="staffing">Advance staffing and call</option><option value="daily">Daily OR assignments</option></select></Field>{stage === 'staffing' && <Field label="Staffing month" hint="Only assignments starting in this month are optimized."><input type="month" required value={month} onChange={e => setMonth(e.target.value)}/></Field>}{stage === 'daily' && <Field label="OR board date"><input type="date" required value={day} onChange={e => setDay(e.target.value)}/></Field>}<div className="optimize-facts"><div><ShieldCheck size={19}/><span><strong>Rules stay in place</strong><small>Eligibility, leave, rest, overlap, and supervision are validated.</small></span></div><div><LockKeyhole size={19}/><span><strong>Locked assignments are preserved</strong><small>Other assignments may move to improve coverage and workload.</small></span></div><div><WandSparkles size={19}/><span><strong>You review the result</strong><small>The optimizer creates a proposal. Any remaining gaps are explicit.</small></span></div></div><ErrorMessage message={error}/></div><div className="modal-actions"><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy}><WandSparkles size={16}/>Run optimization</button></div></form></Modal>; }
function JobDialog({ initial }: {
    initial: any;
}) { const { state, close, open } = useApp(); const job = state.jobs.find(j => j.id === initial.id) || initial; const [fetched, setFetched] = useState<any>(null); useEffect(() => { api(`/jobs/${initial.id}`).then(setFetched).catch(() => { }); }, [initial.id, job.status]); const current = fetched?.status === job.status ? { ...job, ...fetched } : job; const running = ['pending', 'queued', 'running'].includes(current.status); const p = state.proposals.find(p => p.id === (current.proposal_id || current.result?.proposal_id)); return <Modal title="Optimization result" subtitle={`${kindLabel(current.stage || 'Schedule')} · ${current.id}`} onClose={close}><div className="form-body">{running ? <div className="optimization-running"><span className="solver-orbit"><WandSparkles size={32}/></span><h3>Finding a better fit</h3><Spinner label={`Optimization ${current.status}`}/><p>You can close this panel. The optimizer continues in the background.</p></div> : <><div className="proposal-meta"><Badge tone={current.status === 'failed' ? 'red' : 'teal'}>{current.result?.status || current.status}</Badge></div>{(current.error || current.result?.message) && <ErrorMessage message={String(current.error || current.result.message)}/>}<div className="job-metrics">{Object.entries(current.result?.metrics || {}).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{typeof value === 'number' ? Math.round(value * 100) / 100 : String(value)}</strong></div>)}</div>{(current.result?.gaps || []).length > 0 && <div className="issues"><strong>{current.result.gaps.length} gaps remain</strong>{current.result.gaps.slice(0, 8).map((g: any, i: number) => <p key={i}>{typeof g === 'string' ? g : g.message || g.reason || JSON.stringify(g)}</p>)}</div>}{(current.result?.explanations || []).slice(0, 8).map((e: any, i: number) => <p className="muted" key={i}>{typeof e === 'string' ? e : JSON.stringify(e)}</p>)}{!p && !current.error && <p className="muted">{current.result?.changes?.length === 0 ? 'The optimizer found no assignment changes.' : 'If a proposal was generated, it will appear in Schedule review.'}</p>}</>}</div><div className="modal-actions"><button className="button" onClick={close}>{running ? 'Continue in background' : 'Close'}</button>{p && <button className="button primary" onClick={() => open({ type: 'proposal', proposal: p })}>Review proposal<ChevronRight size={16}/></button>}</div></Modal>; }
function RequestDialog({ record }: {
    record?: any;
}) {
    const { state, manager, selectedDay, close, mutate, notify, busy } = useApp();
    const [editRevision] = useState(state.revision);
    const zone = state.data.settings.timezone;
    const [kind, setKind] = useState(record?.kind || 'leave'), [clinician, setClinician] = useState(record?.clinician_id || state.user.clinician_id || state.data.clinicians[0]?.id || ''), [start, setStart] = useState(record?.start ? localInput(record.start, zone) : `${selectedDay}T00:00`), [end, setEnd] = useState(record?.end ? localInput(record.end, zone) : `${selectedDay}T23:59`), [note, setNote] = useState(''), [shift, setShift] = useState(''), [otherShift, setOtherShift] = useState(''), [prefHours, setPrefHours] = useState('12'), [prefSpecialties, setPrefSpecialties] = useState(''), [impact, setImpact] = useState<Impact | null>(null), [previewing, setPreviewing] = useState(false), [error, setError] = useState('');
    useEffect(() => { setImpact(null); }, [start, end, clinician]);
    useEffect(() => { const c = state.data.clinicians.find(c => c.id === clinician); setPrefHours(c?.preferred_shift_hours.join(', ') || '12'); setPrefSpecialties(c?.preferred_specialties.join(', ') || ''); setShift(''); setOtherShift(''); }, [clinician]);
    const interval = () => { const s = zonedISO(start, zone), e = zonedISO(end, zone); if (Date.parse(e) <= Date.parse(s))
        throw new Error('Leave must end after it starts.'); return { start: s, end: e }; };
    const preview = async () => { setPreviewing(true); setError(''); try {
        setImpact(await api<Impact>('/leave-impact', { clinician_id: clinician, ...interval() }));
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setPreviewing(false);
    } };
    const submit = async (e: FormEvent) => { e.preventDefault(); setError(''); try {
        const body: any = { kind, clinician_id: clinician, note, revision: editRevision };
        if (kind === 'leave') {
            Object.assign(body, interval());
            if (record?.replaces_request_id)
                body.details = { replaces_request_id: record.replaces_request_id };
        }
        if (kind === 'swap') {
            const other = state.data.shifts.find(s => s.id === otherShift);
            if (!shift || !other?.clinician_id)
                throw new Error('Select both assigned shifts to exchange.');
            body.details = { shift_id: shift, other_shift_id: otherShift, other_clinician_id: other.clinician_id, accepted_by: [] };
        }
        if (kind === 'preferences') {
            const nums = prefHours.split(',').map(x => Number(x.trim()));
            if (nums.some(n => !Number.isFinite(n) || n <= 0 || n > 48))
                throw new Error('Preferred shift durations must be between 1 and 48 hours.');
            body.details = { preferred_shift_hours: nums, preferred_specialties: prefSpecialties.split(',').map(s => s.trim()).filter(Boolean) };
        }
        const result = await mutate('/requests', body);
        notify(result.request?.status === 'approved' ? 'Request approved and applied.' : 'Request submitted for review.');
        close();
    }
    catch (e) {
        setError((e as Error).message);
    } };
    const shiftLabel = (s: any) => `${formatDate(s.start, zone)} · ${time(s.start, zone)} · ${kindLabel(s.kind)} · ${s.specialty} · ${state.data.clinicians.find(c => c.id === s.clinician_id)?.name}`;
    return <Modal title="Create a request" subtitle={record?.replaces_request_id ? 'Your existing approved leave stays in place until these new dates are approved.' : 'Your schedule stays unchanged until the request is approved.'} onClose={close} wide={kind === 'leave' && !!impact}><form onSubmit={submit}><div className="form-body"><div className="form-grid"><Field label="Request type"><select value={kind} onChange={e => { setKind(e.target.value); setError(''); }}><option value="leave">Time off</option><option value="swap">Shift swap</option><option value="preferences">Update preferences</option></select></Field><Field label="Clinician"><select required value={clinician} disabled={!manager} onChange={e => setClinician(e.target.value)}>{state.data.clinicians.filter(c => manager || c.id === state.user.clinician_id).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></div>{kind === 'leave' ? <><div className="form-grid"><Field label="Unavailable from"><input required type="datetime-local" value={start} onChange={e => setStart(e.target.value)}/></Field><Field label="Available again at"><input required type="datetime-local" value={end} onChange={e => setEnd(e.target.value)}/></Field></div><p className="form-hint">Times use {zone}. For a full two-week vacation, select midnight on the first day and midnight on the day you return. Approved leave overrides all normal availability.</p><button type="button" className="button" disabled={previewing || !clinician} onClick={preview}>{previewing ? <Spinner label="Checking capacity"/> : <><ShieldCheck size={16}/>Preview coverage impact</>}</button>{impact && <ImpactPreview impact={impact}/>}</> : kind === 'swap' ? <><Field label="Your shift"><select required value={shift} onChange={e => setShift(e.target.value)}><option value="">Choose an assigned shift</option>{state.data.shifts.filter(s => s.clinician_id === clinician).map(s => <option key={s.id} value={s.id}>{shiftLabel(s)}</option>)}</select></Field><Field label="Swap with"><select required value={otherShift} onChange={e => setOtherShift(e.target.value)}><option value="">Choose your colleague’s shift</option>{state.data.shifts.filter(s => s.clinician_id && s.clinician_id !== clinician).map(s => <option key={s.id} value={s.id}>{shiftLabel(s)}</option>)}</select></Field><div className="info-strip"><ShieldCheck size={17}/><span>Both assignments are rechecked for eligibility, rest, coverage, and leave. {effectivePolicy(state.data.settings, clinician).require_swap_acceptance ? 'Both clinicians must accept.' : ''}</span></div></> : <div className="form-grid"><Field label="Preferred shift hours" hint="Comma separated: 12, 24"><input required value={prefHours} onChange={e => setPrefHours(e.target.value)}/></Field><Field label="Preferred specialties" hint="Comma separated: OB, Orthopedics"><input value={prefSpecialties} onChange={e => setPrefSpecialties(e.target.value)}/></Field></div>}<Field label="Note (optional)"><textarea rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder={kind === 'leave' ? 'Vacation, personal time, or details for your scheduler…' : 'Add context for your scheduler…'}/></Field><ErrorMessage message={error}/></div><div className="modal-actions"><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy || previewing || (kind === 'leave' && !impact)}><Plus size={16}/>Submit request</button></div></form></Modal>;
}
function ImpactPreview({ impact }: {
    impact: Impact;
}) { const { state } = useApp(); const zone = state.data.settings.timezone; return <div className="impact-preview"><div className="impact-title"><ShieldCheck size={18}/><h3>Coverage impact</h3><Badge tone="amber">Preview</Badge></div><div className="impact-metrics"><div><strong>{Math.round((impact.lost_hours || 0) * 10) / 10}h</strong><span>availability removed</span></div><div><strong>{impact.affected_shifts?.length || 0}</strong><span>shifts affected</span></div><div><strong>{impact.affected_cases?.length || 0}</strong><span>cases affected</span></div></div><CoverageFindings issues={impact.issues||[]} max={5} recommendations={false}/>{impact.affected_shifts?.length > 0 && <div className="affected-list">{impact.affected_shifts.slice(0, 8).map((item: any, index) => { const s = typeof item === 'string' ? state.data.shifts.find(s => s.id === item) : item; return <div key={s?.id || index}><span>{s?.start ? `${formatDate(s.start, zone)} · ${time(s.start, zone)} – ${endTimeLabel(s.start,s.end,zone)}` : JSON.stringify(item)}</span><strong>{s?.specialty} {s?.role}</strong></div>; })}{impact.affected_shifts.length > 8 && <small>And {impact.affected_shifts.length - 8} more shifts.</small>}</div>}<p className="form-hint">Approved leave removes availability. Any uncovered assignments remain visible until a replacement is arranged.</p></div>; }
function RequestDetail({ request: initial }: {
    request: Request;
}) {
    const { state, manager, close, mutate, notify, busy, open } = useApp();
    const [editRevision] = useState(state.revision);
    const request = state.data.requests.find(r => r.id === initial.id) || initial;
    const [impact, setImpact] = useState<Impact | null>(null), [loading, setLoading] = useState(request.kind === 'leave'), [acknowledge, setAcknowledge] = useState(false), [error, setError] = useState('');
    const zone = state.data.settings.timezone, clinician = state.data.clinicians.find(c => c.id === request.clinician_id), own = state.user.clinician_id === request.clinician_id, participant = own || state.user.clinician_id === request.details?.other_clinician_id;
    useEffect(() => { if (request.kind === 'leave' && request.start && request.end)
        api<Impact>('/leave-impact', { clinician_id: request.clinician_id, start: request.start, end: request.end }).then(setImpact).catch(e => setError(e.message)).finally(() => setLoading(false)); }, [request.id, request.status, state.revision]);
    const action = async (action: string) => { setError(''); try {
        await mutate(`/requests/${request.id}/action`, { action, acknowledge_shortage: acknowledge, revision: editRevision });
        notify(action === 'accept' ? 'Swap acceptance recorded.' : action === 'cancel' && request.status === 'approved' && !manager ? 'Leave cancellation submitted for scheduler review.' : request.details?.cancellation_requested ? (action === 'approve' ? 'Leave cancellation approved.' : 'Leave cancellation declined.') : `Request ${action === 'approve' ? 'approved' : action === 'reject' ? 'rejected' : 'cancelled'}.`);
        close();
    }
    catch (e) {
        setError((e as Error).message);
    } };
    const shortage = !!impact && ((impact.issues?.length || 0) > 0 || (impact.affected_shifts?.length || 0) > 0);
    const accepted = request.details?.accepted_by || [];
    return <Modal title={`${kindLabel(request.kind)} request`} subtitle={`${clinician?.name || request.clinician_id} · Submitted ${formatDate(request.created_at, zone)}`} onClose={close} wide><div className="form-body"><Badge tone={request.status === 'pending' ? 'amber' : request.status === 'approved' ? 'teal' : 'muted'}>{request.status}</Badge>{request.start && request.end && <div className="request-period"><div><span>Unavailable from</span><strong>{formatDate(request.start, zone, { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</strong></div><ChevronRight size={18}/><div><span>Available again</span><strong>{formatDate(request.end, zone, { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</strong></div></div>}{request.details?.cancellation_requested && <div className="info-strip"><ShieldCheck size={16}/><span>Cancellation is awaiting scheduler review. This leave remains approved until cancellation is approved.</span></div>}{request.details?.replaces_request_id && <div className="info-strip"><ShieldCheck size={16}/><span>This request changes the dates of an earlier approved leave interval. The original remains approved until these dates are accepted.</span></div>}{request.note && <div className="request-note"><strong>Note</strong><p>{request.note}</p></div>}{request.kind==='leave'&&['pending','approved'].includes(request.status)&&!request.details?.cancellation_requested&&<RepairPreview request={request}/>}{request.kind==='leave'&&<details className="leave-only-impact"><summary>Original leave impact and approval without repair</summary>{loading ? <Spinner label="Checking current coverage"/> : impact && <ImpactPreview impact={impact}/>}</details>} {request.kind === 'swap' && <><div className="change-list">{[request.details?.shift_id, request.details?.other_shift_id].filter(Boolean).map((id: string) => { const s = state.data.shifts.find(s => s.id === id); return <div className="change-card" key={id}><strong>{s ? `${formatDate(s.start, zone)} · ${time(s.start, zone)} – ${endTimeLabel(s.start,s.end,zone)}` : id}</strong><p>{s?.specialty} · {s?.role} · {state.data.clinicians.find(c => c.id === s?.clinician_id)?.name || 'Unassigned'}</p></div>; })}</div><p className="form-hint">Accepted by: {accepted.length ? accepted.map((id: string) => state.data.clinicians.find(c => c.id === id)?.name || id).join(', ') : 'No one yet'}.</p></>}{request.kind === 'preferences' && <div className="detail-pairs">{Object.entries(request.details).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{Array.isArray(value) ? value.join(', ') : String(value)}</strong></div>)}</div>}{request.status === 'pending' && manager && request.kind === 'leave' && shortage && <label className="acknowledgement"><input type="checkbox" checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)}/><span>I have reviewed the impact and acknowledge that approval may leave coverage gaps. These gaps must be resolved separately.</span></label>}<ErrorMessage message={error}/></div><div className="modal-actions"><button className="button" onClick={close}>Close</button>{request.status === 'approved' && request.kind === 'leave' && (manager || own) && <><button className="button" disabled={busy || !!request.details?.cancellation_requested} onClick={() => open({ type: 'request', record: { kind: 'leave', clinician_id: request.clinician_id, start: request.start, end: request.end, replaces_request_id: request.id } })}>Change dates</button><button className="button" disabled={busy || !!request.details?.cancellation_requested} onClick={() => action('cancel')}>{request.details?.cancellation_requested ? 'Cancellation pending' : manager ? 'Cancel leave' : 'Request cancellation'}</button></>}{request.details?.cancellation_requested && request.status === 'approved' && manager && <><button className="button" disabled={busy} onClick={() => action('reject')}>Keep approved leave</button><button className="button primary" disabled={busy} onClick={() => action('approve')}>Approve cancellation</button></>}{request.status === 'pending' && <>{(manager || own) && <button className="button" disabled={busy} onClick={() => action(own && !manager ? 'cancel' : 'reject')}>{own && !manager ? 'Cancel request' : 'Reject'}</button>}{request.kind === 'swap' && participant && !accepted.includes(state.user.clinician_id) && !accepted.includes(state.user.id) && <button className="button" disabled={busy} onClick={() => action('accept')}>Accept swap</button>}{manager && <button className="button primary" disabled={busy || loading || (request.kind === 'leave' && shortage && !acknowledge)} onClick={() => action('approve')}><Check size={16}/>{request.kind==='leave'?'Approve leave without repair':'Approve request'}</button>}</>}{request.status === 'cancelled' && request.kind === 'leave' && (manager || own) && <button className="button primary" onClick={() => open({ type: 'request', record: { kind: 'leave', clinician_id: request.clinician_id } })}>Create revised request</button>}</div></Modal>;
}
function ImportDialog() { const { state, close, mutate, notify, busy } = useApp(); const [kind, setKind] = useState('clinicians'), [csv, setCsv] = useState(''), [preview, setPreview] = useState<any>(null), [error, setError] = useState(''), [checking, setChecking] = useState(false); const example = kind === 'clinicians' ? 'id,name,role,specialties,sites,fte,target_hours\nc21,Jordan Lee,MD,General,main,1,144' : kind === 'shifts' ? 'id,start,end,site_id,specialty,role,kind\nshift-example,2026-09-21T07:00:00-04:00,2026-09-21T19:00:00-04:00,main,General,MD,day' : 'id,title,room,start,end,site_id,specialty,role\ncase-example,General procedure,OR 1,2026-09-21T07:00:00-04:00,2026-09-21T10:00:00-04:00,main,General,MD'; return <Modal title="Import workspace data" subtitle="Preview and validate every row before applying your CSV." onClose={close} wide><div className="form-body"><Field label="Import type"><select value={kind} onChange={e => { setKind(e.target.value); setPreview(null); }}><option value="clinicians">Clinician roster</option><option value="shifts">Coverage slots</option><option value="cases">OR cases</option></select></Field><label className="file-drop"><FileUp size={26}/><strong>Choose a CSV file</strong><span>Or paste its contents below.</span><input aria-label="Choose CSV file" type="file" accept=".csv,text/csv" onChange={async (e) => { const file = e.target.files?.[0]; if (file) {
    setCsv(await file.text());
    setPreview(null);
} }}/></label><Field label="CSV contents"><textarea className="code-input" rows={7} value={csv} placeholder={example} onChange={e => { setCsv(e.target.value); setPreview(null); }}/></Field><p className="form-hint">Use offset-aware ISO dates (for example, 2026-09-21T07:00:00-04:00). Use semicolons inside list fields (for example, OB;Orthopedics). IDs identify records; matching IDs update existing records. The preview reports unsupported or incomplete values.</p><button className="button" disabled={checking || !csv.trim()} onClick={async () => { setChecking(true); setError(''); try {
    setPreview(await api('/import/preview', { kind, csv }));
}
catch (e) {
    setError((e as Error).message);
}
finally {
    setChecking(false);
} }}>{checking ? <Spinner label="Validating CSV"/> : <><ShieldCheck size={16}/>Validate and preview</>}</button>{preview && <div className="import-preview"><h3>{preview.records?.length || 0} valid records <Badge tone={preview.errors?.length ? 'red' : 'teal'}>{preview.errors?.length || 0} errors</Badge></h3>{preview.errors?.length > 0 && <div className="issues">{preview.errors.map((e: any, i: number) => <p key={i}>{typeof e === 'string' ? e : JSON.stringify(e)}</p>)}</div>}<div className="table-wrap"><table><thead><tr><th>IDENTIFIER</th><th>RECORD</th></tr></thead><tbody>{(preview.records || []).slice(0, 12).map((r: any, i: number) => <tr key={i}><td>{r.id || i + 1}</td><td><span className="import-record">{r.name || r.title || `${r.specialty || ''} ${r.role || ''} ${r.start || ''}`}</span></td></tr>)}</tbody></table></div>{preview.records?.length > 12 && <small>Showing the first 12 of {preview.records.length} records.</small>}</div>}<ErrorMessage message={error}/></div><div className="modal-actions"><button className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy || !preview?.token || !!preview?.errors?.length || !preview?.records?.length} onClick={async () => { try {
    await mutate('/import/apply', { token: preview.token, revision: state.revision });
    notify(`${preview.records.length} records imported.`);
    close();
}
catch (e) {
    setError((e as Error).message);
} }}><Check size={16}/>Apply import</button></div></Modal>; }
function ReadOnlyDialog({ kind }: {
    kind: string;
}) { const { close, state } = useApp(); const [data, setData] = useState<any>(null), [error, setError] = useState(''); useEffect(() => { api(kind === 'audit' ? '/audit' : '/ai/status').then(setData).catch(e => setError(e.message)); }, [kind]); const records = Array.isArray(data) ? data : data?.events || data?.entries || data?.audit || []; return <Modal title={kind === 'audit' ? 'Workspace activity' : 'Local AI status'} subtitle={kind === 'audit' ? 'An audit trail of requests, approvals, and applied changes.' : 'Ollama runs on this Mac. There is no cloud fallback.'} onClose={close} wide={kind === 'audit'}><div className="form-body"><ErrorMessage message={error}/>{!data && !error && <Spinner label="Loading"/>}{data && (kind === 'audit' ? (records.length ? <div className="audit-list">{[...records].reverse().map((r: any, i: number) => <div key={r.id || i}><div><strong>{r.action || r.event || r.kind || 'Activity'}</strong><span>{r.created_at || r.timestamp ? formatDate(r.created_at || r.timestamp, state.data.settings.timezone, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}</span></div><p>{r.summary || r.message || r.actor_name || r.user_id || r.actor_id || ''}</p>{r.details && <pre>{typeof r.details === 'string' ? r.details : JSON.stringify(r.details, null, 2)}</pre>}</div>)}</div> : <Empty title="No recorded activity yet"/>) : <div className="detail-pairs">{Object.entries(data).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong></div>)}</div>)}</div><div className="modal-actions"><button className="button" onClick={close}>Close</button>{kind === 'audit' && <a className="button" href="/api/export?kind=shifts" download><Download size={15}/>Export schedule</a>}</div></Modal>; }
function AccountDialog() { const { state, close, mutate, notify, busy } = useApp(); const [password, setPassword] = useState(''), [next, setNext] = useState(''), [confirm, setConfirm] = useState(''), [error, setError] = useState(''); return <Modal title="Your account" subtitle={`${state.user.name} · ${state.user.username} · ${state.user.role}`} onClose={close}><form onSubmit={async (e) => { e.preventDefault(); setError(''); if (next !== confirm) {
    setError('The new passwords do not match.');
    return;
} try {
    await mutate('/auth/password', { current_password: password, new_password: next });
    notify('Password changed. Sign in again with your new password.');
    close();
}
catch (e) {
    setError((e as Error).message);
} }}><div className="form-body"><h3>Change password</h3><Field label="Current password"><input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)}/></Field><Field label="New password" hint="At least 10 characters. You will be signed out after saving."><input required minLength={10} maxLength={256} type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)}/></Field><Field label="Confirm new password"><input required minLength={10} type="password" autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)}/></Field><ErrorMessage message={error}/></div><div className="modal-actions"><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={busy}>Change password</button></div></form></Modal>; }
function UsersDialog() { const { state, close, mutate, notify, busy } = useApp(); const [users, setUsers] = useState<any[]>([]), [error, setError] = useState(''), [form, setForm] = useState({ name: '', username: '', password: '', role: 'clinician', clinician_id: '' }); const set = (key: string, value: string) => setForm(f => ({ ...f, [key]: value })); useEffect(() => { api('/users').then(setUsers).catch(e => setError(e.message)); }, []); return <Modal title="Team accounts" subtitle="Give each team member an individual sign-in and appropriate access." onClose={close} wide><form onSubmit={async (e) => { e.preventDefault(); setError(''); try {
    await mutate('/users', { ...form, clinician_id: form.clinician_id || null });
    setUsers(await api('/users'));
    setForm({ name: '', username: '', password: '', role: 'clinician', clinician_id: '' });
    notify('Team account created.');
}
catch (e) {
    setError((e as Error).message);
} }}><div className="form-body"><div className="table-wrap"><table><thead><tr><th>NAME</th><th>USERNAME</th><th>ACCESS</th></tr></thead><tbody>{users.map(u => <tr key={u.id}><td>{u.name}</td><td>{u.username}</td><td><Badge>{u.role}</Badge></td></tr>)}</tbody></table></div><div className="form-divider"/><h3>Create an account</h3><div className="form-grid"><Field label="Display name"><input required value={form.name} onChange={e => set('name', e.target.value)}/></Field><Field label="Username" hint="Letters, numbers, dots, hyphens, or underscores."><input required minLength={3} maxLength={50} pattern="[a-zA-Z0-9_.\-]+" value={form.username} autoComplete="off" onChange={e => set('username', e.target.value)}/></Field><Field label="Access role"><select value={form.role} onChange={e => set('role', e.target.value)}><option value="clinician">Clinician</option><option value="scheduler">Scheduler</option><option value="admin">Administrator</option></select></Field><Field label="Link clinician" hint="Required for clinician accounts. One account per clinician."><select required={form.role === 'clinician'} value={form.clinician_id} onChange={e => { set('clinician_id', e.target.value); if (!form.name)
    set('name', state.data.clinicians.find(c => c.id === e.target.value)?.name || ''); }}><option value="">No clinician linked</option>{state.data.clinicians.filter(c => !users.some(u => u.clinician_id === c.id)).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></div><Field label="Initial password" hint="At least 10 characters. Share directly with the account holder; they can change it from their account panel."><input required minLength={10} maxLength={256} type="password" autoComplete="new-password" value={form.password} onChange={e => set('password', e.target.value)}/></Field><ErrorMessage message={error}/></div><div className="modal-actions"><button type="button" className="button" onClick={close}>Close</button><button className="button primary" disabled={busy}><Plus size={16}/>Create account</button></div></form></Modal>; }
