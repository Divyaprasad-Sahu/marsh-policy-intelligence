from __future__ import annotations

from collections import defaultdict

from .documents import cached_preloaded_policies
from .generation import _requirement_category
from .models import AdvisoryAnalysis, AdvisoryAnalysisRequest, CoverageGap, PolicyEvidence, PolicyScoreBreakdown, RequirementConflict
from .policy_facts import cached_policy_facts


PRIORITY_POINTS = {"MUST_HAVE": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


async def generate_advisory_analysis(request: AdvisoryAnalysisRequest) -> AdvisoryAnalysis:
    selected = set(request.policy_ids)
    documents = [doc for doc in cached_preloaded_policies() if doc.document_id in selected]
    if not documents:
        raise ValueError("Select at least one preloaded policy.")
    facts = [fact for fact in cached_policy_facts() if fact.policy_id in selected]
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for fact in facts:
        grouped[(fact.policy_id, fact.benefit)].append(fact)

    requirements: list[tuple[str, str, int]] = []
    for item in request.requirements.structured_requirements:
        if item.confirmed:
            requirements.append((item.requirement, _requirement_category(item.category or item.requirement), PRIORITY_POINTS[item.priority]))
    for index, item in enumerate(request.requirements.ranked_coverage_priorities):
        category = _requirement_category(item)
        if not any(existing[1] == category for existing in requirements):
            requirements.append((item, category, max(1, 3 - index // 2)))
    if not requirements:
        requirements = [(risk, _requirement_category(risk), 1) for risk in request.company_profile.inferred_risks]

    names = {doc.document_id: doc.product_name or doc.document_name for doc in documents}
    evidence: list[PolicyEvidence] = []
    gaps: list[CoverageGap] = []
    conflicts: list[RequirementConflict] = []
    points = {doc.document_id: 0 for doc in documents}
    reasons: dict[str, list[str]] = defaultdict(list)

    for requirement, category, weight in requirements:
        established = False
        fully_addressed = False
        for doc in documents:
            matches = grouped.get((doc.document_id, category), [])
            fact = next((value for value in matches if value.fact_type != "exclusion"), None)
            qualified = bool(fact and (fact.conditions or fact.optional or fact.additional_premium))
            if fact:
                established = True
                fully_addressed = fully_addressed or not qualified
                points[doc.document_id] += weight if not qualified else max(1, weight - 1)
                reasons[doc.document_id].append(f"{requirement}: {fact.display_value[:150]}")
            evidence.append(PolicyEvidence(
                policy_id=doc.document_id,
                policy_name=names[doc.document_id],
                benefit_name=category,
                presented_value=fact.display_value if fact else "Not established in supplied policy documents.",
                status="SUPPORTED" if fact else "NOT_ESTABLISHED",
                conditions=fact.conditions if fact else [],
                is_optional=fact.optional if fact else None,
                source_references=[fact.source_reference] if fact else [],
            ))
        status = "FULLY_ADDRESSED" if fully_addressed else "PARTIALLY_ADDRESSED" if established else "INSUFFICIENT_EVIDENCE"
        explanation = (
            "Direct policy evidence addresses this requirement without a detected material qualification."
            if status == "FULLY_ADDRESSED"
            else "The available policy fact contains a condition, optional status, or other qualification."
            if status == "PARTIALLY_ADDRESSED"
            else "The supplied policy documents do not establish this requirement."
        )
        gaps.append(CoverageGap(benefit_name=requirement, status=status, explanation=explanation, policy_ids=[doc.document_id for doc in documents]))
        if weight >= 3 and status != "FULLY_ADDRESSED":
            conflicts.append(RequirementConflict(requirement=requirement, severity="MAJOR", explanation=explanation))

    # Every selected policy is assessed against the same confirmed requirements.
    # Display a normalized fit score so advisors never mistake a raw point count
    # (for example, 9 points) for a low percentage.
    maximum_points = max(1, sum(weight for _, _, weight in requirements))
    normalized_scores = {
        policy_id: round((raw_points / maximum_points) * 100)
        for policy_id, raw_points in points.items()
    }
    ranked = sorted(points, key=lambda policy_id: (points[policy_id], len(reasons[policy_id])), reverse=True)
    score_rows = [PolicyScoreBreakdown(
        policy_id=policy_id,
        policy_name=names[policy_id],
        score=normalized_scores[policy_id],
        evidence_count=len(reasons[policy_id]),
        recommendation_reasons=reasons[policy_id][:4],
    ) for policy_id in ranked]
    return AdvisoryAnalysis(
        company_profile=request.company_profile,
        requirements=request.requirements,
        policy_evidence=evidence,
        policy_scores=score_rows,
        recommended_policy_id=ranked[0],
        coverage_gaps=gaps,
        requirement_conflicts=conflicts,
        warnings=["Evidence-fit scores are normalized against the same advisor-confirmed requirements for every selected policy. They are not a guarantee of coverage or pricing."],
        policy_facts=facts,
    )
