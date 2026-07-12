# Deploying PharmaDrone to a private cloud dashboard

Goal: open the dashboard from any browser, password-protected, keys stored
server-side, 5-report cap. Target: **Render Free**. Backup: Railway $5 (ask first).

You do the clicks; the repo is already configured (`render.yaml`, password gate,
run cap). Budget ~10 minutes.

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

This build targets **Python 3.12.13** and pins Streamlit, pandas, numpy and pyarrow in `requirements.txt`. This avoids accidental upgrades to latest native wheels on Streamlit Cloud/Render, which can produce hard runtime crashes such as segmentation faults rather than normal Python tracebacks.

Two supported paths. Both read the same code; only where secrets live differs.

### Option A — Render Free (env vars)

1. Go to https://render.com and sign in with GitHub.
2. Click **New +** → **Blueprint**.
3. Select your private `pharmadrone` repo. Render reads `render.yaml` and shows a
   `pharmadrone` web service on the **Free** plan.
4. It will prompt for the secret env vars (the ones marked "sync: false"). You do
   **not** need a Gemini key. Fill in only:
   - `APP_PASSWORD` — **choose the password you'll type to open the dashboard**
   - `OPENROUTER_API_KEY` — for the default provider (get it at openrouter.ai/keys)
   - `TAVILY_API_KEY` — web search
   - Leave the other LLM keys (`GROQ_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`)
     **blank** unless you switch `LLM_PROVIDER` to one of them.
   (The non-secret ones — `LLM_PROVIDER=openrouter`,
   `LLM_MODEL=...:free`, `MAX_REPORTS_PER_RUN=5`, `ALLOW_SCALE_RUNS=false`,
   `PYTHON_VERSION=3.12.13` — are already filled from the blueprint.)
5. Click **Apply** / **Create**. First build takes a few minutes.
6. When it's live, Render shows a URL like `https://pharmadrone.onrender.com`.
   Open it → password screen → enter your `APP_PASSWORD`.

### Option B — Streamlit Community Cloud (st.secrets)

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

Whichever you pick, the app behaves identically: same password gate, same 5-report
cap, same hidden scale buttons.

---

## Where to add or change keys later

- **Render:** service → **Environment** tab → edit values → **Save Changes**
  (auto-redeploys).
- **Streamlit Community Cloud:** app → **⋮ → Settings → Secrets** → edit the
  TOML → **Save** (auto-reruns).

Keys live only on the host; they are never sent to the browser or embedded in
any JavaScript.

| Variable | What it does |
|---|---|
| `APP_PASSWORD` | the login password for the dashboard |
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

## Render Free / Streamlit Community Cloud — things to know

Both are free tiers with similar tradeoffs:

- **Sleeps when idle** (Render ~15 min; Streamlit Cloud similar). Cold start on
  next visit takes ~30–60s. Fine for private use.
- **Limited RAM** (Render 512 MB; Streamlit Cloud ~1 GB). v1 fits (no browser
  automation). If a run ever gets killed for memory, keep to the 5-report cap
  or reduce active regions/sources.
- **Disk is ephemeral** on both. Files in `./reports` are wiped on
  restart/redeploy/reboot, so **download the .zip during your session**. The
  dashboard makes this one click.

---

## If Render Free won't cooperate (backup)

If the free instance keeps OOM-ing or won't stay up, the usual fix is **Railway
at ~$5/month** (more RAM, no sleep, persistent volume). That's paid, so **tell me
first and I'll walk you through it** — I won't move you to paid hosting without
your go-ahead. Railway steps are almost identical: connect the private repo, set
the same env vars, start command
`streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`.

---

## Security checklist

- [x] `APP_PASSWORD` set → dashboard is gated
- [x] Keys in Render env vars only (server-side), never in the repo
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

Set these environment variables in Streamlit Cloud, Render, Railway, or the chosen host:

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
