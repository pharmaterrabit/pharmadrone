"""Run configured Checkpoint 6C database migrations.

Usage:
    python -m pharmadrone.storage.migrate
"""
from __future__ import annotations
import json
from .. import db


def main() -> None:
    conn = db.connect()
    try:
        result = conn.ensure_migrations()
        print(json.dumps({"backend": conn.backend, **result}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
