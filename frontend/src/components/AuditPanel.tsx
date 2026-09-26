import { AlertTriangle, ArrowLeft, CheckCircle2, Download, FileText, Gauge, RefreshCw, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { AuditReport } from '../types'

interface Props {
  report: AuditReport
  onBack: () => void
  onApprove: () => void
  onReject: () => void
  onRejectFeedbackChange?: (value: string) => void
  rejectionFeedback?: string
  onReaudit: () => void
  onApplyFix?: (claimId: string) => void
  onEditClaim?: (slideNumber: number, claimId: string, text: string) => void
  onDownloadPitch: () => void
  onDownloadReport: () => void
  advisorName: string
  onAdvisorNameChange: (value: string) => void
  busy: boolean
}

export function AuditPanel({ report, onBack, onApprove, onReject, onRejectFeedbackChange = () => {}, rejectionFeedback = '', onReaudit, onApplyFix = () => {}, onEditClaim = () => {}, onDownloadPitch, onDownloadReport, advisorName, onAdvisorNameChange, busy }: Props) {
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null)
  const [editingClaimId, setEditingClaimId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  useEffect(() => {
    setApprovalMessage(null)
    setEditingClaimId(null)
    setDrafts({})
  }, [report.audit_id, report.audited_pitch_version, report.status])
  const passed = report.status === 'PASS'
  const approved = passed && report.advisor_decision === 'approve'
  const incomplete = report.status === 'AUDIT_INCOMPLETE'
  const verified = new Set(['VERIFIED', 'VERIFIED_WITH_QUALIFICATION'])
  const findings = report.claim_results.filter((item) => !verified.has(item.status))
  const approvalReady = passed && findings.length === 0 && Boolean(report.audited_pitch_hash) && Boolean(advisorName.trim())
  const qualified = report.claim_results.filter((item) => item.status === 'VERIFIED_WITH_QUALIFICATION')
  const statusLabel = (status: string) => status === 'VERIFIED_WITH_QUALIFICATION' ? 'Supported with conditions' : status.replaceAll('_', ' ')
  const citationList = (finding: AuditReport['claim_results'][number]) => {
    // Keep the audit focused on the policy represented by this slide. A single,
    // directly matched citation is clearer than every document that happened to
    // contain similar wording.
    const directCitation = finding.source_references.find((source) => source.document_id === finding.policy_id)
    return directCitation ? [directCitation] : finding.source_references.slice(0, 1)
  }
  const benefitLabel = (finding: AuditReport['claim_results'][number]) => {
    const hasDomiciliaryEvidence = citationList(finding).some((source) => /domiciliary hospitali[sz]ation/i.test(source.excerpt))
    return finding.benefit_name === 'Home care' && hasDomiciliaryEvidence
      ? 'Home care (policy term: Domiciliary hospitalisation)'
      : finding.benefit_name
  }

  return (
    <main className="audit-page">
      <button className="text-button" onClick={onBack} type="button"><ArrowLeft size={16} /> Back to pitch</button>

      <section className={`audit-hero ${passed ? 'pass' : incomplete ? 'incomplete' : 'review'}`}>
        <div className="audit-hero-icon">{passed ? <CheckCircle2 size={34} /> : <ShieldAlert size={34} />}</div>
        <div className="audit-hero-copy">
          <span className="eyebrow">Evidence review</span>
          <h1>{passed ? 'All audited pitch claims matched the supplied evidence' : incomplete ? 'Pitch updated — fresh audit required' : 'The pitch needs advisor review'}</h1>
          <p>{report.summary}</p>
          <p className="audit-explainer">A PASS means every claim passed three checks: selected policy, benefit-relevant clause, and wording with material limits and conditions. It is not a guarantee that the brochures are complete, current, or free from ambiguity.</p>
        </div>
        <div className="audit-score"><strong>{report.supported_claims}/{report.total_claims}</strong><span>audited claims supported</span></div>
      </section>

      <section className="audit-stat-row" aria-label="Audit summary">
        <div><Gauge size={18} /><strong>{report.total_claims}</strong><span>Claims checked</span></div>
        <div><CheckCircle2 size={18} /><strong>{report.supported_claims}</strong><span>Supported by supplied documents</span></div>
        <div><AlertTriangle size={18} /><strong>{findings.length}</strong><span>Need attention</span></div>
        <div><ShieldAlert size={18} /><strong>{report.critical_issues}</strong><span>Critical issues</span></div>
      </section>

      <div className="audit-layout">
        <section className="audit-findings-card">
          <div className="section-heading-row">
            <div><span className="eyebrow">What needs attention</span><h2>{incomplete ? 'Run a fresh audit' : findings.length ? `${findings.length} finding${findings.length === 1 ? '' : 's'} to review` : 'No unresolved findings'}</h2></div>
            <span className="support-rate">{incomplete ? 'Prior audit invalidated' : `${report.supported_claims} of ${report.total_claims} audited claims supported`}</span>
          </div>
          {report.resolved_changes?.length ? <details className="resolved-history"><summary>Resolved changes ({report.resolved_changes.length})</summary>{report.resolved_changes.map((item) => <p key={item.claim_id}><strong>{item.benefit_name}</strong> — RESOLVED — AWAITING RE-AUDIT</p>)}</details> : null}
          {!incomplete && qualified.length ? <section className="qualified-claims" aria-label="Evidence-backed conditions">{qualified.map((item) => { const source = citationList(item)[0]; return <article key={item.claim_id}><CheckCircle2 size={17}/><div><strong>{benefitLabel(item)} - supported with conditions</strong><small>Slide {item.slide_number} · {item.policy_id}</small><p><b>Claim:</b> {item.presented_value}</p><p>{item.explanation}</p><p><b>Policy conditions:</b> {(item.omitted_conditions?.length ? item.omitted_conditions : item.conditions)?.join('; ') || 'See the cited policy clause.'}</p>{source ? <p className="verified-citation"><FileText size={14}/>{source.document_name} · {source.page_number ? `page ${source.page_number}` : source.section || 'matched evidence'}</p> : null}</div></article> })}</section> : null}
          {incomplete ? <div className="audit-empty"><RefreshCw size={28} /><p>This pitch has changed. The findings and approval count shown by the previous audit were cleared. Run the full audit again to calculate the current result.</p></div> : findings.length ? (
            <div className="simple-findings">
              {findings.map((finding) => (
                <article className="simple-finding" key={finding.claim_id}>
                  <div className={`finding-status ${finding.status.toLowerCase()}`}>{statusLabel(finding.status)}</div>
                  <div>
                    <h3>{benefitLabel(finding)}</h3>
                    <p className="finding-location">Slide {finding.slide_number} · {finding.policy_id || 'Policy not established'}</p>
                    <strong>Current statement</strong><p>{finding.presented_value}</p>
                    <strong>Why it needs review</strong><p>{finding.explanation}</p>
                    {finding.omitted_conditions?.length ? <p><strong>Missing qualification:</strong> {finding.omitted_conditions.join('; ')}</p> : null}
                    {finding.suggested_rewrite ? <div className="guidance-box"><strong>Suggested wording</strong><p>{finding.suggested_rewrite}</p><div className="decision-row"><button className="secondary-button" title="Replace this clause with the evidence-backed wording shown above." disabled={busy} onClick={() => onApplyFix(finding.claim_id)} type="button">{busy ? 'Applying…' : 'Apply source-safe wording'}</button><button className="secondary-button" title="Open this clause so you can write and save your own wording." disabled={busy} onClick={() => setEditingClaimId(editingClaimId === finding.claim_id ? null : finding.claim_id)} type="button">{editingClaimId === finding.claim_id ? 'Cancel edit' : 'Edit clause'}</button></div>{editingClaimId === finding.claim_id ? <div className="inline-clause-editor"><label htmlFor={`clause-${finding.claim_id}`}>Edit client-facing clause</label><textarea id={`clause-${finding.claim_id}`} value={drafts[finding.claim_id] ?? finding.suggested_rewrite} onChange={(event) => setDrafts((current) => ({ ...current, [finding.claim_id]: event.target.value }))} /><p>Saving creates a new pitch version. Run the full audit again before approval.</p><button className="primary-button" disabled={busy || !(drafts[finding.claim_id] ?? finding.suggested_rewrite).trim()} onClick={() => onEditClaim(finding.slide_number, finding.claim_id, (drafts[finding.claim_id] ?? finding.suggested_rewrite).trim())} type="button">{busy ? 'Saving…' : 'Save edited clause'}</button></div> : null}</div> : null}
                    {finding.source_references.length ? (
                      <div className="evidence-row"><FileText size={15} /><strong>Policy evidence:</strong>{citationList(finding).map((source) => <span key={`${source.document_id}-${source.page_number ?? source.section}`}>{source.document_name} · {source.page_number ? `page ${source.page_number}` : source.section || 'location not established'}</span>)}</div>
                    ) : <div className="evidence-row warning"><AlertTriangle size={15} /> Evidence location not established</div>}
                  </div>
                </article>
              ))}
            </div>
          ) : <><div className="audit-empty"><CheckCircle2 size={28} /><p>Every claim extracted from this pitch matched evidence in the supplied policy documents. Advisor review is still required.</p></div><div className="audit-readiness"><span>Verification completed</span><div><b>{report.total_claims} claims checked</b><b>{report.supported_claims} claims supported</b><b>5 pitch slides reviewed</b></div><p>Read the audit PDF for the supporting excerpts, conditions, and page references before approval.</p></div><section className="verified-claims"><div className="verified-claims-heading"><div><span className="eyebrow">Verified claims</span><h3>Why each claim passed</h3></div><span>{report.supported_claims} supported</span></div>{report.claim_results.map((finding) => { const source = citationList(finding)[0]; return <article key={finding.claim_id}><CheckCircle2 size={17}/><div><strong>{finding.benefit_name}</strong><small>Slide {finding.slide_number} · {finding.policy_id}</small><p><b>Claim:</b> {finding.presented_value}</p><p><b>Why it passed:</b> {finding.explanation}</p>{source ? <p className="verified-citation"><FileText size={14}/>{source.document_name} · {source.page_number ? `page ${source.page_number}` : source.section || 'matched evidence'}</p> : null}</div></article> })}</section></>}
        </section>

        <aside className="audit-action-card">
          <span className="eyebrow">Downloads</span><h2>{approved ? 'Audit-cleared exports' : 'Audit review report'}</h2><p>{approved ? 'Download the presentation together with its matching verification report.' : 'Download the evidence report now. It is for advisor review and is not client-ready until approval.'}</p>
          <button className="primary-button wide" disabled={busy || !approved} onClick={onDownloadPitch} type="button"><Download size={17} /> Download approved PowerPoint</button>
          <button className="secondary-button wide" disabled={busy} onClick={onDownloadReport} type="button"><FileText size={17} /> {approved ? 'Download matching verification PDF' : 'Download audit review PDF'}</button>
          <div className="decision-divider" />
          <span className="eyebrow">Advisor decision</span>
          <label className="field-label" htmlFor="advisor-name">Advisor name</label>
          <input id="advisor-name" value={advisorName} onChange={(event) => onAdvisorNameChange(event.target.value)} placeholder="Enter advisor name" />
          <label className="field-label" htmlFor="rejection-feedback">Rejection feedback</label>
          <textarea id="rejection-feedback" value={rejectionFeedback} onChange={(event) => onRejectFeedbackChange(event.target.value)} placeholder="Explain what the regenerated pitch must change" />
          {!passed ? <p className="approval-blocked"><ShieldAlert size={16} /> {incomplete ? 'The pitch has changed. Run the full audit again before approval.' : `This audit has ${findings.length} finding${findings.length === 1 ? '' : 's'}. Click Approve to see what must be resolved first.`}</p> : null}
          {approvalMessage ? <p className="approval-click-message"><AlertTriangle size={16} />{approvalMessage}</p> : null}
          <button className={incomplete ? 'primary-button wide' : 'secondary-button wide'} disabled={busy} onClick={onReaudit} type="button"><RefreshCw className={busy ? 'spin' : undefined} size={16} /> {busy ? 'Checking all audit layers…' : 'Run full audit again'}</button>
          <div className="decision-row"><button className="secondary-button danger" disabled={busy || !advisorName.trim() || rejectionFeedback.trim().length < 3} onClick={onReject} type="button">Reject & regenerate</button><button className="primary-button" disabled={busy || !approvalReady} onClick={() => approvalReady ? onApprove() : setApprovalMessage(incomplete ? 'Approval is blocked until the revised pitch has been re-audited.' : `Approval is blocked because ${findings.length} policy claim${findings.length === 1 ? '' : 's'} still require review. Resolve the findings and run the audit again.`)} type="button">Approve exact version</button></div>
        </aside>
      </div>
    </main>
  )
}
