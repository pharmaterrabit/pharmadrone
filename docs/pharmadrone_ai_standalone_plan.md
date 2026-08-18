# PharmaDrone AI standalone product

## Product boundary

PharmaDrone AI is a standalone SaaS chatbot for pharmaceutical business-development users. It is served by FastAPI with its own static web client. It is not a Streamlit page and is not registered in either `pharmatune_ui` or `pharmatune_admin`.

PharmaTune remains the internal intelligence and administration platform. PharmaDrone AI consumes bounded service functions over the same governed PharmaDrone database. It is not registered in Streamlit navigation and does not require Streamlit to run.

The production request path is: standalone browser client → PharmaDrone AI
FastAPI/chat layer → existing PharmaDrone service and data modules → the shared
PostgreSQL database. The service reuses the PR #46 company-specific case-study
workflow, opportunity index, canonical product/API and organisation records,
problem/technology relationships, patent provider status and stored records,
research grants, regulatory/lifecycle context and human-reviewed canonical
links. It never creates a parallel intelligence store.

## Implemented vertical slice

- Email/password registration and login with PBKDF2-HMAC-SHA256 password hashing.
- Signed, expiring authenticated sessions.
- User, workspace and membership isolation.
- ChatGPT-style standalone web interface with history and starter prompts.
- Deterministic chat intent routing that works without an LLM key.
- Bounded target-company search and BD lead generation.
- Company pitch reports built through the PR #46 case-study service.
- Source links, readiness, limitations and suggested validation actions.
- Workspace-scoped saved leads, saved reports and conversations.
- Markdown report export.
- Usage-event recording and a billing-ready status abstraction.
- Optional OpenAI-compatible drafting from bounded tool output only.
- Docker deployment and PostgreSQL development compose file.

## Evidence and safety model

Factual claims come from retained PharmaDrone service output. Generic theme evidence is never represented as company-specific evidence. Prospecting shells and unavailable evidence remain explicit. The optional LLM receives bounded tool output and a strict grounding prompt; a network or configuration failure returns the deterministic response.

The application does not scrape Google Patents, import external discovery results, accept canonical links, expose raw SQL, or call external services on page load. It does not provide legal, FTO, patent-validity, patent-enforceability, regulatory, investment or commercial conclusions.

## Environment variables

- `DATABASE_URL`: production PostgreSQL URL.
- `DATABASE_BACKEND=sqlite` and `SQLITE_PATH`: explicit local-only SQLite configuration.
- `PHARMADRONE_AI_AUTH_SECRET`: required in production; at least 32 characters.
- `PHARMADRONE_AI_ALLOWED_ORIGINS`: comma-separated allowed browser origins.
- `PHARMADRONE_AI_API_DOCS`: set to `0` to disable API docs.
- `OPENAI_API_KEY`, `OPENAI_MODEL`: optional tool-grounded drafting.
- `TAVILY_API_KEY`: existing server-side discovery fallback where an explicitly invoked existing service supports it.
- `EPO_OPS_CLIENT_ID`, `EPO_OPS_CLIENT_SECRET`: existing server-side EPO connector configuration.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`: reserved for later billing integration.

Connector credentials remain server-side. The static browser client calls only
the PharmaDrone AI backend; no connector key is embedded in JavaScript and no
external connector runs on initial page load.

## Deployment

1. Configure a dedicated deployment with PostgreSQL `DATABASE_URL` and a strong `PHARMADRONE_AI_AUTH_SECRET`.
2. Build with `docker build -f apps/pharmadrone-ai/Dockerfile .`.
3. Run the image on port 8000.
4. Confirm `/api/health` reports `healthy`, PostgreSQL, and schema version 21.
5. Register/login, generate leads, build a pitch, save both artifacts and export Markdown.

SQLite is permitted only when local/test mode is explicitly selected. A
production process without `DATABASE_URL`, or with an unchanged development
authentication secret, fails closed rather than creating an empty SQLite app.

For local PostgreSQL, use `docker compose -f apps/pharmadrone-ai/docker-compose.yml up --build`.

## Known limitations

- Stripe checkout and webhook processing are not implemented in this slice.
- Signed sessions are stateless; logout removes the browser token but server-side revocation is not yet implemented.
- Email verification, password reset, MFA and invitation flows remain production hardening work.
- The frontend uses a dependency-free static client rather than Next.js to keep the first vertical slice small and deployable with the Python service.
- Optional LLM mode drafts language from tool results; it does not independently retrieve evidence.
