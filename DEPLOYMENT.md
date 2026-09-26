# Free deployment

The production layout uses a Vercel static frontend, a Render Free Docker web
service, and Supabase Free Postgres. Local development continues to persist to
`backend/data/pitch_workflows.json`.

## 1. Supabase

1. Create a free project.
2. Copy the transaction-pooler connection string and require SSL.
3. Save it as Render's `DATABASE_URL`. The backend creates `pitch_workflows`
   automatically on first boot.

## 2. Render

Create a Blueprint from the repository's `render.yaml`. Supply these secrets:

- `DATABASE_URL`
- `GEMINI_API_KEY`
- `GEMINI_MODEL` (a model enabled for the Google AI Studio free tier)
- `ALLOWED_ORIGINS` (the final Vercel origin, with no trailing slash)

The Docker service must remain a single instance. Free services sleep after
inactivity; the frontend health gate handles wake-up delay.

## 3. Vercel

Import the private repository, select `frontend` as the project root, and set:

- `VITE_API_BASE_URL=https://<render-service>.onrender.com`

Deploy once, then update Render's `ALLOWED_ORIGINS` with the production Vercel
origin and redeploy the backend.

## 4. Production smoke test

Run the complete journey: research, verify, analyse, generate, audit, apply a
safe wording change, re-audit, approve, and download both files. Restart the
Render service and confirm the approved workflow still loads.

