export type DocumentRole = 'client_requirements' | 'reference_policy'

export interface PolicySummary {
  policy_id: string
  filename: string
  insurer: string
  product_name: string
}

export interface StructuredRequirement {
  requirement_id: string
  category: string
  requirement: string
  priority: 'MUST_HAVE' | 'HIGH' | 'MEDIUM' | 'LOW'
  source: 'advisor_chat' | 'advisor_form' | 'company_profile' | 'ai_inference'
  confirmed: boolean
}

export interface SourceReference {
  document_id: string
  document_name: string
  page_number?: number
  section?: string
  excerpt: string
}

export interface PolicyDocument {
  document_id: string
  document_name: string
  role: string
  insurer?: string
  product_name?: string
  file_hash?: string
  chunks: Array<{ chunk_id: string; page_number?: number; section?: string; text: string }>
}

export interface ChartDefinition {
  chart_type: string
  title: string
  categories: string[]
  series: Record<string, number[]>
  displayed_values: string[]
}

export interface ComparisonTable {
  title: string
  columns: string[]
  rows: string[][]
}

export interface PitchSlide {
  slide_number: number
  title: string
  key_message: string
  content_blocks: Array<{ kind: string; text?: string; items: string[] }>
  charts: ChartDefinition[]
  comparison_tables: ComparisonTable[]
  claims: Array<Record<string, unknown>>
  visible_citations: SourceReference[]
}

export interface MarketingPitch {
  pitch_id: string
  pitch_version: number
  pitch_hash: string
  company_profile: {
    company_name: string
    facts: Array<{ name: string; value: string; status: 'verified' | 'assumption'; source_url?: string }>
    inferred_risks: string[]
  }
  pitch_slides: PitchSlide[]
  recommended_policy_id?: string
  generation_warnings: string[]
}

export interface ClaimResult {
  claim_id: string
  slide_number: number
  policy_id: string
  benefit_name: string
  presented_value: string
  status: string
  severity: string
  explanation: string
  suggested_rewrite?: string
  source_references: SourceReference[]
  conditions?: string[]
  omitted_conditions?: string[]
  verified_limit?: string
  is_optional?: boolean
}

export interface AuditReport {
  audit_id: string
  status: 'PASS' | 'REVIEW_REQUIRED' | 'FAIL' | 'AUDIT_INCOMPLETE'
  total_claims: number
  supported_claims: number
  partially_supported_claims: number
  unsupported_claims: number
  contradicted_claims: number
  unverifiable_claims: number
  critical_issues: number
  claim_results: ClaimResult[]
  summary: string
  pitch_id?: string
  audited_pitch_version?: number
  audited_pitch_hash?: string
  advisor_decision?: 'approve' | 'reject'
  resolved_changes?: Array<{ claim_id: string; benefit_name: string; previous_status: string; state: 'RESOLVED_AWAITING_REAUDIT' }>
}

export interface PitchWorkflowState {
  pitch: MarketingPitch
  audit?: AuditReport
  approval_enabled: boolean
  approved: boolean
  advisor_decision?: 'approve' | 'reject'
}
