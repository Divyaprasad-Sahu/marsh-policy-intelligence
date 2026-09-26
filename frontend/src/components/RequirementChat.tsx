import { CheckCircle2, LoaderCircle, MessageCircle, Send } from 'lucide-react'
import type { StructuredRequirement } from '../types'

interface Props {
  message: string
  onMessageChange: (value: string) => void
  response: string | null
  requirements: StructuredRequirement[]
  busy: boolean
  onSend: () => void
  onToggle: (requirementId: string) => void
}

export function RequirementChat({ message, onMessageChange, response, requirements, busy, onSend, onToggle }: Props) {
  return <section className="requirement-chat" aria-label="Additional client requirements">
    <div className="requirement-chat-heading"><MessageCircle size={20}/><div><strong>Add a client requirement</strong><p>Describe a need in plain language. Confirm the suggestion before it affects analysis.</p></div></div>
    <div className="requirement-chat-input"><input value={message} onChange={(event) => onMessageChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); onSend() } }} placeholder="e.g. Employees need annual health checks with minimal co-payment" /><button className="journey-primary" disabled={busy || !message.trim()} onClick={onSend} type="button">{busy ? <LoaderCircle className="spin" size={17}/> : <Send size={17}/>} Add</button></div>
    {response ? <p className="requirement-reply">{response}</p> : null}
    {requirements.length ? <div className="requirement-suggestions">{requirements.map((item) => <button key={item.requirement_id} className={item.confirmed ? 'confirmed' : ''} onClick={() => onToggle(item.requirement_id)} type="button"><CheckCircle2 size={16}/><span><b>{item.requirement}</b><small>{item.priority.replace('_', ' ')} · {item.confirmed ? 'Included in analysis' : 'Click to include in analysis'}</small></span></button>)}</div> : null}
  </section>
}
