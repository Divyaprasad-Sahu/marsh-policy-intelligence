from io import BytesIO

from fastapi.testclient import TestClient
from pptx import Presentation
from pypdf import PdfReader

from app import generation
from app.documents import cached_preloaded_policies
from app.main import _rate_limiter, app


client = TestClient(app)


def test_public_demo_rate_limit_returns_actionable_error(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setattr(_rate_limiter, "max_requests", 1)
    monkeypatch.setattr(_rate_limiter, "window_seconds", 300)
    _rate_limiter._requests.clear()
    try:
        first = client.post("/api/v1/pitches/export", json={})
        assert first.status_code == 422

        limited = client.post("/api/v1/pitches/export", json={})
        assert limited.status_code == 429
        assert "wait a few minutes" in limited.json()["detail"]
        assert int(limited.headers["Retry-After"]) >= 1

        health = client.get("/api/v1/health")
        assert health.status_code == 200
    finally:
        _rate_limiter._requests.clear()


async def no_company_result(company_name: str):
    return None, None


def test_complete_generation_export_and_audit_flow(monkeypatch):
    monkeypatch.setattr(generation, "_wikipedia_company_summary", no_company_result)

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    policies = client.get("/api/v1/policies")
    assert policies.status_code == 200
    policy_ids = [policy["policy_id"] for policy in policies.json()]
    assert len(policy_ids) == 4

    generated = client.post(
        "/api/v1/pitches/generate",
        json={
            "company_name": "Example Technology Company",
            "policy_ids": policy_ids,
            "client_requirements": [
                "Hospitalisation coverage",
                "Ambulance support",
                "Preventive healthcare",
            ],
        },
    )
    assert generated.status_code == 200, generated.text
    pitch = generated.json()
    assert len(pitch["pitch_slides"]) == 5
    assert pitch["pitch_slides"][2]["title"] == "Policy comparison"
    assert pitch["pitch_slides"][3]["comparison_tables"]
    assert pitch["recommended_policy_id"] in policy_ids

    exported = client.post("/api/v1/pitches/export", json=pitch)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert len(Presentation(BytesIO(exported.content)).slides) == 5

    audit = client.post(
        "/api/v1/audits/run",
        json={
            "pitch_slides": pitch["pitch_slides"],
            "policy_docs": [
                document.model_dump(mode="json")
                for document in cached_preloaded_policies()
            ],
            "recommended_policy_id": pitch["recommended_policy_id"],
        },
    )
    assert audit.status_code == 200, audit.text
    report = audit.json()
    assert report["status"] in {"PASS", "REVIEW_REQUIRED", "FAIL"}
    assert report["total_claims"] > 0

    audit_export = client.post("/api/v1/audits/export", json=report)
    assert audit_export.status_code == 200, audit_export.text
    assert audit_export.headers["content-type"].startswith("application/pdf")
    assert audit_export.headers["content-disposition"].endswith('.pdf"')
    pdf = PdfReader(BytesIO(audit_export.content))
    assert len(pdf.pages) >= 2
    audit_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "AI-Generated Pitch Verification Report" in audit_text
    assert report["audit_id"] in audit_text

    approve = client.post(
        "/api/v1/audits/decision",
        json={"audit_report": report, "decision": "approve"},
    )
    if report["status"] == "PASS":
        assert approve.status_code == 200
    else:
        assert approve.status_code == 409

    reject = client.post(
        "/api/v1/audits/decision",
        json={
            "audit_report": report,
            "decision": "reject",
            "reason": "Advisor requested a revised recommendation.",
        },
    )
    assert reject.status_code == 200
    assert reject.json()["accepted"] is True


def test_generation_validation_and_upload_errors(monkeypatch):
    monkeypatch.setattr(generation, "_wikipedia_company_summary", no_company_result)

    missing_company = client.post(
        "/api/v1/pitches/generate",
        json={"company_name": "", "policy_ids": ["abhi-activ-one"]},
    )
    assert missing_company.status_code == 422

    missing_policy = client.post(
        "/api/v1/pitches/generate",
        json={"company_name": "Example Company", "policy_ids": []},
    )
    assert missing_policy.status_code == 422

    unknown_policy = client.post(
        "/api/v1/pitches/generate",
        json={"company_name": "Example Company", "policy_ids": ["unknown-policy"]},
    )
    assert unknown_policy.status_code == 422

    unsupported_upload = client.post(
        "/api/v1/documents/parse",
        files={"file": ("policy.txt", b"not a policy document", "text/plain")},
        data={"role": "reference_policy"},
    )
    assert unsupported_upload.status_code == 422


def test_lovable_and_vercel_preview_cors():
    response = client.options(
        "/api/v1/pitches/generate",
        headers={
            "Origin": "https://example-project.lovable.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://example-project.lovable.app"


def test_apply_source_safe_wording_mutates_version_hash_and_claim(monkeypatch):
    monkeypatch.setattr(generation, "_wikipedia_company_summary", no_company_result)
    policy_ids = [policy["policy_id"] for policy in client.get("/api/v1/policies").json()]
    generated = client.post("/api/v1/pitches/generate", json={
        "company_name": "Mutation Proof Company",
        "policy_ids": policy_ids,
        "client_requirements": ["Hospitalisation coverage", "Ambulance support", "Preventive healthcare"],
    })
    assert generated.status_code == 200, generated.text
    old_pitch = generated.json()
    audited = client.post("/api/v1/pitches/audit", json={"pitch": old_pitch, "uploaded_documents": []})
    assert audited.status_code == 200, audited.text
    finding = next((item for item in audited.json()["claim_results"] if item.get("suggested_rewrite")), None)
    assert finding, "Expected at least one source-safe rewrite in the audit"

    applied = client.post(f"/api/v1/pitches/{old_pitch['pitch_id']}/apply-fix", json={"claim_id": finding["claim_id"]})
    assert applied.status_code == 200, applied.text
    state = applied.json()
    new_pitch = state["pitch"]
    current_claims = [claim for slide in new_pitch["pitch_slides"] for claim in slide["claims"] if claim["claim_id"] == finding["claim_id"]]
    new_text = current_claims[0]["presented_value"] if current_claims else "[removed]"
    assert finding["presented_value"] != new_text
    assert old_pitch["pitch_hash"] != new_pitch["pitch_hash"]
    assert old_pitch["pitch_version"] < new_pitch["pitch_version"]
    assert state["audit"] is None
    assert state["approval_enabled"] is False
    assert state["approved"] is False
