# PharmaDrone AI

PharmaDrone AI is the standalone customer-facing SaaS application. It is separate from the PharmaTune Streamlit intelligence and administration applications, but it uses the same PharmaDrone services, ordered migrations and PostgreSQL-backed intelligence database in production.

Local SQLite development only:

```bash
cp apps/pharmadrone-ai/.env.example .env
python -m pip install -r requirements.txt
python -m uvicorn pharmadrone_ai.app:app --reload --port 8000
```

Open `http://localhost:8000`, register a workspace, and use a starter prompt. Deterministic lead, pitch and evidence workflows work without an LLM key.

Containerised PostgreSQL development:

```bash
docker compose -f apps/pharmadrone-ai/docker-compose.yml up --build
```

Health check:

```bash
curl http://localhost:8000/api/health
```

The app applies additive Migration 21 through the existing ordered migration runner. Never point a development instance at production credentials.

Production must set the repository's existing `DATABASE_URL` to the same
PostgreSQL-backed PharmaDrone intelligence infrastructure used by PharmaTune,
plus a unique `PHARMADRONE_AI_AUTH_SECRET` of at least 32 characters. Do not set
`DATABASE_BACKEND=sqlite` in production. Existing connector variables including
`OPENAI_API_KEY`, `OPENAI_MODEL`, `TAVILY_API_KEY`,
`EPO_OPS_CLIENT_ID` and `EPO_OPS_CLIENT_SECRET` remain server-side and keep
their established names.

The normal product test is: register or log in, start a chat, generate bounded
BD leads, build a company pitch, inspect source links and limitations, save the
lead/report, export Markdown and log out. PharmaDrone AI does not invent
evidence and does not provide legal, FTO, patent-validity, regulatory,
investment or commercial conclusions.
