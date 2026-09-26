import os
from pathlib import Path

import pytest

from app.models import (
    AuditReport,
    AuditStatus,
    CompanyProfile,
    GeneratePitchRequest,
    MarketingPitch,
    PitchSlide,
    RecommendationAudit,
)
from app.workflow_store import WorkflowStore


def make_pitch() -> MarketingPitch:
    return MarketingPitch(
        company_profile=CompanyProfile(company_name="Persistence Test Company"),
        pitch_slides=[
            PitchSlide(slide_number=index, title=f"Slide {index}", key_message="Verified content")
            for index in range(1, 6)
        ],
        recommended_policy_id="hdfc-optima-secure",
    )


def make_request() -> GeneratePitchRequest:
    return GeneratePitchRequest(
        company_name="Persistence Test Company",
        policy_ids=["hdfc-optima-secure"],
    )


def make_audit(pitch: MarketingPitch) -> AuditReport:
    return AuditReport(
        audit_id="audit-persistence-test",
        status=AuditStatus.PASS,
        total_claims=0,
        supported_claims=0,
        partially_supported_claims=0,
        unsupported_claims=0,
        contradicted_claims=0,
        unverifiable_claims=0,
        critical_issues=0,
        claim_results=[],
        slide_results=[],
        chart_checks=[],
        recommendation_check=RecommendationAudit(
            recommended_policy_id="hdfc-optima-secure",
            eligible=True,
            supported=True,
            status="VERIFIED",
            unique_leader=True,
            explanation="Verified by the comparison calculation.",
        ),
        summary="All checks passed.",
        advisor_actions=[],
        pitch_id=pitch.pitch_id,
        audited_pitch_version=pitch.pitch_version,
        audited_pitch_hash=pitch.pitch_hash,
    )


def exercise_store(store: WorkflowStore) -> str:
    pitch = store.save_new(make_pitch(), make_request())
    assert store.get(pitch.pitch_id).pitch.pitch_hash == pitch.pitch_hash

    audit = make_audit(pitch)
    store.save_audit(pitch.pitch_id, audit)
    approved = store.approve(pitch.pitch_id, "Test Advisor")
    assert approved.audit is not None
    assert approved.advisor_decision == "approve"
    assert approved.advisor_name == "Test Advisor"

    updated = store.save_version(pitch.pitch_id, pitch)
    current = store.get(pitch.pitch_id)
    assert updated.pitch_version == pitch.pitch_version + 1
    assert current.audit is None
    assert current.advisor_decision is None
    assert current.advisor_name is None

    rejected = store.reject(pitch.pitch_id, "Test Advisor", "Please revise")
    assert rejected.advisor_decision == "reject"
    assert rejected.rejection_feedback == "Please revise"
    return pitch.pitch_id


def test_json_store_round_trip_survives_reload(tmp_path: Path):
    path = tmp_path / "workflows.json"
    store = WorkflowStore(database_url="", path=path)
    pitch_id = exercise_store(store)

    reloaded = WorkflowStore(database_url="", path=path)
    assert reloaded.get(pitch_id).advisor_decision == "reject"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured")
def test_postgres_store_integration_survives_new_store_instance():
    database_url = os.environ["TEST_DATABASE_URL"]
    store = WorkflowStore(database_url=database_url)
    pitch_id = exercise_store(store)
    try:
        reloaded = WorkflowStore(database_url=database_url)
        assert reloaded.get(pitch_id).advisor_decision == "reject"
    finally:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=10) as connection:
            connection.execute("DELETE FROM pitch_workflows WHERE pitch_id = %s", (pitch_id,))
