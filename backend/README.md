# Marsh AI Pitch Audit Backend

FastAPI implementation of both challenges in `Marsh - Internship Case Study.pdf`. Challenge 1 generates a grounded five-slide policy pitch and Challenge 2 audits its detailed benefit claims before advisor approval.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

## Tests

```powershell
pytest -q
```

## Required function names

The service exposes the exact names used by the case-study brief:

- `generateCompanyProfile(company_name)`
- `generateMarketingPitch()`
- `auditPitchContent(pitch_slides, policy_docs)`

`auditPitchContent` also accepts an optional recommended policy ID so the API can verify that the final recommendation belongs to the preloaded policy catalogue.

## Important behavior

- A pitch must contain exactly five slides.
- Speaker notes are not part of the model or audit.
- Visible policy statements omitted from the structured claim inventory are still detected and audited.
- Uploaded reference policies cannot become the recommendation.
- Unsupported, partial, contradicted, or unverifiable claims produce `REVIEW_REQUIRED`.
- A processing failure produces `AUDIT_INCOMPLETE`, never `PASS`.
- Advisor approval is rejected unless the audit status is `PASS`.
- `POST /api/v1/audits/export` downloads the structured audit report as JSON.

## Challenge 1 endpoints

- `POST /api/v1/company-profile` gathers company information and labels unavailable industry or size data as assumptions.
- `POST /api/v1/documents/parse` parses a PDF or DOCX upload up to 10 MB.
- `POST /api/v1/pitches/generate` generates exactly five slides covering company overview, why choose Marsh, policy benefits mapped to exposures, and one final recommended policy.
- `POST /api/v1/pitches/export` downloads the editable PowerPoint with native charts and visible citations.

The generator remains grounded without an AI key through deterministic retrieval and scoring. If `GEMINI_API_KEY` is absent, the response includes a warning instead of fabricating content.

Optional Gemini configuration:

```powershell
$env:GEMINI_API_KEY="your-key"
$env:GEMINI_MODEL="gemini-2.5-flash"
```

Gemini may improve narrative wording only. Policy facts, figures, comparison values, scoring, citations, and the recommendation remain controlled by deterministic source-document processing.

## Deploy the backend to Render Free

The repository includes a Dockerfile and Render Blueprint. The four
authoritative brochures are bundled read-only in the image. Configure
`DATABASE_URL` with a Supabase pooled Postgres connection so workflows survive
free-service restarts; never commit database or Gemini credentials. See
`../DEPLOYMENT.md` for the complete deployment sequence.
