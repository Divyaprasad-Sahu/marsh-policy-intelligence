from __future__ import annotations

import re
from functools import lru_cache

from .documents import cached_preloaded_policies
from .models import PolicyDocument, PolicyFact, SourceReference


BENEFIT_PATTERNS: dict[str, tuple[str, ...]] = {
    "In-patient hospitalisation": ("in-patient hospitalization", "in-patient hospitalisation", "inpatient hospitalization", "inpatient hospitalisation", "hospitalisation expenses", "hospitalization expenses"),
    "Room rent and ICU": ("room rent", "icu"),
    "Road ambulance": ("road ambulance",),
    "Air ambulance": ("air ambulance",),
    "Pre-hospitalisation": ("pre hospitalisation", "pre-hospitalisation", "pre hospitalization"),
    "Post-hospitalisation": ("post hospitalisation", "post-hospitalisation", "post hospitalization"),
    "Restore or reload": ("restore benefit", "reload", "reassure", "refill benefit"),
    "Co-payment": ("co-payment", "copayment", "co-pay"),
    "Deductible": ("deductible",),
    "Preventive healthcare": ("preventive health", "health check-up", "health checkup"),
    "Maternity": ("maternity", "new born", "newborn"),
    "Organ donor": ("organ donor",),
    "Home care": ("home care", "domiciliary"),
    "Modern treatments": ("modern treatment",),
    "Non-medical expenses": ("non-medical", "non medical", "claim protect"),
    "Waiting period": ("waiting period",),
    "Eligibility": ("eligibility", "eligible"),
}

QUALIFIER_RE = re.compile(
    r"waiting period|subject to|up to|maximum|limit|optional|add-on|additional premium|"
    r"co-?pay|deductible|network|eligible|exclusion|not covered|except",
    re.I,
)
EXCLUSION_RE = re.compile(r"exclusion|not covered|shall not|except|does not cover", re.I)
NUMBER_RE = re.compile(r"(?:INR|Rs\.?|₹)?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|days?|months?|years?|lakhs?|lacs?|crores?|times?)?", re.I)
NON_BENEFIT_NUMBER_RE = re.compile(r"\b(?:pin|uin|registration|telephone|phone|dated?)\b", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _best_sentence(text: str, patterns: tuple[str, ...]) -> str:
    clean = _clean(re.sub(r"[•\n\r]+", ". ", text))
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\s{2,}", clean) if part.strip()]
    candidates = [part for part in parts if any(pattern in part.lower() for pattern in patterns)]
    primary = patterns[0]
    selected = max(candidates or [clean], key=lambda value: (
        primary in value.lower(),
        value.lower().startswith(primary),
        sum(pattern in value.lower() for pattern in patterns),
        bool(NUMBER_RE.search(value)),
        bool(QUALIFIER_RE.search(value)),
        -len(value),
    ))
    checkup = re.search(r"annual health check-?up(?:\s*\(day\s*1\))?", selected, re.I)
    if checkup and "reassure" in selected.lower():
        selected = f"{checkup.group(0)} is available from day 1."
    return selected[:420]


def _fact_type(sentence: str) -> str:
    lowered = sentence.lower()
    if "waiting period" in lowered:
        return "waiting_period"
    if "co-pay" in lowered or "co-payment" in lowered or "copayment" in lowered:
        return "co_payment"
    if "deductible" in lowered:
        return "deductible"
    if "additional premium" in lowered:
        return "additional_premium"
    if "optional" in lowered or "add-on" in lowered:
        return "optional_benefit"
    if EXCLUSION_RE.search(sentence):
        return "exclusion"
    if NUMBER_RE.search(sentence) and not NON_BENEFIT_NUMBER_RE.search(sentence):
        return "coverage_limit"
    if QUALIFIER_RE.search(sentence):
        return "condition"
    return "coverage"


def _numeric(sentence: str) -> tuple[float | None, str | None]:
    if NON_BENEFIT_NUMBER_RE.search(sentence):
        return None, None
    match = NUMBER_RE.search(sentence)
    if not match:
        return None, None
    token = match.group(0).strip()
    number = re.search(r"\d[\d,]*(?:\.\d+)?", token)
    if not number:
        return None, None
    unit_match = re.search(r"%|days?|months?|years?|lakhs?|lacs?|crores?|times?|INR|Rs\.?|₹", token, re.I)
    return float(number.group(0).replace(",", "")), unit_match.group(0) if unit_match else None


def extract_policy_facts(documents: list[PolicyDocument]) -> list[PolicyFact]:
    facts: list[PolicyFact] = []
    for document in documents:
        policy_name = document.product_name or document.document_name
        for benefit, patterns in BENEFIT_PATTERNS.items():
            matching = [
                chunk for chunk in document.chunks
                if any(pattern in _clean(chunk.text).lower() for pattern in patterns)
            ]
            if not matching:
                continue
            matching.sort(key=lambda chunk: (
                not bool(NUMBER_RE.search(chunk.text)),
                not bool(QUALIFIER_RE.search(chunk.text)),
                len(chunk.text),
            ))
            for index, chunk in enumerate(matching[:2], start=1):
                sentence = _best_sentence(chunk.text, patterns)
                fact_type = _fact_type(sentence)
                number, unit = _numeric(sentence)
                conditions = [sentence] if fact_type in {"condition", "waiting_period", "co_payment", "deductible", "optional_benefit", "additional_premium"} else []
                exclusions = [sentence] if fact_type == "exclusion" else []
                facts.append(PolicyFact(
                    fact_id=f"{document.document_id}:{re.sub('[^a-z0-9]+', '-', benefit.lower()).strip('-')}:{index}",
                    policy_id=document.document_id,
                    policy_name=policy_name,
                    benefit=benefit,
                    fact_type=fact_type,
                    display_value=sentence,
                    numeric_value=number,
                    unit=unit,
                    conditions=conditions,
                    exclusions=exclusions,
                    optional="optional" in sentence.lower() or "add-on" in sentence.lower(),
                    additional_premium="additional premium" in sentence.lower(),
                    source_reference=SourceReference(
                        document_id=document.document_id,
                        document_name=document.document_name,
                        page_number=chunk.page_number,
                        section=chunk.section,
                        excerpt=chunk.text,
                    ),
                ))
    return facts


@lru_cache(maxsize=1)
def cached_policy_facts() -> tuple[PolicyFact, ...]:
    return tuple(extract_policy_facts(list(cached_preloaded_policies())))
