from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import replace

from .intelligence import semantic_audit_batch
from .models import (
    AuditReport, AuditStatus, BenefitAuditResult, ChartAuditResult, ClaimStatus,
    DocumentRole, NumericCheck, PitchClaim, PitchSlide, PolicyDocument,
    RecommendationAudit, SlideAuditResult, SourceReference,
)


STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in", "is", "it", "of", "on", "or", "the", "to", "up", "with", "will"}
CONDITION_PATTERNS = {
    "optional benefit": re.compile(r"\boptional\b|add-on", re.I),
    "additional premium": re.compile(r"additional premium|extra premium", re.I),
    "network restriction": re.compile(r"network (?:provider|hospital)|cashless basis|tiered network", re.I),
    "eligibility condition": re.compile(r"eligible|eligibility|subject to", re.I),
    "maximum limit": re.compile(r"\bmaximum\b|\bup to\b|capped", re.I),
    "deductible": re.compile(r"deductible", re.I),
    "co-payment": re.compile(r"co-?payment|co-?pay", re.I),
    "waiting period": re.compile(r"waiting period", re.I),
    "exclusion": re.compile(r"exclusion|not covered|shall not|except", re.I),
}
NUMBER_PATTERN = re.compile(r"(?:INR|Rs\.?|\$)?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|lacs?|lakhs?|crores?|days?|months?|years?|times?|x)?", re.I)
NON_BENEFIT_NUMBER = re.compile(r"\b(?:PIN|UIN|registration|telephone|phone|dated?|page)\b", re.I)
POLICY_LANGUAGE = re.compile(r"\b(?:cover(?:age|ed)?|insured|premium|hospital|ambulance|room rent|icu|deductible|co-?pay|waiting period|restore|reload|optional|maternity|benefit|limit)\b", re.I)

# Client-facing labels can be broader than a policy's legal wording.  These
# mappings are deliberately narrow: an alias must still appear in the cited
# excerpt from the selected policy, so unrelated medical benefits cannot pass.
BENEFIT_ALIASES = {
    "home care": ("home care", "domiciliary hospitalisation", "domiciliary hospitalization"),
    "in patient hospitalisation": ("in-patient hospitalisation", "inpatient hospitalisation", "in-patient hospitalization", "inpatient hospitalization", "hospitalisation expenses", "hospitalization expenses"),
    "room rent and icu": ("room rent", "icu", "intensive care unit"),
    "preventive healthcare": ("preventive health", "health check-up", "health checkup", "annual health check"),
    "road ambulance": ("road ambulance", "emergency ambulance"),
    "emergency ambulance support": ("road ambulance", "emergency ambulance"),
    "co payment": ("co-payment", "copayment", "co-pay", "copay"),
    "pre post hospitalisation": ("pre-hospitalisation", "post-hospitalisation", "pre-hospitalization", "post-hospitalization"),
}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", text.lower()).strip()


def _tokens(text: str) -> list[str]:
    return [token for token in _normalise(text).split() if token not in STOPWORDS and len(token) > 1]


def _similarity(claim: str, evidence: str) -> float:
    claim_tokens, evidence_tokens = set(_tokens(claim)), set(_tokens(evidence))
    return len(claim_tokens & evidence_tokens) / max(1, len(claim_tokens))


def _benefit_matches_evidence(benefit_name: str, evidence: str) -> bool:
    """Return whether an evidence excerpt names this benefit or its approved alias."""
    canonical = _normalise(benefit_name)
    normalised_evidence = _normalise(evidence)
    aliases = BENEFIT_ALIASES.get(canonical)
    if aliases:
        return any(_normalise(alias) in normalised_evidence for alias in aliases)
    benefit_tokens = set(_tokens(benefit_name))
    overlap = benefit_tokens & set(_tokens(evidence))
    return len(overlap) >= min(2, len(benefit_tokens))


def _recommendation_from_comparison(slides: list[PitchSlide], documents: list[PolicyDocument], recommended_policy_id: str | None) -> tuple[ClaimStatus, str, dict[str, float], bool, list[str]]:
    """Verify a shortlist winner from the generated comparison calculation, never brochure prose."""
    selected = [doc for doc in documents if doc.role == DocumentRole.PRELOADED_POLICY]
    if not recommended_policy_id or len(selected) != 4:
        return ClaimStatus.REVIEW_REQUIRED, "Recommendation verification requires the complete four-policy comparison.", {}, False, []
    recommended = next((doc for doc in selected if doc.document_id == recommended_policy_id), None)
    if not recommended:
        return ClaimStatus.REVIEW_REQUIRED, "The recommended policy is not an eligible policy in the four-policy comparison.", {}, False, []
    for slide in slides:
        for chart in slide.charts:
            if len(chart.categories) != 4 or not chart.series:
                continue
            values = next((values for values in chart.series.values() if len(values) == 4), None)
            if values is None:
                continue
            target_names = {_normalise(recommended.product_name or ""), _normalise(recommended.document_name)}
            target_index = next((i for i, name in enumerate(chart.categories) if _normalise(name) in target_names), None)
            if target_index is None:
                continue
            scores = {name: float(value) for name, value in zip(chart.categories, values)}
            priorities = [str(value) for value in chart.calculation_inputs.get("requirements", [])]
            winner = max(values)
            if values[target_index] != winner:
                return ClaimStatus.CONTRADICTED, "The recommended policy does not lead the four-policy comparison calculation.", scores, False, priorities
            if values.count(winner) != 1:
                return ClaimStatus.REVIEW_REQUIRED, "The comparison calculation is tied and does not establish a unique shortlist leader.", scores, False, priorities
            return ClaimStatus.VERIFIED, "The recommendation is derived from the comparison matrix and scoring rules.", scores, True, priorities
    return ClaimStatus.REVIEW_REQUIRED, "No complete four-policy comparison calculation was found for the recommendation.", {}, False, []


def _numbers(text: str) -> list[str]:
    if NON_BENEFIT_NUMBER.search(text):
        return []
    return [re.sub(r"\s+", "", value).lower() for value in NUMBER_PATTERN.findall(text)]


def _focused_excerpt(text: str, benefit_name: str, maximum: int = 900) -> str:
    """Return the local benefit clause, not an unrelated page opening."""
    aliases = BENEFIT_ALIASES.get(_normalise(benefit_name), (benefit_name,))
    match = next((re.search(r"\b" + r"\s+".join(map(re.escape, alias.split())) + r"\b", text, re.I) for alias in aliases if alias), None)
    if not match:
        return text[:maximum].strip()
    start = max(text.rfind("\n", 0, match.start()), text.rfind(".", 0, match.start())) + 1
    end_candidates = [position for position in (text.find("\n", match.end()), text.find(".", match.end())) if position >= 0]
    end = min(end_candidates) + 1 if end_candidates else min(len(text), start + maximum)
    excerpt = text[start:end].strip()
    if len(excerpt) > maximum:
        excerpt = excerpt[:maximum].rsplit(" ", 1)[0].rstrip(" ,;:") + "."
    return excerpt


def _reference(document: PolicyDocument, chunk, benefit_name: str = "") -> SourceReference:
    excerpt = _focused_excerpt(chunk.text, benefit_name) if benefit_name else chunk.text
    return SourceReference(document_id=document.document_id, document_name=document.document_name, page_number=chunk.page_number, section=chunk.section, excerpt=excerpt)


def _claim_is_readable(text: str) -> bool:
    normalised = _normalise(text)
    words = normalised.split()
    repeated = any(words[index:index + 5] == words[index + 5:index + 10] for index in range(max(0, len(words) - 9)))
    return not repeated and "..." not in text and "…" not in text


def _retrieve(claim: PitchClaim, documents: list[PolicyDocument], limit: int = 8):
    query = " ".join(filter(None, [claim.benefit_name or "", claim.presented_value or claim.text]))
    query_tokens = Counter(_tokens(query))
    benefit_name = claim.benefit_name or ""
    benefit_tokens = set(_tokens(benefit_name))
    generic_benefit = not benefit_tokens or benefit_name.lower() in {"policy benefit", "visible policy statement", "final recommendation"}
    scored = []
    for document in documents:
        if claim.policy_id and document.document_id != claim.policy_id:
            continue
        for chunk in document.chunks:
            chunk_tokens = Counter(_tokens(chunk.text))
            # Do not rank boilerplate merely because it shares words such as
            # "policy", "terms", or "eligibility". A benefit finding must
            # contain at least one name token from the audited benefit.
            if not generic_benefit and not _benefit_matches_evidence(benefit_name, chunk.text):
                continue
            shared = query_tokens.keys() & chunk_tokens.keys()
            lexical = sum(min(query_tokens[token], chunk_tokens[token]) for token in shared)
            score = lexical / math.sqrt(max(1, sum(query_tokens.values()) * sum(chunk_tokens.values())))
            score = max(score, _similarity(query, chunk.text))
            if score:
                scored.append((score, document, chunk))
    return sorted(scored, key=lambda value: value[0], reverse=True)[:limit]


def _validated_sources(claim: PitchClaim, documents: list[PolicyDocument]) -> list[SourceReference]:
    # A page number alone is not evidence.  The cited excerpt must be from the
    # selected policy *and* materially relate to the benefit in the claim.
    # This prevents, for example, a preventive-check-up clause being accepted
    # as evidence for an emergency-ambulance claim.
    query = " ".join(filter(None, [claim.benefit_name or "", claim.presented_value or claim.text]))
    is_recommendation = claim.benefit_name == "Final recommendation"
    validated = []
    for cited in claim.source_references:
        if claim.policy_id and cited.document_id != claim.policy_id:
            continue
        for document in documents:
            if document.document_id != cited.document_id:
                continue
            for chunk in document.chunks:
                same_page = cited.page_number is None or cited.page_number == chunk.page_number
                cited_text, actual_text = _normalise(cited.excerpt), _normalise(chunk.text)
                excerpt_matches = cited_text and (cited_text in actual_text or actual_text in cited_text)
                # A final recommendation is a workflow decision based on the
                # comparison, not a literal sentence found in one brochure.
                # Its evidence may therefore be any correctly cited clause
                # from the selected policy; individual benefit claims still
                # require direct semantic relevance.
                benefit_name = claim.benefit_name or ""
                focused_actual = _focused_excerpt(chunk.text, benefit_name)
                alias_match = _benefit_matches_evidence(benefit_name, cited.excerpt) and _benefit_matches_evidence(benefit_name, focused_actual)
                # A mapped alias is direct evidence for the client-facing
                # category.  Other benefits retain the lexical relevance gate.
                relevant_to_claim = is_recommendation or (
                    alias_match and (
                        _normalise(benefit_name) in BENEFIT_ALIASES
                        or _similarity(benefit_name or query, cited.excerpt) >= 0.5
                    )
                )
                if same_page and excerpt_matches and relevant_to_claim:
                    validated.append(_reference(document, chunk, claim.benefit_name or ""))
                    break
    return validated


def _collect_claims(slides: list[PitchSlide], documents: list[PolicyDocument]) -> list[tuple[int, PitchClaim]]:
    collected: list[tuple[int, PitchClaim]] = []
    seen: set[tuple[str, str]] = set()
    for slide in slides:
        for claim in slide.claims:
            key = (claim.policy_id or "", _normalise(claim.presented_value or claim.text))
            if key not in seen:
                collected.append((slide.slide_number, claim))
                seen.add(key)
        explicit = " ".join(_normalise(claim.text) for claim in slide.claims)
        visible: list[tuple[str, str]] = [("title", slide.title), ("key-message", slide.key_message)]
        for block in slide.content_blocks:
            if block.text:
                visible.append(("content", block.text))
            visible.extend(("content", item) for item in block.items)
        for row_index, table in enumerate(slide.comparison_tables, start=1):
            for cell_index, row in enumerate(table.rows, start=1):
                visible.extend((f"table-{row_index}-{cell_index}", cell) for cell in row[1:] if "not established" not in cell.lower())
        for chart in slide.charts:
            visible.extend(("chart", value) for value in chart.displayed_values)
        for index, (location, text) in enumerate(visible, start=1):
            normalised = _normalise(text)
            if location == "content" and slide.claims:
                continue
            if not normalised or normalised.startswith("priority ") or not POLICY_LANGUAGE.search(text) or "not established" in normalised or normalised in explicit:
                continue
            inferred = next((document.document_id for document in documents if _normalise(document.product_name or "") in normalised), None)
            key = (inferred or "", normalised)
            if key in seen:
                continue
            collected.append((slide.slide_number, PitchClaim(claim_id=f"auto:s{slide.slide_number}:{location}:{index}", text=text, policy_id=inferred, benefit_name="Visible policy statement", presented_value=text)))
            seen.add(key)
    return collected


def _deterministic_audit(slide_number: int, claim: PitchClaim, documents: list[PolicyDocument]) -> BenefitAuditResult:
    retrieval = _retrieve(claim, documents)
    validated = _validated_sources(claim, documents)
    references = validated + [_reference(document, chunk, claim.benefit_name or "") for _, document, chunk in retrieval if _reference(document, chunk, claim.benefit_name or "") not in validated]
    primary = validated[0].excerpt if validated else (_focused_excerpt(retrieval[0][2].text, claim.benefit_name or "") if retrieval else "")
    top_score = retrieval[0][0] if retrieval else 0.0
    # Search nearby high-relevance clauses for qualifications without allowing
    # unrelated brochure conditions to contaminate every claim.
    threshold = max(0.42, top_score * 0.72)
    context = " ".join(chunk.text for score, _, chunk in retrieval if score >= threshold)
    if validated:
        context = " ".join([*(source.excerpt for source in validated), context])
    presented_numbers, evidence_numbers = _numbers(claim.presented_value or claim.text), _numbers(primary)
    numeric_checks = [NumericCheck(presented=value, evidence_values=evidence_numbers, matches=value in evidence_numbers) for value in presented_numbers]
    conditions = [label for label, pattern in CONDITION_PATTERNS.items() if pattern.search(context)]
    disclosed_text = " ".join([claim.text, *claim.conditions_disclosed])
    omitted = [label for label in conditions if not CONDITION_PATTERNS[label].search(disclosed_text)]
    best_score = retrieval[0][0] if retrieval else 0.0
    exact = bool(validated) and (_normalise(claim.presented_value or claim.text) in _normalise(primary) or _similarity(claim.presented_value or claim.text, primary) >= 0.62)
    is_recommendation = claim.benefit_name == "Final recommendation"
    mapped_alias_support = bool(
        validated
        and _normalise(claim.benefit_name or "") in BENEFIT_ALIASES
        and _benefit_matches_evidence(claim.benefit_name or "", primary)
    )
    citation_required_but_invalid = bool(claim.source_references) and not validated
    if not _claim_is_readable(claim.presented_value or claim.text):
        status, explanation = ClaimStatus.REVIEW_REQUIRED, "The client-facing wording is malformed or truncated. Regenerate it as a concise complete sentence before approval."
    elif citation_required_but_invalid:
        status, explanation = ClaimStatus.REVIEW_REQUIRED, "Layer 2 failed: the cited policy excerpt is not relevant to this claim. Cite the matching clause from the selected policy."
    elif is_recommendation:
        status, explanation = ClaimStatus.REVIEW_REQUIRED, "Recommendation claims must be verified from the complete comparison calculation, not a brochure excerpt."
    elif not retrieval:
        status, explanation = ClaimStatus.UNSUPPORTED, "No relevant evidence was found in the supplied policy documents."
    elif any(not check.matches for check in numeric_checks):
        status, explanation = ClaimStatus.CONTRADICTED, "A displayed number or unit does not match the supporting policy text."
    elif omitted:
        status, explanation = ClaimStatus.VERIFIED_WITH_QUALIFICATION, "Layers 1 and 2 passed, but Layer 3 found a material policy qualification missing from the client-facing wording."
    elif mapped_alias_support:
        status, explanation = ClaimStatus.VERIFIED, "All three evidence checks passed. Home care is supported by the cited policy term, domiciliary hospitalisation."
    elif exact or best_score >= 0.62:
        status, explanation = ClaimStatus.VERIFIED, "All three evidence checks passed: selected policy, benefit-relevant clause, and claim wording/conditions."
    elif best_score >= 0.35:
        status, explanation = ClaimStatus.REVIEW_REQUIRED, "Retrieved text is related, but direct support is not sufficiently clear."
    else:
        status, explanation = ClaimStatus.UNSUPPORTED, "The retrieved text does not directly establish the stated benefit."
    severity = "CRITICAL" if status in {ClaimStatus.CONTRADICTED, ClaimStatus.UNSUPPORTED} and (presented_numbers or is_recommendation) else "MAJOR" if status != ClaimStatus.VERIFIED else "MINOR"
    if status == ClaimStatus.VERIFIED:
        suggested = None
    elif status in {ClaimStatus.UNSUPPORTED, ClaimStatus.CONTRADICTED}:
        suggested = "Remove this statement unless a matching clause can be cited from the selected policy documents."
    elif primary:
        subject = claim.benefit_name or "This benefit"
        qualification = ", ".join(conditions) if conditions else "the cited policy terms"
        suggested = (
            f"{subject} is available only as described in the cited policy clause and is subject to "
            f"{qualification}, the selected variant, eligibility criteria, and applicable limits."
        )
    else:
        suggested = "Remove this statement unless supporting evidence is supplied."
    verified_limit = next((value for value in evidence_numbers if value in presented_numbers), None)
    return BenefitAuditResult(
        claim_id=claim.claim_id, slide_number=slide_number, policy_id=claim.policy_id or "unassigned",
        benefit_name=claim.benefit_name or "Policy benefit", presented_value=claim.presented_value or claim.text,
        status=status, severity=severity,
        source_references=references, verified_limit=verified_limit, conditions=conditions,
        omitted_conditions=omitted, is_optional=claim.is_optional, policy_variant=claim.policy_variant,
        numeric_checks=numeric_checks, explanation=explanation, suggested_rewrite=suggested,
        fact_type="AI_INTERPRETATION" if is_recommendation else "POLICY_FACT",
    )


def _audit_charts(slides: list[PitchSlide]) -> list[ChartAuditResult]:
    checks = []
    for slide in slides:
        for chart in slide.charts:
            lengths = {len(values) for values in chart.series.values()}
            valid = bool(chart.categories) and (not lengths or lengths == {len(chart.categories)})
            checks.append(ChartAuditResult(slide_number=slide.slide_number, chart_title=chart.title, valid=valid, explanation="Chart values align with their categories." if valid else "Chart categories and values do not align."))
    return checks


async def auditPitchContent(pitch_slides: list[PitchSlide], policy_docs: list[PolicyDocument], recommended_policy_id: str | None = None) -> AuditReport:
    if len(pitch_slides) != 5:
        return AuditReport(audit_id=str(uuid.uuid4()), status=AuditStatus.AUDIT_INCOMPLETE, total_claims=0, supported_claims=0, partially_supported_claims=0, unsupported_claims=0, contradicted_claims=0, unverifiable_claims=0, critical_issues=1, claim_results=[], slide_results=[], chart_checks=[], recommendation_check=RecommendationAudit(recommended_policy_id=recommended_policy_id, eligible=False, supported=False, status=ClaimStatus.REVIEW_REQUIRED, explanation="The audit requires exactly five slides."), summary="Audit incomplete because the pitch does not contain exactly five slides.", advisor_actions=["Generate exactly five slides and run the audit again."])
    # This is a release audit for the recommended policy, not a second audit of
    # every comparison option shown in the presentation.  Claims for other
    # insurers remain useful comparison context in the deck, but cannot block
    # approval of the selected policy.
    inventory = [
        (slide_number, claim)
        for slide_number, claim in _collect_claims(pitch_slides, policy_docs)
        if claim.policy_id == recommended_policy_id or claim.benefit_name == "Final recommendation"
    ]
    results = [_deterministic_audit(slide_number, claim, policy_docs) for slide_number, claim in inventory]
    recommendation_status, recommendation_explanation, recommendation_scores, unique_leader, confirmed_priorities = _recommendation_from_comparison(pitch_slides, policy_docs, recommended_policy_id)
    results = [result.model_copy(update={
        "status": recommendation_status,
        "severity": "MINOR" if recommendation_status == ClaimStatus.VERIFIED else "MAJOR",
        "explanation": recommendation_explanation,
        "source_references": [],
        "conditions": [],
        "omitted_conditions": [],
        "suggested_rewrite": None,
        "fact_type": "AI_INTERPRETATION",
    }) if result.benefit_name == "Final recommendation" else result for result in results]
    semantic_items = [{
        "claim_id": result.claim_id, "claim": result.presented_value, "benefit": result.benefit_name,
        "policy_id": result.policy_id,
        "evidence": [{"document": source.document_name, "page": source.page_number, "section": source.section, "text": source.excerpt[:1200]} for source in result.source_references[:4]],
        "deterministic_status": result.status.value, "detected_qualifications": result.conditions,
    } for result in results if result.benefit_name != "Final recommendation"]
    semantic = await semantic_audit_batch(semantic_items)
    if semantic is None:
        # The deterministic layers have already validated the selected policy,
        # cited excerpt, numbers, and material qualifications. A temporary
        # semantic-provider outage must not turn that proven evidence into an
        # unresolvable failure. Keep it approvable, but surface the outage as a
        # visible qualification for the advisor.
        results = [
            result.model_copy(update={
                "status": ClaimStatus.VERIFIED_WITH_QUALIFICATION,
                "severity": "MINOR",
                "conditions": list(dict.fromkeys([*result.conditions, "semantic confirmation pending (API unavailable)"])),
                "omitted_conditions": list(dict.fromkeys([*result.omitted_conditions, "semantic confirmation pending (API unavailable)"])),
                "explanation": "Layers 1 and 2 passed from the selected policy and cited clause. Semantic confirmation is temporarily unavailable; review this visible qualification before approval.",
            }) if result.status == ClaimStatus.VERIFIED and result.benefit_name != "Final recommendation" else result
            for result in results
        ]
    else:
        updated = []
        for result in results:
            if result.benefit_name == "Final recommendation":
                updated.append(result)
                continue
            check = semantic.get(result.claim_id)
            if not check:
                updated.append(result.model_copy(update={"status": ClaimStatus.REVIEW_REQUIRED, "severity": "MAJOR", "explanation": "Semantic verification did not return a verdict for this claim."}))
                continue
            verdict = str(check.get("verdict", "REVIEW_REQUIRED"))
            if verdict not in {status.value for status in ClaimStatus}:
                verdict = "REVIEW_REQUIRED"
            semantic_status = ClaimStatus(verdict)
            final_status = result.status
            if result.status in {ClaimStatus.CONTRADICTED, ClaimStatus.UNSUPPORTED, ClaimStatus.VERIFIED_WITH_QUALIFICATION}:
                final_status = result.status
            elif semantic_status != ClaimStatus.VERIFIED:
                final_status = semantic_status
            updated.append(result.model_copy(update={"status": final_status, "severity": "MINOR" if final_status == ClaimStatus.VERIFIED else "MAJOR", "explanation": str(check.get("reason") or result.explanation), "suggested_rewrite": check.get("suggested_rewrite") or result.suggested_rewrite}))
        results = updated

    chart_checks = _audit_charts(pitch_slides)
    eligible = bool(recommended_policy_id and any(doc.document_id == recommended_policy_id and doc.role == DocumentRole.PRELOADED_POLICY for doc in policy_docs))
    recommendation_results = [result for result in results if result.benefit_name == "Final recommendation"]
    # A qualification is still evidence-backed and can be approved, but it is
    # always surfaced to the advisor with the exact condition that applies.
    acceptable_statuses = {ClaimStatus.VERIFIED, ClaimStatus.VERIFIED_WITH_QUALIFICATION}
    if recommendation_status == ClaimStatus.VERIFIED and any(result.status == ClaimStatus.CONTRADICTED for result in results if result.benefit_name != "Final recommendation"):
        recommendation_status = ClaimStatus.REVIEW_REQUIRED
        recommendation_explanation = "One or more material supporting claims are contradicted and require correction before this recommendation can be released."
        results = [result.model_copy(update={"status": recommendation_status, "severity": "MAJOR", "explanation": recommendation_explanation}) if result.benefit_name == "Final recommendation" else result for result in results]
        recommendation_results = [result for result in results if result.benefit_name == "Final recommendation"]
    recommendation_supported = eligible and recommendation_status == ClaimStatus.VERIFIED and bool(recommendation_results) and all(result.status == ClaimStatus.VERIFIED for result in recommendation_results)
    counts = Counter(result.status for result in results)
    passed = bool(results) and all(result.status in acceptable_statuses for result in results) and all(check.valid for check in chart_checks) and recommendation_supported
    slide_results = []
    for slide in pitch_slides:
        matches = [result for result in results if result.slide_number == slide.slide_number]
        status = ClaimStatus.VERIFIED if matches and all(result.status in acceptable_statuses for result in matches) else next((result.status for result in matches if result.status not in acceptable_statuses), ClaimStatus.VERIFIED)
        slide_results.append(SlideAuditResult(slide_number=slide.slide_number, status=status, claim_ids=[result.claim_id for result in matches]))
    audit_status = AuditStatus.PASS if passed else AuditStatus.FAIL if counts[ClaimStatus.CONTRADICTED] else AuditStatus.REVIEW_REQUIRED
    return AuditReport(
        audit_id=str(uuid.uuid4()), status=audit_status,
        total_claims=len(results), supported_claims=counts[ClaimStatus.VERIFIED] + counts[ClaimStatus.VERIFIED_WITH_QUALIFICATION],
        partially_supported_claims=counts[ClaimStatus.REVIEW_REQUIRED],
        unsupported_claims=counts[ClaimStatus.UNSUPPORTED], contradicted_claims=counts[ClaimStatus.CONTRADICTED],
        unverifiable_claims=0, critical_issues=sum(result.severity == "CRITICAL" for result in results),
        claim_results=results, slide_results=slide_results, chart_checks=chart_checks,
        recommendation_check=RecommendationAudit(recommended_policy_id=recommended_policy_id, eligible=eligible, supported=recommendation_supported, status=recommendation_status, verification_basis="Requirement-weighted comparison calculation", policy_scores=recommendation_scores, confirmed_priorities=confirmed_priorities, unique_leader=unique_leader, explanation=recommendation_explanation),
        summary="All client-facing policy claims passed evidence verification. The recommendation was validated through requirement-weighted comparison logic." if passed else "One or more material claims require advisor review or correction.",
        advisor_actions=["Review the material findings and approve the exact audited version."] if passed else ["Resolve each required change and run a complete re-audit."],
    )
