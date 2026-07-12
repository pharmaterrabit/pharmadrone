"""Database configuration for Checkpoint 6C.

Production uses PostgreSQL through ``DATABASE_URL``. SQLite is available only
when explicitly requested for local development/tests, or when a caller passes
an explicit SQLite path to ``pharmadrone.db.connect``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


class DatabaseConfigurationError(RuntimeError):
    """Raised when durable database configuration is missing or invalid."""


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str
    url: str
    sqlite_path: Path | None = None
    app_env: str = "local"
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout: int = 10
    connect_timeout: int = 8
    connect_retries: int = 3

    @property
    def is_postgresql(self) -> bool:
        return self.backend == "postgresql"

    @property
    def is_sqlite(self) -> bool:
        return self.backend == "sqlite"


def _read_env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value:
        return value
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def _as_int(key: str, default: int) -> int:
    raw = _read_env(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def normalize_postgres_url(url: str) -> str:
    cleaned = (url or "").strip()
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned[len("postgres://"):]
    if cleaned.startswith("postgresql://") and "+" not in cleaned.split("://", 1)[0]:
        cleaned = "postgresql+psycopg://" + cleaned[len("postgresql://"):]
    return cleaned


def configured_database(explicit_sqlite_path: Path | str | None = None) -> DatabaseConfig:
    """Resolve the active database without silent production fallback.

    Passing ``explicit_sqlite_path`` is an explicit local/test choice and is
    used by existing automated tests. Application runtime should call this
    without a path and configure ``DATABASE_URL`` or ``DATABASE_BACKEND``.
    """
    if explicit_sqlite_path is not None:
        path = Path(explicit_sqlite_path).expanduser().resolve()
        return DatabaseConfig(
            backend="sqlite",
            url=f"sqlite+pysqlite:///{path}",
            sqlite_path=path,
            app_env="test/local-explicit",
            connect_timeout=_as_int("DATABASE_CONNECT_TIMEOUT", 8),
            connect_retries=max(1, _as_int("DATABASE_CONNECT_RETRIES", 3)),
        )

    app_env = _read_env("APP_ENV", "").strip().lower()
    backend = _read_env("DATABASE_BACKEND", "").strip().lower()
    database_url = _read_env("DATABASE_URL", "").strip()

    if database_url:
        return DatabaseConfig(
            backend="postgresql",
            url=normalize_postgres_url(database_url),
            app_env=app_env or "production",
            pool_size=max(1, _as_int("DATABASE_POOL_SIZE", 5)),
            max_overflow=max(0, _as_int("DATABASE_MAX_OVERFLOW", 5)),
            pool_timeout=max(1, _as_int("DATABASE_POOL_TIMEOUT", 10)),
            connect_timeout=max(1, _as_int("DATABASE_CONNECT_TIMEOUT", 8)),
            connect_retries=max(1, _as_int("DATABASE_CONNECT_RETRIES", 3)),
        )

    if backend in {"postgres", "postgresql"}:
        raise DatabaseConfigurationError(
            "PostgreSQL is configured but DATABASE_URL is missing. "
            "Production will not fall back to disposable SQLite storage."
        )

    explicit_local = backend == "sqlite" or app_env in {"local", "development", "dev", "test", "testing"}
    if explicit_local:
        raw_path = _read_env("SQLITE_PATH", "pharmadrone.db")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return DatabaseConfig(
            backend="sqlite",
            url=f"sqlite+pysqlite:///{path}",
            sqlite_path=path,
            app_env=app_env or "local",
            connect_timeout=max(1, _as_int("DATABASE_CONNECT_TIMEOUT", 8)),
            connect_retries=max(1, _as_int("DATABASE_CONNECT_RETRIES", 3)),
        )

    raise DatabaseConfigurationError(
        "No durable database is configured. Set DATABASE_URL for PostgreSQL. "
        "For explicit local development only, set DATABASE_BACKEND=sqlite "
        "and optionally SQLITE_PATH."
    )
