import {useEffect,useRef,useState} from 'react';
import {ArrowUp,CalendarDays,ChevronRight,MessageSquare,RefreshCw,ShieldCheck,Sparkles,Users,X} from 'lucide-react';
import {useApp} from './context';
import {api,newId,formatDate,kindLabel,type ChatResult,type RecordLink} from './types';
import {Badge,ErrorMessage,Spinner} from './components';
import {ChangesView,recordLabel} from './review';

type Message={id:string;role:'user'|'assistant';text:string;result?:ChatResult;submitted?:boolean;outcome?:string};

export function ChatPanel({onClose}:{onClose:()=>void}) {
 const {state,mutate,open,notify,setView}=useApp();
 const storageKey=`atlas-chat-v2-${state.user.id}`;
 const readStored=()=>{try{return JSON.parse(sessionStorage.getItem(storageKey)||'{}')}catch{return {}}};
 const [messages,setMessages]=useState<Message[]>(()=>readStored().messages||[]),[conversation,setConversation]=useState<string|undefined>(()=>readStored().conversation),[input,setInput]=useState(''),[sending,setSending]=useState(false),[applying,setApplying]=useState(''),[error,setError]=useState('');
 const bottom=useRef<HTMLDivElement>(null),field=useRef<HTMLTextAreaElement>(null);
 useEffect(()=>{sessionStorage.setItem(storageKey,JSON.stringify({messages,conversation}));bottom.current?.scrollIntoView({behavior:'smooth',block:'end'})},[messages,conversation,sending]);
 const send=async(text=input,selection?:{clarification_id:string;choice_id:string})=>{
  if((!text.trim()&&!selection)||sending)return;
  setSending(true);setError('');setInput('');setMessages(m=>[...m,{id:newId(),role:'user',text:text.trim()}]);
  try{const response=await api<ChatResult>('/chat',{message:selection?'':text.trim(),conversation_id:conversation,...(selection?{selection}:{})});setConversation(response.conversation_id||conversation);setMessages(m=>[...m,{id:newId(),role:'assistant',text:response.reply,result:response}])}
  catch(e){setError((e as Error).message)}finally{setSending(false);field.current?.focus()}
 };
 const apply=async(message:Message)=>{
  if(!message.result?.action)return;setApplying(message.id);setError('');
  try{
   if(typeof message.result.base_revision!=='number'||message.result.base_revision!==state.revision)throw new Error('This preview is out of date. Ask Atlas to prepare the request again against the current schedule.');
   const action=message.result.action;if(!['request','proposal'].includes(action.type))throw new Error('Use the calendar or Requests page to submit this change.');
   const response=await mutate(action.type==='request'?'/requests':'/proposals',{...action.payload,revision:message.result.base_revision});
   setMessages(items=>items.map(m=>m.id===message.id?{...m,submitted:true,outcome:action.type==='proposal'?'Proposal ready for review':response.request?.status==='approved'?'Approved and applied':'Awaiting review'}:m));
   if(action.type==='proposal')open({type:'proposal',proposal:response.proposal||response});else{notify('Request submitted from chat.');setView('requests')}
  }catch(e){setError((e as Error).message)}finally{setApplying('')}
 };
 const latestAssistant=messages.filter(m=>m.role==='assistant').at(-1)?.id;
 return <aside className="chat-panel" aria-label="Atlas local AI assistant">
  <header className="chat-header"><div className="chat-avatar"><Sparkles size={21}/></div><div><h2>Ask Atlas</h2><span><span className="local-dot"/>Local AI assistant</span></div><button className="icon-button" title="New conversation" aria-label="New conversation" disabled={sending} onClick={()=>{setMessages([]);setConversation(undefined);setError('')}}><RefreshCw size={15}/></button><button className="icon-button" aria-label="Close chat" onClick={onClose}><X size={18}/></button></header>
  <div className="chat-privacy"><ShieldCheck size={13}/>Questions answered from your workspace</div>
  <div className="chat-messages">
   {!messages.length&&<div className="chat-welcome"><span className="chat-welcome-symbol"><MessageSquare size={28}/><span>✦</span></span><h3>A little help with<br/>your schedule.</h3><p>Ask about assignments, explore coverage, or prepare a change in plain English.</p><div className="chat-suggestions">{['What coverage gaps do we have?','Who is on shift tomorrow?','How do I request two weeks off?'].map(s=><button key={s} onClick={()=>send(s)}>{s}<ChevronRight size={15}/></button>)}</div><div className="chat-assurance"><ShieldCheck size={15}/><span>Changes are previewed first and follow your group’s approval rules.</span></div></div>}
   {messages.map(message=><div key={message.id} className={`chat-message ${message.role}`}>
    <div className="message-label">{message.role==='user'?'You':<><Sparkles size={12}/>Atlas</>}</div>
    {message.text&&<div className={`message-bubble ${message.result?.unavailable?'unavailable':''}`}>{message.text}</div>}
    {!!message.result?.references?.length&&<ChatReferences references={message.result.references}/>}
    {message.result?.clarification&&<div className="chat-clarification">
     {message.result.clarification.prompt!==message.text&&<p>{message.result.clarification.prompt}</p>}
     <div className="clarification-choices">{message.result.clarification.choices.map(choice=><button className="button" key={choice.id} disabled={sending||latestAssistant!==message.id||message.result?.base_revision!==state.revision} onClick={()=>send(choice.label,{clarification_id:message.result!.clarification!.id,choice_id:choice.id})}>{choice.label}<ChevronRight size={14}/></button>)}</div>
     {latestAssistant===message.id&&message.result.base_revision!==state.revision&&<small>The schedule has changed. Ask your original question again to get current choices.</small>}
    </div>}
    {message.result?.proposal&&<button className="button compact" onClick={()=>open({type:'proposal',proposal:message.result?.proposal})}>Review proposal<ChevronRight size={15}/></button>}
    {message.result?.action&&<div className="chat-action"><div className="chat-action-title"><Badge tone={message.submitted?'teal':'amber'}>{message.submitted?(message.outcome||'Submitted'):message.result.base_revision===state.revision?'Preview only':'Outdated preview'}</Badge><strong>{message.result.action.type==='request'?kindLabel(message.result.action.payload.kind||'request'):'Schedule change'}</strong></div><ActionDetails action={message.result.action}/>
     {!message.submitted&&message.result.base_revision!==state.revision&&<p className="form-hint">The schedule changed. Ask Atlas to prepare the request again.</p>}
     {message.submitted?<p className="form-hint">Current status: {message.outcome||'Submitted for review'}. Open Requests or Schedule review for the latest status.</p>:<button className="button primary" disabled={!!applying||message.result.base_revision!==state.revision} onClick={()=>apply(message)}>{applying===message.id?<Spinner label="Submitting"/>:message.result.action.type==='request'?'Submit request':'Create review proposal'}<ChevronRight size={14}/></button>}
    </div>}
   </div>)}
   {sending&&<div className="chat-thinking"><Sparkles size={14}/><span>Atlas is checking your workspace</span><span className="thinking-dots">•••</span></div>}<div ref={bottom}/>
  </div>
  <div className="chat-composer"><ErrorMessage message={error}/><form onSubmit={e=>{e.preventDefault();send()}}><textarea ref={field} aria-label="Message Atlas" placeholder="Ask about your schedule…" rows={2} value={input} disabled={sending} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/><button aria-label="Send message" className="chat-send" disabled={sending||!input.trim()}><ArrowUp size={19}/></button></form><small>Local AI can make mistakes. Check dates and changes.</small></div>
 </aside>;
}

export function ChatReferences({references}:{references:RecordLink[]}) {
 const {state,navigateRecord}=useApp();const [limit,setLimit]=useState(8);
 const unique=Array.from(new Map(references.filter(r=>r&&['shifts','cases','clinicians','requests'].includes(r.resource)).map(r=>[`${r.resource}:${r.id}`,r])).values());
 return <div className="chat-references">{unique.slice(0,limit).map(reference=>{
  const found=state.data[reference.resource].some(r=>r.id===reference.id);
  const label=reference.label&&!reference.label.includes(reference.id)?reference.label:recordLabel(state.data,reference.resource,reference.id);
  return <button className="chat-record-link" key={`${reference.resource}:${reference.id}`} disabled={!found} onClick={()=>navigateRecord(reference.resource,reference.id)}>
   {reference.resource==='clinicians'?<Users size={15}/>:<CalendarDays size={15}/>}<span>{label}{reference.reasons?.map(reason=><small key={reason}>{reason}</small>)}</span><ChevronRight size={15}/>
  </button>
 })}{limit<unique.length&&<button className="chat-more" onClick={()=>setLimit(n=>n+8)}>Show {Math.min(8,unique.length-limit)} more results ({unique.length-limit} remaining)<ChevronRight size={14}/></button>}</div>;
}

function ActionDetails({action}:{action:{type:string;payload:Record<string,any>}}) {
 const {state,navigateRecord}=useApp();const payload=action.payload,zone=state.data.settings.timezone;
 if(action.type==='proposal')return <>{payload.summary&&<p>{payload.summary}</p>}<ChangesView changes={payload.changes||[]}/></>;
 const clinician=state.data.clinicians.find(c=>c.id===payload.clinician_id);
 return <div className="chat-action-details"><strong>{clinician?.name||'Current clinician'}</strong>
  {payload.start&&<span>From: {formatDate(payload.start,zone,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'})}</span>}
  {payload.end&&<span>Until: {formatDate(payload.end,zone,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'})}</span>}
  {payload.note&&<p>{payload.note}</p>}
  {payload.details&&Object.entries(payload.details).filter(([key])=>!['accepted_by','other_clinician_id','replaces_request_id'].includes(key)).map(([key,value])=>key==='shift_id'||key==='other_shift_id'?<button className="record-link" key={key} onClick={()=>navigateRecord('shifts',String(value))}>{recordLabel(state.data,'shifts',String(value))}<ChevronRight size={14}/></button>:<span key={key}>{key.replaceAll('_',' ')}: {Array.isArray(value)?value.join(', '):String(value)}</span>)}
 </div>;
}
