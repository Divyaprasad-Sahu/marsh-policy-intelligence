from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentRole(str, Enum):
    PRELOADED_POLICY = "preloaded_policy"
    REFERENCE_POLICY = "reference_policy"
    CLIENT_REQUIREMENTS = "client_requirements"


class AuditStatus(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"
    AUDIT_INCOMPLETE = "AUDIT_INCOMPLETE"


class ClaimStatus(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_QUALIFICATION = "VERIFIED_WITH_QUALIFICATION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class SourceReference(BaseModel):
    document_id: str
    document_name: str
    page_number: int | None = None
    section: str | None = None
    excerpt: str


class DocumentChunk(BaseModel):
    chunk_id: str
    page_number: int | None = None
    section: str | None = None
    text: str


class PolicyDocument(BaseModel):
    document_id: str
    document_name: str
    role: DocumentRole
    insurer: str | None = None
    product_name: str | None = None
    file_hash: str | None = None
    chunks: list[DocumentChunk] = Field(default_factory=list)


class CompanyFact(BaseModel):
    name: str
    value: str
    status: Literal["verified", "assumption"]
    source_url: str | None = None


class CompanyProfile(BaseModel):
    company_name: str
    facts: list[CompanyFact] = Field(default_factory=list)
    inferred_risks: list[str] = Field(default_factory=list)


class CompanyResearchRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)


class CompanyResearchResult(BaseModel):
    profile: CompanyProfile
    sources: list[str] = Field(default_factory=list)
    missing_field_checklist: list[str] = Field(default_factory=list)
    research_note: str = "Confirm all assumptions before analysis."
    suggested_requirements: "ClientRequirements" = Field(default_factory=lambda: ClientRequirements())
    verification_questions: list[str] = Field(default_factory=list)


class ClientRequirements(BaseModel):
    industry: str | None = None
    employee_count: str | None = None
    operating_locations: list[str] = Field(default_factory=list)
    workforce_profile: str | None = None
    ranked_coverage_priorities: list[str] = Field(default_factory=list)
    budget_guidance: str | None = None
    structured_requirements: list["StructuredRequirement"] = Field(default_factory=list)


class StructuredRequirement(BaseModel):
    requirement_id: str
    category: str
    requirement: str
    priority: Literal["MUST_HAVE", "HIGH", "MEDIUM", "LOW"]
    source: Literal["advisor_chat", "advisor_form", "company_profile", "ai_inference"]
    confirmed: bool = False


class RequirementChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    existing_requirements: list[StructuredRequirement] = Field(default_factory=list)


class RequirementChatResponse(BaseModel):
    assistant_message: str
    requirements: list[StructuredRequirement]
    needs_confirmation: bool = True
    semantic_processing_available: bool = True


class PolicyEvidence(BaseModel):
    policy_id: str
    policy_name: str
    benefit_name: str
    presented_value: str
    status: Literal["SUPPORTED", "NOT_ESTABLISHED"]
    conditions: list[str] = Field(default_factory=list)
    is_optional: bool | None = None
    source_references: list[SourceReference] = Field(default_factory=list)


class PolicyFact(BaseModel):
    fact_id: str
    policy_id: str
    policy_name: str
    benefit: str
    fact_type: Literal[
        "coverage", "coverage_limit", "waiting_period", "condition",
        "exclusion", "eligibility", "co_payment", "deductible",
        "optional_benefit", "additional_premium",
    ] = "coverage"
    display_value: str
    numeric_value: float | None = None
    unit: str | None = None
    conditions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    optional: bool = False
    additional_premium: bool = False
    source_reference: SourceReference


class PolicyScoreBreakdown(BaseModel):
    policy_id: str
    policy_name: str
    score: int
    evidence_count: int
    recommendation_reasons: list[str] = Field(default_factory=list)


class CoverageGap(BaseModel):
    benefit_name: str
    status: Literal[
        "COVERED", "CONDITIONAL", "NOT_ESTABLISHED",
        "FULLY_ADDRESSED", "PARTIALLY_ADDRESSED", "NOT_ADDRESSED", "INSUFFICIENT_EVIDENCE",
    ]
    explanation: str
    policy_ids: list[str] = Field(default_factory=list)


class RequirementConflict(BaseModel):
    requirement: str
    explanation: str
    severity: Literal["MAJOR", "MINOR"] = "MINOR"


class AdvisoryAnalysisRequest(BaseModel):
    company_profile: CompanyProfile
    requirements: ClientRequirements = Field(default_factory=ClientRequirements)
    policy_ids: list[str] = Field(min_length=1)
    uploaded_documents: list[PolicyDocument] = Field(default_factory=list)


class AdvisoryAnalysis(BaseModel):
    company_profile: CompanyProfile
    requirements: ClientRequirements
    policy_evidence: list[PolicyEvidence]
    policy_scores: list[PolicyScoreBreakdown]
    recommended_policy_id: str
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    requirement_conflicts: list[RequirementConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    policy_facts: list[PolicyFact] = Field(default_factory=list)


class GeneratePitchRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    policy_ids: list[str] = Field(min_length=1)
    client_requirements: list[str] = Field(default_factory=list)
    uploaded_documents: list[PolicyDocument] = Field(default_factory=list)
    verified_company_profile: CompanyProfile | None = None
    requirements_context: ClientRequirements | None = None


class ContentBlock(BaseModel):
    kind: Literal["text", "bullets", "callout"] = "text"
    text: str | None = None
    items: list[str] = Field(default_factory=list)


class ChartDefinition(BaseModel):
    chart_type: str
    title: str
    categories: list[str] = Field(default_factory=list)
    series: dict[str, list[float]] = Field(default_factory=dict)
    displayed_values: list[str] = Field(default_factory=list)
    calculation_inputs: dict[str, Any] = Field(default_factory=dict)


class ComparisonTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str]]

    @model_validator(mode="after")
    def rows_match_columns(self) -> "ComparisonTable":
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every comparison-table row must match the column count")
        return self


class PitchClaim(BaseModel):
    claim_id: str
    text: str
    policy_id: str | None = None
    benefit_name: str | None = None
    presented_value: str | None = None
    is_optional: bool | None = None
    policy_variant: str | None = None
    conditions_disclosed: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)


class PitchSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_number: int = Field(ge=1, le=5)
    title: str
    key_message: str
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    charts: list[ChartDefinition] = Field(default_factory=list)
    comparison_tables: list[ComparisonTable] = Field(default_factory=list)
    claims: list[PitchClaim] = Field(default_factory=list)
    visible_citations: list[SourceReference] = Field(default_factory=list)


class MarketingPitch(BaseModel):
    pitch_id: str = Field(default_factory=lambda: "")
    pitch_version: int = 1
    pitch_hash: str = ""
    company_profile: CompanyProfile
    pitch_slides: list[PitchSlide]
    recommended_policy_id: str | None = None
    generation_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_five_slides(self) -> "MarketingPitch":
        if len(self.pitch_slides) != 5:
            raise ValueError("A marketing pitch must contain exactly five slides")
        expected = [1, 2, 3, 4, 5]
        if sorted(slide.slide_number for slide in self.pitch_slides) != expected:
            raise ValueError("Slide numbers must be exactly 1 through 5")
        return self


class NumericCheck(BaseModel):
    presented: str
    evidence_values: list[str] = Field(default_factory=list)
    matches: bool


class BenefitAuditResult(BaseModel):
    claim_id: str
    slide_number: int
    policy_id: str
    benefit_name: str
    presented_value: str
    status: ClaimStatus
    severity: Literal["CRITICAL", "MAJOR", "MINOR"]
    source_references: list[SourceReference] = Field(default_factory=list)
    verified_limit: str | None = None
    conditions: list[str] = Field(default_factory=list)
    omitted_conditions: list[str] = Field(default_factory=list)
    is_optional: bool | None = None
    policy_variant: str | None = None
    numeric_checks: list[NumericCheck] = Field(default_factory=list)
    explanation: str
    suggested_rewrite: str | None = None
    fact_type: Literal["POLICY_FACT", "COMPANY_FACT", "AI_INTERPRETATION", "ADVISOR_INPUT"] = "POLICY_FACT"


class ChartAuditResult(BaseModel):
    slide_number: int
    chart_title: str
    valid: bool
    explanation: str


class SlideAuditResult(BaseModel):
    slide_number: int
    status: ClaimStatus
    claim_ids: list[str] = Field(default_factory=list)


class RecommendationAudit(BaseModel):
    recommended_policy_id: str | None
    eligible: bool
    supported: bool
    status: ClaimStatus = ClaimStatus.REVIEW_REQUIRED
    verification_basis: str = "Requirement-weighted comparison calculation"
    policy_scores: dict[str, float] = Field(default_factory=dict)
    confirmed_priorities: list[str] = Field(default_factory=list)
    unique_leader: bool = False
    explanation: str


class AuditReport(BaseModel):
    audit_id: str
    status: AuditStatus
    total_claims: int
    supported_claims: int
    partially_supported_claims: int
    unsupported_claims: int
    contradicted_claims: int
    unverifiable_claims: int
    critical_issues: int
    claim_results: list[BenefitAuditResult]
    slide_results: list[SlideAuditResult]
    chart_checks: list[ChartAuditResult]
    recommendation_check: RecommendationAudit
    summary: str
    advisor_actions: list[str]
    company_name: str | None = None
    audited_at: datetime | None = None
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    requirement_conflicts: list[RequirementConflict] = Field(default_factory=list)
    advisor_name: str | None = None
    advisor_decision: Literal["approve", "reject"] | None = None
    advisor_decision_at: datetime | None = None
    advisor_rejection_reason: str | None = None
    pitch_id: str | None = None
    audited_pitch_version: int | None = None
    audited_pitch_hash: str | None = None
    audit_hash: str | None = None
    approved_at: datetime | None = None


class AuditRequest(BaseModel):
    pitch_slides: list[PitchSlide]
    policy_docs: list[PolicyDocument]
    recommended_policy_id: str | None = None


class AuditPitchRequest(BaseModel):
    pitch: MarketingPitch
    uploaded_documents: list[PolicyDocument] = Field(default_factory=list)


class AdvisorDecisionRequest(BaseModel):
    audit_report: AuditReport
    advisor_name: str = Field(default="Advisor (not recorded)", min_length=1, max_length=120)
    decision: Literal["approve", "reject"]
    reason: str | None = None


class AdvisorDecisionResponse(BaseModel):
    accepted: bool
    decision: Literal["approve", "reject"]
    message: str
    advisor_name: str
    decided_at: datetime
    pitch_id: str | None = None
    pitch_version: int | None = None


class PitchEditRequest(BaseModel):
    slide_number: int = Field(ge=1, le=5)
    claim_id: str | None = None
    new_text: str = Field(min_length=1, max_length=1200)


class PitchFixRequest(BaseModel):
    claim_id: str


class RejectRegenerateRequest(BaseModel):
    advisor_name: str = Field(min_length=1, max_length=120)
    feedback: str = Field(min_length=3, max_length=2000)


class PitchWorkflowState(BaseModel):
    pitch: MarketingPitch
    audit: AuditReport | None = None
    approval_enabled: bool = False
    approved: bool = False
    advisor_decision: Literal["approve", "reject"] | None = None
