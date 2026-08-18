# PharmaDrone AI production deployment

This runbook deploys the standalone PharmaDrone AI FastAPI application. It
does not deploy, embed or modify the PharmaTune Streamlit dashboard.

## Runtime contract

- Container port: `8000`.
- Production start command:

  ```bash
  ./apps/pharmadrone-ai/start-production.sh
  ```

  The script validates the production environment and then executes:

  ```bash
  python -m uvicorn pharmadrone_ai.app:app --host 0.0.0.0 --port 8000
  ```

- Health check: `GET /api/health`.
- Database: the existing shared PostgreSQL intelligence database through
  `DATABASE_URL`. Production startup rejects a missing, non-PostgreSQL or
  SQLite URL and rejects `DATABASE_BACKEND=sqlite`.
- Migrations: the existing ordered migration runner through
  `python -m pharmadrone.storage.migrate`.

Terminate TLS at the deployment platform or reverse proxy and expose only the
intended public origin. Do not expose the database directly.

## Required environment

Copy `apps/pharmadrone-ai/.env.production.example` to a secret, untracked
deployment environment and replace every placeholder.

Required:

- `DATABASE_URL`: production PostgreSQL URL for the existing PharmaDrone data.
- `PHARMADRONE_AI_AUTH_SECRET`: unique random value of at least 32 characters.
- `PHARMADRONE_AI_ALLOWED_ORIGINS`: comma-separated deployed browser origins.

Existing optional server-side connector settings:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `TAVILY_API_KEY`
- `EPO_OPS_CLIENT_ID`
- `EPO_OPS_CLIENT_SECRET`

These credentials are read by Python services only. The static browser client
calls the PharmaDrone AI API and must never receive database or connector
credentials. Keep API docs disabled in production with
`PHARMADRONE_AI_API_DOCS=0` unless an explicit operational need is approved.

## Docker deployment

The production Compose definition has no bundled SQLite or PostgreSQL service;
it requires the durable external `DATABASE_URL`.

```bash
cp apps/pharmadrone-ai/.env.production.example /secure/path/pharmadrone-ai.env
docker compose \
  --env-file /secure/path/pharmadrone-ai.env \
  -f apps/pharmadrone-ai/docker-compose.production.yml \
  build
docker compose \
  --env-file /secure/path/pharmadrone-ai.env \
  -f apps/pharmadrone-ai/docker-compose.production.yml \
  run --rm pharmadrone-ai python -m pharmadrone.storage.migrate
docker compose \
  --env-file /secure/path/pharmadrone-ai.env \
  -f apps/pharmadrone-ai/docker-compose.production.yml \
  up -d
```

The image health check calls `http://127.0.0.1:8000/api/health`.

## Deployment checklist

1. Set `DATABASE_URL` to the existing production PostgreSQL database.
2. Set a new `PHARMADRONE_AI_AUTH_SECRET` of at least 32 characters.
3. Set `PHARMADRONE_AI_ALLOWED_ORIGINS` to the deployed HTTPS origin(s).
4. Set only the approved server-side connector credentials required by the
   deployment.
5. Run `python -m pharmadrone.storage.migrate` and confirm schema version 21.
6. Start the standalone app on port 8000.
7. Check `GET /api/health` and confirm `healthy`, `postgresql` and schema 21.
8. Register the first user and workspace; no production user is pre-seeded.
9. Test evidence-grounded BD lead generation.
10. Test a company pitch report.
11. Test saving a lead and report and exporting the report as Markdown.

Health command:

```bash
curl --fail --show-error --silent https://ai.example.com/api/health
```

Expected shape:

```json
{
  "status": "healthy",
  "product": "PharmaDrone AI",
  "database_backend": "postgresql",
  "schema_version": 21
}
```

## Authenticated smoke test

The smoke test does not seed or fabricate data. It requires at least one real
retained lead candidate in the configured PharmaDrone database and fails if
none is available. On the first run, use `--register`; later runs should log in
to the same dedicated smoke-test account without that flag. The password is
prompted without echo unless `PHARMADRONE_AI_SMOKE_PASSWORD` is supplied by a
secret manager.

```bash
PHARMADRONE_AI_SMOKE_EMAIL=deployment-check@example.com \
python apps/pharmadrone-ai/smoke_test.py \
  --base-url https://ai.example.com \
  --register
```

The script checks, in order:

1. `/api/health` and PostgreSQL/schema status.
2. Registration when requested.
3. Login.
4. BD lead generation from retained evidence.
5. Company pitch generation.
6. Saving the returned real lead.
7. Saving the returned pitch report.
8. Markdown report export.

## Incomplete production capabilities

The deployment must not represent these items as complete:

- Stripe billing is not complete.
- Email verification is not complete.
- Password reset is not complete.
- MFA is not complete.
- Production rate limiting is not complete.
- Server-side token revocation is not complete.

Use an appropriately restricted launch audience until the required auth,
billing, abuse-prevention and operational controls are implemented.
