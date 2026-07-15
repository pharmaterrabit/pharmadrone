# Deploying PharmaDrone to a private cloud dashboard

Goal: open the dashboard from any browser, password-protected, with keys stored
as Streamlit Community Cloud secrets and a 5-report cap.

The repository is configured for Streamlit Community Cloud (`app.py`, pinned
dependencies, password gate and run cap).

---

## Part 1 — Put the code in a PRIVATE GitHub repo

1. Create an empty **private** repo on GitHub (e.g. `pharmadrone`). Don't add a
   README/gitignore — the project already has them.
2. In a terminal, from the `pharmadrone` folder:

```bash
git init
git add .
git commit -m "PharmaDrone v1"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/pharmadrone.git
git push -u origin main
```

`.env`, the database, and generated reports are already in `.gitignore`, so **no
keys and no secrets get pushed**. (Double-check: `git status` should not list `.env`.)

---

## Part 2 — Deploy

## Runtime stability note

This build targets **Python 3.12.13** and pins Streamlit, pandas, numpy and pyarrow in `requirements.txt`. This avoids accidental upgrades to native wheels on Streamlit Community Cloud, which can produce hard runtime crashes such as segmentation faults rather than normal Python tracebacks.

### Streamlit Community Cloud (`st.secrets`)

Streamlit Cloud's secrets panel uses TOML and is read via `st.secrets`, not
plain environment variables. The app already checks `st.secrets` automatically
as a fallback, so this works with no code changes.

1. Go to https://share.streamlit.io and sign in with GitHub.
2. **New app** → pick your private `pharmadrone` repo, branch `main`, main file
   `app.py`.
3. Before or after first deploy: app **⋮ menu → Settings → Secrets** → paste the
   contents of `.streamlit/secrets.toml.example` with your real values filled in
   (at minimum `APP_PASSWORD`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`). Save.
4. Streamlit rebuilds automatically. Open the app's `*.streamlit.app` URL →
   password screen → enter your `APP_PASSWORD`.

`requirements.txt` and `.python-version` are pinned for a stable Python 3.12 deployment. In Streamlit Cloud, select Python 3.12 in Advanced settings if shown, then deploy with the pinned `requirements.txt`.

## Where to add or change keys later

- **Streamlit Community Cloud:** app → **⋮ → Settings → Secrets** → edit the
  TOML → **Save** (auto-reruns).

Keys live only on the host; they are never sent to the browser or embedded in
any JavaScript.

| Variable | What it does |
|---|---|
| `APP_PASSWORD` | the login password for the dashboard |
| `PLATFORM_ADMIN_PASSWORD` | distinct password for the Checkpoint 6D-B Platform Administration experience |
| `WORKSPACE_ADMIN_PASSWORD` | distinct password for organisation-scoped Workspace Administration |
| `WORKSPACE_ADMIN_ORGANISATION_ID` | organisation ID that enforces the workspace administrator boundary |
| `LLM_PROVIDER` | `openrouter` (default) / `groq` / `openai` / `gemini` |
| `LLM_MODEL` | model string for that provider (cheap/free default set) |
| `OPENROUTER_API_KEY` | LLM key — needed if provider=openrouter (default) |
| `GROQ_API_KEY` | LLM key — needed if provider=groq |
| `OPENAI_API_KEY` | LLM key — needed if provider=openai |
| `GEMINI_API_KEY` | LLM key — optional, needed only if provider=gemini |
| `TAVILY_API_KEY` | web discovery (required) |
| `CONTACT_EMAIL` | optional, politer Crossref/OpenAlex |
| `MAX_REPORTS_PER_RUN` | hard cap per click (default 5) |
| `ALLOW_SCALE_RUNS` | `false` hides the 20/80 buttons; set `true` to unlock |

For Checkpoint 6D-B, first add a distinct `PLATFORM_ADMIN_PASSWORD` and sign in to create the production organisation. Copy the generated `org-…` identifier into `WORKSPACE_ADMIN_ORGANISATION_ID`, add a distinct `WORKSPACE_ADMIN_PASSWORD`, save the host settings, and then validate the scoped login. Never reuse the Customer / Analyst password for either administrator role.

**To switch LLM provider later:** change `LLM_PROVIDER` (and `LLM_MODEL`), add that
provider's key, Save Changes. If the selected provider's key is missing, the app
shows a clear error naming the exact variable to set.

---

## Run the 5-report milestone online

1. Open the URL, log in.
2. Tab **④ Connectors** → *Run connector test* → confirm sources are OK.
3. Tab **① Generate** → **Generate 5 Test Reports**.
4. Review reports + **source coverage summary** + any connector failures.
5. Tab **③ Results & Export** → **Download all outputs (.zip)** to save them
   (see the free-tier note below).

Scale runs stay locked until you set `ALLOW_SCALE_RUNS=true`.

---

## Streamlit Community Cloud — things to know

- **Sleeps when idle.** A cold start on
  next visit takes ~30–60s. Fine for private use.
- **Limited RAM.** v1 fits (no browser
  automation). If a run ever gets killed for memory, keep to the 5-report cap
  or reduce active regions/sources.
- **Disk is ephemeral.** Files in `./reports` are wiped on
  restart/redeploy/reboot, so **download the .zip during your session**. The
  dashboard makes this one click.

---

## Security checklist

- [x] `APP_PASSWORD` set → dashboard is gated
- [x] Keys in Streamlit secrets only (server-side), never in the repo
- [x] `.env` / secrets git-ignored
- [x] Password compared in Python on the server — not exposed to frontend JS
- [x] Run capped at 5 per click; scale buttons hidden

## Phase 2 persistence note

Phase 2 stores indexed PharmaTune evidence and queue state in local SQLite
(`pharmadrone.db`). This is acceptable for the MVP/local Streamlit workflow, but
it is not durable production SaaS persistence on free hosted tiers where disk can
be wiped on restart or redeploy. Download `opportunity_index.csv` and the reports
ZIP during the session if you need to retain outputs.

## Checkpoint 5A discovery caps

Checkpoint 5A uses bounded official-source pagination. The defaults are safe for the Streamlit MVP and require no API keys:

```text
OPENFDA_RECALL_PAGE_SIZE=50
OPENFDA_RECALL_MAX_PAGES_PER_CATEGORY=3
OPENFDA_SHORTAGE_PAGE_SIZE=50
OPENFDA_SHORTAGE_MAX_PAGES=6
CLINICALTRIALS_PAGE_SIZE=50
CLINICALTRIALS_MAX_PAGES_PER_TOPIC=2
MAX_DISCOVERY_RECORDS_PER_SOURCE=300
```

Keep `MAX_REPORTS_PER_RUN=5`; discovery may index many evidence-backed previews, but full report generation remains capped. Increase source limits only after reviewing runtime and manual validation precision.

## Checkpoint 6B audit persistence

The human audit registry uses separate SQLite tables in `pharmadrone.db`. Audit versions persist across app/process restarts while the same filesystem/database is retained. Streamlit Community Cloud and similar free hosts may reset local disk during redeploys or infrastructure replacement, so regularly download the internal audit and change-history exports. Production multi-user persistence will require a durable managed database in a later checkpoint.

# Checkpoint 6C PostgreSQL deployment

## Production configuration

Set these values in Streamlit Community Cloud secrets:

```text
APP_ENV=production
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT=10
DATABASE_CONNECT_TIMEOUT=8
DATABASE_CONNECT_RETRIES=3
```

Do not set `DATABASE_BACKEND=sqlite` in production. Do not commit the URL or paste it into reports. The application accepts `postgres://` and normalises it to the psycopg SQLAlchemy driver.

Run migrations explicitly when desired:

```bash
python -m pharmadrone.storage.migrate
```

Migrations also run safely at application startup.

## Local development

```bash
cp .env.example .env
# Set:
APP_ENV=local
DATABASE_BACKEND=sqlite
SQLITE_PATH=pharmadrone.db
streamlit run app.py
```

SQLite local mode is explicit. Production without `DATABASE_URL` fails rather than creating an empty local database.

## Import the existing SQLite Human Audit database

Back up the SQLite file first, configure `DATABASE_URL`, then run:

```bash
python -m pharmadrone.storage.import_sqlite \
  --sqlite-path /absolute/path/to/pharmadrone.db \
  --source-label checkpoint-6B-production
```

The importer:

- reports source and destination counts;
- preserves benchmark batches, immutable queue snapshots, audit versions, timestamps, approvals, and corrections;
- maps parent/version IDs safely;
- prevents duplicates on repeat execution;
- reports conflicts and rejected rows rather than silently discarding them;
- commits the import atomically.

Review the JSON summary. Any conflict must be resolved before deleting the SQLite backup.

## Backup

### Application audit backup

Results & Export → Human Validation Queue → **Durable audit backup (.zip)** includes:

- CSV for every audit table;
- full JSON audit data;
- migration/schema version;
- export timestamp;
- benchmark batch IDs;
- record counts;
- SHA-256 file checksums.

### PostgreSQL physical/logical backup

Create a compressed logical backup:

```bash
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL" > pharmatune-$(date +%F).dump
```

Verify:

```bash
pg_restore --list pharmatune-YYYY-MM-DD.dump >/dev/null
```

Restore into a clean database:

```bash
pg_restore --clean --if-exists --no-owner --no-acl \
  --dbname "$RESTORE_DATABASE_URL" pharmatune-YYYY-MM-DD.dump
```

Run the application migrations and compare audit counts/checksums after restore.

## Manual PostgreSQL persistence validation checklist

1. Configure the application with managed PostgreSQL.
2. Confirm the UI says backend `POSTGRESQL`.
3. Confirm schema version and migration count are displayed.
4. Import the frozen 100-record benchmark and confirm 100 queue records.
5. Confirm historical corrections for `D-0202-2025`, `D-0386-2024`, and `NCT00990444`.
6. Save one new audit decision and record its source ID/version.
7. Restart or redeploy the application.
8. Confirm the new audit version remains and counts are unchanged.
9. Download the durable audit backup and verify its manifest/checksums.
10. Confirm external/outreach approval states and full history remain correct.
11. Temporarily use an invalid/unavailable PostgreSQL URL in a safe test deployment.
12. Confirm a controlled database error appears and no SQLite database is created.

Do not declare Checkpoint 6C stable until steps 1–12 pass against the production-like managed PostgreSQL service.

---

# Checkpoint 6C.1 — GitHub Actions scheduled refresh

## Repository secrets

In GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

Required:

- `DATABASE_URL` — the same durable Neon PostgreSQL database used by Streamlit.

Optional, only when their configured jobs are enabled:

- `TAVILY_API_KEY`
- `OPENROUTER_API_KEY`

Do not create or upload `.env`, `secrets.toml`, database dumps or generated credential files as workflow artifacts. GitHub masks registered secrets, but application output must still avoid printing connection strings or keys.

Streamlit Secrets and GitHub Actions Secrets are separate. Configure both environments independently.

## Workflow

`.github/workflows/pharmatune_refresh.yml`

- automatic: daily at 03:17 UTC;
- manual: Actions → PharmaTune scheduled refresh → Run workflow;
- supports due jobs, one source, force, dry run and lookback days.

The daily invocation is deliberately tolerant of GitHub cron delay. PostgreSQL `next_due_at` controls which source jobs actually run.

## Migrations

Application startup and scheduler CLI apply ordered migrations automatically. Manual verification:

```bash
python -m pharmadrone.storage.migrate
python -m pharmadrone.scheduler status
```

Expected schema after Checkpoint 6C.1: version 5, five applied migrations.

## Guardrails

Configure through GitHub Actions repository variables or environment values:

```text
SCHEDULER_MAX_PAGES_PER_CONNECTOR=3
SCHEDULER_MAX_RECORDS_PER_CONNECTOR=300
SCHEDULER_MAX_PROCESSING_SECONDS=900
SCHEDULER_MAX_LLM_CALLS=0
SCHEDULER_MAX_TAVILY_CALLS=10
SCHEDULER_MAX_ESTIMATED_SPEND_USD=2.00
SCHEDULER_MAX_CONCURRENT_JOBS=1
SCHEDULER_RETRY_ATTEMPTS=3
SCHEDULER_LOOKBACK_DAYS=14
SCHEDULER_MONTHLY_URL_CHECK_LIMIT=50
SCHEDULER_URL_CHECK_TIMEOUT_SECONDS=8
```

The current scheduler makes zero LLM calls. A cost/volume stop is recorded as `Partial`, not as a complete successful refresh. Monthly maintenance performs bounded official-source URL availability checks and stores append-only results in `source_url_checks`.

## Manual live validation checklist

1. Add `DATABASE_URL` and optional keys to GitHub Actions Secrets.
2. Run the workflow manually with **dry run**.
3. Confirm due sources are identified.
4. Run one low-volume source manually, such as `openfda_shortages`.
5. Confirm `refresh_runs` and `source_refresh_runs` contain the run.
6. Confirm retrieved/created/updated/unchanged/rejected counts.
7. Run the identical source again.
8. Confirm no duplicate `source_records`, stable lead IDs or opportunities.
9. Confirm cursor/watermark changes only after success.
10. Temporarily force a safe connector failure and confirm other sources continue.
11. Confirm Streamlit System Health shows the failure without secrets.
12. Confirm the frozen benchmark remains 100 records with unchanged audit counts.
13. Confirm Streamlit shows the latest refresh status.
14. Confirm the GitHub job runs while Streamlit is sleeping/stopped.
15. Allow one scheduled automatic run to complete before declaring stability.

## Backup and recovery

Checkpoint 6C audit backup/restore procedures remain authoritative. PostgreSQL operational tables are included in normal `pg_dump` backups:

```bash
pg_dump --format=custom --no-owner --file=pharmatune.dump "$DATABASE_URL"
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" pharmatune.dump
```

Never paste the complete `DATABASE_URL` into logs, screenshots or issue reports.
