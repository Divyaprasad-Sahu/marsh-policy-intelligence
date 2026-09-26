from __future__ import annotations

import os
import logging
import hashlib
import json
import re
import uuid
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from .audit import auditPitchContent
from .documents import PRELOADED_POLICIES, cached_preloaded_policies, parse_docx_bytes, parse_pdf_bytes
from .generation import generateCompanyProfile, generateCompanyRequirements, generateMarketingPitch, suggestCompanyNames
from .analysis import generate_advisory_analysis
from .intelligence import extract_requirements
from .models import (
    AdvisorDecisionRequest,
    AdvisorDecisionResponse,
    AuditPitchRequest,
    AuditRequest,
    AuditReport,
    AuditStatus,
    CompanyProfile,
    CompanyResearchRequest,
    CompanyResearchResult,
    AdvisoryAnalysis,
    AdvisoryAnalysisRequest,
    DocumentRole,
    GeneratePitchRequest,
    MarketingPitch,
    PolicyDocument,
    RequirementChatRequest,
    RequirementChatResponse,
    PitchEditRequest,
    PitchFixRequest,
    PitchWorkflowState,
    RejectRegenerateRequest,
)
from .pptx_export import export_pitch_pptx
from .pdf_export import export_audit_pdf
from .workflow_store import workflow_store


app = FastAPI(
    title="Marsh AI Pitch Audit API",
    version="0.1.0",
    description="Grounded policy-benefit generation and claim-level audit service.",
)
logger = logging.getLogger(__name__)


class _RateLimiter:
    """Small single-instance limiter for costly public demo endpoints."""

    def __init__(self) -> None:
        self.window_seconds = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300")))
        self.max_requests = max(1, int(os.getenv("RATE_LIMIT_REQUESTS", "30")))
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - requests[0])))
                return False, retry_after
            requests.append(now)
            return True, 0


_rate_limiter = _RateLimiter()
_RATE_LIMITED_PREFIXES = (
    "/api/v1/company-research",
    "/api/v1/advisory-analysis",
    "/api/v1/requirements/chat",
    "/api/v1/pitches/generate",
    "/api/v1/pitches/audit",
    "/api/v1/pitches/export",
    "/api/v1/audits/export",
)


def _normalised_label(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _mutate_pitch_claim(pitch: MarketingPitch, finding, replacement: str | None) -> bool:
    """Replace or remove a finding everywhere it is presented on its slide."""
    slide = next((item for item in pitch.pitch_slides if item.slide_number == finding.slide_number), None)
    if not slide:
        return False
    changed = False
    old = finding.presented_value
    retained = []
    for claim in slide.claims:
        if claim.claim_id != finding.claim_id:
            retained.append(claim)
            continue
        changed = True
        if replacement is not None:
            claim.text = replacement
            claim.presented_value = replacement
            retained.append(claim)
    slide.claims = retained
    for block in slide.content_blocks:
        if block.text == old:
            block.text = replacement
            changed = True
        next_items = [replacement if item == old else item for item in block.items]
        block.items = [item for item in next_items if item is not None]
        changed = changed or next_items != block.items
    if slide.key_message == old:
        slide.key_message = replacement or "This unsupported statement was removed pending refreshed analysis."
        changed = True
    benefit = _normalised_label(finding.benefit_name)
    variant = _normalised_label(finding.policy_variant)
    for table in slide.comparison_tables:
        policy_column = next((index for index, label in enumerate(table.columns) if variant and _normalised_label(label) == variant), None)
        for row in table.rows:
            if row and _normalised_label(row[0]) == benefit and policy_column is not None and policy_column < len(row):
                row[policy_column] = replacement or "Not established"
                changed = True
    return changed


def _download_filename(company: str | None, document_type: str, extension: str, version: int | None = None) -> str:
    company_slug = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-") or "client"
    version_part = f"_v{version}" if version is not None else ""
    date_part = datetime.now(timezone.utc).date().isoformat()
    return f"{company_slug}_{document_type}{version_part}_{date_part}.{extension}"

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=os.getenv(
        "ALLOWED_ORIGIN_REGEX",
        r"https://([a-z0-9-]+\.)?(vercel\.app|lovable\.app)$",
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    expose_headers=["Content-Disposition"],
)


@app.middleware("http")
async def public_demo_rate_limit(request: Request, call_next):
    enabled = os.getenv("RATE_LIMIT_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    is_limited = request.method == "POST" and (
        request.url.path.startswith(_RATE_LIMITED_PREFIXES)
        or request.url.path.endswith(("/reaudit", "/reject-regenerate"))
    )
    if enabled and is_limited:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        client_ip = forwarded or (request.client.host if request.client else "unknown")
        allowed, retry_after = _rate_limiter.allow(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demo request limit reached. Please wait a few minutes and try again."},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/policies")
async def policies() -> list[dict[str, str]]:
    return [
        {
            "policy_id": policy_id,
            "filename": metadata["filename"],
            "insurer": metadata["insurer"],
            "product_name": metadata["product"],
        }
        for policy_id, metadata in PRELOADED_POLICIES.items()
    ]


@app.post("/api/v1/company-profile", response_model=CompanyProfile)
async def company_profile(company_name: str) -> CompanyProfile:
    try:
        return await generateCompanyProfile(company_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/company-suggestions", response_model=list[str])
async def company_suggestions(q: str = Query(min_length=2, max_length=100)) -> list[str]:
    return await suggestCompanyNames(q)


@app.post("/api/v1/company-research", response_model=CompanyResearchResult)
async def company_research(request: CompanyResearchRequest) -> CompanyResearchResult:
    profile = await generateCompanyProfile(request.company_name)
    suggestions = await generateCompanyRequirements(profile)
    sources = [fact.source_url for fact in profile.facts if fact.source_url]
    missing = [
        fact.name for fact in profile.facts
        if not fact.value.strip() or "requires advisor confirmation" in fact.value.lower()
    ]
    if not suggestions.operating_locations:
        missing.append("Operating locations")
    if not suggestions.workforce_profile:
        missing.append("Workforce profile")
    if not suggestions.ranked_coverage_priorities:
        missing.append("Ranked coverage priorities")
    questions = [
        "Is the suggested industry correct?",
        "Is the employee count or size range correct?",
        "Are the operating locations and workforce profile correct?",
        "Do the suggested coverage priorities reflect the client's needs?",
    ]
    return CompanyResearchResult(profile=profile, sources=list(dict.fromkeys(sources)), missing_field_checklist=list(dict.fromkeys(missing)), suggested_requirements=suggestions, verification_questions=questions, research_note="AI suggestions are pre-filled below. Confirm or edit them before analysis.")


@app.post("/api/v1/requirements/chat", response_model=RequirementChatResponse)
async def requirements_chat(request: RequirementChatRequest) -> RequirementChatResponse:
    """Turn advisor conversation into explicit requirements that still require confirmation."""
    return await extract_requirements(request.message, request.existing_requirements)


@app.post("/api/v1/advisory-analysis", response_model=AdvisoryAnalysis)
async def advisory_analysis(request: AdvisoryAnalysisRequest) -> AdvisoryAnalysis:
    try:
        return await generate_advisory_analysis(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/pitches/generate", response_model=MarketingPitch)
async def generate_pitch(request: GeneratePitchRequest) -> MarketingPitch:
    try:
        uploaded_requirements = [
            chunk.text
            for document in request.uploaded_documents
            if document.role == DocumentRole.CLIENT_REQUIREMENTS
            for chunk in document.chunks[:5]
        ]
        requirements = list(request.client_requirements)
        if request.requirements_context:
            requirements.extend(
                item.requirement
                for item in request.requirements_context.structured_requirements
                if item.confirmed
            )
        pitch = await generateMarketingPitch(
            company_name=request.company_name,
            selected_policy_ids=request.policy_ids,
            client_requirements=[*requirements, *uploaded_requirements],
            company_profile=request.verified_company_profile,
        )
        return workflow_store.save_new(pitch, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/documents/parse", response_model=PolicyDocument)
async def parse_document(
    file: UploadFile = File(...),
    role: DocumentRole = Form(...),
) -> PolicyDocument:
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files must be 10 MB or smaller.")
    filename = file.filename or "upload"
    safe_id = re.sub(r"[^a-z0-9]+", "-", filename.lower()).strip("-")
    document_id = f"upload-{safe_id}-{uuid.uuid4().hex[:8]}"
    try:
        if filename.lower().endswith(".pdf"):
            return parse_pdf_bytes(data, document_id, filename, role)
        if filename.lower().endswith(".docx"):
            return parse_docx_bytes(data, document_id, filename, role)
        raise ValueError("Only PDF and DOCX files are supported.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/pitches/export")
async def export_pitch(pitch: MarketingPitch) -> Response:
    payload = export_pitch_pptx(pitch)
    filename = re.sub(r"[^a-zA-Z0-9]+", "-", pitch.company_profile.company_name).strip("-") or "company"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}-insurance-pitch.pptx"'},
    )


@app.post("/api/v1/audits/run", response_model=AuditReport)
async def run_audit(request: AuditRequest) -> AuditReport:
    return await auditPitchContent(
        request.pitch_slides,
        request.policy_docs,
        request.recommended_policy_id,
    )


@app.post("/api/v1/pitches/audit", response_model=AuditReport)
async def audit_generated_pitch(request: AuditPitchRequest) -> AuditReport:
    pitch = request.pitch
    if pitch.pitch_id:
        try:
            current = workflow_store.get(pitch.pitch_id).pitch
        except KeyError:
            # Recover a pitch that is still open in the browser after a local
            # backend restart. It is re-audited before any edit or approval.
            recovered_request = GeneratePitchRequest(
                company_name=pitch.company_profile.company_name,
                policy_ids=[document.document_id for document in cached_preloaded_policies()],
                client_requirements=[],
                uploaded_documents=request.uploaded_documents,
                verified_company_profile=pitch.company_profile,
            )
            workflow_store.restore(pitch, recovered_request)
            current = pitch
        # A source-safe action may have created a newer server version while
        # the browser still displays the old snapshot. Audit the authoritative
        # server version instead of failing the advisor workflow.
        pitch = current
    documents = [*cached_preloaded_policies(), *request.uploaded_documents]
    report = await auditPitchContent(
        pitch.pitch_slides,
        documents,
        pitch.recommended_policy_id,
    )
    report = report.model_copy(update={
        "company_name": pitch.company_profile.company_name,
        "audited_at": datetime.now(timezone.utc),
        "pitch_id": pitch.pitch_id or None,
        "audited_pitch_version": pitch.pitch_version,
        "audited_pitch_hash": pitch.pitch_hash or None,
    })
    audit_payload = report.model_dump(mode="json", exclude={"audit_hash", "advisor_name", "advisor_decision", "advisor_decision_at", "approved_at"})
    report = report.model_copy(update={"audit_hash": hashlib.sha256(json.dumps(audit_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
    if pitch.pitch_id:
        workflow_store.save_audit(pitch.pitch_id, report)
    return report


def _workflow_state(pitch_id: str) -> PitchWorkflowState:
    try:
        record = workflow_store.get(pitch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pitch workflow was not found.") from exc
    exact_pass = bool(
        record.audit
        and record.audit.status == AuditStatus.PASS
        and record.audit.audited_pitch_hash == record.pitch.pitch_hash
        and record.audit.audited_pitch_version == record.pitch.pitch_version
    )
    return PitchWorkflowState(
        pitch=record.pitch,
        audit=record.audit,
        approval_enabled=exact_pass,
        approved=bool(
            record.advisor_decision == "approve" and record.advisor_name and record.decision_at
            and record.audit and record.audit.advisor_name and record.audit.advisor_decision_at
            and record.audit.approved_at and exact_pass
        ),
        advisor_decision=record.advisor_decision,
    )


@app.get("/api/v1/pitches/{pitch_id}/state", response_model=PitchWorkflowState)
async def pitch_state(pitch_id: str) -> PitchWorkflowState:
    return _workflow_state(pitch_id)


@app.post("/api/v1/pitches/{pitch_id}/edit", response_model=PitchWorkflowState)
async def edit_pitch(pitch_id: str, request: PitchEditRequest) -> PitchWorkflowState:
    try:
        record = workflow_store.get(pitch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pitch workflow was not found.") from exc
    current = record.pitch
    pitch = current.model_copy(deep=True)
    slide = next((item for item in pitch.pitch_slides if item.slide_number == request.slide_number), None)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide was not found.")
    changed = False
    if request.claim_id:
        for claim in slide.claims:
            if claim.claim_id == request.claim_id:
                old = claim.text
                claim.text = request.new_text
                claim.presented_value = request.new_text
                for block in slide.content_blocks:
                    if block.text == old:
                        block.text = request.new_text
                    block.items = [request.new_text if item == old else item for item in block.items]
                changed = True
                break
        # Some audit findings originate from visible text rather than an
        # explicit PitchClaim.  They still need to be editable in place.
        if not changed and record.audit:
            finding = next((item for item in record.audit.claim_results if item.claim_id == request.claim_id), None)
            if finding:
                old = finding.presented_value
                for block in slide.content_blocks:
                    if block.text == old:
                        block.text = request.new_text
                        changed = True
                    replacement_items = [request.new_text if item == old else item for item in block.items]
                    changed = changed or replacement_items != block.items
                    block.items = replacement_items
                if slide.key_message == old:
                    slide.key_message = request.new_text
                    changed = True
    else:
        slide.key_message = request.new_text
        changed = True
    if not changed:
        raise HTTPException(status_code=404, detail="This claim is no longer present in the current pitch version. Run a full audit again, then edit the refreshed finding.")
    workflow_store.save_version(pitch_id, pitch)
    return _workflow_state(pitch_id)


@app.post("/api/v1/pitches/{pitch_id}/apply-fix", response_model=PitchWorkflowState)
async def apply_pitch_fix(pitch_id: str, request: PitchFixRequest) -> PitchWorkflowState:
    try:
        record = workflow_store.get(pitch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pitch workflow was not found.") from exc
    if not record.audit:
        raise HTTPException(status_code=409, detail="Run the audit before applying a suggested fix.")
    finding = next((item for item in record.audit.claim_results if item.claim_id == request.claim_id), None)
    if not finding or not finding.suggested_rewrite:
        raise HTTPException(status_code=422, detail="This finding has no suggested rewrite.")
    pitch = record.pitch.model_copy(deep=True)
    remove_statement = bool(re.match(r"^\s*remove (?:this|the) statement\b", finding.suggested_rewrite, re.I))
    replacement = None if remove_statement else finding.suggested_rewrite.strip()
    old_hash, old_version, old_text = record.pitch.pitch_hash, record.pitch.pitch_version, finding.presented_value
    changed = _mutate_pitch_claim(pitch, finding, replacement)
    if not changed:
        raise HTTPException(status_code=409, detail="The audited statement could not be located in the current pitch. Run a fresh audit and try again.")
    updated = workflow_store.save_version(pitch_id, pitch)
    new_text = replacement or "[removed]"
    logger.info(
        "source_safe_change_applied pitch_id=%s claim_id=%s old_text=%r new_text=%r old_hash=%s new_hash=%s old_version=%s new_version=%s",
        pitch_id, finding.claim_id, old_text, new_text, old_hash, updated.pitch_hash, old_version, updated.pitch_version,
    )
    if old_text == new_text or old_hash == updated.pitch_hash or old_version >= updated.pitch_version:
        raise HTTPException(status_code=500, detail="The pitch mutation did not produce a new version and hash.")
    return _workflow_state(pitch_id)


@app.post("/api/v1/pitches/{pitch_id}/reaudit", response_model=PitchWorkflowState)
async def re_audit_pitch(pitch_id: str) -> PitchWorkflowState:
    state = _workflow_state(pitch_id)
    await audit_generated_pitch(AuditPitchRequest(pitch=state.pitch))
    return _workflow_state(pitch_id)


@app.post("/api/v1/pitches/{pitch_id}/reject-regenerate", response_model=PitchWorkflowState)
async def reject_and_regenerate(pitch_id: str, request: RejectRegenerateRequest) -> PitchWorkflowState:
    try:
        record = workflow_store.get(pitch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pitch workflow was not found.") from exc
    workflow_store.reject(pitch_id, request.advisor_name, request.feedback)
    source = record.generation_request
    regenerated = await generateMarketingPitch(
        company_name=source.company_name,
        selected_policy_ids=source.policy_ids,
        client_requirements=[*source.client_requirements, f"Advisor feedback: {request.feedback}"],
        company_profile=source.verified_company_profile,
    )
    workflow_store.save_version(pitch_id, regenerated)
    await re_audit_pitch(pitch_id)
    return _workflow_state(pitch_id)


@app.post("/api/v1/pitches/{pitch_id}/approve", response_model=AdvisorDecisionResponse)
async def approve_pitch(pitch_id: str, advisor_name: str = Query(min_length=1, max_length=120)) -> AdvisorDecisionResponse:
    state = _workflow_state(pitch_id)
    if not state.approval_enabled:
        raise HTTPException(status_code=409, detail="Approval requires a PASS audit for this exact pitch version.")
    record = workflow_store.approve(pitch_id, advisor_name)
    approved_at = record.decision_at or datetime.now(timezone.utc)
    record.audit = record.audit.model_copy(update={
        "advisor_name": advisor_name,
        "advisor_decision": "approve",
        "advisor_decision_at": approved_at,
        "approved_at": approved_at,
    }) if record.audit else None
    if record.audit:
        workflow_store.save_audit(pitch_id, record.audit)
    return AdvisorDecisionResponse(accepted=True, decision="approve", message="Pitch version approved for client use.", advisor_name=advisor_name, decided_at=approved_at, pitch_id=pitch_id, pitch_version=record.pitch.pitch_version)


@app.get("/api/v1/pitches/{pitch_id}/final.pptx")
async def download_final_pptx(pitch_id: str) -> Response:
    state = _workflow_state(pitch_id)
    if not state.approved:
        raise HTTPException(status_code=409, detail="Only the approved, audited pitch version can be downloaded.")
    payload = export_pitch_pptx(state.pitch)
    filename = _download_filename(state.pitch.company_profile.company_name, "policy-pitch", "pptx", state.pitch.pitch_version)
    return Response(content=payload, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/v1/pitches/{pitch_id}/verification.pdf")
async def download_verification_pdf(pitch_id: str) -> Response:
    state = _workflow_state(pitch_id)
    if not state.approved or not state.audit:
        raise HTTPException(status_code=409, detail="Only the approved, audited pitch version can be downloaded.")
    payload = export_audit_pdf(state.audit, approval_persisted=True)
    filename = _download_filename(state.pitch.company_profile.company_name, "approved-audit-report", "pdf", state.pitch.pitch_version)
    return Response(content=payload, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/v1/pitches/{pitch_id}/audit-review.pdf")
async def download_audit_review_pdf(pitch_id: str) -> Response:
    """Return the evidence report for the current audited version, before approval.

    This lets an advisor inspect the complete audit and its citations while the
    client-ready export remains protected by the approval gate above.
    """
    state = _workflow_state(pitch_id)
    audit = state.audit
    if not audit:
        raise HTTPException(status_code=409, detail="Run the audit before downloading its review report.")
    if audit.audited_pitch_hash != state.pitch.pitch_hash or audit.audited_pitch_version != state.pitch.pitch_version:
        raise HTTPException(status_code=409, detail="The audit report does not match the current pitch version. Run the audit again.")
    payload = export_audit_pdf(audit)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_download_filename(state.pitch.company_profile.company_name, "audit-review", "pdf", state.pitch.pitch_version)}"'},
    )


@app.post("/api/v1/audits/reaudit", response_model=AuditReport)
async def rerun_audit(request: AuditRequest) -> AuditReport:
    return await auditPitchContent(
        request.pitch_slides,
        request.policy_docs,
        request.recommended_policy_id,
    )


@app.post("/api/v1/audits/decision", response_model=AdvisorDecisionResponse)
async def advisor_decision(request: AdvisorDecisionRequest) -> AdvisorDecisionResponse:
    if request.decision == "approve" and request.audit_report.status != AuditStatus.PASS:
        raise HTTPException(
            status_code=409,
            detail="Only a pitch with a PASS audit may be approved.",
        )
    if request.decision == "reject" and not request.reason:
        raise HTTPException(status_code=422, detail="A rejection reason is required.")
    return AdvisorDecisionResponse(
        accepted=True,
        decision=request.decision,
        message=(
            "Pitch approved for client use."
            if request.decision == "approve"
            else "Pitch rejected and remains unavailable for client use."
        ),
        advisor_name=request.advisor_name,
        decided_at=datetime.now(timezone.utc),
    )


@app.post("/api/v1/audits/export")
async def export_audit(report: AuditReport) -> Response:
    payload = export_audit_pdf(report)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_filename(report.company_name, "audit-report", "pdf", report.audited_pitch_version)}"'
        },
    )
