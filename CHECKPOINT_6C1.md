# Checkpoint 6C.1 — Scheduled incremental refresh

Status: **implemented, live validation pending**.

Checkpoint 6C.1 runs source refreshes outside Streamlit through GitHub Actions. The Streamlit process remains UI-only and reads durable scheduler state from PostgreSQL.

## Scheduler

Workflow: `.github/workflows/pharmatune_refresh.yml`

- Cron: `17 3 * * *` — 03:17 UTC daily.
- Manual `workflow_dispatch` supports due sources, one selected source, forced refresh, dry run and bounded lookback.
- PostgreSQL source advisory locks prevent overlapping jobs for the same source.
- GitHub Actions concurrency prevents overlapping orchestrators from the same repository.

## Initial cadences

| Source job | Cadence | Incremental strategy |
|---|---|---|
| `openfda_enforcement` | Daily | Bounded taxonomy/latest sweep; source ID + deterministic checksum; FDA report date watermark. |
| `openfda_shortages` | Daily | Newest-first bounded pages; update-date watermark; package NDC/stable source key. |
| `openfda_labels` | Daily | Bounded priority-index label context refresh; source-ID checksum deduplication. |
| `clinicaltrials` | Every two days | Bounded changed/stopped study topics; LastUpdatePostDate watermark and NCT identity. |
| `europepmc` | Weekly | Bounded priority-opportunity literature queries; DOI/PMID/source identity and checksum. |
| `openalex` | Weekly | Bounded priority-opportunity literature queries; OpenAlex/source identity and checksum. |
| `crossref` | Weekly | Bounded priority-opportunity literature queries; DOI/source identity and checksum. |
| `tavily` | Weekly | Disabled without key; bounded calls/spend; official/context evidence only. |
| `monthly_maintenance` | Monthly | Bounded official-source URL availability checks plus deterministic stale/current-relevance flagging without rewriting frozen source or audit records. |

Sources without a reliable modified-since API use a bounded lookback or bounded newest-first sweep and repeat-safe checksum ingestion.

## Operational schema — migration 5

- `source_refresh_state`
- `refresh_runs`
- `source_refresh_runs`
- `source_records`
- `source_record_changes`
- `opportunity_refresh_flags`
- `source_url_checks`
- `scheduler_notifications`

Cursors and watermarks advance only after the source transaction commits. Changed source records retain previous/new checksums, field changes, source timestamps, ingestion timestamps and run IDs.

## CLI

```bash
python -m pharmadrone.scheduler run-due
python -m pharmadrone.scheduler run-due --dry-run
python -m pharmadrone.scheduler run-source clinicaltrials
python -m pharmadrone.scheduler run-source openfda_enforcement --force --lookback-days 30
python -m pharmadrone.scheduler status
python -m pharmadrone.scheduler retry-failed
```

Exit codes:

- `0` — healthy completion or dry run
- `2` — partial/failed source completion requiring operator review
- `3` — command/configuration failure

## Frozen benchmark isolation

Scheduled records are written to the current source store and opportunity index only. The scheduler snapshots all frozen benchmark/audit counts before and after each source transaction and rolls back if they change. It never grants human, external-use or outreach approval.

## Stability gate

Do not tag Checkpoint 6C.1 stable until:

1. One manual GitHub Actions run succeeds.
2. The identical source is repeated without duplicate source records or opportunities.
3. PostgreSQL stores run/source history.
4. Streamlit displays that run.
5. The frozen 100-target benchmark remains unchanged.
6. One automatic scheduled run completes successfully.
