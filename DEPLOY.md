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
   `PYTHON_VERSION` — are already filled from the blueprint.)
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

`requirements.txt` and `.python-version` are already set up for this — no extra
config file is needed for Streamlit Cloud itself.

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
