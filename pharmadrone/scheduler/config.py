"""Checkpoint 6C.1 scheduler configuration and cadence helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Iterable

UTC = timezone.utc


@dataclass(frozen=True)
class SourceSpec:
    name: str
    source_type: str
    cadence: str
    enabled_env: str = ""
    default_enabled: bool = True
    creates_opportunities: bool = False


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("openfda_enforcement", "regulatory", "daily", creates_opportunities=True),
    SourceSpec("openfda_shortages", "regulatory", "daily", creates_opportunities=True),
    SourceSpec("openfda_labels", "regulatory context", "daily"),
    SourceSpec("clinicaltrials", "clinical trial registry", "every_two_days", creates_opportunities=True),
    SourceSpec("europepmc", "literature", "weekly"),
    SourceSpec("openalex", "literature", "weekly"),
    SourceSpec("crossref", "literature", "weekly"),
    SourceSpec("tavily", "web enrichment", "weekly", enabled_env="TAVILY_API_KEY", default_enabled=False),
    SourceSpec("monthly_maintenance", "maintenance", "monthly"),
)

CADENCE_DELTAS = {
    "daily": timedelta(days=1),
    "every_two_days": timedelta(days=2),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(UTC).replace(microsecond=0).isoformat()


def next_due(cadence: str, *, from_time: datetime | None = None) -> str:
    return iso((from_time or utc_now()) + CADENCE_DELTAS[cadence])


def source_spec(name: str) -> SourceSpec:
    for spec in SOURCE_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"Unknown scheduled source: {name}")


def source_enabled(spec: SourceSpec) -> bool:
    override = os.getenv(f"SCHEDULER_ENABLE_{spec.name.upper()}")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on"}
    if spec.enabled_env:
        return bool(os.getenv(spec.enabled_env))
    return spec.default_enabled


def enabled_specs() -> list[SourceSpec]:
    return [s for s in SOURCE_SPECS if source_enabled(s)]


def _int(name: str, default: int, minimum: int = 0, maximum: int = 100000) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _float(name: str, default: float, minimum: float = 0.0, maximum: float = 100000.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True)
class Guardrails:
    max_pages_per_connector: int
    max_records_per_connector: int
    max_processing_seconds: int
    max_llm_calls: int
    max_tavily_calls: int
    max_estimated_spend_usd: float
    max_concurrent_jobs: int
    retry_attempts: int
    lookback_days: int


def guardrails(*, lookback_days: int | None = None) -> Guardrails:
    return Guardrails(
        max_pages_per_connector=_int("SCHEDULER_MAX_PAGES_PER_CONNECTOR", 3, 1, 20),
        max_records_per_connector=_int("SCHEDULER_MAX_RECORDS_PER_CONNECTOR", 300, 1, 5000),
        max_processing_seconds=_int("SCHEDULER_MAX_PROCESSING_SECONDS", 900, 30, 7200),
        max_llm_calls=_int("SCHEDULER_MAX_LLM_CALLS", 0, 0, 1000),
        max_tavily_calls=_int("SCHEDULER_MAX_TAVILY_CALLS", 10, 0, 1000),
        max_estimated_spend_usd=_float("SCHEDULER_MAX_ESTIMATED_SPEND_USD", 2.0, 0.0, 1000.0),
        max_concurrent_jobs=_int("SCHEDULER_MAX_CONCURRENT_JOBS", 1, 1, 8),
        retry_attempts=_int("SCHEDULER_RETRY_ATTEMPTS", 3, 1, 6),
        lookback_days=lookback_days if lookback_days is not None else _int("SCHEDULER_LOOKBACK_DAYS", 14, 1, 365),
    )


def source_names(specs: Iterable[SourceSpec] = SOURCE_SPECS) -> list[str]:
    return [s.name for s in specs]


def next_orchestrator_run(now: datetime | None = None) -> str:
    """Next documented GitHub cron target: 03:17 UTC daily."""
    current = (now or utc_now()).astimezone(UTC)
    target = current.replace(hour=3, minute=17, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return iso(target)
