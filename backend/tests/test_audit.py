import pytest
from pypdf import PdfWriter
from pydantic import ValidationError

from app.audit import auditPitchContent
from app.documents import parse_pdf_bytes
from app.generation import _normalise, generateMarketingPitch
from app.pptx_export import export_pitch_pptx
from app.pdf_export import export_audit_pdf
from app.models import (
    AuditStatus,
    BenefitAuditResult,
    ChartDefinition,
    ClaimStatus,
    ContentBlock,
    DocumentChunk,
    DocumentRole,
    PitchClaim,
    PitchSlide,
    PolicyDocument,
    SourceReference,
)


def make_document() -> PolicyDocument:
    return PolicyDocument(
        document_id="policy-a",
        document_name="Policy A.pdf",
        role=DocumentRole.PRELOADED_POLICY,
        chunks=[
            DocumentChunk(
                chunk_id="policy-a:p2:c1",
                page_number=2,
                text="Road ambulance is covered up to INR 2,50,000 per hospitalisation.",
            ),
            DocumentChunk(
                chunk_id="policy-a:p3:c1",
                page_number=3,
                text="The wellness benefit is optional and available on payment of an additional premium.",
            ),
        ],
    )


def make_slides(claims: list[PitchClaim]) -> list[PitchSlide]:
    slides = [
        PitchSlide(
            slide_number=index,
            title=f"Slide {index}",
            key_message="Evidence-based analysis",
        )
        for index in range(1, 6)
    ]
    slides[2].claims.extend(claims)
    slides[3].charts.append(
        ChartDefinition(
            chart_type="bar",
            title="Policy fit",
            categories=["Ambulance"],
            series={"Policy A": [2]},
        )
    )
    return slides


@pytest.mark.asyncio
async def test_supported_claim_and_recommendation_pass(monkeypatch):
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "VERIFIED", "reason": "Semantic match confirmed."} for item in items}
    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    claim = PitchClaim(
        claim_id="c1",
        text="Policy A is recommended because road ambulance is covered up to INR 2,50,000 per hospitalisation.",
        policy_id="policy-a",
        benefit_name="Final recommendation",
        presented_value="INR 2,50,000 per hospitalisation",
    )
    report = await auditPitchContent(make_slides([claim]), [make_document()], "policy-a")
    assert report.status == AuditStatus.REVIEW_REQUIRED
    assert report.claim_results[0].status == ClaimStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_wrong_number_is_contradicted():
    claim = PitchClaim(
        claim_id="c1",
        text="Road ambulance is covered up to INR 5,00,000 per hospitalisation.",
        policy_id="policy-a",
        benefit_name="Road ambulance",
        presented_value="INR 5,00,000 per hospitalisation",
    )
    report = await auditPitchContent(make_slides([claim]), [make_document()], "policy-a")
    assert report.status == AuditStatus.FAIL
    assert report.claim_results[0].status == ClaimStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_missing_optional_condition_requires_review():
    claim = PitchClaim(
        claim_id="c1",
        text="Policy A includes a wellness benefit.",
        policy_id="policy-a",
        benefit_name="Wellness",
    )
    report = await auditPitchContent(make_slides([claim]), [make_document()], "policy-a")
    assert report.status == AuditStatus.REVIEW_REQUIRED
    assert "optional benefit" in report.claim_results[0].omitted_conditions


@pytest.mark.asyncio
async def test_domiciliary_hospitalisation_supports_home_care(monkeypatch):
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "VERIFIED", "reason": "The cited domiciliary hospitalisation clause supports the Home care category."} for item in items}

    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    text = "Domiciliary hospitalisation covers medical expenses during treatment at home, subject to policy terms."
    document = make_document().model_copy(update={"chunks": [DocumentChunk(chunk_id="policy-a:p4:c1", page_number=4, text=text)]})
    claim = PitchClaim(
        claim_id="home-care",
        text="Home care is available as described in the cited policy clause.",
        policy_id="policy-a",
        benefit_name="Home care",
        presented_value="Home care is available as described in the cited policy clause.",
        source_references=[SourceReference(document_id="policy-a", document_name="Policy A.pdf", page_number=4, excerpt=text)],
    )
    report = await auditPitchContent(make_slides([claim]), [document], "policy-a")
    result = next(item for item in report.claim_results if item.claim_id == "home-care")
    assert result.status in {ClaimStatus.VERIFIED, ClaimStatus.VERIFIED_WITH_QUALIFICATION}
    assert "domiciliary hospitalisation" in result.explanation.lower()


@pytest.mark.asyncio
async def test_preventive_evidence_does_not_support_home_care(monkeypatch):
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "UNSUPPORTED", "reason": "Preventive health evidence is unrelated to Home care."} for item in items}

    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    text = "Preventive health check-up benefits are available after each policy year."
    document = make_document().model_copy(update={"chunks": [DocumentChunk(chunk_id="policy-a:p4:c1", page_number=4, text=text)]})
    claim = PitchClaim(
        claim_id="home-care",
        text="Home care is available.", policy_id="policy-a", benefit_name="Home care", presented_value="Home care is available.",
        source_references=[SourceReference(document_id="policy-a", document_name="Policy A.pdf", page_number=4, excerpt=text)],
    )
    report = await auditPitchContent(make_slides([claim]), [document], "policy-a")
    result = next(item for item in report.claim_results if item.claim_id == "home-care")
    assert result.status == ClaimStatus.UNSUPPORTED


@pytest.mark.asyncio
async def test_reference_policy_cannot_be_recommended():
    doc = make_document().model_copy(update={"role": DocumentRole.REFERENCE_POLICY})
    claim = PitchClaim(
        claim_id="c1",
        text="Policy A is recommended because road ambulance is covered up to INR 2,50,000 per hospitalisation.",
        policy_id="policy-a",
        benefit_name="Road ambulance",
        presented_value="INR 2,50,000 per hospitalisation",
    )
    report = await auditPitchContent(make_slides([claim]), [doc], "policy-a")
    assert report.status == AuditStatus.REVIEW_REQUIRED
    assert not report.recommendation_check.eligible


@pytest.mark.asyncio
async def test_requires_exactly_five_slides():
    report = await auditPitchContent(make_slides([])[:4], [make_document()], "policy-a")
    assert report.status == AuditStatus.AUDIT_INCOMPLETE


def test_password_protected_pdf_is_rejected():
    import io

    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    writer.write(buffer)

    with pytest.raises(ValueError, match="Password-protected PDF"):
        parse_pdf_bytes(
            buffer.getvalue(),
            document_id="locked",
            name="locked.pdf",
            role=DocumentRole.REFERENCE_POLICY,
        )


def test_speaker_notes_are_rejected():
    with pytest.raises(ValidationError):
        PitchSlide.model_validate(
            {
                "slide_number": 1,
                "title": "Test",
                "key_message": "Test",
                "speaker_notes": "Speaker notes must not be accepted.",
            }
        )


@pytest.mark.asyncio
async def test_visible_unregistered_policy_statement_is_audited():
    slides = make_slides([])
    slides[3].content_blocks.append(
        ContentBlock(
            kind="text",
            text="Policy A covers air ambulance up to INR 9,99,999.",
        )
    )
    report = await auditPitchContent(slides, [make_document()], "policy-a")
    assert report.status == AuditStatus.FAIL
    assert any(result.claim_id.startswith("auto:") for result in report.claim_results)


@pytest.mark.asyncio
async def test_marketing_pitch_follows_challenge_one_structure():
    pitch = await generateMarketingPitch(
        company_name="Example Technology",
        policy_docs=[make_document()],
        selected_policy_ids=["policy-a"],
        client_requirements=["Ambulance coverage", "Hospitalisation"],
    )
    assert len(pitch.pitch_slides) == 5
    assert pitch.pitch_slides[0].title == "Policy A.pdf for Example Technology"
    assert pitch.pitch_slides[2].title == "Policy comparison"
    assert pitch.pitch_slides[3].title == "Why Policy A.pdf fits"
    assert pitch.pitch_slides[3].comparison_tables
    assert pitch.pitch_slides[3].claims
    assert pitch.recommended_policy_id == "policy-a"


@pytest.mark.asyncio
async def test_generated_single_policy_pitch_cannot_claim_shortlist_lead(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "VERIFIED", "reason": "Semantic match confirmed."} for item in items}
    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    document = make_document()
    pitch = await generateMarketingPitch(
        company_name="Example Technology",
        policy_docs=[document],
        selected_policy_ids=["policy-a"],
        client_requirements=["Ambulance coverage", "Hospitalisation"],
    )
    report = await auditPitchContent(
        pitch.pitch_slides,
        [document],
        pitch.recommended_policy_id,
    )
    assert report.status == AuditStatus.REVIEW_REQUIRED
    assert not report.recommendation_check.supported


def test_malformed_table_marker_is_not_joined_to_percentage():
    cleaned = _normalise("3 100% of SI available only for `15 Lakh; below `15 lac up to `10,000")
    assert "3100%" not in cleaned
    assert "100% of SI" in cleaned
    assert "₹15 Lakh" in cleaned
    assert "₹10,000" in cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("benefit", ["In-patient hospitalisation", "Room rent and ICU"])
async def test_preventive_clause_cannot_verify_unrelated_benefit(monkeypatch, benefit):
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "VERIFIED", "reason": "Incorrectly optimistic model response."} for item in items}
    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    text = "Preventive Health Check-up is available once per insured per policy year."
    document = make_document().model_copy(update={"chunks": [DocumentChunk(chunk_id="policy-a:p5:c1", page_number=5, text=text)]})
    claim = PitchClaim(
        claim_id="mismatch", text=f"{benefit} is covered.", policy_id="policy-a", benefit_name=benefit,
        source_references=[SourceReference(document_id="policy-a", document_name="Policy A.pdf", page_number=5, excerpt=text)],
    )
    report = await auditPitchContent(make_slides([claim]), [document], "policy-a")
    assert report.status == AuditStatus.REVIEW_REQUIRED
    assert report.claim_results[0].status in {ClaimStatus.REVIEW_REQUIRED, ClaimStatus.UNSUPPORTED}


@pytest.mark.asyncio
async def test_recommendation_must_be_unique_four_policy_calculation_leader(monkeypatch):
    async def semantic(items):
        return {item["claim_id"]: {"verdict": "VERIFIED", "reason": "Evidence matches."} for item in items}
    monkeypatch.setattr("app.audit.semantic_audit_batch", semantic)
    docs = [make_document().model_copy(update={
        "document_id": f"policy-{letter}", "document_name": f"Policy {letter.upper()}.pdf",
        "product_name": f"Policy {letter.upper()}",
        "chunks": [DocumentChunk(chunk_id=f"policy-{letter}:p2:c1", page_number=2, text="Road ambulance is covered up to INR 2,50,000 per hospitalisation.")],
    }) for letter in "abcd"]
    claim = PitchClaim(claim_id="rec", text="Policy A leads the shortlist.", policy_id="policy-a", benefit_name="Final recommendation")
    slides = make_slides([claim])
    slides[3].charts = [ChartDefinition(chart_type="bar", title="Confirmed-priority fit points", categories=["Policy A", "Policy B", "Policy C", "Policy D"], series={"Fit points": [9, 8, 7, 6]})]
    report = await auditPitchContent(slides, docs, "policy-a")
    assert report.recommendation_check.supported
    assert report.recommendation_check.verification_basis == "Requirement-weighted comparison calculation"
    assert report.recommendation_check.policy_scores == {"Policy A": 9.0, "Policy B": 8.0, "Policy C": 7.0, "Policy D": 6.0}
    assert report.recommendation_check.unique_leader
    assert report.claim_results[0].status == ClaimStatus.VERIFIED
    assert report.claim_results[0].source_references == []
    assert report.claim_results[0].suggested_rewrite is None
    slides[3].charts[0].series = {"Fit points": [9, 10, 7, 6]}
    failed = await auditPitchContent(slides, docs, "policy-a")
    assert failed.status == AuditStatus.FAIL
    assert not failed.recommendation_check.supported


def test_audit_schema_contains_no_confidence_fields():
    assert "confidence" not in BenefitAuditResult.model_fields
    from app.models import AuditReport
    assert "confidence_score" not in AuditReport.model_fields


@pytest.mark.asyncio
async def test_pending_review_pdf_never_says_rejected_or_confidence():
    import io
    from datetime import datetime, timezone
    from pypdf import PdfReader
    claim = PitchClaim(claim_id="c1", text="Road ambulance is covered up to INR 5,00,000.", policy_id="policy-a", benefit_name="Road ambulance")
    report = await auditPitchContent(make_slides([claim]), [make_document()], "policy-a")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(export_audit_pdf(report))).pages)
    assert "Advisor decision: Pending" in text
    assert "REJECTED" not in text
    assert "Confidence" not in text
    assert "Audit methodology" in text
    assert "Recommendation validation" in text
    assert "Recommendation calculation" in text
    now = datetime.now(timezone.utc)
    approved = report.model_copy(update={
        "status": AuditStatus.PASS, "advisor_name": "Test Advisor", "advisor_decision": "approve",
        "advisor_decision_at": now, "approved_at": now, "audited_pitch_version": 2,
        "audited_pitch_hash": "pitch-hash", "audit_hash": "audit-hash",
    })
    approved_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(export_audit_pdf(approved, approval_persisted=True))).pages)
    assert "Approved Version" in approved_text
    assert "pitch-hash" in approved_text
    assert "audit-hash" in approved_text
    assert "Test Advisor" in approved_text


@pytest.mark.asyncio
async def test_powerpoint_export_contains_five_slides():
    import io
    from pptx import Presentation

    pitch = await generateMarketingPitch(
        company_name="Example Technology",
        policy_docs=[make_document()],
        selected_policy_ids=["policy-a"],
    )
    presentation = Presentation(io.BytesIO(export_pitch_pptx(pitch)))
    assert len(presentation.slides) == 5
    assert all("speaker_notes" not in slide.model_dump() for slide in pitch.pitch_slides)


@pytest.mark.asyncio
async def test_powerpoint_export_contains_no_truncation_markers():
    import io
    from pptx import Presentation
    pitch = await generateMarketingPitch(
        company_name="Example Technology",
        policy_docs=[make_document()],
        selected_policy_ids=["policy-a"],
    )
    pitch.pitch_slides[0].content_blocks[0].items.append("A deliberately incomplete benefit sentence ending nur…")
    presentation = Presentation(io.BytesIO(export_pitch_pptx(pitch)))
    rendered = "\n".join(shape.text for slide in presentation.slides for shape in slide.shapes if hasattr(shape, "text"))
    assert "..." not in rendered
    assert "…" not in rendered
