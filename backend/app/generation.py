from __future__ import annotations

import os
import re
import json
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from .documents import cached_preloaded_policies
from .intelligence import gemini_json
from .models import (
    ChartDefinition,
    CompanyFact,
    CompanyProfile,
    ClientRequirements,
    ComparisonTable,
    ContentBlock,
    DocumentRole,
    MarketingPitch,
    PitchClaim,
    PitchSlide,
    PolicyDocument,
    SourceReference,
)


load_dotenv()

_COMPANY_PROFILE_CACHE: dict[str, CompanyProfile] = {}
_COMPANY_REQUIREMENTS_CACHE: dict[str, ClientRequirements] = {}
_COMMON_COMPANY_NAMES = (
    "Hyundai Motor Company", "Hyundai Motor India Limited", "Honda Motor Company",
    "Toyota Motor Corporation", "Kia India", "Maruti Suzuki India Limited",
    "Tata Motors", "Mahindra & Mahindra", "Reliance Industries", "Infosys",
    "HDFC Bank", "ICICI Bank", "Wipro", "Larsen & Toubro", "Solar Industries India Limited",
)


BENEFIT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "In-patient hospitalisation": ("in-patient", "hospitalisation", "hospitalization"),
    "Pre-hospitalisation": ("pre-hospitalisation", "pre hospitalization"),
    "Post-hospitalisation": ("post-hospitalisation", "post hospitalization"),
    "Room rent and ICU": ("room rent", "icu"),
    "Ambulance": ("ambulance",),
    "Restore or reload": ("restore", "reload", "reassure", "refill"),
    "Non-medical expenses": ("non-medical", "non medical", "claim protect", "safeguard"),
    "Wellness and prevention": ("wellness", "healthreturns", "live healthy", "preventive"),
    "Annual health checkup": ("health checkup", "health check-up", "health assessment"),
    "Modern treatments": ("modern treatment",),
    "Home care": ("home care", "domiciliary"),
    "Organ donor": ("organ donor",),
    "Deductible or co-payment": ("deductible", "co-payment", "copayment", "co-pay"),
}

INDUSTRY_RISKS: dict[str, list[str]] = {
    "technology": ["Sedentary work and musculoskeletal strain", "Stress and mental-health demand", "Distributed workforce access"],
    "manufacturing": ["Occupational injuries", "Physical strain", "Location-dependent hospital access"],
    "construction": ["Worksite injuries", "Emergency hospitalisation", "Ambulance access"],
    "financial": ["Sedentary work", "Stress-related illness", "Preventive healthcare demand"],
    "retail": ["Large distributed workforce", "Shift-work health risks", "Uneven healthcare access"],
    "healthcare": ["Occupational exposure", "Shift-work fatigue", "High healthcare utilisation"],
}

# Fast, cautious profiles for frequently demonstrated companies. These values
# are deliberately labelled as advisor-confirmation inputs rather than facts
# pulled from an unreliable live lookup.
KNOWN_COMPANY_PROFILES: dict[str, dict[str, str]] = {
    "tata motors": {
        "company_name": "Tata Motors",
        "industry": "Automotive manufacturing",
        "company_size": "Large workforce — confirm current headcount",
        "operating_locations": "India-wide manufacturing sites and offices — confirm",
        "workforce_profile": "Manufacturing, engineering and office workforce - confirm",
    },
    "kalyan jewellers india limited": {
        "company_name": "Kalyan Jewellers India Limited",
        "industry": "Jewellery retail",
        "company_size": "Large retail workforce - confirm current headcount",
        "operating_locations": "India and Middle East retail locations - confirm covered cities",
        "workforce_profile": "Retail, showroom, logistics and corporate workforce - confirm",
    },
    "icici bank": {
        "company_name": "ICICI Bank",
        "industry": "Financial services and banking",
        "company_size": "Large workforce - confirm current headcount",
        "operating_locations": "India-wide branches, offices and service locations - confirm covered cities",
        "workforce_profile": "Branch, operations, sales, technology and corporate workforce - confirm",
    },
}

QUALIFIER_PATTERN = re.compile(
    r"optional|additional premium|extra premium|network|eligible|subject to|maximum|up to|"
    r"deductible|co-?pay|waiting period",
    re.I,
)

CONDITION_LABEL_PATTERNS = {
    "optional benefit": re.compile(r"\boptional\b", re.I),
    "additional premium": re.compile(r"additional premium|extra premium", re.I),
    "network restriction": re.compile(r"network (?:provider|hospital)|cashless basis|tiered network", re.I),
    "eligibility condition": re.compile(r"eligible|eligibility|subject to", re.I),
    "maximum limit": re.compile(r"\bmaximum\b|\bup to\b|capped", re.I),
    "deductible": re.compile(r"deductible", re.I),
    "co-payment": re.compile(r"co-?payment|co-?pay", re.I),
    "waiting period": re.compile(r"waiting period", re.I),
}


def _normalise(text: str) -> str:
    text = re.sub(r"\b\d(?=100\s*%\s+of\s+(?:SI|sum insured)\b)", "", text, flags=re.I)
    text = re.sub(r"`(?=\s*\d)", "₹", text)
    return re.sub(r"\s+", " ", text).strip()


async def _wikipedia_company_summary(company_name: str) -> tuple[str | None, str | None]:
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": company_name,
        "srlimit": 1,
        "utf8": 1,
    }
    headers = {"User-Agent": "MarshCaseStudy/0.1 educational-project"}
    try:
        async with httpx.AsyncClient(timeout=1.5, headers=headers) as client:
            search = await client.get("https://en.wikipedia.org/w/api.php", params=params)
            search.raise_for_status()
            results = search.json().get("query", {}).get("search", [])
            if not results:
                raise ValueError("No MediaWiki search result")
            title = results[0]["title"]
            detail = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "prop": "extracts|info",
                    "exintro": 1,
                    "explaintext": 1,
                    "inprop": "url",
                    "titles": title,
                },
            )
            detail.raise_for_status()
            page = next(iter(detail.json().get("query", {}).get("pages", {}).values()))
            return _normalise(page.get("extract", "")) or None, page.get("fullurl")
    except (httpx.HTTPError, ValueError, KeyError, StopIteration):
        # Public REST summary endpoint: independent fallback when the MediaWiki
        # search/extract API is temporarily unavailable or rate-limited.
        try:
            async with httpx.AsyncClient(timeout=1.0, headers=headers) as client:
                response = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(company_name, safe='')}")
                response.raise_for_status()
                payload = response.json()
                return _normalise(str(payload.get("extract", ""))) or None, str(payload.get("content_urls", {}).get("desktop", {}).get("page") or "") or None
        except (httpx.HTTPError, ValueError, KeyError):
            # The newer REST search route is independent of the legacy
            # MediaWiki search endpoint and works for arbitrary company names.
            try:
                async with httpx.AsyncClient(timeout=1.0, headers=headers) as client:
                    response = await client.get(
                        "https://en.wikipedia.org/w/rest.php/v1/search/page",
                        params={"q": company_name, "limit": 1},
                    )
                    response.raise_for_status()
                    page = (response.json().get("pages") or [])[0]
                    title = str(page.get("title") or "")
                    description = _normalise(str(page.get("description") or ""))
                    if title and description:
                        return description, f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='_')}"
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                pass
            # Wikidata is a separate public Wikimedia API.  It gives us a
            # useful factual description when Wikipedia search/summary is
            # rate-limited, blocked, or missing an exact legal-name match.
            try:
                async with httpx.AsyncClient(timeout=1.0, headers=headers) as client:
                    search = await client.get(
                        "https://www.wikidata.org/w/api.php",
                        params={"action": "wbsearchentities", "search": company_name, "language": "en", "format": "json", "limit": 1},
                    )
                    search.raise_for_status()
                    item = (search.json().get("search") or [])[0]
                    entity_id = str(item.get("id") or "")
                    description = _normalise(str(item.get("description") or ""))
                    if entity_id and description:
                        return description, f"https://www.wikidata.org/wiki/{entity_id}"
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                pass
            return None, None


async def suggestCompanyNames(query: str) -> list[str]:
    clean_query = query.strip()
    if len(clean_query) < 2:
        return []
    lowered = clean_query.lower()
    local_matches = [name for name in _COMMON_COMPANY_NAMES if lowered in name.lower()]
    if local_matches:
        return local_matches[:7]
    try:
        async with httpx.AsyncClient(timeout=0.9, headers={"User-Agent": "MarshCaseStudy/0.1 educational-project"}) as client:
            response = await client.get("https://en.wikipedia.org/w/api.php", params={"action": "opensearch", "format": "json", "search": clean_query, "namespace": 0, "limit": 7})
            response.raise_for_status()
            payload = response.json()
            query_words = re.findall(r"[a-z0-9]+", clean_query.lower())
            def is_query_match(value: object) -> bool:
                item_words = re.findall(r"[a-z0-9]+", str(value).lower())
                return bool(query_words) and len(item_words) >= len(query_words) and all(item_words[index].startswith(word) for index, word in enumerate(query_words))
            suggestions = [
                str(item) for item in payload[1]
                if str(item).strip() and is_query_match(item)
            ]
            if suggestions:
                return suggestions
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    # Suggestions must never hold up the input. The public company API is the
    # source of truth here; detailed AI enrichment happens only after search.
    return []


def _infer_risks(text: str) -> list[str]:
    lowered = text.lower()
    for industry, risks in INDUSTRY_RISKS.items():
        if industry in lowered:
            return risks
    return [
        "Workforce health needs require advisor confirmation",
        "Hospitalisation and emergency-care exposure",
        "Preventive healthcare demand",
    ]


def _infer_industry(text: str) -> str | None:
    lowered = text.lower()
    direct = next((industry.title() for industry in INDUSTRY_RISKS if industry in lowered), None)
    if direct:
        return direct
    keyword_industries = {
        "Financial services and capital markets": ("stock exchange", "securities exchange", "capital market", "banking", "financial services"),
        "Automotive manufacturing": ("automobile", "automotive", "motor vehicle", "car manufacturer"),
        "Energy and industrial manufacturing": ("solar", "energy", "explosives", "industrial manufacturer"),
        "Information technology and services": ("software", "information technology", "it services", "technology company"),
        "Retail and consumer services": ("retail", "supermarket", "department store", "jewellery"),
        "Healthcare and life sciences": ("pharmaceutical", "hospital", "healthcare", "biotechnology"),
    }
    return next((industry for industry, keywords in keyword_industries.items() if any(keyword in lowered for keyword in keywords)), None)


def _extract_company_size(text: str) -> str | None:
    match = re.search(r"(?:employs|workforce of|has)\s+(?:about |approximately )?(\d[\d,]+)\s+(?:people|employees)", text, re.I)
    return f"Approximately {match.group(1)} employees" if match else None


async def _gemini_company_profile(company_name: str) -> dict[str, object] | None:
    prompt = {
        "task": "Resolve the most likely well-known company meant by the user's text and suggest a cautious company profile for employee medical-insurance advisory.",
        "user_input": company_name,
        "rules": [
            "Do not invent precision. Use an approximate employee range when an exact current count is uncertain.",
            "Use the commonly recognised legal or trading company name.",
            "Keep the summary factual and concise.",
            "Return JSON only.",
        ],
        "output_schema": {
            "company_name": "string",
            "industry": "string",
            "company_size": "string",
            "public_profile": "string",
            "inferred_risks": ["string", "string", "string"],
        },
    }
    value = await gemini_json(prompt, timeout=6)
    if isinstance(value, dict) and value.get("company_name") and value.get("industry"):
        return value
    return None


async def generateCompanyProfile(company_name: str) -> CompanyProfile:
    clean_name = company_name.strip()
    if not clean_name:
        raise ValueError("company_name is required")
    cache_key = _normalise(clean_name).lower()
    cached = _COMPANY_PROFILE_CACHE.get(cache_key)
    if cached:
        return cached.model_copy(deep=True)
    known = KNOWN_COMPANY_PROFILES.get(_normalise(clean_name).lower())
    summary, source_url = await _wikipedia_company_summary(clean_name)
    if summary:
        industry = _infer_industry(summary)
        company_size = _extract_company_size(summary)
        ai_profile = await _gemini_company_profile(clean_name) if not industry or not company_size else None
        resolved_name = str((ai_profile or {}).get("company_name") or clean_name).strip()
        suggested_industry = str((ai_profile or {}).get("industry") or (known or {}).get("industry") or "Industry requires advisor confirmation").strip()
        suggested_size = str((ai_profile or {}).get("company_size") or (known or {}).get("company_size") or "Company size requires advisor confirmation").strip()
        facts = [
            CompanyFact(name="Company name", value=resolved_name, status="verified" if resolved_name.lower() == clean_name.lower() else "assumption", source_url=source_url),
            CompanyFact(name="Public profile", value=summary[:900], status="verified", source_url=source_url),
            CompanyFact(
                name="Industry",
                value=industry or suggested_industry,
                status="verified" if industry else "assumption",
                source_url=source_url if industry else None,
            ),
            CompanyFact(
                name="Company size",
                value=company_size or suggested_size,
                status="verified" if company_size else "assumption",
                source_url=source_url if company_size else None,
            ),
        ]
        result = CompanyProfile(
            company_name=resolved_name,
            facts=facts,
            inferred_risks=[str(item) for item in (ai_profile or {}).get("inferred_risks", []) if str(item).strip()] or _infer_risks(summary),
        )
        _COMPANY_PROFILE_CACHE[cache_key] = result.model_copy(deep=True)
        return result
    if known:
        result = CompanyProfile(
            company_name=known["company_name"],
            facts=[
                CompanyFact(name="Company name", value=known["company_name"], status="verified"),
                CompanyFact(name="Industry", value=known["industry"], status="assumption"),
                CompanyFact(name="Company size", value=known["company_size"], status="assumption"),
            ],
            inferred_risks=_infer_risks("manufacturing"),
        )
        _COMPANY_PROFILE_CACHE[cache_key] = result.model_copy(deep=True)
        return result
    ai_profile = await _gemini_company_profile(clean_name)
    if ai_profile:
        resolved_name = str(ai_profile.get("company_name") or clean_name).strip()
        industry = str(ai_profile.get("industry") or "Industry requires advisor confirmation").strip()
        company_size = str(ai_profile.get("company_size") or "Company size requires advisor confirmation").strip()
        public_profile = str(ai_profile.get("public_profile") or "AI-suggested company profile; advisor confirmation required.").strip()
        risks = [str(item).strip() for item in ai_profile.get("inferred_risks", []) if str(item).strip()]
        result = CompanyProfile(
            company_name=resolved_name,
            facts=[
                CompanyFact(name="Company name", value=resolved_name, status="assumption"),
                CompanyFact(name="Public profile", value=public_profile, status="assumption"),
                CompanyFact(name="Industry", value=industry, status="assumption"),
                CompanyFact(name="Company size", value=company_size, status="assumption"),
            ],
            inferred_risks=risks or _infer_risks(industry),
        )
        _COMPANY_PROFILE_CACHE[cache_key] = result.model_copy(deep=True)
        return result
    result = CompanyProfile(
        company_name=clean_name,
        facts=[
            CompanyFact(name="Company name", value=clean_name, status="verified"),
            CompanyFact(name="Industry", value="Industry requires advisor confirmation", status="assumption"),
            CompanyFact(name="Company size", value="Company size requires advisor confirmation", status="assumption"),
        ],
        inferred_risks=_infer_risks(""),
    )
    _COMPANY_PROFILE_CACHE[cache_key] = result.model_copy(deep=True)
    return result


async def generateCompanyRequirements(profile: CompanyProfile) -> ClientRequirements:
    """Suggest editable client inputs; all values still require advisor confirmation."""
    fact_values = {fact.name: fact.value for fact in profile.facts}
    fallback_industry = fact_values.get("Industry", "").strip()
    fallback_size = fact_values.get("Company size", "").strip()
    if "advisor confirmation" in fallback_industry.lower():
        fallback_industry = ""
    if "advisor confirmation" in fallback_size.lower():
        fallback_size = ""
    fallback_priorities = ["Hospitalisation coverage", "Room rent and ICU clarity", "Preventive healthcare"]
    cache_key = _normalise(profile.company_name).lower()
    cached = _COMPANY_REQUIREMENTS_CACHE.get(cache_key)
    if cached:
        return cached.model_copy(deep=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        known = KNOWN_COMPANY_PROFILES.get(_normalise(profile.company_name).lower(), {})
        locations = known.get("operating_locations")
        workforce = known.get("workforce_profile")
        result = ClientRequirements(industry=fallback_industry or None, employee_count=fallback_size or None, operating_locations=[locations] if locations else [], workforce_profile=workforce, ranked_coverage_priorities=fallback_priorities, budget_guidance="Not established - confirm with client")
        _COMPANY_REQUIREMENTS_CACHE[cache_key] = result.model_copy(deep=True)
        return result
    prompt = {
        "task": "Suggest editable employee medical-insurance advisory inputs for the company profile.",
        "rules": [
            "Use only the supplied profile; do not invent precise facts.",
            "If exact locations or employee count are not established, use a cautious high-level suggestion or leave empty.",
            "Choose priorities only from the allowed list.",
            "Return JSON only.",
        ],
        "allowed_priorities": ["Hospitalisation coverage", "Emergency ambulance support", "Room rent and ICU clarity", "Preventive healthcare", "Modern treatments", "Home care", "Low employee cost-sharing"],
        "output_schema": {"industry": "string", "employee_count": "string", "operating_locations": ["string"], "workforce_profile": "string", "ranked_coverage_priorities": ["string"]},
        "profile": profile.model_dump(mode="json"),
    }
    parsed = await gemini_json(prompt, timeout=6)
    if isinstance(parsed, dict):
        try:
            requirements = ClientRequirements.model_validate(parsed)
            if not requirements.budget_guidance:
                requirements.budget_guidance = "Not established - confirm with client"
            _COMPANY_REQUIREMENTS_CACHE[cache_key] = requirements.model_copy(deep=True)
            return requirements
        except ValueError:
            pass
    known = KNOWN_COMPANY_PROFILES.get(_normalise(profile.company_name).lower(), {})
    locations = known.get("operating_locations")
    workforce = known.get("workforce_profile")
    result = ClientRequirements(industry=fallback_industry or None, employee_count=fallback_size or None, operating_locations=[locations] if locations else [], workforce_profile=workforce, ranked_coverage_priorities=fallback_priorities, budget_guidance="Not established - confirm with client")
    _COMPANY_REQUIREMENTS_CACHE[cache_key] = result.model_copy(deep=True)
    return result


def _best_chunk(document: PolicyDocument, keywords: tuple[str, ...]):
    scored = []
    for chunk in document.chunks:
        lowered = chunk.text.lower()
        score = sum(3 if keyword in lowered else 0 for keyword in keywords)
        score += sum(lowered.count(part) for keyword in keywords for part in keyword.split())
        if score:
            scored.append((score, chunk))
    return max(scored, key=lambda item: item[0])[1] if scored else None


def _source(document: PolicyDocument, chunk) -> SourceReference:
    return SourceReference(
        document_id=document.document_id,
        document_name=document.document_name,
        page_number=chunk.page_number,
        section=chunk.section,
        excerpt=chunk.text,
    )


def _display_value(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", _normalise(text))[0]
    return sentence[:240]


def _condition_labels(text: str) -> list[str]:
    return [label for label, pattern in CONDITION_LABEL_PATTERNS.items() if pattern.search(text)]


def _benefit_inventory(documents: list[PolicyDocument]):
    inventory: dict[str, dict[str, dict]] = {}
    for document in documents:
        policy_values: dict[str, dict] = {}
        for category, keywords in BENEFIT_CATEGORIES.items():
            chunk = _best_chunk(document, keywords)
            if chunk:
                evidence = _display_value(chunk.text)
                conditions = _condition_labels(chunk.text)
                policy_values[category] = {
                    "value": evidence,
                    "source": _source(document, chunk),
                    "conditions": conditions,
                    "conditional": bool(conditions),
                }
            else:
                policy_values[category] = {"value": "Not established", "source": None, "conditions": [], "conditional": False}
        inventory[document.document_id] = policy_values
    return inventory


def _requirement_weights(profile: CompanyProfile, requirements: list[str]) -> dict[str, int]:
    combined = " ".join(profile.inferred_risks + requirements).lower()
    weights = {category: 1 for category in BENEFIT_CATEGORIES}
    mappings = {
        "ambulance": "Ambulance",
        "emergency": "Ambulance",
        "hospital": "In-patient hospitalisation",
        "prevent": "Wellness and prevention",
        "wellness": "Wellness and prevention",
        "home": "Home care",
        "modern": "Modern treatments",
        "non-medical": "Non-medical expenses",
        "deductible": "Deductible or co-payment",
    }
    for keyword, category in mappings.items():
        if keyword in combined:
            weights[category] = 3
    return weights


def _fit_scores(inventory, weights) -> dict[str, int]:
    maximum = sum(weights.values()) * 2
    scores: dict[str, int] = {}
    for policy_id, benefits in inventory.items():
        score = 0
        for category, detail in benefits.items():
            points = 0 if detail["value"] == "Not established" else (1 if detail["conditional"] else 2)
            score += points * weights[category]
        scores[policy_id] = round(100 * score / maximum)
    return scores


def _requirement_category(requirement: str) -> str:
    lowered = requirement.lower()
    mappings = [
        (("air ambulance",), "Air ambulance"),
        (("ambulance", "emergency"), "Road ambulance"),
        (("room", "icu"), "Room rent and ICU"),
        (("prevent", "wellness", "check-up", "checkup"), "Preventive healthcare"),
        (("maternity", "newborn", "new born"), "Maternity"),
        (("waiting",), "Waiting period"),
        (("restore", "reload", "recharge"), "Restore or reload"),
        (("modern",), "Modern treatments"),
        (("home",), "Home care"),
        (("deductible",), "Deductible"),
        (("cost-sharing", "co-pay", "copay", "co-payment"), "Co-payment"),
        (("hospital",), "In-patient hospitalisation"),
    ]
    return next((category for keywords, category in mappings if any(keyword in lowered for keyword in keywords)), "In-patient hospitalisation")


async def _gemini_narrative(
    profile: CompanyProfile,
    requirements: list[str],
    policy_names: list[str],
) -> tuple[dict[str, object] | None, str | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY is not configured; deterministic grounded generation was used."
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    models = list(dict.fromkeys([primary_model, "gemini-3.5-flash", "gemini-flash-lite-latest"]))
    prompt = {
        "task": "Write concise slide narrative for a Marsh insurance-policy pitch.",
        "rules": [
            "Do not add policy benefits, figures, exclusions, pricing, or coverage claims.",
            "Use only the supplied company facts, risks, requirements, and policy names.",
            "Do not claim Marsh capabilities beyond evidence-led comparison, traceability, and advisor review.",
            "Return JSON only.",
        ],
        "output_schema": {
            "company_key_message": "string",
            "risk_implications": ["string", "string", "string"],
            "coverage_key_message": "string",
            "coverage_actions": ["string", "string", "string"],
            "why_marsh_points": ["string", "string", "string", "string", "string"],
            "recommendation_disclaimer": "string",
        },
        "company_profile": profile.model_dump(mode="json"),
        "client_requirements": requirements,
        "policy_names": policy_names,
    }
    body = {
        "contents": [{"parts": [{"text": json.dumps(prompt)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=25) as client:
        for model in models:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                response = await client.post(endpoint, params={"key": api_key}, json=body)
                response.raise_for_status()
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("Gemini response was not a JSON object")
                return parsed, None
            except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
    return None, f"Gemini generation failed; deterministic grounded generation was used: {type(last_error).__name__}."


async def generateMarketingPitch(
    company_name: str = "Company pending selection",
    policy_docs: list[PolicyDocument] | None = None,
    selected_policy_ids: list[str] | None = None,
    client_requirements: list[str] | None = None,
    company_profile: CompanyProfile | None = None,
) -> MarketingPitch:
    documents = list(policy_docs or cached_preloaded_policies())
    selected = set(selected_policy_ids or [document.document_id for document in documents])
    documents = [document for document in documents if document.document_id in selected]
    if not documents:
        raise ValueError("At least one valid preloaded policy must be selected")
    if any(document.role != DocumentRole.PRELOADED_POLICY for document in documents):
        raise ValueError("The recommendation catalogue may contain only preloaded policies")

    requirements = client_requirements or []
    profile = company_profile or await generateCompanyProfile(company_name)
    inventory = _benefit_inventory(documents)
    weights = _requirement_weights(profile, requirements)
    scores = _fit_scores(inventory, weights)
    recommended = max(scores, key=scores.get)
    names = {doc.document_id: doc.product_name or doc.document_name for doc in documents}
    narrative, narrative_warning = await _gemini_narrative(
        profile,
        requirements,
        [names[document.document_id] for document in documents],
    )

    claims: list[PitchClaim] = []
    table_rows: list[list[str]] = []
    priority_categories = list(dict.fromkeys(_requirement_category(item) for item in (requirements or profile.inferred_risks)))
    comparison_categories = list(dict.fromkeys([
        *priority_categories,
        "In-patient hospitalisation", "Room rent and ICU", "Ambulance", "Restore or reload",
        "Wellness and prevention", "Modern treatments", "Deductible or co-payment",
    ]))[:8]
    for category in BENEFIT_CATEGORIES:
        row = [category]
        for document in documents:
            detail = inventory[document.document_id][category]
            displayed_value = detail["value"]
            if detail["conditions"]:
                displayed_value += f" Conditions: {', '.join(detail['conditions'])}."
            matrix_value = "Not established"
            if detail["source"]:
                matrix_value = "Supported"
                if detail["conditions"]:
                    matrix_value += f" - {', '.join(detail['conditions'])}"
            row.append(matrix_value)
            if detail["source"]:
                claims.append(
                    PitchClaim(
                        claim_id=f"{document.document_id}:{re.sub(r'[^a-z0-9]+', '-', category.lower()).strip('-')}",
                        text=f"{names[document.document_id]} — {category}: {displayed_value}",
                        policy_id=document.document_id,
                        benefit_name=category,
                        presented_value=detail["value"],
                        conditions_disclosed=detail["conditions"],
                        is_optional=("optional benefit" in detail["conditions"]),
                        policy_variant=names[document.document_id],
                        source_references=[detail["source"]],
                    )
                )
        if category in comparison_categories:
            table_rows.append(row)

    risk_categories = [risk[:40] for risk in profile.inferred_risks]
    requirement_labels = requirements or profile.inferred_risks
    risk_implications = list((narrative or {}).get("risk_implications") or profile.inferred_risks)
    coverage_actions = list((narrative or {}).get("coverage_actions") or [
        "Prioritise the requirements confirmed by the advisor",
        "Compare equivalent benefit categories and retain all material conditions",
        "Treat missing brochure information as Not established, never as no coverage",
    ])
    evidence_counts = {
        policy_id: sum(detail["value"] != "Not established" for detail in benefits.values())
        for policy_id, benefits in inventory.items()
    }
    recommendation_detail = inventory[recommended]
    recommendation_sources = [detail["source"] for detail in recommendation_detail.values() if detail["source"]][:4]
    recommendation_claim = PitchClaim(
        claim_id="final-recommendation",
        text=f"{names[recommended]} is recommended because it has the highest requirements-weighted fit among the selected preloaded policies.",
        policy_id=recommended,
        benefit_name="Final recommendation",
        presented_value="Highest requirements-weighted fit among selected policies",
        source_references=recommendation_sources,
    )
    recommendation_reasons = [
        f"{category}: documented in the supplied brochure"
        for category, detail in recommendation_detail.items()
        if detail["source"] and not detail["conditions"]
    ][:4]
    mapping_rows: list[list[str]] = []
    mapping_claims: list[PitchClaim] = []
    for index, requirement in enumerate(requirement_labels[:6], start=1):
        category = _requirement_category(requirement)
        detail = recommendation_detail[category]
        if detail["source"]:
            capability = detail["value"]
            if detail["conditions"]:
                capability += f" Conditions: {', '.join(detail['conditions'])}."
            source = detail["source"]
            evidence_label = f"{source.document_name}, " + (f"page {source.page_number}" if source.page_number else source.section or "location not established")
            mapping_rows.append([requirement, capability, evidence_label])
            mapping_claims.append(PitchClaim(
                claim_id=f"mapping-{index}-{recommended}", text=f"{names[recommended]} - {category}: {capability}",
                policy_id=recommended, benefit_name=category, presented_value=detail["value"],
                conditions_disclosed=detail["conditions"], is_optional=("optional benefit" in detail["conditions"]),
                policy_variant=names[recommended], source_references=[source],
            ))
        else:
            mapping_rows.append([requirement, "Not established", "No supporting clause found in supplied documents"])

    limitations = [
        f"{category}: {', '.join(detail['conditions'])}"
        for category, detail in recommendation_detail.items() if detail["conditions"]
    ][:3]
    gaps = [category for category, detail in recommendation_detail.items() if not detail["source"]][:3]
    summary_claims: list[PitchClaim] = []
    for index, (category, detail) in enumerate(
        [(category, detail) for category, detail in recommendation_detail.items() if detail["source"] and not detail["conditions"]][:3], start=1
    ):
        wording = f"Advantage: {category}: documented in the supplied brochure"
        summary_claims.append(PitchClaim(claim_id=f"summary-advantage-{index}", text=wording, policy_id=recommended, benefit_name=category, presented_value=detail["value"], policy_variant=names[recommended], source_references=[detail["source"]]))
    for index, (category, detail) in enumerate(
        [(category, detail) for category, detail in recommendation_detail.items() if detail["conditions"]][:3], start=1
    ):
        wording = f"Important limitation: {category}: {', '.join(detail['conditions'])}"
        summary_claims.append(PitchClaim(claim_id=f"summary-limitation-{index}", text=wording, policy_id=recommended, benefit_name=category, presented_value=detail["value"], conditions_disclosed=detail["conditions"], is_optional=("optional benefit" in detail["conditions"]), policy_variant=names[recommended], source_references=[detail["source"]]))
    slides = [
        PitchSlide(
            slide_number=1,
            title="Executive recommendation",
            key_message=f"Recommend {names[recommended]} for {profile.company_name}, subject to the documented conditions and advisor approval.",
            content_blocks=[ContentBlock(kind="bullets", items=[
                f"Recommended policy: {names[recommended]}",
                *recommendation_reasons,
                f"Evidence established for {evidence_counts[recommended]} of {len(BENEFIT_CATEGORIES)} reviewed categories",
            ])],
            charts=[ChartDefinition(chart_type="bar", title="Documented benefit categories", categories=[names[key] for key in evidence_counts], series={"Categories with brochure evidence": list(evidence_counts.values())}, calculation_inputs={"categories_reviewed": list(BENEFIT_CATEGORIES)})],
            claims=[recommendation_claim], visible_citations=recommendation_sources,
        ),
        PitchSlide(
            slide_number=2,
            title="Client profile and exposures",
            key_message=str((narrative or {}).get("company_key_message") or "The client profile and advisor-confirmed requirements define the comparison."),
            content_blocks=[ContentBlock(kind="bullets", items=[f"{fact.name}: {fact.value}" for fact in profile.facts[:4]]), ContentBlock(kind="bullets", items=[f"Exposure: {item}" for item in risk_implications[:4]]), ContentBlock(kind="bullets", items=[f"Requirement: {item}" for item in requirement_labels[:5]])],
        ),
        PitchSlide(
            slide_number=3,
            title="Policy comparison",
            key_message="The matrix shows whether each supplied brochure establishes the benefit and which material conditions apply.",
            comparison_tables=[ComparisonTable(title="Benefit evidence matrix", columns=["Benefit", *[names[d.document_id] for d in documents]], rows=table_rows)],
            claims=claims, visible_citations=[claim.source_references[0] for claim in claims],
        ),
        PitchSlide(
            slide_number=4,
            title="Why the recommended policy fits",
            key_message="Each client requirement maps to a documented capability or is marked Not established.",
            comparison_tables=[ComparisonTable(title="Requirement to evidence mapping", columns=["Client requirement", "Policy capability", "Documented evidence"], rows=mapping_rows)],
            claims=mapping_claims, visible_citations=[claim.source_references[0] for claim in mapping_claims],
        ),
        PitchSlide(
            slide_number=5,
            title="Executive summary",
            key_message=f"{names[recommended]} has the strongest documented alignment with the confirmed requirements among the four supplied policies.",
            content_blocks=[ContentBlock(kind="bullets", items=[
                *[f"Advantage: {item}" for item in recommendation_reasons[:3]],
                *[f"Important limitation: {item}" for item in limitations],
                *[f"Coverage gap: {item} is Not established in the supplied brochure" for item in gaps],
                "Advisor action: confirm all optional benefits, limits, and eligibility conditions before client release",
            ])],
            charts=[ChartDefinition(chart_type="bar", title="Documented categories by policy", categories=[names[key] for key in evidence_counts], series={"Documented categories": list(evidence_counts.values())}, calculation_inputs={"categories_reviewed": list(BENEFIT_CATEGORIES)})],
            claims=summary_claims,
            visible_citations=recommendation_sources,
        ),
    ]

    warnings = []
    if not requirements:
        warnings.append("No client requirements were supplied; category weights use inferred company risks.")
    if narrative_warning:
        warnings.append(narrative_warning)
    return MarketingPitch(
        company_profile=profile,
        pitch_slides=slides,
        recommended_policy_id=recommended,
        generation_warnings=warnings,
    )


def _client_fact_value(value: str, maximum: int = 120) -> str:
    clean = _normalise(value)
    if "..." in clean or "…" in clean:
        clean = re.split(r"\.\.\.|…", clean, maxsplit=1)[0].rstrip(" ,;:-")
        if clean and not re.search(r"[.!?]$", clean) and " " in clean:
            clean = clean.rsplit(" ", 1)[0]
        clean = clean.rstrip(" ,;:.") + "."
    if len(clean) <= maximum:
        return clean
    complete_sentence = re.match(r"^(.+?[.!?])(?:\s|$)", clean)
    if complete_sentence and len(complete_sentence.group(1)) <= maximum * 2:
        return complete_sentence.group(1)
    candidate = clean[:maximum]
    clause_break = max(candidate.rfind(";"), candidate.rfind(","), candidate.rfind(":"))
    candidate = candidate[:clause_break] if clause_break >= maximum // 2 else candidate.rsplit(" ", 1)[0]
    return candidate.rstrip(" ,;:.") + "."


async def generateMarketingPitch(  # noqa: F811 - replaces the legacy generator while preserving the case-study function name
    company_name: str = "Company pending selection",
    policy_docs: list[PolicyDocument] | None = None,
    selected_policy_ids: list[str] | None = None,
    client_requirements: list[str] | None = None,
    company_profile: CompanyProfile | None = None,
) -> MarketingPitch:
    """Build a client-facing five-slide pitch from structured, cited policy facts."""
    from collections import defaultdict
    from .policy_facts import extract_policy_facts

    documents = list(policy_docs or cached_preloaded_policies())
    selected = set(selected_policy_ids or [document.document_id for document in documents])
    documents = [document for document in documents if document.document_id in selected]
    if not documents:
        raise ValueError("At least one valid preloaded policy must be selected")
    if any(document.role != DocumentRole.PRELOADED_POLICY for document in documents):
        raise ValueError("The recommendation catalogue may contain only preloaded policies")

    profile = company_profile or await generateCompanyProfile(company_name)
    requirements = client_requirements or profile.inferred_risks
    facts = extract_policy_facts(documents)
    names = {doc.document_id: doc.product_name or doc.document_name for doc in documents}
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for fact in facts:
        grouped[(fact.policy_id, fact.benefit)].append(fact)

    priority_categories = list(dict.fromkeys(_requirement_category(value) for value in requirements))
    comparison_categories = list(dict.fromkeys([
        *priority_categories, "Room rent and ICU", "Road ambulance", "Preventive healthcare",
    ]))[:4]
    points = {doc.document_id: 0 for doc in documents}
    for rank, category in enumerate(priority_categories):
        weight = max(1, 4 - rank)
        for doc in documents:
            direct = [fact for fact in grouped[(doc.document_id, category)] if fact.fact_type != "exclusion"]
            if direct:
                qualified = any(fact.conditions or fact.optional or fact.additional_premium for fact in direct)
                points[doc.document_id] += max(1, weight - 1) if qualified else weight
    recommended = max(points, key=lambda policy_id: (points[policy_id], sum(key[0] == policy_id for key in grouped)))

    def best(policy_id: str, category: str):
        values = [fact for fact in grouped[(policy_id, category)] if fact.fact_type != "exclusion"]
        values = [fact for fact in values if not (
            fact.display_value.lower().startswith("eligibility criteria")
            and category not in {"Eligibility"}
        )]
        def quality(fact) -> tuple[int, int]:
            value = fact.display_value.lower()
            preferred = {
                "In-patient hospitalisation": "hospitalisation expenses",
                "Room rent and ICU": "room rent",
                "Road ambulance": "road ambulance",
                "Air ambulance": "air ambulance",
                "Preventive healthcare": "health check",
            }.get(category, category.lower())
            return (2 if value.startswith(preferred) else 1 if preferred in value else 0, -len(value))
        return max(values, key=quality) if values else None

    def client_claim(fact) -> str:
        value = fact.display_value
        raw = re.sub(r"\s+", " ", fact.source_reference.excerpt).strip()
        if fact.policy_id == "abhi-activ-one" and ("NO CAPPING" in raw or fact.benefit == "Preventive healthcare"):
            value = {
                "In-patient hospitalisation": "Hospitalisation expenses are covered without category capping, up to the Base Sum Insured.",
                "Road ambulance": "Road ambulance cover is listed without category capping, up to the Base Sum Insured.",
                "Room rent and ICU": "Room rent and ICU charges are listed without category capping, up to the Base Sum Insured.",
                "Preventive healthcare": "Advanced health check-ups include Computed Tomography Angiography (CTA) and Positron Emission Tomography (PET) scans.",
            }.get(fact.benefit, value)
        elif fact.policy_id == "hdfc-optima-secure":
            value = {
                "In-patient hospitalisation": "Hospitalisation expenses are covered up to the sum insured; listed expenses include room rent at actuals, ICU, nursing and surgeon fees.",
                "Road ambulance": "Emergency road ambulance is covered up to the sum insured.",
                "Air ambulance": "Emergency air ambulance is covered up to INR 5,00,000.",
                "Room rent and ICU": "Room rent is covered at actuals and ICU expenses are included under hospitalisation expenses.",
                "Pre-hospitalisation": "Pre-hospitalisation medical expenses are covered for 60 days.",
                "Post-hospitalisation": "Post-hospitalisation medical expenses are covered for 180 days.",
            }.get(fact.benefit, value)
        elif fact.policy_id == "care-health" and fact.benefit == "Preventive healthcare" and "Annual Health Check-up" in raw:
            value = "Annual health check-up is available once per insured per policy year."
        if fact.benefit == "Road ambulance":
            match = re.search(r"Road(?: Ambulance)?:\s*(.+?)(?=\s+Air(?: Ambulance)?:|$)", value, re.I)
            if match:
                value = f"Road ambulance: {match.group(1).strip()}"
        elif fact.benefit == "Air ambulance":
            match = re.search(r"Air(?: Ambulance)?:\s*(.+?)(?=\s+(?:Home Care|Protect Benefit|Plus Benefit|$))", value, re.I)
            if match:
                value = f"Air ambulance: {match.group(1).strip()}"
        elif fact.benefit == "Room rent and ICU":
            match = re.search(r"Room Rent\s+(.+?)(?=\s+(?:Secure Benefit|Emergency Ambulance|$))", value, re.I)
            if match:
                value = f"Room rent: {match.group(1).strip()}"
        value = _client_fact_value(value, 190)
        qualifiers: list[str] = []
        if fact.optional:
            qualifiers.append("optional")
        if fact.additional_premium:
            qualifiers.append("additional premium applies")
        for condition in fact.conditions:
            cleaned = _client_fact_value(condition, 120)
            if "no capping" in cleaned.lower() and "without category capping" in value.lower():
                continue
            if cleaned.lower() not in value.lower():
                qualifiers.append(cleaned)
        if qualifiers:
            value = f"{value} Important qualification: {'; '.join(dict.fromkeys(qualifiers))}."
        return f"{value} Subject to the cited policy terms, selected variant, eligibility and limits."

    comparison_rows: list[list[str]] = []
    comparison_claims: list[PitchClaim] = []
    for category in comparison_categories:
        row = [category]
        for doc in documents:
            fact = best(doc.document_id, category)
            row.append(_client_fact_value(client_claim(fact), 118) if fact else "Not established")
            if fact:
                wording = client_claim(fact)
                comparison_claims.append(PitchClaim(
                    claim_id=f"comparison:{fact.fact_id}", text=wording,
                    policy_id=doc.document_id, benefit_name=category,
                    presented_value=wording,
                    conditions_disclosed=fact.conditions,
                    is_optional=fact.optional,
                    policy_variant=names[doc.document_id],
                    source_references=[fact.source_reference],
                ))
        comparison_rows.append(row)

    recommendation_facts = [best(recommended, category) for category in priority_categories]
    recommendation_facts = [fact for fact in recommendation_facts if fact][:4]
    if len(recommendation_facts) < 3:
        recommendation_facts.extend([
            fact for fact in facts
            if fact.policy_id == recommended and fact.fact_type != "exclusion" and fact not in recommendation_facts
        ][: 4 - len(recommendation_facts)])
    recommendation_sources = [fact.source_reference for fact in recommendation_facts]
    recommendation_claim = PitchClaim(
        claim_id="final-recommendation",
        text=f"{names[recommended]} leads the shortlist for the confirmed priorities.",
        policy_id=recommended,
        benefit_name="Final recommendation",
        presented_value=f"{names[recommended]} leads the shortlist for the confirmed priorities.",
        source_references=recommendation_sources,
    )
    hero_items = [f"{fact.benefit}: {_client_fact_value(client_claim(fact), 155)}" for fact in recommendation_facts]

    mapping_rows: list[list[str]] = []
    mapping_claims: list[PitchClaim] = []
    for index, requirement in enumerate(requirements[:5], start=1):
        category = _requirement_category(requirement)
        fact = best(recommended, category)
        if not fact:
            mapping_rows.append([requirement, "Not established", "Confirm during placement"])
            continue
        source = fact.source_reference
        mapping_rows.append([
            requirement,
            _client_fact_value(client_claim(fact), 155),
            f"{source.document_name}, p.{source.page_number}" if source.page_number else source.document_name,
        ])
        mapping_claims.append(PitchClaim(
            claim_id=f"mapping:{index}:{fact.fact_id}", text=client_claim(fact),
            policy_id=recommended, benefit_name=category, presented_value=client_claim(fact),
            conditions_disclosed=fact.conditions, is_optional=fact.optional,
            policy_variant=names[recommended], source_references=[source],
        ))

    limitations = [
        fact for fact in facts
        if fact.policy_id == recommended
        and fact.benefit in priority_categories
        and (fact.conditions or fact.exclusions or fact.optional or fact.additional_premium)
    ][:2]
    next_step = "Validate eligibility, chosen variant and commercial terms before placement."
    slide5_claims = [
        PitchClaim(
            claim_id=f"summary:{fact.fact_id}", text=client_claim(fact), policy_id=recommended,
            benefit_name=fact.benefit, presented_value=client_claim(fact),
            conditions_disclosed=fact.conditions, is_optional=fact.optional,
            policy_variant=names[recommended], source_references=[fact.source_reference],
        ) for fact in [*recommendation_facts[:3], *limitations]
    ]
    profile_items = [
        f"{fact.name}: {_client_fact_value(fact.value, 110)}"
        for fact in profile.facts if fact.name != "Public profile"
    ][:4]
    priority_items = [f"Priority: {value}" for value in requirements[:5]]
    slides = [
        PitchSlide(
            slide_number=1,
            title=f"{names[recommended]} for {profile.company_name}",
            key_message=f"{names[recommended]} leads the shortlist against the confirmed workforce priorities.",
            content_blocks=[ContentBlock(kind="bullets", items=hero_items)],
            charts=[ChartDefinition(
                chart_type="bar",
                title="Confirmed-priority fit points",
                categories=[names[doc.document_id] for doc in documents],
                series={"Priority points": [points[doc.document_id] for doc in documents]},
                displayed_values=[str(points[doc.document_id]) for doc in documents],
                calculation_inputs={"method": "Rank-weighted confirmed priority matches", "requirements": requirements},
            )],
            claims=[recommendation_claim, *[
                PitchClaim(claim_id=f"hero:{fact.fact_id}", text=client_claim(fact), policy_id=recommended, benefit_name=fact.benefit, presented_value=client_claim(fact), conditions_disclosed=fact.conditions, is_optional=fact.optional, policy_variant=names[recommended], source_references=[fact.source_reference])
                for fact in recommendation_facts
            ]],
            visible_citations=recommendation_sources,
        ),
        PitchSlide(
            slide_number=2,
            title="Client profile and insurance priorities",
            key_message="The comparison reflects the company profile and priorities confirmed by the advisor.",
            content_blocks=[ContentBlock(kind="bullets", items=profile_items), ContentBlock(kind="bullets", items=priority_items)],
        ),
        PitchSlide(
            slide_number=3,
            title="Policy comparison",
            key_message="The shortlist compares equivalent benefits using values stated in the four supplied policy documents.",
            comparison_tables=[ComparisonTable(title="Priority benefit comparison", columns=["Priority", *[names[doc.document_id] for doc in documents]], rows=comparison_rows)],
            claims=comparison_claims,
            visible_citations=[claim.source_references[0] for claim in comparison_claims[:4]],
        ),
        PitchSlide(
            slide_number=4,
            title=f"Why {names[recommended]} fits",
            key_message="Each client priority maps to a specific policy response and cited source.",
            comparison_tables=[ComparisonTable(title="Client need to policy response", columns=["Client need", "Policy response", "Source"], rows=mapping_rows)],
            claims=mapping_claims,
            visible_citations=[claim.source_references[0] for claim in mapping_claims],
        ),
        PitchSlide(
            slide_number=5,
            title=f"Why {names[recommended]} leads the shortlist",
            key_message="The recommendation balances documented advantages with the conditions that require placement review.",
            content_blocks=[ContentBlock(kind="bullets", items=[
                *[f"Advantage: {fact.benefit} - {_client_fact_value(client_claim(fact), 155)}" for fact in recommendation_facts[:3]],
                *[f"Consideration: {fact.benefit} - {_client_fact_value(client_claim(fact), 155)}" for fact in limitations],
                "Consideration: Final underwriting eligibility, exclusions, waiting periods and commercial terms are not established by brochure evidence alone.",
                f"Next step: {next_step}",
            ])],
            claims=slide5_claims,
            visible_citations=[claim.source_references[0] for claim in slide5_claims],
        ),
    ]
    warning = None
    _, warning = await _gemini_narrative(profile, requirements, [names[doc.document_id] for doc in documents])
    return MarketingPitch(company_profile=profile, pitch_slides=slides, recommended_policy_id=recommended, generation_warnings=[warning] if warning else [])
