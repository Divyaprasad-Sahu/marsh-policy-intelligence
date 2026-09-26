import type { AuditReport, MarketingPitch, PitchWorkflowState, PolicyDocument, PolicySummary, StructuredRequirement } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    throw new Error(detail || `Request failed with ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: (signal?: AbortSignal) => request<{ status: string }>('/api/v1/health', { signal }),
  policies: () => request<PolicySummary[]>('/api/v1/policies'),
  companySuggestions: (query: string, signal?: AbortSignal) => request<string[]>(`/api/v1/company-suggestions?q=${encodeURIComponent(query)}`, { signal }),

  researchCompany: (company_name: string) => request<{
    profile: MarketingPitch['company_profile']
    sources: string[]
    missing_field_checklist: string[]
    research_note: string
    suggested_requirements: { industry?: string; employee_count?: string; operating_locations: string[]; workforce_profile?: string; ranked_coverage_priorities: string[]; budget_guidance?: string }
    verification_questions: string[]
  }>('/api/v1/company-research', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ company_name }),
  }),

  analyze: (payload: {
    company_profile: MarketingPitch['company_profile']
    requirements: { industry?: string; employee_count?: string; operating_locations: string[]; workforce_profile?: string; ranked_coverage_priorities: string[]; budget_guidance?: string; structured_requirements?: StructuredRequirement[] }
    policy_ids: string[]
  }) => request<any>('/api/v1/advisory-analysis', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),

  parseDocument: async (file: File, role: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('role', role)
    return request<PolicyDocument>('/api/v1/documents/parse', { method: 'POST', body: form })
  },

  requirementChat: (message: string, existing_requirements: StructuredRequirement[]) => request<{ assistant_message: string; requirements: StructuredRequirement[]; needs_confirmation: boolean }>('/api/v1/requirements/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, existing_requirements }),
  }),

  generate: (payload: {
    company_name: string
    policy_ids: string[]
    client_requirements: string[]
    uploaded_documents: PolicyDocument[]
    requirements_context?: { industry?: string; employee_count?: string; operating_locations: string[]; workforce_profile?: string; ranked_coverage_priorities: string[]; budget_guidance?: string; structured_requirements?: StructuredRequirement[] }
  }) => request<MarketingPitch>('/api/v1/pitches/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),

  audit: (pitch: MarketingPitch, uploadedDocuments: PolicyDocument[]) =>
    request<AuditReport>('/api/v1/pitches/audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pitch, uploaded_documents: uploadedDocuments }),
    }),

  workflowState: (pitchId: string) => request<PitchWorkflowState>(`/api/v1/pitches/${pitchId}/state`),
  editPitch: (pitchId: string, slideNumber: number, claimId: string, newText: string) => request<PitchWorkflowState>(`/api/v1/pitches/${pitchId}/edit`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slide_number: slideNumber, claim_id: claimId, new_text: newText }),
  }),
  applyFix: (pitchId: string, claimId: string) => request<PitchWorkflowState>(`/api/v1/pitches/${pitchId}/apply-fix`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ claim_id: claimId }),
  }),
  reaudit: (pitchId: string) => request<PitchWorkflowState>(`/api/v1/pitches/${pitchId}/reaudit`, { method: 'POST' }),
  approvePitch: (pitchId: string, advisorName: string) => request<{ accepted: boolean; message: string }>(`/api/v1/pitches/${pitchId}/approve?advisor_name=${encodeURIComponent(advisorName)}`, { method: 'POST' }),
  rejectRegenerate: (pitchId: string, advisorName: string, feedback: string) => request<PitchWorkflowState>(`/api/v1/pitches/${pitchId}/reject-regenerate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ advisor_name: advisorName, feedback }),
  }),

  finalPitch: async (pitchId: string) => {
    const response = await fetch(`${API_BASE}/api/v1/pitches/${pitchId}/final.pptx`)
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Approved PowerPoint download failed')
    return response.blob()
  },
  finalReport: async (pitchId: string) => {
    const response = await fetch(`${API_BASE}/api/v1/pitches/${pitchId}/verification.pdf`)
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Approved verification report download failed')
    return response.blob()
  },
  reviewReport: async (pitchId: string) => {
    const response = await fetch(`${API_BASE}/api/v1/pitches/${pitchId}/audit-review.pdf`)
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Audit review report download failed')
    return response.blob()
  },

  exportPitch: async (pitch: MarketingPitch) => {
    const response = await fetch(`${API_BASE}/api/v1/pitches/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pitch),
    })
    if (!response.ok) throw new Error('PowerPoint export failed')
    const blob = await response.blob()
    if (!blob.type.includes('presentationml.presentation')) {
      throw new Error('The server did not return a valid PowerPoint file')
    }
    return blob
  },

  exportAudit: async (report: AuditReport) => {
    const response = await fetch(`${API_BASE}/api/v1/audits/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(report),
    })
    if (!response.ok) throw new Error('Audit PDF export failed')
    const blob = await response.blob()
    if (!blob.type.includes('application/pdf')) {
      throw new Error('The server did not return a valid PDF file')
    }
    return blob
  },

  decide: (auditReport: AuditReport, advisorName: string, decision: 'approve' | 'reject', reason?: string) =>
    request<{ accepted: boolean; message: string }>('/api/v1/audits/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audit_report: auditReport, advisor_name: advisorName, decision, reason }),
    }),
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Keep the blob URL alive long enough for Chromium and the in-app browser
  // to resolve the file after the asynchronous click handler finishes.
  window.setTimeout(() => URL.revokeObjectURL(url), 3_000)
}
