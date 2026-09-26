from __future__ import annotations

import json
import os
import re
import uuid

import httpx

from .models import RequirementChatResponse, StructuredRequirement


async def gemini_json(prompt: dict, timeout: float = 35) -> object | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    models = list(dict.fromkeys([
        os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
    ]))
    body = {
        "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.05, "responseMimeType": "application/json"},
    }
    # Treat the supplied timeout as a budget for the entire fallback chain,
    # rather than waiting that long for every model in sequence.
    per_model_timeout = max(3, timeout / max(1, len(models)))
    async with httpx.AsyncClient(timeout=per_model_timeout) as client:
        for model in models:
            try:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": api_key},
                    json=body,
                )
                response.raise_for_status()
                return json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
            except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError):
                continue
    return None


def _normalise_category(text: str) -> str:
    lowered = text.lower()
    mapping = {
        "maternity": "Maternity",
        "ambulance": "Road ambulance",
        "room": "Room rent and ICU",
        "icu": "Room rent and ICU",
        "co-pay": "Co-payment",
        "copay": "Co-payment",
        "waiting": "Waiting period",
        "restore": "Restore or reload",
        "home": "Home care",
        "modern": "Modern treatments",
        "prevent": "Preventive healthcare",
        "wellness": "Preventive healthcare",
        "hospital": "In-patient hospitalisation",
    }
    return next((value for key, value in mapping.items() if key in lowered), text.strip().title())


async def extract_requirements(message: str, existing: list[StructuredRequirement]) -> RequirementChatResponse:
    prompt = {
        "task": "Extract only requirements explicitly stated by an insurance advisor. Merge duplicates with existing requirements.",
        "message": message,
        "existing_requirements": [item.model_dump(mode="json") for item in existing],
        "priorities": ["MUST_HAVE", "HIGH", "MEDIUM", "LOW"],
        "rules": [
            "Do not invent a requirement.",
            "If urgency is unclear use MEDIUM and ask one targeted confirmation question.",
            "Return concise standardized categories.",
            "Return JSON only.",
        ],
        "schema": {"assistant_message": "string", "requirements": [{"category": "string", "requirement": "string", "priority": "MUST_HAVE|HIGH|MEDIUM|LOW"}]},
    }
    result = await gemini_json(prompt)
    if isinstance(result, dict) and isinstance(result.get("requirements"), list):
        merged = {item.category.lower(): item for item in existing}
        for value in result["requirements"]:
            if not isinstance(value, dict) or not value.get("requirement"):
                continue
            category = _normalise_category(str(value.get("category") or value["requirement"]))
            priority = str(value.get("priority") or "MEDIUM")
            if priority not in {"MUST_HAVE", "HIGH", "MEDIUM", "LOW"}:
                priority = "MEDIUM"
            merged[category.lower()] = StructuredRequirement(
                requirement_id=merged.get(category.lower(), StructuredRequirement(requirement_id=str(uuid.uuid4()), category=category, requirement=str(value["requirement"]), priority=priority, source="advisor_chat")).requirement_id,
                category=category,
                requirement=str(value["requirement"]).strip(),
                priority=priority,
                source="advisor_chat",
                confirmed=False,
            )
        return RequirementChatResponse(
            assistant_message=str(result.get("assistant_message") or "Please confirm the captured requirements."),
            requirements=list(merged.values()),
            needs_confirmation=True,
            semantic_processing_available=True,
        )

    category = _normalise_category(message)
    priority = "MUST_HAVE" if re.search(r"must|essential|extremely important", message, re.I) else "HIGH" if re.search(r"important|prefer|strong", message, re.I) else "MEDIUM"
    fallback = StructuredRequirement(requirement_id=str(uuid.uuid4()), category=category, requirement=message.strip(), priority=priority, source="advisor_chat", confirmed=False)
    merged = [item for item in existing if item.category.lower() != category.lower()] + [fallback]
    return RequirementChatResponse(
        assistant_message="Semantic processing could not be completed. Review and confirm this interpretation manually.",
        requirements=merged,
        needs_confirmation=True,
        semantic_processing_available=False,
    )


async def semantic_audit_batch(items: list[dict]) -> dict[str, dict] | None:
    if not items:
        return {}
    prompt = {
        "task": "Audit each client-facing insurance claim against only its supplied policy evidence.",
        "items": items,
        "allowed_verdicts": ["VERIFIED", "VERIFIED_WITH_QUALIFICATION", "REVIEW_REQUIRED", "CONTRADICTED", "UNSUPPORTED"],
        "rules": [
            "Benefit identity must match. Preventive health check-up is not hospitalisation or modern treatment.",
            "Domiciliary hospitalisation is not emergency ambulance.",
            "Domiciliary hospitalisation may support the client-facing category Home care only when the supplied evidence explicitly names domiciliary hospitalisation. When it does, retain that exact policy term in the reason and do not treat it as evidence for unrelated benefits.",
            "A supporting clause does not cancel a material waiting period, exclusion, optional status, or additional premium.",
            "Do not treat PIN codes, phone numbers, UINs, dates, registration IDs, or page numbers as benefit limits.",
            "Policy evidence is the only authority. Return JSON only.",
            "Return VERIFIED, not VERIFIED_WITH_QUALIFICATION, when the claim accurately states the benefit and explicitly says it is subject to the cited policy terms, selected variant, eligibility and limits, unless a specific omitted condition would materially change the claim.",
            "A concise duration statement such as pre-hospitalisation 60 days or post-hospitalisation 180 days is verified when the supplied evidence states that same duration.",
            "Do not contradict a claim merely because it is concise; use CONTRADICTED only when it conflicts with the evidence.",
        ],
        "schema": {"results": [{"claim_id": "string", "verdict": "allowed verdict", "reason": "string", "important_qualification": "string|null", "suggested_rewrite": "string|null"}]},
    }
    # A release gate must fail safely but remain responsive.  If no semantic
    # provider responds within this bounded fallback budget, callers mark the
    # claim for review instead of leaving the advisor waiting indefinitely.
    result = await gemini_json(prompt, timeout=18)
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        return None
    return {str(item.get("claim_id")): item for item in result["results"] if isinstance(item, dict) and item.get("claim_id")}
