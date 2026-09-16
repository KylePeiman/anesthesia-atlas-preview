import { useEffect, useState, useCallback } from 'react';
import { CalendarDays, LayoutDashboard, Users, Settings2, Inbox, LogOut, MessageSquare, ShieldCheck, ChevronRight, RefreshCw, Wifi, Menu, X, Activity, ArrowUpRight, MapPin } from 'lucide-react';
import { api, ApiError, dateKey, type State, type Job, type RecordResource } from './types';
import { AppContext, type ModalSpec } from './context';
import { Badge, ErrorMessage, Spinner } from './components';
import { CalendarScreen, BoardScreen, RequestsScreen, StaffScreen, SettingsScreen, ReviewScreen } from './screens';
import { AppModal } from './dialogs';
import { ChatPanel } from './chat';
import {PreviewBanner} from './review';
import {STATIC_PREVIEW} from './static-demo';
export default function App() {
    const [selectedSite, setSelectedSite] = useState('all'), [focusedRecord, setFocusedRecord] = useState<{
        resource: string;
        id: string;
    } | null>(null), [previewJob, setPreviewJob] = useState<Job | null>(null);
    const [state, setState] = useState<State | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [view, setView] = useState('calendar'), [selectedDay, setSelectedDay] = useState(''), [modal, setModal] = useState<ModalSpec | null>(null), [chatOpen, setChatOpen] = useState(false), [mobileMenu, setMobileMenu] = useState(false), [toast, setToast] = useState<{
        message: string;
        tone: string;
    } | null>(null), [initialError, setInitialError] = useState('');
    const notify = useCallback((message: string, tone = 'success') => setToast({ message, tone }), []);
    const refresh = useCallback(async () => { try {
        const s = await api<State>('/state');
        setState(s);
        setInitialError('');
        setSelectedDay(d => d || dateKey(s.data.shifts[0]?.start || new Date().toISOString(), s.data.settings.timezone));
    }
    catch (e) {
        if (e instanceof ApiError && e.status === 401) {
            setState(null);setModal(null);setChatOpen(false);setPreviewJob(null);setFocusedRecord(null);setSelectedSite('all');setSelectedDay('');
        }
        else
            throw e;
    } }, []);
    useEffect(() => { refresh().catch(e => setInitialError(e.message)).finally(() => setLoading(false)); }, [refresh]);
    useEffect(() => { if (!toast)
        return; const t = setTimeout(() => setToast(null), 6500); return () => clearTimeout(t); }, [toast]);
    const jobsRunning = state?.jobs.some(j => ['pending', 'queued', 'running'].includes(j.status));
    useEffect(() => { if (!state)
        return; const interval = setInterval(() => refresh().catch(() => { }), jobsRunning ? 2500 : 15000); return () => clearInterval(interval); }, [!!state, jobsRunning, refresh]);
    useEffect(()=>{if(!modal&&focusedRecord)document.getElementById(`record-${focusedRecord.id}`)?.scrollIntoView({behavior:'smooth',block:'center'});},[modal,focusedRecord,view]);
    const mutate = async <T,>(path: string, body?: unknown, method?: string): Promise<T> => { setBusy(true); try {
        const result = await api<T>(path, body, method);
        await refresh();
        return result;
    }
    catch (e) {
        if (e instanceof ApiError && e.status === 409) {
            await refresh();
            throw new ApiError(409, `${e.message} The latest schedule is now loaded. Reopen the editor to review current data, or create an updated proposal preview.`);
        }
        throw e;
    }
    finally {
        setBusy(false);
    } };
    if (loading)
        return <div className="boot"><div className="brand-mark">A</div><Spinner label="Opening Anesthesia Atlas"/></div>;
    if (!state)
        return <Login onLogin={refresh} error={initialError}/>;
    const manager = ['admin', 'scheduler'].includes(state.user.role), admin = state.user.role === 'admin';
    const pending = state.data.requests.filter(x => x.status === 'pending' || x.details?.cancellation_requested).length;
    const proposalCount = state.proposals.filter(x => x.status === 'pending').length;
    const navigation = [{ id: 'calendar', label: 'Staffing calendar', icon: CalendarDays }, { id: 'board', label: 'Daily OR board', icon: LayoutDashboard }, { id: 'requests', label: 'Requests', icon: Inbox, count: pending }, { id: 'review', label: 'Schedule review', icon: ShieldCheck, count: proposalCount }, { id: 'staff', label: 'Clinician directory', icon: Users }, ...(manager ? [{ id: 'settings', label: 'Settings & data', icon: Settings2 }] : [])];
    const navigate = (id: string) => { setView(id); setMobileMenu(false); setPreviewJob(null); setFocusedRecord(null); };
    const scheduleData = previewJob?.result?.preview ? { ...state.data, ...previewJob.result.preview } : state.data;
    const scheduleIssues = previewJob ? Array.from(new Map([...(previewJob.result?.new_issues || []), ...(previewJob.result?.existing_issues || []), ...(previewJob.result?.gaps || [])].map(i => [`${i.resource}:${i.id}:${i.code}:${i.message}`, i])).values()) : state.issues;
    const navigateRecord = (resource: RecordResource, id: string, contextJob?: Job) => { const data = contextJob?.result?.preview ? { ...state.data, ...contextJob.result.preview } : state.data; const record = data[resource].find(r => r.id === id); if (!record) {
        notify('This record is no longer available. Refresh the result and try again.', 'error');
        return;
    } setPreviewJob(contextJob || null); setFocusedRecord({ resource, id }); setMobileMenu(false); if ('start' in record && record.start)
        setSelectedDay(dateKey(record.start, state.data.settings.timezone)); if ('site_id' in record)
        setSelectedSite(record.site_id); setView(resource === 'cases' ? 'board' : resource === 'shifts' ? 'calendar' : resource === 'clinicians' ? 'staff' : 'requests'); setModal(resource === 'requests' ? { type: 'request-detail', record } : { type: 'record-detail', resource, record, previewJob: contextJob }); };
    const logout = async () => { try {
        await api('/auth/logout', {});
        setState(null);
        setModal(null);
        setChatOpen(false);
        setSelectedDay('');
        setPreviewJob(null);setFocusedRecord(null);setSelectedSite('all');
    }
    catch (e) {
        notify((e as Error).message, 'error');
    } };
    return <AppContext.Provider value={{ state, manager, admin, busy, refresh, notify, mutate, open: setModal, close: () => setModal(null), selectedDay, setSelectedDay, setView: navigate, openChat: () => setChatOpen(true), selectedSite, setSelectedSite, focusedRecord, previewJob, clearPreview: () => { setPreviewJob(null); setFocusedRecord(null); }, scheduleData, scheduleIssues, navigateRecord }}>
 <div className={`app-shell ${chatOpen ? 'with-chat' : ''}`}>
 {mobileMenu && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileMenu(false)}/>}
 <aside className={`sidebar ${mobileMenu ? 'open' : ''}`}><a className="brand" href="#calendar" onClick={e => { e.preventDefault(); navigate('calendar'); }}><span className="brand-mark"><Activity size={26}/></span><span>Anesthesia<strong>Atlas</strong></span></a><div className="workspace-label"><span className="workspace-dot"/>Demonstration group</div><div className="nav-label">WORKSPACE</div><nav>{navigation.map(({ id, label, icon: Icon, count }) => <button key={id} onClick={() => navigate(id)} className={`nav-item ${view === id ? 'active' : ''}`}><Icon size={19}/><span>{label}</span>{!!count && <span className="nav-count">{count}</span>}</button>)}</nav><div className="sidebar-note"><div className="tiny-heading"><Wifi size={15}/>LOCAL WORKSPACE</div><p>Your team. Your schedule.<br />Running on this Mac.</p><span className="local-dot"/> Local AI through Ollama</div><div className="account"><div className="avatar">{state.user.name?.split(' ').map(w => w[0]).slice(0, 2).join('') || 'A'}</div><button className="account-name" title="Your account and password" onClick={() => setModal({ type: 'account' })}><strong>{state.user.name || state.user.username}</strong><span>{state.user.role}</span></button><button className="icon-button" title="Sign out" aria-label="Sign out" onClick={logout}><LogOut size={17}/></button></div></aside>
 <div className="main-shell"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-only" aria-label="Open navigation" onClick={() => setMobileMenu(true)}><Menu size={21}/></button><span className="desktop-only">Workspace</span><ChevronRight size={14} className="desktop-only"/><strong>{navigation.find(n => n.id === view)?.label}</strong></div><div className="topbar-right"><Badge tone="amber">Fictional demo</Badge><span className="timezone desktop-only">{state.data.settings.timezone}</span><button className="icon-button" aria-label="Refresh schedule" title="Refresh schedule" onClick={() => refresh().then(() => notify('Schedule is up to date.')).catch(e => notify(e.message, 'error'))}><RefreshCw size={17} className={busy ? 'spin' : ''}/></button><button className={`chat-toggle ${chatOpen ? 'selected' : ''}`} aria-label={chatOpen?'Close Ask Atlas':'Ask Atlas'} aria-expanded={chatOpen} onClick={() => setChatOpen(!chatOpen)}><MessageSquare size={16}/><span>Ask Atlas</span><span className="ai-spark">✦</span></button></div></header>
 <main>{STATIC_PREVIEW&&<div className="static-preview-banner" role="status"><ShieldCheck size={16}/><span><strong>Public visual preview</strong> · This GitHub Pages version uses fictional in-browser data. Editing, optimization, leave approval, and local AI are available only in the local Atlas installation.</span></div>}<PreviewBanner/>{view === 'calendar' ? <CalendarScreen /> : view === 'board' ? <BoardScreen /> : view === 'requests' ? <RequestsScreen /> : view === 'staff' ? <StaffScreen /> : view === 'settings' ? <SettingsScreen /> : <ReviewScreen />}</main><footer className="app-footer"><span><ShieldCheck size={13}/>Demonstration rules · Review before clinical use</span><span>Revision {state.revision} · {state.data.sites.length} locations</span></footer></div>
 {chatOpen && <ChatPanel onClose={() => setChatOpen(false)}/>}
 </div>{modal && <AppModal key={`${modal.type}:${modal.record?.id||modal.proposal?.id||modal.job?.id||''}`} spec={modal}/>} {toast && <div className={`toast ${toast.tone}`} role="status"><span>{toast.message}</span><button className="icon-button" onClick={() => setToast(null)} aria-label="Dismiss notification"><X size={16}/></button></div>}
 </AppContext.Provider>;
}
function Login({ onLogin, error: initialError }: {
    onLogin: () => Promise<void>;
    error: string;
}) {
    const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState(initialError), [busy, setBusy] = useState(false);
    return <div className="login-shell"><div className="login-aside"><a className="brand" href="#"><span className="brand-mark"><Activity size={28}/></span><span>Anesthesia<strong>Atlas</strong></span></a><div className="login-message"><div className="eyebrow">CLARITY FOR EVERY SHIFT</div><h1>A better rhythm<br />for your team.</h1><p>Thoughtful schedules, clear coverage, and room for life outside the OR.</p><div className="login-illustration"><div className="illustration-top"><span>YOUR CONNECTED WORKDAY</span><CalendarDays size={18}/></div>{[['07:00', 'OR coverage', 'Coordinated across specialties'], ['12:00', 'Relief & handoffs', 'The right people, in place'], ['19:00', 'Call begins', 'A clear plan for the night']].map(([t, title, sub]) => <div className="illustration-row" key={t}><span>{t}</span><i /><div><strong>{title}</strong><small>{sub}</small></div></div>)}</div></div><div className="login-local"><Wifi size={16}/> Running locally · Powered by OR-Tools</div></div><div className="login-main"><div className="login-card"><Badge tone="teal">LOCAL WORKSPACE</Badge><h2>Welcome to Atlas</h2><p>Sign in to your anesthesia scheduling workspace.</p><form onSubmit={async (e) => { e.preventDefault(); setBusy(true); setError(''); try {
        await api('/auth/login', { username, password });
        await onLogin();
    }
    catch (err) {
        setError((err as Error).message);
    }
    finally {
        setBusy(false);
    } }}><label className="field"><span>Username</span><input name="username" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} placeholder="Your username" autoFocus/></label><label className="field"><span>Password</span><input name="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Your password"/></label><ErrorMessage message={error}/><button className="button primary login-button" disabled={busy}>{busy ? <Spinner label="Signing in"/> : <>Sign in<ArrowUpRight size={18}/></>}</button></form><div className="login-help demo-login-hint"><MapPin size={18}/><div><strong>Try the demo</strong><p>Administrator: <b>admin / admin</b><br/>Clinician view: <b>user / user</b></p></div></div><small className="login-disclaimer">This workspace starts with fictional staff and example scheduling rules.</small></div></div></div>;
}
