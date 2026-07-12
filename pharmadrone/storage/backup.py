"""Audit backup/export helpers with schema and checksum metadata."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile
from typing import Any

AUDIT_TABLES = (
    "audit_benchmark_batches",
    "audit_queue_records",
    "human_audit_versions",
    "human_audit_corrections",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rows(conn, table: str) -> list[dict[str, Any]]:
    order = "imported_at" if table == "audit_benchmark_batches" else "id"
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()]


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fields = sorted({key for row in rows for key in row.keys()})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def build_audit_backup(conn) -> bytes:
    migration_rows = [dict(r) for r in conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()]
    table_rows = {table: _rows(conn, table) for table in AUDIT_TABLES}
    exported_at = _now()
    files: dict[str, bytes] = {}
    for table, rows in table_rows.items():
        files[f"csv/{table}.csv"] = _csv_bytes(rows)
    json_payload = {
        "exported_at": exported_at,
        "schema_version": max([int(r["version"]) for r in migration_rows], default=0),
        "migrations": migration_rows,
        "benchmark_batches": table_rows["audit_benchmark_batches"],
        "record_counts": {table: len(rows) for table, rows in table_rows.items()},
        "tables": table_rows,
    }
    files["audit_backup.json"] = json.dumps(json_payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    checksums = {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()}
    manifest = {
        "format": "PharmaTune Checkpoint 6C audit backup",
        "exported_at": exported_at,
        "database_backend": conn.backend,
        "schema_version": json_payload["schema_version"],
        "migration_count": len(migration_rows),
        "record_counts": json_payload["record_counts"],
        "benchmark_batch_ids": [row.get("batch_id") for row in table_rows["audit_benchmark_batches"]],
        "file_sha256": checksums,
    }
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()
