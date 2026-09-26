import { AlertTriangle, ArrowRight, CheckCircle2, Download, FileSearch, FileText, LoaderCircle, MessageCircle, Send, ShieldCheck, Sparkles, Target, UserCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, downloadBlob } from '../api'
import type { AuditReport, MarketingPitch, PolicySummary, StructuredRequirement } from '../types'
import { AuditPanel } from './AuditPanel'
import { PitchWorkspace } from './PitchWorkspace'
import { RequirementChat } from './RequirementChat'

type Busy = 'research' | 'analysis' | 'pitch' | 'audit' | 'export' | 'decision' | 'requirements' | null
const steps = ['Company search', 'Verify profile', 'Client requirements', 'Insurance analysis', 'Recommendation & gaps', 'Pitch & audit', 'Approve & download']
const priorities = [
  'In-patient hospitalisation',
  'Pre- and post-hospitalisation',
  'Room rent and ICU clarity',
  'Emergency ambulance support',
  'Restore or reload benefit',
  'Cashless hospital network',
  'Annual health check-ups',
  'Preventive healthcare',
  'Modern treatments',
  'Home healthcare',
  'Organ donor expenses',
  'Low employee cost-sharing',
  'Waiting-period clarity',
  'Maternity and newborn cover',
  'Pre-existing disease continuity',
  'Mental health support',
  'Day-care procedures',
  'OPD and teleconsultation',
  'Critical illness cover',
]

const advisorConfirmationPrompt = (value: string) => /requires advisor confirmation/i.test(value)
const downloadName = (company: string, type: string, version: number, extension: string) => {
  const slug = company.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'client'
  return `${slug}_${type}_v${version}_${new Date().toISOString().slice(0, 10)}.${extension}`
}

function reAuditRequired(previous: AuditReport | null, pitchVersion: number, resolvedClaimId?: string): AuditReport | null {
  if (!previous) return null
  const resolved = resolvedClaimId ? previous.claim_results.find((item) => item.claim_id === resolvedClaimId) : undefined
  return {
    ...previous,
    status: 'AUDIT_INCOMPLETE',
    total_claims: 0,
    supported_claims: 0,
    partially_supported_claims: 0,
    unsupported_claims: 0,
    contradicted_claims: 0,
    unverifiable_claims: previous.total_claims,
    critical_issues: 0,
    summary: `Pitch updated — fresh audit required. Version ${pitchVersion} has no current audit result.`,
    audited_pitch_version: undefined,
    audited_pitch_hash: undefined,
    advisor_decision: undefined,
    claim_results: [],
    resolved_changes: resolved ? [{ claim_id: resolved.claim_id, benefit_name: resolved.benefit_name, previous_status: resolved.status, state: 'RESOLVED_AWAITING_REAUDIT' }] : [],
  }
}
const companyNameSuggestions = ['Tata Consultancy Services', 'Tata Motors', 'Tata Steel', 'Tata Consumer Products', 'Tata Power', 'Reliance Industries', 'Reliance Retail', 'Reliance Jio', 'Trent Limited', 'Infosys', 'HDFC Bank', 'ICICI Bank', 'Wipro', 'Mahindra & Mahindra', 'Larsen & Toubro', 'Maruti Suzuki India Limited', 'Hyundai Motor Company', 'Hyundai Motor India Limited', 'Honda Motor Company', 'Toyota Motor Corporation', 'Kia India', 'Solar Industries India Limited', 'Dixon Technologies India Limited', 'Kalyan Jewellers India Limited', 'Siemens Limited', 'Hindustan Unilever Limited', 'Bajaj Finance Limited', 'Adani Enterprises Limited', 'Vishal Mega Mart']
const confirmationQuestions: Record<string, string> = {
  Industry: 'What is the client’s primary industry?',
  'Company size': 'Approximately how many employees need cover?',
  'Operating locations': 'Which cities or operating locations need cover?',
  'Workforce profile': 'What is the main workforce profile: office, field, factory, or mixed?',
}
const discoveryQuestions = [
  { id: 'headcount', question: 'What is the expected insured headcount at policy inception?', placeholder: 'e.g. 5,000 employees', profileField: 'Company size' },
  { id: 'population', question: 'Who needs cover: employees only, or employees and dependants?', placeholder: 'e.g. Employees, spouse and up to two children' },
  { id: 'locations', question: 'Which locations need a strong cashless hospital network?', placeholder: 'e.g. Mumbai, Pune, Jamshedpur', profileField: 'Operating locations' },
  { id: 'sum-insured', question: 'What sum insured and co-payment approach is preferred?', placeholder: 'e.g. ₹5 lakh, no employee co-payment' },
  { id: 'conditions', question: 'Are maternity, pre-existing disease cover, or waiting-period waivers required?', placeholder: 'Describe mandatory requirements or write None' },
  { id: 'renewal', question: 'What is the current insurer, renewal date, and any material claims concern?', placeholder: 'e.g. Renewal 1 April; claims experience to be reviewed' },
]

export function AdvisoryJourney() {
  const [started, setStarted] = useState(false)
  const [company, setCompany] = useState('')
  const [companySuggestions, setCompanySuggestions] = useState<string[]>([])
  const [companyInputFocused, setCompanyInputFocused] = useState(false)
  const [profile, setProfile] = useState<MarketingPitch['company_profile'] | null>(null)
  const [missing, setMissing] = useState<string[]>([])
  const [policies, setPolicies] = useState<PolicySummary[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [showBaselineDocuments, setShowBaselineDocuments] = useState(false)
  const [industry, setIndustry] = useState('')
  const [employees, setEmployees] = useState('')
  const [locations, setLocations] = useState('')
  const [workforce, setWorkforce] = useState('')
  const [discoveryAnswers, setDiscoveryAnswers] = useState<Record<string, string>>({})
  const [budget, setBudget] = useState('')
  const [selectedPriorities, setSelectedPriorities] = useState<string[]>([])
  const [structuredRequirements, setStructuredRequirements] = useState<StructuredRequirement[]>([])
  const [requirementMessage, setRequirementMessage] = useState('')
  const [requirementReply, setRequirementReply] = useState<string | null>(null)
  const [detailsConfirmed, setDetailsConfirmed] = useState(false)
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const [confirmationTarget, setConfirmationTarget] = useState<HTMLElement | null>(null)
  const [recommendationChatTarget, setRecommendationChatTarget] = useState<HTMLElement | null>(null)
  const [analysis, setAnalysis] = useState<any>(null)
  const [pitch, setPitch] = useState<MarketingPitch | null>(null)
  const [audit, setAudit] = useState<AuditReport | null>(null)
  const [showAuditScreen, setShowAuditScreen] = useState(false)
  const [activeSlide, setActiveSlide] = useState(1)
  const [advisorName, setAdvisorName] = useState('')
  const [rejectionFeedback, setRejectionFeedback] = useState('')
  const [busy, setBusy] = useState<Busy>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.policies().then((items) => { setPolicies(items); setSelected(items.map((item) => item.policy_id)) }).catch((reason: Error) => setError(reason.message)) }, [])
  useEffect(() => {
    const query = company.trim()
    if (query.length < 1) {
      setCompanySuggestions([])
      return
    }

    const queryLower = query.toLocaleLowerCase()
    const localMatches = companyNameSuggestions
      .filter((name) => name.toLocaleLowerCase().startsWith(queryLower))
      .concat(companyNameSuggestions.filter((name) => !name.toLocaleLowerCase().startsWith(queryLower) && name.toLocaleLowerCase().includes(queryLower)))
      .slice(0, 6)
    setCompanySuggestions(localMatches)
    if (query.length < 2) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      api.companySuggestions(query, controller.signal)
        .then((matches) => setCompanySuggestions(Array.from(new Set([...matches, ...localMatches])).slice(0, 8)))
        .catch((reason: Error) => { if (reason.name !== 'AbortError') setCompanySuggestions(localMatches) })
    }, 60)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [company])
  useEffect(() => {
    if (!started) return
    const baseline = document.querySelector('#requirements .baseline-control')
    if (!baseline?.parentElement) return
    const anchor = document.createElement('div')
    anchor.className = 'inline-confirmation-anchor'
    baseline.parentElement.insertBefore(anchor, baseline)
    setConfirmationTarget(anchor)
    return () => { setConfirmationTarget(null); anchor.remove() }
  }, [started])
  useEffect(() => {
    if (!analysis) return
    const card = document.querySelector('#recommendation .recommendation-card')
    if (!card) return
    const anchor = document.createElement('div')
    anchor.className = 'recommendation-chat-anchor'
    card.insertBefore(anchor, card.querySelector('button'))
    setRecommendationChatTarget(anchor)
    return () => { setRecommendationChatTarget(null); anchor.remove() }
  }, [analysis])
  useEffect(() => { if (profile) setDetailsConfirmed(false) }, [industry, employees, locations, workforce, budget, selectedPriorities, structuredRequirements])
  const selectedNames = useMemo(() => policies.filter((item) => selected.includes(item.policy_id)).map((item) => item.product_name), [policies, selected])
  const advance = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const navigateStep = (index: number) => {
    const sectionIds = ['company-search', 'verify-profile', 'requirements', 'analysis', 'recommendation', 'pitch', 'approval']
    if (index === 6 && (!profile || !analysis || !pitch || !audit)) {
      const nextRequired = !profile ? 'company-search' : !analysis ? 'requirements' : !pitch ? 'recommendation' : 'pitch'
      setError('Complete steps 1–6, including the policy comparison, pitch generation, and audit, before opening Approve & download.')
      advance(nextRequired)
      return
    }
    setError(null)
    advance(sectionIds[index])
  }
  const updateFact = (name: string, value: string) => {
    setProfile((current) => current ? { ...current, facts: current.facts.map((fact) => fact.name === name ? { ...fact, value, status: 'verified' as const } : fact) } : current)
    if (name === 'Company size') setEmployees(value)
    if (name === 'Industry') setIndustry(value)
  }
  const confirmationValue = (item: string) => item === 'Industry' ? industry : item === 'Company size' ? employees : item === 'Operating locations' ? locations : workforce
  const answerConfirmation = (item: string, value: string) => {
    if (item === 'Industry') { setIndustry(value); updateFact('Industry', value) }
    else if (item === 'Company size') { setEmployees(value); updateFact('Company size', value) }
    else if (item === 'Operating locations') setLocations(value)
    else if (item === 'Workforce profile') setWorkforce(value)
    if (value.trim()) setMissing((current) => current.filter((entry) => entry !== item))
  }
  const saveDiscoveryAnswer = (question: typeof discoveryQuestions[number]) => {
    const answer = discoveryAnswers[question.id]?.trim()
    if (!answer) return
    if (question.profileField === 'Company size') answerConfirmation('Company size', answer)
    if (question.profileField === 'Operating locations') answerConfirmation('Operating locations', answer)
    if (['headcount', 'locations'].includes(question.id)) return
    setStructuredRequirements((current) => {
      const requirement = `${question.question} Advisor answer: ${answer}`
      const next = { requirement_id: `discovery-${question.id}`, category: 'client-discovery', requirement, priority: 'HIGH' as const, source: 'advisor_form' as const, confirmed: true }
      return [...current.filter((item) => item.requirement_id !== next.requirement_id), next]
    })
  }

  async function research() {
    if (!company.trim()) return setError('Enter a company name to begin.')
    setBusy('research'); setError(null)
    try {
      const result = await api.researchCompany(company.trim())
      setProfile(result.profile); setMissing(result.missing_field_checklist); setDetailsConfirmed(false); setConfirmationOpen(true); setNotice('API suggestions are ready. Please confirm or edit them before analysis.'); advance('verify-profile')
      const fact = (name: string) => result.profile.facts.find((item) => item.name === name)?.value || ''
      const suggested = result.suggested_requirements
      setIndustry(suggested.industry || fact('Industry').replace(' requires advisor confirmation', ''))
      setEmployees(suggested.employee_count || fact('Company size').replace(' requires advisor confirmation', ''))
      setLocations((suggested.operating_locations || []).join(', '))
      setWorkforce(suggested.workforce_profile || '')
      setBudget(suggested.budget_guidance || 'Not established — confirm with client')
      const normalisedPriorities: Record<string, string> = {
        'Hospitalisation coverage': 'In-patient hospitalisation',
        'Home care': 'Home healthcare',
      }
      setSelectedPriorities((suggested.ranked_coverage_priorities || []).map((item) => normalisedPriorities[item] || item).filter((item) => priorities.includes(item)))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Company research failed.') } finally { setBusy(null) }
  }

  async function analyze() {
    if (!profile || !selectedPriorities.length) return setError('Complete the company profile and choose at least one client priority.')
    if (!detailsConfirmed) { setConfirmationOpen(true); return }
    setBusy('analysis'); setError(null)
    try {
      const result = await api.analyze({ company_profile: profile, requirements: { industry, employee_count: employees, operating_locations: locations.split(',').map((item) => item.trim()).filter(Boolean), workforce_profile: workforce, ranked_coverage_priorities: selectedPriorities, budget_guidance: budget || undefined, structured_requirements: structuredRequirements }, policy_ids: selected })
      setAnalysis(result); setNotice('Evidence-led comparison completed.'); advance('analysis')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Analysis failed.') } finally { setBusy(null) }
  }

  async function generatePitch() {
    if (!profile) return
    setBusy('pitch'); setError(null)
    try {
      const result = await api.generate({ company_name: profile.company_name, policy_ids: selected, client_requirements: selectedPriorities, uploaded_documents: [], verified_company_profile: profile, requirements_context: { industry, employee_count: employees, operating_locations: locations.split(',').map((item) => item.trim()).filter(Boolean), workforce_profile: workforce, ranked_coverage_priorities: selectedPriorities, budget_guidance: budget || undefined, structured_requirements: structuredRequirements } } as any)
      setPitch(result); setActiveSlide(1); setNotice('The source-backed five-slide presentation is ready.'); advance('pitch')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Pitch generation failed.') } finally { setBusy(null) }
  }

  async function runAudit() {
    if (!pitch) return
    setBusy('audit'); setError(null)
    try {
      const result = await api.audit(pitch, [])
      const serverState = pitch.pitch_id ? await api.workflowState(pitch.pitch_id) : null
      if (serverState) setPitch(serverState.pitch)
      setAudit(result)
      setShowAuditScreen(true)
      window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'instant' }))
      setNotice(result.status === 'PASS' ? 'Audit passed. Advisor approval is enabled.' : 'Audit completed. Review the findings before approval.')
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Audit failed.') } finally { setBusy(null) }
  }

  async function addRequirementFromChat() {
    if (!requirementMessage.trim()) return
    setBusy('requirements'); setError(null)
    try {
      const result = await api.requirementChat(requirementMessage.trim(), structuredRequirements)
      setStructuredRequirements(result.requirements)
      setRequirementReply(result.assistant_message)
      setRequirementMessage('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not interpret the additional requirement.') } finally { setBusy(null) }
  }

  async function applyFix(claimId: string) {
    if (!pitch?.pitch_id) return
    setBusy('audit'); setError(null)
    try {
      const state = await api.applyFix(pitch.pitch_id, claimId)
      setPitch(state.pitch)
      setAudit(state.audit || reAuditRequired(audit, state.pitch.pitch_version, claimId))
      setShowAuditScreen(true)
      setNotice('Change applied. Run a fresh audit.')
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The suggested fix could not be applied.') } finally { setBusy(null) }
  }

  async function editClaim(slideNumber: number, claimId: string, text: string) {
    if (!pitch?.pitch_id) return
    setBusy('audit'); setError(null)
    try { const state = await api.editPitch(pitch.pitch_id, slideNumber, claimId, text); setPitch(state.pitch); setAudit(state.audit || reAuditRequired(audit, state.pitch.pitch_version, claimId)); setShowAuditScreen(true); setNotice('Pitch updated — fresh audit required.') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The pitch statement could not be updated.') } finally { setBusy(null) }
  }

  async function exportDeck() { if (!pitch?.pitch_id) return; setBusy('export'); try { downloadBlob(await api.finalPitch(pitch.pitch_id), downloadName(pitch.company_profile.company_name, 'policy-pitch', pitch.pitch_version, 'pptx')) } catch (reason) { setError(reason instanceof Error ? reason.message : 'PowerPoint export failed.') } finally { setBusy(null) } }
  async function exportReport() {
    if (!pitch?.pitch_id) return
    setBusy('export')
    try {
      const approved = audit?.advisor_decision === 'approve'
      let payload: Blob
      if (approved) {
        payload = await api.finalReport(pitch.pitch_id)
      } else {
        try {
          payload = await api.reviewReport(pitch.pitch_id)
        } catch (reason) {
          // A review document can still be rendered from the exact audit being
          // displayed if a local development server was restarted mid-review.
          if (!audit) throw reason
          payload = await api.exportAudit(audit)
        }
      }
      downloadBlob(payload, downloadName(pitch.company_profile.company_name, approved ? 'approved-audit-report' : 'audit-review', pitch.pitch_version, 'pdf'))
      setNotice(approved ? 'Your matching verification PDF is downloading.' : 'Your audit review PDF is downloading.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'PDF export failed.') } finally { setBusy(null) }
  }
  async function decide(decision: 'approve' | 'reject') {
    if (!pitch?.pitch_id) return
    setBusy('decision'); setError(null)
    try {
      if (decision === 'approve') {
        const result = await api.approvePitch(pitch.pitch_id, advisorName)
        const state = await api.workflowState(pitch.pitch_id)
        setAudit(state.audit || audit); setPitch(state.pitch); setShowAuditScreen(true); setNotice(result.message)
      } else {
        const state = await api.rejectRegenerate(pitch.pitch_id, advisorName, rejectionFeedback)
        setPitch(state.pitch); setAudit(state.audit || audit); setShowAuditScreen(true); setRejectionFeedback(''); setNotice('Feedback was recorded. A new pitch version was generated and audited.')
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Decision failed.') } finally { setBusy(null) }
  }

  if (!started) return <div className="journey-shell landing-shell">
    <header className="journey-header"><button className="journey-brand brand-home" type="button"><span>m</span><div><strong>Marsh Policy Intelligence</strong><small>Evidence-led medical policy advisory</small></div></button><div className="journey-state"><i /> Secure advisor workspace</div></header>
    <section className="journey-hero landing-hero"><div className="landing-copy"><span className="journey-eyebrow">Marsh · Intern case study</span><h1>Turn policy evidence into advice your client can trust.</h1><p>Research the company, confirm AI-suggested requirements, compare the four supplied medical policies, verify every client-facing claim, and leave with a detailed PowerPoint and readable audit report.</p><div className="landing-actions"><button onClick={() => setStarted(true)} className="journey-primary landing-cta" type="button">Start advisory review <ArrowRight size={18} /></button><span><ShieldCheck size={15}/> Evidence-controlled recommendations</span></div></div><div className="landing-visual" aria-label="Advisory workflow preview"><div className="landing-orbit orbit-one"/><div className="landing-orbit orbit-two"/><div className="landing-flow-card flow-company"><FileSearch size={20}/><small>01 · Research</small><strong>Understand the client</strong><span>Public facts + advisor confirmation</span></div><div className="landing-flow-card flow-evidence"><FileText size={20}/><small>02 · Compare</small><strong>Map policy evidence</strong><span>Benefits, conditions and gaps</span></div><div className="landing-flow-card flow-audit"><ShieldCheck size={20}/><small>03 · Verify</small><strong>Audit every claim</strong><span>PASS before client release</span></div><div className="landing-live"><i/><span>Workflow ready</span><b>4 policies</b></div></div></section>
    <div className="landing-proof-row"><span>Company research</span><ArrowRight size={14}/><span>Requirements confirmation</span><ArrowRight size={14}/><span>Policy comparison</span><ArrowRight size={14}/><span>Claim audit</span><ArrowRight size={14}/><span>PPTX + PDF</span></div>
  </div>

  if (showAuditScreen && audit && pitch) return <div className="journey-shell"><header className="journey-header"><button className="journey-brand brand-home" onClick={() => setStarted(false)} type="button" aria-label="Return to landing page"><span>m</span><div><strong>Marsh Policy Intelligence</strong><small>Evidence-led medical policy advisory</small></div></button><div className="journey-state"><i /> Secure advisor workspace</div></header><nav className="journey-nav" aria-label="Advisory workflow">{steps.map((step, index) => <button key={step} onClick={() => { setShowAuditScreen(false); window.setTimeout(() => advance(['company-search','verify-profile','requirements','analysis','recommendation','pitch','approval'][index]), 0) }} type="button"><b>{String(index + 1).padStart(2, '0')}</b>{step}</button>)}</nav>{error ? <div className="journey-alert error"><AlertTriangle size={17}/>{error}<button onClick={() => setError(null)} type="button">Dismiss</button></div> : null}{notice ? <div className="journey-alert success"><CheckCircle2 size={17}/>{notice}<button onClick={() => setNotice(null)} type="button">Dismiss</button></div> : null}<main className="journey-content audit-standalone"><AuditPanel key={pitch.pitch_version} report={audit} onBack={() => setShowAuditScreen(false)} onApprove={() => decide('approve')} onReject={() => decide('reject')} rejectionFeedback={rejectionFeedback} onRejectFeedbackChange={setRejectionFeedback} onApplyFix={applyFix} onEditClaim={editClaim} onReaudit={runAudit} onDownloadPitch={exportDeck} onDownloadReport={exportReport} advisorName={advisorName} onAdvisorNameChange={setAdvisorName} busy={Boolean(busy)} /></main></div>

  return <div className="journey-shell">
    <header className="journey-header"><button className="journey-brand brand-home" onClick={() => setStarted(false)} type="button" aria-label="Return to landing page"><span>m</span><div><strong>Marsh Policy Intelligence</strong><small>Evidence-led medical policy advisory</small></div></button><div className="journey-state"><i /> Secure advisor workspace</div></header>
    <nav className="journey-nav" aria-label="Advisory workflow">{steps.map((step, index) => <button key={step} onClick={() => navigateStep(index)} type="button"><b>{String(index + 1).padStart(2, '0')}</b>{step}</button>)}</nav>
    <datalist id="company-suggestions">{companySuggestions.map((item) => <option key={item} value={item} />)}</datalist>
    {error ? <div className="journey-alert error"><AlertTriangle size={17}/>{error}<button onClick={() => setError(null)} type="button">Dismiss</button></div> : null}{notice ? <div className="journey-alert success"><CheckCircle2 size={17}/>{notice}<button onClick={() => setNotice(null)} type="button">Dismiss</button></div> : null}
    {profile && confirmationOpen && confirmationTarget ? createPortal(<div className="inline-confirmation" role="group" aria-labelledby="confirmation-title"><div><CheckCircle2 size={20}/><strong id="confirmation-title">Check whether the selected information is correct.</strong></div><div className="confirmation-actions"><button className="journey-secondary" onClick={() => { setDetailsConfirmed(false); setConfirmationOpen(false) }} type="button">Continue editing</button><button className="journey-primary" onClick={() => { setDetailsConfirmed(true); setConfirmationOpen(false) }} type="button"><CheckCircle2 size={17}/>Information is correct</button></div></div>, confirmationTarget) : null}
    {recommendationChatTarget ? createPortal(<RequirementChat message={requirementMessage} onMessageChange={setRequirementMessage} response={requirementReply} requirements={structuredRequirements} busy={busy === 'requirements'} onSend={addRequirementFromChat} onToggle={(requirementId) => setStructuredRequirements((current) => current.map((item) => item.requirement_id === requirementId ? { ...item, confirmed: !item.confirmed } : item))} />, recommendationChatTarget) : null}

    <main className="journey-content">
      <section id="company-search" className="journey-section"><span className="journey-eyebrow">01 · Company search</span><div className="journey-section-grid"><div><h2>Start with the client company</h2><p>Research uses a public company source. Any uncertain information remains marked for advisor confirmation.</p><label>Company name<div className="company-autocomplete"><input value={company} onFocus={() => setCompanyInputFocused(true)} onBlur={() => window.setTimeout(() => setCompanyInputFocused(false), 120)} onChange={(event) => setCompany(event.target.value)} placeholder="e.g. Your client company" />{companyInputFocused && company.trim() ? <div className="company-suggestion-menu" role="listbox">{companySuggestions.map((name) => <button key={name} type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => { setCompany(name); setCompanyInputFocused(false) }}>{name}</button>)}{!companySuggestions.some((name) => name.toLowerCase() === company.trim().toLowerCase()) ? <button className="use-typed-company" type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => setCompanyInputFocused(false)}>Use “{company.trim()}” and research it</button> : null}</div> : null}</div></label><button className="journey-primary" disabled={busy === 'research'} onClick={research} type="button">{busy === 'research' ? <><LoaderCircle className="spin" size={17}/> Researching</> : <><FileSearch size={17}/> Research company</>}</button></div><div className="journey-side-card"><Sparkles size={24}/><strong>Grounded research</strong><p>Verified facts show their public source. Assumptions are never silently treated as facts.</p></div></div></section>
      <section id="verify-profile" className="journey-section"><span className="journey-eyebrow">02 · Profile verification</span><h2>Confirm the profile before comparing policies</h2>{profile ? <div className="fact-grid">{profile.facts.map((fact) => <label key={fact.name}><span>{fact.name} <em className={fact.status}>{fact.status === 'verified' ? 'Verified' : 'Review required'}</em></span><textarea value={fact.value} onChange={(event) => { const typed = event.target.value; const nextValue = advisorConfirmationPrompt(fact.value) && typed.startsWith(fact.value) ? typed.slice(fact.value.length) : typed; updateFact(fact.name, nextValue) }} />{fact.source_url ? <small>Source: {fact.source_url}</small> : null}</label>)}<div className="missing-card profile-guidance-card"><div className="profile-guidance-heading"><AlertTriangle size={18}/><div><strong>Complete client profile</strong><p>Confirm the details below to finish this step. Answers feed directly into policy comparison.</p></div></div>{discoveryQuestions.map((question, index) => <label key={question.id}><span><b>{String(index + 1).padStart(2, '0')}</b>{question.question}</span><input value={question.profileField === 'Company size' ? employees : question.profileField === 'Operating locations' ? locations : discoveryAnswers[question.id] || ''} onChange={(event) => question.profileField ? answerConfirmation(question.profileField, event.target.value) : setDiscoveryAnswers((current) => ({ ...current, [question.id]: event.target.value }))} onBlur={() => saveDiscoveryAnswer(question)} placeholder={question.placeholder} /></label>)}</div></div> : <div className="journey-empty">Search for a company to begin profile verification.</div>}</section>
      <section id="requirements" className="journey-section">
        <span className="journey-eyebrow">03 · Client requirements</span>
        <h2>Capture the decision criteria that matter to this client</h2>
        <div className="requirements-layout">
          <div className="form-grid requirements-panel">
            <div className="requirements-panel-heading"><strong>Client profile & budget</strong><span>Confirm the workforce context before comparing policies.</span></div>
            <label>Industry<input value={industry} onChange={(event) => setIndustry(event.target.value)} placeholder="Confirm industry" /></label>
            <label>Employee count<input value={employees} onChange={(event) => setEmployees(event.target.value)} placeholder="Confirm employee count" /></label>
            <label>Operating locations<input value={locations} onChange={(event) => setLocations(event.target.value)} placeholder="Mumbai, Bengaluru, ..." /></label>
            <label>Workforce profile<input value={workforce} onChange={(event) => setWorkforce(event.target.value)} placeholder="e.g. distributed, field-based" /></label>
            <label className="wide">Budget guidance (optional)<input value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="Advisor guidance only" /></label>
            <div className="left-priority-panel"><strong>Additional coverage priorities</strong><span>Choose any relevant benefits.</span><div className="priority-grid">{priorities.slice(14).map((item) => <button key={item} className={selectedPriorities.includes(item) ? 'selected' : ''} onClick={() => setSelectedPriorities((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item])} type="button"><Target size={15}/>{item}</button>)}</div></div>
          </div>
          <div className="priority-panel requirements-panel">
            <div className="requirements-panel-heading"><strong>Ranked coverage priorities</strong><span>Select every core benefit that matters to this client.</span></div>
            <div className="priority-grid">{priorities.slice(0, 14).map((item) => <button key={item} className={selectedPriorities.includes(item) ? 'selected' : ''} onClick={() => setSelectedPriorities((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item])} type="button"><Target size={15}/>{item}</button>)}</div>
            <div className="priority-selection-summary"><div><strong>{selectedPriorities.length}</strong><span>benefits selected</span></div><small>Select or remove options at any time before analysis.</small></div>
          </div>
        </div>
        <div className="baseline-control"><button className="journey-secondary" onClick={() => setShowBaselineDocuments((current) => !current)} type="button"><FileText size={16}/>{showBaselineDocuments ? 'Hide baseline documents' : `Show baseline documents (${selected.length})`}</button>{showBaselineDocuments ? <div className="policy-pick baseline-list">{policies.map((item) => <label key={item.policy_id}><input checked={selected.includes(item.policy_id)} onChange={() => setSelected((current) => current.includes(item.policy_id) ? current.filter((id) => id !== item.policy_id) : [...current, item.policy_id])} type="checkbox" />{item.product_name}<small>{item.insurer}</small></label>)}</div> : null}</div>
        <button className="journey-primary" disabled={!profile || !selectedPriorities.length || busy === 'analysis'} onClick={analyze} type="button">{busy === 'analysis' ? <><LoaderCircle className="spin" size={17}/> Analysing evidence</> : <>Analyse selected policies <ArrowRight size={17}/></>}</button>
      </section>
      <section id="analysis" className="journey-section"><span className="journey-eyebrow">04 · Insurance analysis</span><h2>Evidence-led comparison</h2>{analysis ? <div className="score-grid">{analysis.policy_scores?.map((item: any, index: number) => <article key={item.policy_id} className={index === 0 ? 'score-card top' : 'score-card'}><span>{index === 0 ? 'Leading fit' : `Rank ${index + 1}`}</span><strong>{item.policy_name}</strong><b>{item.score} pts</b><p>{item.evidence_count} matched requirement facts from supplied evidence</p></article>)}</div> : <div className="journey-empty">Run the comparison after confirming client priorities.</div>}</section>
      <section id="recommendation" className="journey-section"><span className="journey-eyebrow">05 · Recommendation and coverage gaps</span><h2>What the evidence supports — and what still needs attention</h2>{analysis ? <div className="recommendation-layout"><article className="recommendation-card"><CheckCircle2 size={22}/><span>Requirements-weighted recommendation</span><strong>{analysis.policy_scores?.find((item: any) => item.policy_id === analysis.recommended_policy_id)?.policy_name || 'Not established'}</strong><p>The recommendation is limited to selected preloaded policies and uses the same requirements for every comparison.</p><button className="journey-primary" disabled={busy === 'pitch'} onClick={generatePitch} type="button">{busy === 'pitch' ? <><LoaderCircle className="spin" size={17}/> Generating</> : <>Generate detailed five-slide pitch <FileText size={17}/></>}</button></article><article className="gap-card"><h3>Coverage gaps and conflicts</h3>{[...(analysis.coverage_gaps || []), ...(analysis.requirement_conflicts || [])].slice(0, 8).map((item: any, index: number) => <div key={index}><b>{item.benefit_name || item.requirement}</b><span>{item.status || item.severity}</span><p>{item.explanation}</p></div>)}{!(analysis.coverage_gaps || []).length ? <p>No gaps are available until evidence is analysed.</p> : null}</article></div> : <div className="journey-empty">This section will show only after the policy comparison is complete.</div>}</section>
      <section id="pitch" className="journey-section"><span className="journey-eyebrow">06 · Pitch verification</span><h2>Preview the client presentation before audit</h2>{pitch ? <><div className="journey-action-row"><p>Exactly five slides, each generated from the selected evidence.</p><button className="journey-secondary" onClick={exportDeck} disabled={busy === 'export'} type="button"><Download size={16}/> Download .pptx</button><button className="journey-primary" onClick={runAudit} disabled={busy === 'audit'} type="button"><ShieldCheck size={16}/>{busy === 'audit' ? 'Auditing' : 'Run detailed audit'}</button></div><PitchWorkspace pitch={pitch} activeSlide={activeSlide} onSlideChange={setActiveSlide}/></> : <div className="journey-empty">Generate the presentation after reviewing the policy recommendation.</div>}</section>
      <section id="audit" className="journey-section"><span className="journey-eyebrow">07 · Detailed audit</span><h2>Simple review before client release</h2>{audit && pitch ? <AuditPanel report={audit} onBack={() => advance('pitch')} onApprove={() => decide('approve')} onReject={() => decide('reject')} rejectionFeedback={rejectionFeedback} onRejectFeedbackChange={setRejectionFeedback} onApplyFix={applyFix} onEditClaim={editClaim} onReaudit={runAudit} onDownloadPitch={exportDeck} onDownloadReport={exportReport} advisorName={advisorName} onAdvisorNameChange={setAdvisorName} busy={Boolean(busy)} /> : <div className="journey-empty">Run the detailed audit to inspect conditions, source excerpts, and any required changes.</div>}</section>
      <section id="approval" className="journey-section approval-section"><UserCheck size={25}/><div><span className="journey-eyebrow">08 · Approval and downloads</span><h2>Release only after a PASS audit</h2><p>PowerPoint and PDF downloads remain available from the audit view. Approval is governed by the current audit result and recorded with the advisor’s name.</p></div></section>
    </main>
  </div>
}
