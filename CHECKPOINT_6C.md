# Checkpoint 6C — Durable PostgreSQL Persistence

## Scope

Checkpoint 6C is a database-infrastructure checkpoint layered over the frozen Checkpoint 6A.5.2 deterministic engine and frozen Checkpoint 6B Human Audit workflow. It does not change discovery, scores, stable lead IDs, signal tiers, ClinicalTrials evidence rules, company-role logic, approval gates, report generation, source connectors, or exports.

## Active backends

- **Production:** PostgreSQL through `DATABASE_URL`.
- **Local development and tests:** SQLite only when explicitly selected with `DATABASE_BACKEND=sqlite`, `APP_ENV=local/test`, or an explicit path supplied by automated tests.

Production never silently falls back to SQLite. Missing or unavailable PostgreSQL produces a controlled startup error without displaying credentials.

## Storage architecture

- `pharmadrone/storage/config.py` — safe environment configuration.
- `pharmadrone/storage/database.py` — pooled SQLAlchemy connection adapter, transactions, rollback, credential-safe failures.
- `pharmadrone/storage/migrations/` — ordered migration history and schema definitions.
- `pharmadrone/storage/import_sqlite.py` — repeat-safe SQLite audit import.
- `pharmadrone/storage/backup.py` — full CSV/JSON audit backup with schema/checksum manifest.
- `pharmadrone/db.py` — backward-compatible persistence facade used by existing business logic.

## Schema migrations

The `schema_migrations` table records ordered versions, names, checksums, and application timestamps.

1. Checkpoint 6A core opportunity/index/source-health schema.
2. Checkpoint 6B audit schema.
3. Checkpoint 6C import tracking and indexes.
4. Legacy additive-column compatibility.

Each unapplied migration runs in a transaction. Repeated execution is safe. A checksum/name mismatch stops startup rather than guessing.

## Preserved audit model

The following tables and semantics are preserved:

- `audit_benchmark_batches`
- `audit_queue_records`
- `human_audit_versions`
- `human_audit_corrections`

Audit versions and corrections remain append-only through the application. Audit decisions and optional corrections are written atomically. Outreach approval cannot be stored when external approval is false.

Historical source-ID-keyed corrections remain seeded:

- `D-0202-2025`
- `D-0386-2024`
- `NCT00990444`

They do not automatically grant external or outreach approval.

## Database status UI

Results & Export shows only safe metadata:

- backend
- connection health
- schema version
- migration count/status
- last successful operation
- audit queue/version/correction counts

Credentials, full URLs, passwords, hosts, and secrets are never shown.

## Stability status

Implementation and SQLite regression testing can be completed locally. **Checkpoint 6C must not be declared stable until a managed PostgreSQL instance has passed the restart/redeployment persistence checklist in `DEPLOY.md`.**
