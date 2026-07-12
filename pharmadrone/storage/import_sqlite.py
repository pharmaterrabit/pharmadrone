"""One-time, repeat-safe import of Checkpoint 6B audit data into PostgreSQL.

CLI:
    python -m pharmadrone.storage.import_sqlite --sqlite-path /path/pharmadrone.db

The active destination must be PostgreSQL through DATABASE_URL.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .. import db

AUDIT_TABLES = (
    "audit_benchmark_batches",
    "audit_queue_records",
    "human_audit_versions",
    "human_audit_corrections",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _source_rows(src: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    exists = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    return [dict(row) for row in src.execute(f"SELECT * FROM {table} ORDER BY id" if table != "audit_benchmark_batches" else f"SELECT * FROM {table} ORDER BY imported_at").fetchall()]


def _count(conn, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"] if row else 0)


def _record_import(conn, import_id: str, source_label: str, table: str, source_pk: str,
                   row_sha: str, destination_pk: str, status: str, note: str = "") -> None:
    conn.execute(
        """INSERT OR IGNORE INTO persistence_import_records
        (import_id, source_label, source_table, source_primary_key, row_sha256,
         destination_primary_key, status, note, imported_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (import_id, source_label, table, source_pk, row_sha, destination_pk, status, note, _now()),
    )


def import_sqlite_audit(sqlite_path: Path | str, *, source_label: str | None = None,
                        destination_conn=None, require_postgresql: bool = True) -> dict[str, Any]:
    path = Path(sqlite_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    source_label = source_label or str(path)
    source_sha = _file_hash(path)
    import_id = f"sqlite-{hashlib.sha256((source_label + source_sha).encode()).hexdigest()[:24]}"

    own_dest = destination_conn is None
    dest = destination_conn or db.connect()
    if require_postgresql and dest.backend != "postgresql":
        if own_dest:
            dest.close()
        raise RuntimeError("SQLite audit import destination must be PostgreSQL through DATABASE_URL.")

    existing_run = dest.execute(
        "SELECT status, summary_json FROM persistence_import_runs WHERE import_id=?", (import_id,)
    ).fetchone()
    if existing_run and existing_run["status"] == "completed":
        summary = json.loads(existing_run.get("summary_json") or "{}")
        summary["already_imported"] = True
        if own_dest:
            dest.close()
        return summary

    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    source_counts = {table: len(_source_rows(src, table)) for table in AUDIT_TABLES}
    before_counts = {table: _count(dest, table) for table in AUDIT_TABLES}
    summary: dict[str, Any] = {
        "import_id": import_id,
        "source_label": source_label,
        "source_sha256": source_sha,
        "started_at": _now(),
        "source_counts": source_counts,
        "destination_before": before_counts,
        "imported": {table: 0 for table in AUDIT_TABLES},
        "skipped": {table: 0 for table in AUDIT_TABLES},
        "conflicts": [],
        "rejected": [],
        "already_imported": False,
    }

    version_id_map: dict[int, int] = {}
    try:
        with dest.transaction():
            dest.execute(
                """INSERT INTO persistence_import_runs
                (import_id, source_label, source_sha256, started_at, status, summary_json)
                VALUES (?,?,?,?,?,?)""",
                (import_id, source_label, source_sha, summary["started_at"], "running", "{}"),
            )

            for row in _source_rows(src, "audit_benchmark_batches"):
                existing = dest.execute(
                    "SELECT * FROM audit_benchmark_batches WHERE batch_id=?", (row["batch_id"],)
                ).fetchone()
                if existing:
                    if existing["sha256"] != row["sha256"]:
                        summary["conflicts"].append({"table": "audit_benchmark_batches", "key": row["batch_id"], "reason": "checksum mismatch"})
                        _record_import(dest, import_id, source_label, "audit_benchmark_batches", row["batch_id"], _row_hash(row), row["batch_id"], "conflict", "checksum mismatch")
                    else:
                        summary["skipped"]["audit_benchmark_batches"] += 1
                    continue
                dest.execute(
                    """INSERT INTO audit_benchmark_batches
                    (batch_id, filename, sha256, imported_at, row_count, is_golden, notes)
                    VALUES (?,?,?,?,?,?,?)""",
                    (row["batch_id"], row.get("filename"), row["sha256"], row["imported_at"], row["row_count"], row.get("is_golden", 1), row.get("notes")),
                )
                summary["imported"]["audit_benchmark_batches"] += 1
                _record_import(dest, import_id, source_label, "audit_benchmark_batches", row["batch_id"], _row_hash(row), row["batch_id"], "imported")

            for row in _source_rows(src, "audit_queue_records"):
                pk = str(row["id"])
                existing = dest.execute(
                    "SELECT * FROM audit_queue_records WHERE batch_id=? AND audit_key=?",
                    (row["batch_id"], row["audit_key"]),
                ).fetchone()
                if existing:
                    if existing["original_row_hash"] != row["original_row_hash"]:
                        summary["conflicts"].append({"table": "audit_queue_records", "key": f"{row['batch_id']}|{row['audit_key']}", "reason": "immutable snapshot hash mismatch"})
                        _record_import(dest, import_id, source_label, "audit_queue_records", pk, _row_hash(row), str(existing["id"]), "conflict", "snapshot hash mismatch")
                    else:
                        summary["skipped"]["audit_queue_records"] += 1
                    continue
                result = dest.execute(
                    """INSERT INTO audit_queue_records
                    (batch_id, audit_key, stable_lead_id, source_type, source_id,
                     original_row_json, original_row_hash, created_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (row["batch_id"], row["audit_key"], row.get("stable_lead_id"), row.get("source_type"), row.get("source_id"), row["original_row_json"], row["original_row_hash"], row["created_at"]),
                )
                dest_id_row = dest.execute(
                    "SELECT id FROM audit_queue_records WHERE batch_id=? AND audit_key=?", (row["batch_id"], row["audit_key"])
                ).fetchone()
                dest_id = str(dest_id_row["id"])
                summary["imported"]["audit_queue_records"] += 1
                _record_import(dest, import_id, source_label, "audit_queue_records", pk, _row_hash(row), dest_id, "imported")

            for row in _source_rows(src, "human_audit_versions"):
                source_id = int(row["id"])
                existing = dest.execute(
                    "SELECT * FROM human_audit_versions WHERE audit_key=? AND audit_version=?",
                    (row["audit_key"], row["audit_version"]),
                ).fetchone()
                if existing:
                    comparable = {k: row.get(k) for k in row if k not in {"id", "parent_audit_id"}}
                    existing_comp = {k: existing.get(k) for k in comparable}
                    same_content = _row_hash(comparable) == _row_hash(existing_comp)
                    is_bootstrap_seed = (
                        str(existing.get("source_snapshot_hash") or "") == "historical-checkpoint-6a"
                        and str(existing.get("reviewer_name") or "") == "Checkpoint 6A manual audit"
                        and int(existing.get("audit_version") or 0) == 1
                    )
                    later = dest.execute(
                        "SELECT COUNT(*) AS n FROM human_audit_versions WHERE audit_key=? AND audit_version>1",
                        (row["audit_key"],),
                    ).fetchone()
                    if not same_content and is_bootstrap_seed and int(later["n"] if later else 0) == 0:
                        # The startup seed is a deterministic bootstrap placeholder,
                        # not a user-authored decision. Reconcile it atomically with
                        # the source database row so original timestamps/version are
                        # preserved before any later audit history exists.
                        dest.execute("DELETE FROM human_audit_corrections WHERE audit_version_id=?", (existing["id"],))
                        dest.execute("DELETE FROM human_audit_versions WHERE id=?", (existing["id"],))
                        summary.setdefault("bootstrap_seeds_reconciled", []).append(row["audit_key"])
                        existing = None
                    elif existing:
                        version_id_map[source_id] = int(existing["id"])
                        if not same_content:
                            summary["conflicts"].append({"table": "human_audit_versions", "key": f"{row['audit_key']}|v{row['audit_version']}", "reason": "version content mismatch"})
                            _record_import(dest, import_id, source_label, "human_audit_versions", str(source_id), _row_hash(row), str(existing["id"]), "conflict", "version content mismatch")
                        else:
                            summary["skipped"]["human_audit_versions"] += 1
                        continue
                parent_dest = version_id_map.get(int(row["parent_audit_id"])) if row.get("parent_audit_id") else None
                columns = [k for k in row.keys() if k not in {"id", "parent_audit_id"}]
                columns.insert(5, "parent_audit_id")
                values = []
                for col in columns:
                    values.append(parent_dest if col == "parent_audit_id" else row.get(col))
                placeholders = ",".join("?" for _ in columns)
                cur = dest.execute(
                    f"INSERT INTO human_audit_versions ({','.join(columns)}) VALUES ({placeholders})",
                    tuple(values),
                )
                dest_id = int(cur.lastrowid)
                version_id_map[source_id] = dest_id
                summary["imported"]["human_audit_versions"] += 1
                _record_import(dest, import_id, source_label, "human_audit_versions", str(source_id), _row_hash(row), str(dest_id), "imported")

            for row in _source_rows(src, "human_audit_corrections"):
                source_id = str(row["id"])
                mapped_version = version_id_map.get(int(row["audit_version_id"]))
                if not mapped_version:
                    summary["rejected"].append({"table": "human_audit_corrections", "key": source_id, "reason": "audit version mapping unavailable"})
                    _record_import(dest, import_id, source_label, "human_audit_corrections", source_id, _row_hash(row), "", "rejected", "audit version mapping unavailable")
                    continue
                existing = dest.execute(
                    """SELECT id FROM human_audit_corrections
                    WHERE audit_key=? AND field_name=? AND COALESCE(original_value,'')=COALESCE(?, '')
                      AND COALESCE(corrected_value,'')=COALESCE(?, '') AND corrected_at=?
                      AND COALESCE(reviewer_name,'')=COALESCE(?, '') LIMIT 1""",
                    (row["audit_key"], row.get("field_name"), row.get("original_value"), row.get("corrected_value"), row["corrected_at"], row.get("reviewer_name")),
                ).fetchone()
                if existing:
                    summary["skipped"]["human_audit_corrections"] += 1
                    continue
                columns = [k for k in row.keys() if k not in {"id", "audit_version_id"}]
                columns.insert(0, "audit_version_id")
                values = [mapped_version] + [row.get(c) for c in columns[1:]]
                placeholders = ",".join("?" for _ in columns)
                cur = dest.execute(
                    f"INSERT INTO human_audit_corrections ({','.join(columns)}) VALUES ({placeholders})",
                    tuple(values),
                )
                summary["imported"]["human_audit_corrections"] += 1
                _record_import(dest, import_id, source_label, "human_audit_corrections", source_id, _row_hash(row), str(cur.lastrowid), "imported")

            summary["completed_at"] = _now()
            summary["destination_after"] = {table: _count(dest, table) for table in AUDIT_TABLES}
            summary["status"] = "completed_with_conflicts" if summary["conflicts"] or summary["rejected"] else "completed"
            dest.execute(
                "UPDATE persistence_import_runs SET completed_at=?, status=?, summary_json=? WHERE import_id=?",
                (summary["completed_at"], "completed", json.dumps(summary, sort_keys=True), import_id),
            )
    except Exception:
        # The destination transaction rolls back. Record nothing partially.
        raise
    finally:
        src.close()
        if own_dest:
            dest.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Checkpoint 6B SQLite audit data into PostgreSQL")
    parser.add_argument("--sqlite-path", required=True)
    parser.add_argument("--source-label", default=None)
    args = parser.parse_args()
    summary = import_sqlite_audit(args.sqlite_path, source_label=args.source_label)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
