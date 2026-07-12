"""Backend-neutral connection adapter built on SQLAlchemy 2.x."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Iterable, Mapping

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine, Connection, CursorResult
from sqlalchemy.exc import DBAPIError, OperationalError, IntegrityError
from sqlalchemy.pool import QueuePool

from .config import DatabaseConfig, DatabaseConfigurationError


class DatabaseUnavailableError(RuntimeError):
    """A controlled, credential-safe database availability error."""


class DatabaseConstraintError(RuntimeError):
    """A permanent validation/constraint error that should not be retried."""


class DBRow(dict):
    """Mapping row compatible with sqlite3.Row-style integer indexing."""
    def __init__(self, mapping: Mapping[str, Any]):
        super().__init__(mapping)
        self._keys = list(mapping.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)


class ResultAdapter:
    def __init__(self, result: CursorResult | None, *, forced_lastrowid: Any = None):
        self._result = result
        self._forced_lastrowid = forced_lastrowid

    @property
    def lastrowid(self):
        if self._forced_lastrowid is not None:
            return self._forced_lastrowid
        if self._result is None:
            return None
        return getattr(self._result, "lastrowid", None)

    @property
    def rowcount(self) -> int:
        if self._result is None:
            return 0
        return int(getattr(self._result, "rowcount", 0) or 0)

    def fetchone(self):
        if self._result is None or not self._result.returns_rows:
            return None
        row = self._result.mappings().fetchone()
        return DBRow(row) if row is not None else None

    def fetchall(self):
        if self._result is None or not self._result.returns_rows:
            return []
        return [DBRow(row) for row in self._result.mappings().fetchall()]


_ENGINE_CACHE: dict[str, Engine] = {}
_ENGINE_LOCK = threading.Lock()
_LAST_SUCCESS: dict[str, str] = {"timestamp": "", "operation": ""}

_CONFLICT_KEYS = {
    "opportunities": ("id",),
    "opportunity_index": ("stable_lead_id",),
    "opportunity_run_summary": ("run_id",),
    "opportunity_enrichment": ("stable_lead_id",),
}
_RETURNING_ID_TABLES = {"human_audit_versions", "human_audit_corrections"}


def _safe_error(exc: Exception) -> str:
    # Never include DSNs/credentials. Keep enough detail for an operator.
    name = exc.__class__.__name__
    if isinstance(exc, OperationalError):
        return f"{name}: database connection unavailable"
    if isinstance(exc, IntegrityError):
        return f"{name}: database constraint rejected the operation"
    return f"{name}: database operation failed"


def _mark_success(operation: str) -> None:
    _LAST_SUCCESS["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    _LAST_SUCCESS["operation"] = operation[:120]


def last_successful_operation() -> dict[str, str]:
    return dict(_LAST_SUCCESS)


def get_engine(config: DatabaseConfig) -> Engine:
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(config.url)
        if engine is not None:
            return engine
        if config.is_postgresql:
            engine = create_engine(
                config.url,
                future=True,
                pool_pre_ping=True,
                poolclass=QueuePool,
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_timeout=config.pool_timeout,
                connect_args={"connect_timeout": config.connect_timeout},
            )
        else:
            engine = create_engine(
                config.url,
                future=True,
                pool_pre_ping=True,
                connect_args={"timeout": config.connect_timeout, "check_same_thread": False},
            )
        _ENGINE_CACHE[config.url] = engine
        return engine


def dispose_engines() -> None:
    with _ENGINE_LOCK:
        for engine in _ENGINE_CACHE.values():
            engine.dispose()
        _ENGINE_CACHE.clear()


def _qmark_to_named(sql: str, params: Iterable[Any] | Mapping[str, Any] | None):
    if params is None or isinstance(params, Mapping):
        return sql, params or {}
    values = list(params)
    index = 0
    out = []
    in_single = False
    in_double = False
    for char in sql:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == "?" and not in_single and not in_double:
            out.append(f":p{index}")
            index += 1
        else:
            out.append(char)
    if index != len(values):
        raise ValueError(f"SQL placeholder count {index} does not match parameter count {len(values)}")
    return "".join(out), {f"p{i}": value for i, value in enumerate(values)}


def _transform_postgres_sql(sql: str) -> tuple[str, bool]:
    stripped = sql.strip().rstrip(";")
    returning_id = False

    ignore = re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", stripped, flags=re.I)
    if ignore:
        stripped = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", stripped, flags=re.I)
        stripped += " ON CONFLICT DO NOTHING"

    replace = re.match(
        r"^INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)$",
        stripped,
        flags=re.I | re.S,
    )
    if replace:
        table = replace.group(1)
        columns = [c.strip() for c in replace.group(2).split(",")]
        values = replace.group(3)
        keys = _CONFLICT_KEYS.get(table)
        if not keys:
            raise DatabaseConstraintError(f"No PostgreSQL upsert key configured for table {table}")
        updates = [c for c in columns if c not in keys]
        update_sql = ", ".join(f"{c}=EXCLUDED.{c}" for c in updates)
        stripped = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values}) "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {update_sql}"
        )

    insert_match = re.match(r"^INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped, flags=re.I)
    if insert_match and insert_match.group(1) in _RETURNING_ID_TABLES and " RETURNING " not in stripped.upper():
        stripped += " RETURNING id"
        returning_id = True
    return stripped, returning_id


class DatabaseConnection:
    """Small compatibility adapter used by existing frozen business logic."""
    def __init__(self, engine: Engine, config: DatabaseConfig):
        self.engine = engine
        self.config = config
        self.backend = config.backend
        self._atomic_depth = 0
        last_exc: Exception | None = None
        for attempt in range(max(1, config.connect_retries)):
            try:
                self._conn: Connection = engine.connect()
                self._conn.exec_driver_sql("SELECT 1")
                self._conn.commit()
                _mark_success("connect")
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < max(1, config.connect_retries):
                    time.sleep(min(0.25 * (2 ** attempt), 1.0))
        else:
            raise DatabaseUnavailableError(_safe_error(last_exc or RuntimeError("connection failed"))) from None

    @property
    def dialect(self) -> str:
        return self.backend

    def execute(self, sql: str, params: Iterable[Any] | Mapping[str, Any] | None = None) -> ResultAdapter:
        returning_id = False
        statement = sql
        if self.backend == "postgresql":
            statement, returning_id = _transform_postgres_sql(statement)
        statement, named = _qmark_to_named(statement, params)
        try:
            result = self._conn.execute(text(statement), named)
            forced = None
            if returning_id and result.returns_rows:
                row = result.fetchone()
                forced = row[0] if row else None
                result.close()
                result = None
            _mark_success(statement.split(None, 1)[0].upper() if statement.strip() else "execute")
            return ResultAdapter(result, forced_lastrowid=forced)
        except IntegrityError as exc:
            if self._atomic_depth == 0:
                self._conn.rollback()
            raise DatabaseConstraintError(_safe_error(exc)) from None
        except (OperationalError, DBAPIError) as exc:
            if self._atomic_depth == 0:
                self._conn.rollback()
            raise DatabaseUnavailableError(_safe_error(exc)) from None

    def executescript(self, script: str) -> None:
        statements = [part.strip() for part in script.split(";") if part.strip()]
        for statement in statements:
            self.execute(statement)

    def commit(self) -> None:
        # Frozen business helpers historically commit after each write. Inside
        # an explicit scheduler/import transaction those commits must not split
        # the atomic unit; SQLAlchemy has already sent the statements to the
        # active transaction, so defer the real commit to transaction().
        if self._atomic_depth > 0:
            _mark_success("transaction deferred commit")
            return
        try:
            self._conn.commit()
            _mark_success("commit")
        except Exception as exc:
            self._conn.rollback()
            raise DatabaseUnavailableError(_safe_error(exc)) from None

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self):
        # SQLAlchemy autobegins even for reads. Finish any prior unit before an
        # explicitly atomic operation, then begin a clean transaction.
        if self._conn.in_transaction():
            self._conn.commit()
        tx = self._conn.begin()
        self._atomic_depth += 1
        try:
            yield self
            if tx.is_active:
                tx.commit()
            _mark_success("transaction commit")
        except Exception:
            if tx.is_active:
                tx.rollback()
            raise
        finally:
            self._atomic_depth = max(0, self._atomic_depth - 1)

    def has_table(self, table: str) -> bool:
        return inspect(self.engine).has_table(table)

    def columns(self, table: str) -> set[str]:
        return {col["name"] for col in inspect(self.engine).get_columns(table)}

    def ensure_column(self, table: str, column: str, spec: str) -> None:
        if column not in self.columns(table):
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
            self.commit()

    def ensure_migrations(self) -> dict[str, Any]:
        from .migrations import run_migrations
        return run_migrations(self)


def open_connection(config: DatabaseConfig) -> DatabaseConnection:
    return DatabaseConnection(get_engine(config), config)
