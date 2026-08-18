# PharmaDrone AI

PharmaDrone AI is the standalone customer-facing SaaS application. It is separate from the PharmaTune Streamlit intelligence and administration applications.

Local SQLite development:

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
